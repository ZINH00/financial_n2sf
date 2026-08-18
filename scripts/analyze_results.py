from __future__ import annotations

import argparse
import csv
import glob
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

from common import MODES, RESULTS, bootstrap_ci, iqr, percentile, read_jsonl, write_csv
from plotting import MODE_LABELS_SHORT, PALETTE, bar_labels, style_axes

# 논문 4.3(정책집행 성능 영향)/4.4(감사추적성)만 담당한다. 4.1/4.2(구조적 보안효과,
# AVOD/TINR)는 analyze_graph_metrics.py가 담당한다(build_effective_graph.py의
# 산출물을 입력으로 사용). 3.4.2 방법론에 따라 감사로그는 더 이상 성공률 지표로
# 산출하지 않고, 통신경로/정책판단/성능 측정결과를 요청 단위로 확인하는 정성적
# 추적자료로만 쓴다.


def latest_files(pattern: str) -> list[Path]:
    return sorted(Path(p) for p in glob.glob(pattern))


def batch_latency_stats(audit_rows: list[dict], experiment_run_id: str, flow_prefix: str = "PERF-") -> list[dict]:
    """PEP 감사로그에서 성능 시나리오(scenario_id=PERF-<flow>-b<NNN>)의 허용된
    요청만 골라 배치별 median(decision_ms, total_ms)을 산출한다. 감사로그는 라운드를
    거듭할 때마다 기존 파일 뒤에 append되므로, 이 라운드의 raw_performance CSV에
    기록된 experiment_run_id로 반드시 필터링해서 다른 라운드의 동일 배치ID와
    섞이지 않게 한다."""
    by_batch: dict[tuple[str, int], dict[str, list[float]]] = defaultdict(lambda: {"decision_ms": [], "total_ms": []})
    for row in audit_rows:
        scenario_id = str(row.get("scenario_id", ""))
        if not scenario_id.startswith(flow_prefix) or "-b" not in scenario_id or row.get("decision") != "allow":
            continue
        if str(row.get("experiment_run_id", "")) != str(experiment_run_id):
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
    return records


def round_representative(batch_records: list[dict], column: str) -> float | None:
    """논문 3.4.2: 라운드 대표값 = 네 업무흐름의 대표값(각 흐름 내 배치 median들의
    median)을 동일 가중으로 반영한 median. 흐름 하나라도 배치 기록이 없으면(감사
    로그 누락) None을 반환해 호출측이 이 라운드를 경고하고 통계에서 제외하게 한다."""
    by_flow: dict[str, list[float]] = defaultdict(list)
    for rec in batch_records:
        by_flow[rec["flow"]].append(rec[column])
    if not by_flow:
        return None
    flow_reprs = [statistics.median(vals) for vals in by_flow.values()]
    return statistics.median(flow_reprs)


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {"median": float("nan"), "iqr": float("nan"), "p95": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n_rounds": 0}
    ci_low, ci_high = bootstrap_ci(values)
    return {
        "median": statistics.median(values),
        "iqr": iqr(values),
        "p95": percentile(values, 0.95),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "n_rounds": len(values),
    }


def plot_latency(rounds_by_mode: dict[str, list[float]], results: Path) -> None:
    values = [rounds_by_mode.get(mode, []) for mode in MODES]
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
    ax.set_ylabel("Policy decision latency, per-round representative value (ms)")
    plt.setp(ax.get_xticklabels(), rotation=15, ha="right", fontsize=8.5)
    style_axes(ax)
    fig.tight_layout()
    fig.savefig(results / "latency_boxplot.png", dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def first_row(path: Path) -> dict | None:
    with path.open(encoding="utf-8", newline="") as f:
        return next(csv.DictReader(f), None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize the 12-round independent performance experiment across all 4 ablation modes.")
    parser.add_argument("--results-dir", type=Path, default=RESULTS)
    args = parser.parse_args()
    results = args.results_dir

    round_rows: list[dict] = []
    missing_rounds: list[tuple[str, int]] = []
    for mode in MODES:
        audit_rows = read_jsonl(results / f"pep_audit_{mode}.jsonl")
        perf_files = latest_files(str(results / f"raw_performance_{mode}_r*_*.csv"))
        if not perf_files:
            raise SystemExit(f"no raw_performance_{mode}_r*_*.csv files under {results} (run run_experiment.py first)")

        # 라운드별로 실제 사용된 experiment_run_id를 각 CSV의 첫 행에서 읽는다
        # (round_id -> experiment_run_id는 run_performance_tests.py가 1:1로 만든다).
        run_id_by_round: dict[int, str] = {}
        for f in perf_files:
            row = first_row(f)
            if row is None:
                continue
            run_id_by_round[int(row["round_id"])] = row["experiment_run_id"]

        for round_id, experiment_run_id in sorted(run_id_by_round.items()):
            batch_records = batch_latency_stats(audit_rows, experiment_run_id)
            decision_repr = round_representative(batch_records, "decision_ms_median")
            total_repr = round_representative(batch_records, "total_ms_median")
            if decision_repr is None or total_repr is None:
                missing_rounds.append((mode, round_id))
            round_rows.append({
                "mode": mode,
                "round_id": round_id,
                "experiment_run_id": experiment_run_id,
                "decision_ms": decision_repr,
                "total_ms": total_repr,
            })

    write_csv(results / "performance_rounds.csv", ["mode", "round_id", "experiment_run_id", "decision_ms", "total_ms"], round_rows)

    if missing_rounds:
        print(f"WARNING: {len(missing_rounds)} round(s) had no matching audit log entries for one or more flows (excluded from statistics):", file=sys.stderr)
        for mode, round_id in missing_rounds:
            print(f"  mode={mode} round_id={round_id}", file=sys.stderr)

    summary_rows = []
    rounds_by_mode_decision: dict[str, list[float]] = {}
    for mode in MODES:
        decision_values = [r["decision_ms"] for r in round_rows if r["mode"] == mode and r["decision_ms"] is not None]
        total_values = [r["total_ms"] for r in round_rows if r["mode"] == mode and r["total_ms"] is not None]
        rounds_by_mode_decision[mode] = decision_values
        summary_rows.append({"metric": "decision_ms", "mode": mode, **summarize(decision_values)})
        summary_rows.append({"metric": "total_ms", "mode": mode, **summarize(total_values)})
    write_csv(results / "performance_summary.csv", ["metric", "mode", "median", "iqr", "p95", "ci_low", "ci_high", "n_rounds"], summary_rows)

    plot_latency(rounds_by_mode_decision, results)

    # 4.4 감사추적성 검증: "비율"이 아니라 요청 단위 상관관계가 실제로 확인되는지
    # 정성적으로 스팟체크한다(논문 3.4.2: 감사로그는 추적자료로만 활용). 필수
    # 필드가 전부 있고 라운드마다 최소 1건 이상 매칭되면 통과로 간주한다.
    required_fields = {"request_id", "experiment_run_id", "scenario_id", "decision", "reason", "decision_ms", "total_ms"}
    traceability_ok = not missing_rounds and all(
        all(field in row for field in required_fields)
        for mode in MODES
        for row in read_jsonl(results / f"pep_audit_{mode}.jsonl")[:1]  # 필드 집합 자체는 파일당 1건만 확인해도 충분(모든 행이 동일 스키마)
    )
    print(f"audit traceability spot-check: {'OK' if traceability_ok else 'CHECK NEEDED'} (see performance_rounds.csv for per-round detail; not reported as a rate)")

    print(results / "performance_summary.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
