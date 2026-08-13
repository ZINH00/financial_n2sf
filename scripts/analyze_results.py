from __future__ import annotations

import argparse
import glob
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from scipy.stats import mannwhitneyu

from common import bootstrap_ci, iqr, percentile, read_jsonl, to_bool_series

# README §11 평가 지표 정의가 여기서 산출하는 값과 항상 일치하도록 관리한다:
# Authorized Flow Success Rate, Unauthorized Flow Block Rate(+policy/structural scope),
# Cross-Business Service/DB Reachability Rate, Blast Radius(mean/max),
# Policy Decision(PEP 처리 지연시간) Latency, Audit Completeness.

REQUIRED_AUDIT_FIELDS = [
    "request_id", "experiment_run_id", "scenario_id", "user_role",
    "verified_workload_identity", "source_business", "destination", "action",
    "purpose", "data_grade", "policy_version", "decision", "reason",
    "decision_ms", "upstream_ms", "total_ms", "upstream_status", "ts",
]

# U01-U05는 Rego 정책의 역할/목적/행위/방향/업무관계 세분화 여부에 따라 baseline과
# proposed 결과가 갈리는 시나리오다. U06-U08은 PEP의 구조적 방어(단말 신뢰, 워크로드
# 신원 검증)에 걸리므로 baseline에서도 deny이며, 정책 세분화의 효과를 보여주지 않는다.
# Unauthorized Flow Block Rate 하나만 보면 이 둘이 섞여 baseline의 차단률이 실제보다
# 높아 보이므로, 정책 세분화의 순수 효과만 보는 보조 지표를 별도로 산출한다.
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
    "proposed": "#eb6834",
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
        ax.annotate(fmt(height), (bar.get_x() + bar.get_width() / 2, height), textcoords="offset points", xytext=(0, 4), ha="center", fontsize=9, color=PALETTE["text_primary"])


def plot_security_effectiveness(summary: pd.DataFrame, results: Path) -> None:
    metrics = ["authorized_flow_success_rate", "unauthorized_flow_block_rate", "cross_business_app_reachability_rate", "cross_business_db_reachability_rate"]
    labels = ["Authorized Flow\nSuccess Rate", "Unauthorized Flow\nBlock Rate", "Cross-Business Service\nReachability Rate", "Cross-Business DB\nReachability Rate"]
    plot_df = summary.set_index("metric").loc[metrics]
    x = list(range(len(metrics)))
    width = 0.32
    fig, ax = plt.subplots(figsize=(9.5, 5))
    bars_b = ax.bar([i - width / 2 for i in x], plot_df["baseline"], width, label="Baseline (flat network)", color=PALETTE["baseline"])
    bars_p = ax.bar([i + width / 2 for i in x], plot_df["proposed"], width, label="Proposed (role-based microsegmentation)", color=PALETTE["proposed"])
    _bar_labels(ax, bars_b, lambda h: f"{h*100:.0f}%")
    _bar_labels(ax, bars_p, lambda h: f"{h*100:.0f}%")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8.5)
    ax.set_ylabel("Rate")
    ax.set_ylim(0, 1.15)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=2)
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
    for col in ("baseline", "proposed"):
        if col not in pivot:
            pivot[col] = 0
    x = list(range(len(pivot.index)))
    width = 0.32
    fig, ax = plt.subplots(figsize=(8, 5))
    bars_b = ax.bar([i - width / 2 for i in x], pivot["baseline"], width, label="Baseline", color=PALETTE["baseline"])
    bars_p = ax.bar([i + width / 2 for i in x], pivot["proposed"], width, label="Proposed", color=PALETTE["proposed"])
    _bar_labels(ax, bars_b, lambda h: f"{int(h)}")
    _bar_labels(ax, bars_p, lambda h: f"{int(h)}")
    ax.set_xticks(x)
    ax.set_xticklabels(pivot.index, fontsize=9)
    ax.set_ylabel("Blast radius (reachable cross-business DBs, max 4)")
    ax.set_ylim(0, 4.8)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=2)
    _style_axes(ax)
    fig.tight_layout()
    fig.savefig(results / "blast_radius.png", dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def plot_latency(batch_b: pd.DataFrame, batch_p: pd.DataFrame, results: Path) -> None:
    values_b = batch_b["decision_ms_median"].dropna().tolist() if not batch_b.empty else []
    values_p = batch_p["decision_ms_median"].dropna().tolist() if not batch_p.empty else []
    if not values_b and not values_p:
        return
    fig, ax = plt.subplots(figsize=(6, 5))
    bp = ax.boxplot([values_b, values_p], tick_labels=["Baseline", "Proposed"], patch_artist=True, widths=0.5, medianprops={"color": PALETTE["text_primary"], "linewidth": 1.5})
    for patch, color in zip(bp["boxes"], (PALETTE["baseline"], PALETTE["proposed"])):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)
        patch.set_edgecolor(color)
    for element in ("whiskers", "caps"):
        for line in bp[element]:
            line.set_color(PALETTE["muted"])
    ax.set_ylabel("Policy decision latency, per-batch median (ms)")
    _style_axes(ax)
    fig.tight_layout()
    fig.savefig(results / "latency_boxplot.png", dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize experiment outputs after both modes have been executed.")
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    results = args.results_dir

    patterns = {
        "policy_baseline": latest(str(results / "raw_policy_baseline_*.csv")),
        "policy_proposed": latest(str(results / "raw_policy_proposed_*.csv")),
        "cds_baseline": latest(str(results / "raw_cds_baseline_*.csv")),
        "cds_proposed": latest(str(results / "raw_cds_proposed_*.csv")),
        "reach_baseline": latest(str(results / "raw_reachability_baseline_*.csv")),
        "reach_proposed": latest(str(results / "raw_reachability_proposed_*.csv")),
        "perf_baseline": latest(str(results / "raw_performance_baseline_*.csv")),
        "perf_proposed": latest(str(results / "raw_performance_proposed_*.csv")),
    }
    missing = [k for k, v in patterns.items() if v is None]
    if missing:
        raise SystemExit("Missing result files: " + ", ".join(missing))

    pb, pp = pd.read_csv(patterns["policy_baseline"]), pd.read_csv(patterns["policy_proposed"])
    cb, cp = pd.read_csv(patterns["cds_baseline"]), pd.read_csv(patterns["cds_proposed"])
    rb, rp = pd.read_csv(patterns["reach_baseline"]), pd.read_csv(patterns["reach_proposed"])
    fb, fp = pd.read_csv(patterns["perf_baseline"]), pd.read_csv(patterns["perf_proposed"])
    perf_run_id_b = str(fb["experiment_run_id"].iloc[0]) if len(fb) else None
    perf_run_id_p = str(fp["experiment_run_id"].iloc[0]) if len(fp) else None

    audit_b = read_jsonl(results / "pep_audit_baseline.jsonl")
    audit_p = read_jsonl(results / "pep_audit_proposed.jsonl")
    if not audit_b or not audit_p:
        print("warning: pep_audit_{baseline,proposed}.jsonl not found or empty under --results-dir; "
              "latency/audit-completeness metrics will be NaN. Did the PEP write to ./results as /logs?")

    pb = classify_execution_errors(pb, audit_b)
    pp = classify_execution_errors(pp, audit_p)

    authorized_b = pb[pb["scenario_id"].astype(str).str.startswith("A")]
    authorized_p = pp[pp["scenario_id"].astype(str).str.startswith("A")]
    unauthorized_b = pb[pb["scenario_id"].astype(str).str.startswith("U")]
    unauthorized_p = pp[pp["scenario_id"].astype(str).str.startswith("U")]
    unauthorized_policy_b = unauthorized_b[unauthorized_b["violation_type"].isin(POLICY_VIOLATION_TYPES)]
    unauthorized_policy_p = unauthorized_p[unauthorized_p["violation_type"].isin(POLICY_VIOLATION_TYPES)]
    unauthorized_structural_b = unauthorized_b[unauthorized_b["violation_type"].isin(STRUCTURAL_VIOLATION_TYPES)]
    unauthorized_structural_p = unauthorized_p[unauthorized_p["violation_type"].isin(STRUCTURAL_VIOLATION_TYPES)]
    cross_b = rb[rb["relationship"] == "cross_business_db"]
    cross_p = rp[rp["relationship"] == "cross_business_db"]
    cross_app_b = rb[rb["relationship"] == "cross_business_app"]
    cross_app_p = rp[rp["relationship"] == "cross_business_app"]

    blast_b = blast_radius(rb)
    blast_p = blast_radius(rp)
    blast_b["mode"] = "baseline"
    blast_p["mode"] = "proposed"
    blast_df = pd.concat([blast_b, blast_p], ignore_index=True)
    blast_df.to_csv(results / "blast_radius.csv", index=False)

    batch_b = batch_latency_stats(audit_b, perf_run_id_b)
    batch_p = batch_latency_stats(audit_p, perf_run_id_p)
    latency_by_flow = pd.concat([
        batch_b.assign(mode="baseline"),
        batch_p.assign(mode="proposed"),
    ], ignore_index=True)
    latency_by_flow.to_csv(results / "latency_by_flow.csv", index=False)

    decision_b = summarize_latency(batch_b, "decision_ms_median")
    decision_p = summarize_latency(batch_p, "decision_ms_median")
    total_b = summarize_latency(batch_b, "total_ms_median")
    total_p = summarize_latency(batch_p, "total_ms_median")

    completeness_b = audit_completeness(pb, audit_b)
    completeness_p = audit_completeness(pp, audit_p)

    summary = pd.DataFrame([
        {"metric": "authorized_flow_success_rate", "baseline": rate_valid(authorized_b, "allow"), "proposed": rate_valid(authorized_p, "allow")},
        {"metric": "unauthorized_flow_block_rate", "baseline": rate_valid(unauthorized_b, "deny"), "proposed": rate_valid(unauthorized_p, "deny")},
        {"metric": "unauthorized_flow_block_rate_policy_scope", "baseline": rate_valid(unauthorized_policy_b, "deny"), "proposed": rate_valid(unauthorized_policy_p, "deny")},
        {"metric": "unauthorized_flow_block_rate_structural_scope", "baseline": rate_valid(unauthorized_structural_b, "deny"), "proposed": rate_valid(unauthorized_structural_p, "deny")},
        {"metric": "cross_business_app_reachability_rate", "baseline": to_bool_series(cross_app_b["reachable"]).mean() if len(cross_app_b) else float("nan"), "proposed": to_bool_series(cross_app_p["reachable"]).mean() if len(cross_app_p) else float("nan")},
        {"metric": "cross_business_db_reachability_rate", "baseline": to_bool_series(cross_b["reachable"]).mean() if len(cross_b) else float("nan"), "proposed": to_bool_series(cross_p["reachable"]).mean() if len(cross_p) else float("nan")},
        {"metric": "blast_radius_mean", "baseline": blast_b["blast_radius"].mean() if len(blast_b) else float("nan"), "proposed": blast_p["blast_radius"].mean() if len(blast_p) else float("nan")},
        {"metric": "blast_radius_max", "baseline": blast_b["blast_radius"].max() if len(blast_b) else float("nan"), "proposed": blast_p["blast_radius"].max() if len(blast_p) else float("nan")},
        {"metric": "decision_latency_median_ms", "baseline": decision_b["median"], "proposed": decision_p["median"]},
        {"metric": "decision_latency_p95_ms", "baseline": decision_b["p95"], "proposed": decision_p["p95"]},
        {"metric": "total_latency_median_ms", "baseline": total_b["median"], "proposed": total_p["median"]},
        {"metric": "total_latency_p95_ms", "baseline": total_b["p95"], "proposed": total_p["p95"]},
        {"metric": "audit_completeness_rate", "baseline": completeness_b, "proposed": completeness_p},
    ])
    summary["relative_change"] = (summary["proposed"] - summary["baseline"]) / summary["baseline"].replace(0, pd.NA)
    summary.to_csv(results / "experiment_summary.csv", index=False)

    latency_ci = pd.DataFrame([
        {"metric": "decision_ms", "mode": "baseline", **decision_b},
        {"metric": "decision_ms", "mode": "proposed", **decision_p},
        {"metric": "total_ms", "mode": "baseline", **total_b},
        {"metric": "total_ms", "mode": "proposed", **total_p},
    ])
    latency_ci.to_csv(results / "latency_confidence_intervals.csv", index=False)

    # 반복된 동일 요청을 독립 표본처럼 Fisher 검정에 넣지 않고, 보안·기능
    # 시나리오는 exact count/rate로만 보고한다.
    #
    # cross_business_{app,db}_reachability의 "기대값"은 baseline과 proposed가
    # 서로 다르다: baseline(flat network)은 설계상 교차 업무 도달이 가능한 것이
    # 정상이므로 reachable=true가 기대값이고, proposed(업무별 segment)는
    # reachable=false가 기대값이다. 두 모드에 같은 기대값(false)을 쓰면 baseline이
    # 마치 "잘못된 결과"만 낸 것처럼 보이는데, baseline은 원래 그렇게 동작하도록
    # 설계된 비교군이므로 이는 잘못된 계산이다.
    def count_row(category: str, mode: str, df: pd.DataFrame) -> dict:
        # total/expected_match은 "정책판단이 정상 완료된 요청" 기준으로만 집계한다
        # (AFSR/UFBR의 N을 실행 오류로 오염시키지 않기 위함). 실행 오류 건수는
        # execution_errors로 별도 확인할 수 있다.
        errors = int(df["is_execution_error"].sum())
        valid = df[~df["is_execution_error"]]
        matched = int((valid["actual"] == valid["expected"]).sum()) if len(valid) else 0
        return {"category": category, "mode": mode, "total": len(valid), "expected_match": matched, "execution_errors": errors}

    exact_counts = pd.DataFrame([
        count_row("authorized_flows", "baseline", authorized_b),
        count_row("authorized_flows", "proposed", authorized_p),
        count_row("unauthorized_flows", "baseline", unauthorized_b),
        count_row("unauthorized_flows", "proposed", unauthorized_p),
        count_row("unauthorized_flows_policy_scope", "baseline", unauthorized_policy_b),
        count_row("unauthorized_flows_policy_scope", "proposed", unauthorized_policy_p),
        count_row("unauthorized_flows_structural_scope", "baseline", unauthorized_structural_b),
        count_row("unauthorized_flows_structural_scope", "proposed", unauthorized_structural_p),
        {"category": "cross_business_app_reachability", "mode": "baseline", "total": len(cross_app_b), "expected_match": int(to_bool_series(cross_app_b["reachable"]).sum()), "execution_errors": 0},
        {"category": "cross_business_app_reachability", "mode": "proposed", "total": len(cross_app_p), "expected_match": int((~to_bool_series(cross_app_p["reachable"])).sum()), "execution_errors": 0},
        {"category": "cross_business_db_reachability", "mode": "baseline", "total": len(cross_b), "expected_match": int(to_bool_series(cross_b["reachable"]).sum()), "execution_errors": 0},
        {"category": "cross_business_db_reachability", "mode": "proposed", "total": len(cross_p), "expected_match": int((~to_bool_series(cross_p["reachable"])).sum()), "execution_errors": 0},
        {"category": "cds_transfer", "mode": "baseline", "total": len(cb), "expected_match": int(to_bool_series(cb["matches_expected"]).sum()), "execution_errors": 0},
        {"category": "cds_transfer", "mode": "proposed", "total": len(cp), "expected_match": int(to_bool_series(cp["matches_expected"]).sum()), "execution_errors": 0},
    ])
    exact_counts["match_rate"] = exact_counts["expected_match"] / exact_counts["total"].replace(0, pd.NA)
    exact_counts.to_csv(results / "exact_count_summary.csv", index=False)

    # 성능은 표본 규모가 크므로(배치×요청) 배치 median 간 Mann-Whitney U를 보조 지표로 유지.
    stats_rows = []
    if len(batch_b) and len(batch_p):
        for column, label in (("decision_ms_median", "decision_ms"), ("total_ms_median", "total_ms")):
            u_stat, mw_p = mannwhitneyu(batch_b[column], batch_p[column], alternative="two-sided")
            stats_rows.append({"test": f"Mann-Whitney U - batch median {label} (baseline vs proposed)", "statistic": u_stat, "p_value": mw_p, "n_batches_baseline": len(batch_b), "n_batches_proposed": len(batch_p)})
    pd.DataFrame(stats_rows).to_csv(results / "statistical_tests.csv", index=False)

    plot_security_effectiveness(summary, results)
    plot_blast_radius(blast_df, results)
    plot_latency(batch_b, batch_p, results)

    print(results / "experiment_summary.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
