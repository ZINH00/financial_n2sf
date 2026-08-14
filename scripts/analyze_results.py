from __future__ import annotations

import argparse
import glob
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from scipy.stats import mannwhitneyu

from common import MODE_AXES, MODES, bootstrap_ci, iqr, percentile, read_jsonl, to_bool_series

# README §11 평가 지표 정의가 여기서 산출하는 값과 항상 일치하도록 관리한다:
# Authorized Flow Success Rate, Unauthorized Flow Block Rate(+policy/structural scope),
# Cross-Business Service/DB Reachability Rate, Blast Radius(mean/max),
# Policy Decision(PEP 처리 지연시간) Latency, Audit Completeness.
#
# 2x2 ablation(정책 세분화 축 x 네트워크 분리 축) 설계이므로 모든 지표를 baseline/
# policy_only/segmentation_only/proposed 4개 모드에 대해 동일한 방식으로 산출하고,
# 어느 지표가 어느 축에만 반응하는지를 axis_group 행으로 별도 표시한다(README §11).

REQUIRED_AUDIT_FIELDS = [
    "request_id", "experiment_run_id", "scenario_id", "user_role",
    "verified_workload_identity", "source_business", "destination", "action",
    "purpose", "data_grade", "policy_version", "decision", "reason",
    "decision_ms", "upstream_ms", "total_ms", "upstream_status", "ts",
]

# U01-U05는 Rego 정책의 역할/목적/행위/방향/업무관계 세분화 여부(정책 축)에 따라
# 모드별 결과가 갈리는 시나리오다. U06-U08은 PEP의 구조적 방어(단말 신뢰, 워크로드
# 신원 검증)에 걸리므로 정책이 광범위한 모드에서도 deny이며, 정책 세분화의 효과를
# 보여주지 않는다. Unauthorized Flow Block Rate 하나만 보면 이 둘이 섞여 광범위
# 정책 모드의 차단률이 실제보다 높아 보이므로, 정책 세분화의 순수 효과만 보는
# 보조 지표를 별도로 산출한다.
POLICY_VIOLATION_TYPES = {"role_mismatch", "purpose_mismatch", "action_mismatch", "reverse_direction", "unrelated_cross_business"}
STRUCTURAL_VIOLATION_TYPES = {"untrusted_device", "unregistered_workload", "identity_spoofing"}

# AFSR/UFBR(Authorized/Unauthorized Flow Success/Block Rate)은 "보안통제가 명시적으로
# 내린 정책 판단"의 비율이어야 한다. 그런데 run_policy_tests.py의 actual(allow/deny)은
# HTTP status 2xx 여부로만 정해지므로, PEP/OPA 연결 실패나 목적 workload 장애 같은
# 정책과 무관한 실행 오류도 그대로 "deny"에 섞여 들어간다. 아래 reason들은 PEP가
# 실제 Rego 판단에 도달하지 못했음을 뜻하므로 AFSR/UFBR 분모·분자에서 제외하고
# 별도 execution_errors로 집계한다(unregistered_workload/workload_signature_invalid는
# 반대로 U07/U08이 검증하려는 정책적 판단 그 자체이므로 제외 대상이 아니다).
STRUCTURAL_ERROR_REASONS = {"opa_transport_error", "opa_error", "upstream_transport_error", "unknown_destination"}

# 4개 모드 중 어느 지표가 정책 축/네트워크 축 중 어디에만 반응해야 하는지 논문에서
# 바로 인용할 수 있도록, experiment_summary.csv에 축별 평균(다른 축은 평균으로 소거)
# 행을 추가하는 대상 지표 목록.
AXIS_SENSITIVE_METRICS = [
    "unauthorized_flow_block_rate_policy_scope",
    "cross_business_app_reachability_rate",
    "cross_business_db_reachability_rate",
    "blast_radius_mean",
    "blast_radius_max",
]

MODE_LABELS = {
    "baseline": "Baseline\n(flat + broad)",
    "policy_only": "Policy-only\n(flat + fine-grained)",
    "segmentation_only": "Segmentation-only\n(segmented + broad)",
    "proposed": "Proposed\n(segmented + fine-grained)",
}
MODE_LABELS_SHORT = {
    "baseline": "Baseline",
    "policy_only": "Policy-only",
    "segmentation_only": "Segmentation-only",
    "proposed": "Proposed",
}


def classify_execution_errors(policy_df: pd.DataFrame, audit_rows: list[dict]) -> pd.DataFrame:
    """attempt_id+experiment_run_id로 PEP 감사로그와 1:1 조인해, 각 요청이 정책과
    무관한 실행 오류(STRUCTURAL_ERROR_REASONS)로 끝났는지 표시하는 is_execution_error
    컬럼을 추가한다. 감사로그에서 매칭되는 기록이 아예 없으면(예: 요청 자체가 PEP에
    도달하지 못함) 보수적으로 실행 오류로 취급한다."""
    index: dict[tuple[str, str], dict] = {}
    for row in audit_rows:
        key = (str(row.get("scenario_id", "")), str(row.get("experiment_run_id", "")))
        index[key] = row
    is_error = []
    audit_reason = []
    for _, prow in policy_df.iterrows():
        key = (str(prow["attempt_id"]), str(prow["experiment_run_id"]))
        rec = index.get(key)
        reason = rec.get("reason") if rec else None
        audit_reason.append(reason)
        is_error.append(reason is None or reason in STRUCTURAL_ERROR_REASONS)
    out = policy_df.copy()
    out["audit_reason"] = audit_reason
    out["is_execution_error"] = is_error
    return out


def rate_valid(df: pd.DataFrame, value: str) -> float:
    """is_execution_error가 True인 행(정책판단이 완료되지 않은 요청)을 제외하고 비율을 계산한다."""
    valid = df[~df["is_execution_error"]] if "is_execution_error" in df.columns else df
    return float((valid["actual"].astype(str) == value).mean()) if len(valid) else float("nan")

PALETTE = {
    "surface": "#fcfcfb",
    "text_primary": "#0b0b0b",
    "text_secondary": "#52514e",
    "muted": "#898781",
    "gridline": "#e1e0d9",
    "axis": "#c3c2b7",
    "baseline": "#2a78d6",
    "policy_only": "#eb6834",
    "segmentation_only": "#1f9e89",
    "proposed": "#d1a12a",
}


def latest(pattern: str) -> Path | None:
    matches = sorted(Path(p) for p in glob.glob(pattern))
    return matches[-1] if matches else None


def blast_radius(reach_df: pd.DataFrame) -> pd.DataFrame:
    """업무별 Blast Radius = 해당 업무 컨테이너에서 직접 도달 가능한 타 업무 DB 수."""
    cross = reach_df[reach_df["relationship"] == "cross_business_db"].copy()
    cross["source_business"] = cross["source_container"].str.replace("_app", "", regex=False)
    cross["reachable_bool"] = to_bool_series(cross["reachable"])
    per_business = cross.groupby("source_business")["reachable_bool"].sum().reset_index()
    per_business.columns = ["source_business", "blast_radius"]
    return per_business


def audit_completeness(policy_df: pd.DataFrame, audit_rows: list[dict]) -> float:
    """필수 필드를 모두 포함하고 개별 요청과 1:1로 상관관계가 확인된 감사로그 수 /
    전체 요청 수. `--repeat`로 동일 시나리오를 여러 번 반복해도 요청마다 고유한
    attempt_id(예: A01-r001)를 PEP의 scenario_id 필드로 사용하므로, 30번 반복 중
    일부만 로그에 남아도 그 차이가 그대로 드러난다(coarse하게 scenario_id만으로
    묶으면 1건만 완전해도 30건 전체가 매칭된 것처럼 보이는 문제를 피한다)."""
    if policy_df.empty:
        return float("nan")
    index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in audit_rows:
        key = (str(row.get("scenario_id", "")), str(row.get("experiment_run_id", "")))
        index[key].append(row)
    matched = 0
    for _, prow in policy_df.iterrows():
        key = (str(prow["attempt_id"]), str(prow["experiment_run_id"]))
        candidates = index.get(key, [])
        if any(all(field in c for field in REQUIRED_AUDIT_FIELDS) for c in candidates):
            matched += 1
    return matched / len(policy_df)


def batch_latency_stats(audit_rows: list[dict], experiment_run_id: str | None, flow_prefix: str = "PERF-") -> pd.DataFrame:
    """PEP 감사로그에서 성능 시나리오(scenario_id=PERF-<flow>-b<NNN>)의 허용된
    요청만 골라 배치별 median(decision_ms, total_ms)을 산출한다. 감사로그는 같은
    모드를 재실행할 때마다 기존 파일 뒤에 append되므로(README §7.3), 최신
    raw_performance_*.csv에 기록된 experiment_run_id로 반드시 필터링해서 이전
    실행의 동일 배치ID(예: PERF-loan_to_credit-b001)와 섞이지 않게 한다."""
    by_batch: dict[tuple[str, int], dict[str, list[float]]] = defaultdict(lambda: {"decision_ms": [], "total_ms": []})
    for row in audit_rows:
        scenario_id = str(row.get("scenario_id", ""))
        if not scenario_id.startswith(flow_prefix) or "-b" not in scenario_id or row.get("decision") != "allow":
            continue
        if experiment_run_id is not None and str(row.get("experiment_run_id", "")) != str(experiment_run_id):
            continue
        flow_name, _, batch_part = scenario_id[len(flow_prefix):].rpartition("-b")
        try:
            batch_id = int(batch_part)
        except ValueError:
            continue
        by_batch[(flow_name, batch_id)]["decision_ms"].append(float(row["decision_ms"]))
        by_batch[(flow_name, batch_id)]["total_ms"].append(float(row["total_ms"]))
    records = []
    for (flow_name, batch_id), values in sorted(by_batch.items()):
        if not values["decision_ms"]:
            continue
        records.append({
            "flow": flow_name,
            "batch_id": batch_id,
            "decision_ms_median": statistics.median(values["decision_ms"]),
            "total_ms_median": statistics.median(values["total_ms"]),
            "n": len(values["decision_ms"]),
        })
    return pd.DataFrame(records)


def summarize_latency(batch_df: pd.DataFrame, column: str) -> dict[str, float]:
    if column not in batch_df or batch_df.empty:
        return {"median": float("nan"), "iqr": float("nan"), "p95": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n_batches": 0}
    values = batch_df[column].dropna().tolist()
    ci_low, ci_high = bootstrap_ci(values)
    return {
        "median": statistics.median(values) if values else float("nan"),
        "iqr": iqr(values),
        "p95": percentile(values, 0.95),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "n_batches": len(values),
    }


def load_mode_data(mode: str, results_dir: Path) -> dict:
    """단일 모드의 원본 CSV·감사로그를 모두 로드하고 실행오류 분류·집계까지 마친
    결과를 딕셔너리로 반환한다. 4개 모드 각각에 대해 동일하게 호출된다."""
    patterns = {
        "policy": latest(str(results_dir / f"raw_policy_{mode}_*.csv")),
        "cds": latest(str(results_dir / f"raw_cds_{mode}_*.csv")),
        "reach": latest(str(results_dir / f"raw_reachability_{mode}_*.csv")),
        "perf": latest(str(results_dir / f"raw_performance_{mode}_*.csv")),
    }
    missing = [k for k, v in patterns.items() if v is None]
    if missing:
        raise SystemExit(f"Missing result files for mode={mode}: " + ", ".join(missing))

    pdf = pd.read_csv(patterns["policy"])
    cdf = pd.read_csv(patterns["cds"])
    rdf = pd.read_csv(patterns["reach"])
    fdf = pd.read_csv(patterns["perf"])
    perf_run_id = str(fdf["experiment_run_id"].iloc[0]) if len(fdf) else None

    audit = read_jsonl(results_dir / f"pep_audit_{mode}.jsonl")
    if not audit:
        print(f"warning: pep_audit_{mode}.jsonl not found or empty under --results-dir; "
              f"latency/audit-completeness metrics will be NaN for mode={mode}. Did the PEP write to ./results as /logs?")

    pdf = classify_execution_errors(pdf, audit)

    authorized = pdf[pdf["scenario_id"].astype(str).str.startswith("A")]
    unauthorized = pdf[pdf["scenario_id"].astype(str).str.startswith("U")]
    unauthorized_policy = unauthorized[unauthorized["violation_type"].isin(POLICY_VIOLATION_TYPES)]
    unauthorized_structural = unauthorized[unauthorized["violation_type"].isin(STRUCTURAL_VIOLATION_TYPES)]
    cross_db = rdf[rdf["relationship"] == "cross_business_db"]
    cross_app = rdf[rdf["relationship"] == "cross_business_app"]

    blast = blast_radius(rdf)
    blast["mode"] = mode

    batch = batch_latency_stats(audit, perf_run_id)

    # 네트워크가 flat(공유망)이면 업무 간 직접 도달이 가능한 것이 설계상 정상이므로
    # reachable=true가 기대값이고, segmented(분리망)면 reachable=false가 기대값이다.
    # baseline/policy_only는 정책축과 무관하게 둘 다 flat 네트워크이므로 도달 가능한
    # 것이 "잘못된 결과"가 아니라 애초에 그렇게 설계된 비교군의 정상 동작이다.
    reachability_expected_true = MODE_AXES[mode]["network"] == "flat"

    return {
        "mode": mode,
        "policy_df": pdf,
        "cds_df": cdf,
        "reach_df": rdf,
        "authorized": authorized,
        "unauthorized": unauthorized,
        "unauthorized_policy": unauthorized_policy,
        "unauthorized_structural": unauthorized_structural,
        "cross_db": cross_db,
        "cross_app": cross_app,
        "blast": blast,
        "batch": batch,
        "completeness": audit_completeness(pdf, audit),
        "decision_stats": summarize_latency(batch, "decision_ms_median"),
        "total_stats": summarize_latency(batch, "total_ms_median"),
        "reachability_expected_true": reachability_expected_true,
    }


def _style_axes(ax) -> None:
    ax.set_facecolor(PALETTE["surface"])
    ax.figure.set_facecolor(PALETTE["surface"])
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(PALETTE["axis"])
    ax.tick_params(colors=PALETTE["text_secondary"])
    ax.yaxis.grid(True, color=PALETTE["gridline"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.xaxis.label.set_color(PALETTE["text_primary"])
    ax.yaxis.label.set_color(PALETTE["text_primary"])
    ax.title.set_color(PALETTE["text_primary"])


def _bar_labels(ax, bars, fmt) -> None:
    for bar in bars:
        height = bar.get_height()
        ax.annotate(fmt(height), (bar.get_x() + bar.get_width() / 2, height), textcoords="offset points", xytext=(0, 4), ha="center", fontsize=7.5, color=PALETTE["text_primary"])


def plot_security_effectiveness(summary_by_mode: pd.DataFrame, results: Path) -> None:
    metrics = ["authorized_flow_success_rate", "unauthorized_flow_block_rate", "cross_business_app_reachability_rate", "cross_business_db_reachability_rate"]
    labels = ["Authorized Flow\nSuccess Rate", "Unauthorized Flow\nBlock Rate", "Cross-Business Service\nReachability Rate", "Cross-Business DB\nReachability Rate"]
    x = list(range(len(metrics)))
    n_modes = len(MODES)
    width = 0.8 / n_modes
    fig, ax = plt.subplots(figsize=(11.5, 5.5))
    for i, mode in enumerate(MODES):
        offset = (i - (n_modes - 1) / 2) * width
        vals = [float(summary_by_mode.loc[(summary_by_mode["metric"] == m) & (summary_by_mode["mode"] == mode), "value"].iloc[0]) for m in metrics]
        bars = ax.bar([xi + offset for xi in x], vals, width, label=MODE_LABELS_SHORT[mode], color=PALETTE[mode])
        _bar_labels(ax, bars, lambda h: f"{h*100:.0f}%")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8.5)
    ax.set_ylabel("Rate")
    ax.set_ylim(0, 1.15)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=4)
    _style_axes(ax)
    fig.tight_layout()
    fig.savefig(results / "security_effectiveness.png", dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def plot_blast_radius(blast_df: pd.DataFrame, results: Path) -> None:
    if blast_df.empty:
        return
    pivot = blast_df.pivot(index="source_business", columns="mode", values="blast_radius").fillna(0)
    order = [b for b in ["customer", "loan", "credit", "aml", "approval"] if b in pivot.index]
    pivot = pivot.reindex(order)
    for mode in MODES:
        if mode not in pivot:
            pivot[mode] = 0
    x = list(range(len(pivot.index)))
    n_modes = len(MODES)
    width = 0.8 / n_modes
    fig, ax = plt.subplots(figsize=(9.5, 5))
    for i, mode in enumerate(MODES):
        offset = (i - (n_modes - 1) / 2) * width
        bars = ax.bar([xi + offset for xi in x], pivot[mode], width, label=MODE_LABELS_SHORT[mode], color=PALETTE[mode])
        _bar_labels(ax, bars, lambda h: f"{int(h)}")
    ax.set_xticks(x)
    ax.set_xticklabels(pivot.index, fontsize=9)
    ax.set_ylabel("Blast radius (reachable cross-business DBs, max 4)")
    ax.set_ylim(0, 4.8)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=4)
    _style_axes(ax)
    fig.tight_layout()
    fig.savefig(results / "blast_radius.png", dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def plot_latency(batch_by_mode: dict[str, pd.DataFrame], results: Path) -> None:
    values = [batch_by_mode[mode]["decision_ms_median"].dropna().tolist() if not batch_by_mode[mode].empty else [] for mode in MODES]
    if not any(values):
        return
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    bp = ax.boxplot(values, tick_labels=[MODE_LABELS_SHORT[m] for m in MODES], patch_artist=True, widths=0.5, medianprops={"color": PALETTE["text_primary"], "linewidth": 1.5})
    for patch, mode in zip(bp["boxes"], MODES):
        patch.set_facecolor(PALETTE[mode])
        patch.set_alpha(0.75)
        patch.set_edgecolor(PALETTE[mode])
    for element in ("whiskers", "caps"):
        for line in bp[element]:
            line.set_color(PALETTE["muted"])
    ax.set_ylabel("Policy decision latency, per-batch median (ms)")
    plt.setp(ax.get_xticklabels(), rotation=15, ha="right", fontsize=8.5)
    _style_axes(ax)
    fig.tight_layout()
    fig.savefig(results / "latency_boxplot.png", dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize experiment outputs after all four ablation modes have been executed.")
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    results = args.results_dir

    data = {mode: load_mode_data(mode, results) for mode in MODES}

    # ---- 모드별 롱포맷 experiment_summary.csv ----
    summary_rows = []
    for mode in MODES:
        d = data[mode]
        per_mode_metrics = [
            ("authorized_flow_success_rate", rate_valid(d["authorized"], "allow")),
            ("unauthorized_flow_block_rate", rate_valid(d["unauthorized"], "deny")),
            ("unauthorized_flow_block_rate_policy_scope", rate_valid(d["unauthorized_policy"], "deny")),
            ("unauthorized_flow_block_rate_structural_scope", rate_valid(d["unauthorized_structural"], "deny")),
            ("cross_business_app_reachability_rate", to_bool_series(d["cross_app"]["reachable"]).mean() if len(d["cross_app"]) else float("nan")),
            ("cross_business_db_reachability_rate", to_bool_series(d["cross_db"]["reachable"]).mean() if len(d["cross_db"]) else float("nan")),
            ("blast_radius_mean", d["blast"]["blast_radius"].mean() if len(d["blast"]) else float("nan")),
            ("blast_radius_max", d["blast"]["blast_radius"].max() if len(d["blast"]) else float("nan")),
            ("decision_latency_median_ms", d["decision_stats"]["median"]),
            ("decision_latency_p95_ms", d["decision_stats"]["p95"]),
            ("total_latency_median_ms", d["total_stats"]["median"]),
            ("total_latency_p95_ms", d["total_stats"]["p95"]),
            ("audit_completeness_rate", d["completeness"]),
        ]
        for metric, value in per_mode_metrics:
            summary_rows.append({
                "metric": metric,
                "mode": mode,
                "policy_axis": MODE_AXES[mode]["policy"],
                "network_axis": MODE_AXES[mode]["network"],
                "value": value,
                "axis_group": "",
            })
    summary_by_mode = pd.DataFrame(summary_rows)

    baseline_vals = summary_by_mode[summary_by_mode["mode"] == "baseline"].set_index("metric")["value"]

    def relative_change(row: pd.Series) -> float:
        if row["mode"] == "baseline":
            return float("nan")
        base = baseline_vals.get(row["metric"], float("nan"))
        if not base or pd.isna(base):
            return float("nan")
        return (row["value"] - base) / base

    summary_by_mode["relative_change_vs_baseline"] = summary_by_mode.apply(relative_change, axis=1)

    # ---- 축별 평균(다른 축은 평균으로 소거) 행: "정책축만 반응/네트워크축만 반응"
    # 패턴을 숫자로 직접 확인할 수 있게 한다. 예를 들어
    # unauthorized_flow_block_rate_policy_scope는 policy_axis로 묶으면(broad≈0,
    # finegrained≈1) 뚜렷이 갈리지만, network_axis로 묶으면(flat≈0.5, segmented≈0.5)
    # 차이가 사라져야 정책축에만 반응한다는 주장이 성립한다. mode 칸이 비어 있는
    # 행은 "그 축의 값을 고정하고 나머지 축은 평균으로 소거했다"는 뜻이다.
    axis_rows = []
    for metric in AXIS_SENSITIVE_METRICS:
        metric_slice = summary_by_mode[summary_by_mode["metric"] == metric]
        for axis_key, axis_values in (("policy", ("broad", "finegrained")), ("network", ("flat", "segmented"))):
            for axis_value in axis_values:
                modes_in_group = [m for m in MODES if MODE_AXES[m][axis_key] == axis_value]
                vals = metric_slice[metric_slice["mode"].isin(modes_in_group)]["value"].astype(float).tolist()
                axis_rows.append({
                    "metric": metric,
                    "mode": "",
                    "policy_axis": axis_value if axis_key == "policy" else "",
                    "network_axis": axis_value if axis_key == "network" else "",
                    "value": statistics.mean(vals) if vals else float("nan"),
                    "relative_change_vs_baseline": float("nan"),
                    "axis_group": f"{axis_key}_axis={axis_value} (mean over {','.join(modes_in_group)})",
                })
    axis_summary = pd.DataFrame(axis_rows)

    summary_out = pd.concat([summary_by_mode, axis_summary], ignore_index=True)
    summary_out = summary_out[["metric", "mode", "policy_axis", "network_axis", "value", "relative_change_vs_baseline", "axis_group"]]
    summary_out.to_csv(results / "experiment_summary.csv", index=False)

    # ---- blast_radius.csv (롱포맷, 4개 모드) ----
    blast_df = pd.concat([data[mode]["blast"] for mode in MODES], ignore_index=True)
    blast_df.to_csv(results / "blast_radius.csv", index=False)

    # ---- latency_by_flow.csv / latency_confidence_intervals.csv (롱포맷, 4개 모드) ----
    latency_by_flow = pd.concat([data[mode]["batch"].assign(mode=mode) for mode in MODES], ignore_index=True)
    latency_by_flow.to_csv(results / "latency_by_flow.csv", index=False)

    latency_ci_rows = []
    for mode in MODES:
        d = data[mode]
        latency_ci_rows.append({"metric": "decision_ms", "mode": mode, **d["decision_stats"]})
        latency_ci_rows.append({"metric": "total_ms", "mode": mode, **d["total_stats"]})
    pd.DataFrame(latency_ci_rows).to_csv(results / "latency_confidence_intervals.csv", index=False)

    # ---- exact_count_summary.csv (롱포맷, 4개 모드) ----
    # 반복된 동일 요청을 독립 표본처럼 Fisher 검정에 넣지 않고, 보안·기능
    # 시나리오는 exact count/rate로만 보고한다. cross_business_{app,db}
    # reachability의 "기대값" 방향은 네트워크 축(flat/segmented)에 따라 다르므로
    # load_mode_data가 계산해 둔 reachability_expected_true를 그대로 사용한다.
    def count_row(category: str, mode: str, df: pd.DataFrame) -> dict:
        # total/expected_match은 "정책판단이 정상 완료된 요청" 기준으로만 집계한다
        # (AFSR/UFBR의 N을 실행 오류로 오염시키지 않기 위함). 실행 오류 건수는
        # execution_errors로 별도 확인할 수 있다.
        errors = int(df["is_execution_error"].sum())
        valid = df[~df["is_execution_error"]]
        matched = int((valid["actual"] == valid["expected"]).sum()) if len(valid) else 0
        return {"category": category, "mode": mode, "total": len(valid), "expected_match": matched, "execution_errors": errors}

    exact_rows = []
    for mode in MODES:
        d = data[mode]
        exact_rows.append(count_row("authorized_flows", mode, d["authorized"]))
        exact_rows.append(count_row("unauthorized_flows", mode, d["unauthorized"]))
        exact_rows.append(count_row("unauthorized_flows_policy_scope", mode, d["unauthorized_policy"]))
        exact_rows.append(count_row("unauthorized_flows_structural_scope", mode, d["unauthorized_structural"]))

        expected_true = d["reachability_expected_true"]
        cross_app_bool = to_bool_series(d["cross_app"]["reachable"]) if len(d["cross_app"]) else pd.Series(dtype=bool)
        cross_db_bool = to_bool_series(d["cross_db"]["reachable"]) if len(d["cross_db"]) else pd.Series(dtype=bool)
        app_match = int(cross_app_bool.sum()) if expected_true else int((~cross_app_bool).sum())
        db_match = int(cross_db_bool.sum()) if expected_true else int((~cross_db_bool).sum())
        exact_rows.append({"category": "cross_business_app_reachability", "mode": mode, "total": len(d["cross_app"]), "expected_match": app_match, "execution_errors": 0})
        exact_rows.append({"category": "cross_business_db_reachability", "mode": mode, "total": len(d["cross_db"]), "expected_match": db_match, "execution_errors": 0})

        cdf = d["cds_df"]
        exact_rows.append({"category": "cds_transfer", "mode": mode, "total": len(cdf), "expected_match": int(to_bool_series(cdf["matches_expected"]).sum()), "execution_errors": 0})

    exact_counts = pd.DataFrame(exact_rows)
    exact_counts["match_rate"] = exact_counts["expected_match"] / exact_counts["total"].replace(0, pd.NA)
    exact_counts.to_csv(results / "exact_count_summary.csv", index=False)

    # ---- statistical_tests.csv: 성능은 표본 규모가 크므로(배치×요청) 배치 median
    # 간 Mann-Whitney U를 보조 지표로 유지한다. 4-way 전체 조합 비교는 범위 밖이므로
    # 기존 2조건 비교(baseline vs proposed)만 유지한다. ----
    stats_rows = []
    batch_baseline = data["baseline"]["batch"]
    batch_proposed = data["proposed"]["batch"]
    if len(batch_baseline) and len(batch_proposed):
        for column, label in (("decision_ms_median", "decision_ms"), ("total_ms_median", "total_ms")):
            u_stat, mw_p = mannwhitneyu(batch_baseline[column], batch_proposed[column], alternative="two-sided")
            stats_rows.append({"test": f"Mann-Whitney U - batch median {label} (baseline vs proposed)", "statistic": u_stat, "p_value": mw_p, "n_batches_baseline": len(batch_baseline), "n_batches_proposed": len(batch_proposed)})
    pd.DataFrame(stats_rows).to_csv(results / "statistical_tests.csv", index=False)

    plot_security_effectiveness(summary_by_mode, results)
    plot_blast_radius(blast_df, results)
    plot_latency({mode: data[mode]["batch"] for mode in MODES}, results)

    print(results / "experiment_summary.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
