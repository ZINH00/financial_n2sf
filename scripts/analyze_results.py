from __future__ import annotations

import argparse
import glob
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from scipy.stats import fisher_exact, mannwhitneyu


def latest(pattern: str) -> Path | None:
    matches = sorted(Path(p) for p in glob.glob(pattern))
    return matches[-1] if matches else None


def rate(series: pd.Series, value: str) -> float:
    return float((series == value).mean()) if len(series) else float("nan")


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

    unauthorized_b = pb[pb["scenario_id"].astype(str).str.startswith("U")]
    unauthorized_p = pp[pp["scenario_id"].astype(str).str.startswith("U")]
    authorized_b = pb[pb["scenario_id"].astype(str).str.startswith("A")]
    authorized_p = pp[pp["scenario_id"].astype(str).str.startswith("A")]
    cross_b = rb[rb["relationship"] == "cross_business_db"]
    cross_p = rp[rp["relationship"] == "cross_business_db"]

    summary = pd.DataFrame([
        {"metric": "authorized_flow_success_rate", "baseline": rate(authorized_b["actual"], "allow"), "proposed": rate(authorized_p["actual"], "allow")},
        {"metric": "unauthorized_flow_success_rate", "baseline": rate(unauthorized_b["actual"], "allow"), "proposed": rate(unauthorized_p["actual"], "allow")},
        {"metric": "cross_business_db_reachability_rate", "baseline": rate(cross_b["reachable"].astype(str), "true"), "proposed": rate(cross_p["reachable"].astype(str), "true")},
        {"metric": "cds_policy_match_rate", "baseline": rate(cb["matches_expected"].astype(str), "true"), "proposed": rate(cp["matches_expected"].astype(str), "true")},
        {"metric": "performance_p50_ms", "baseline": fb[fb.status_code.between(200,299)]["latency_ms"].median(), "proposed": fp[fp.status_code.between(200,299)]["latency_ms"].median()},
        {"metric": "performance_p95_ms", "baseline": fb[fb.status_code.between(200,299)]["latency_ms"].quantile(.95), "proposed": fp[fp.status_code.between(200,299)]["latency_ms"].quantile(.95)},
    ])
    summary["relative_change"] = (summary["proposed"] - summary["baseline"]) / summary["baseline"].replace(0, pd.NA)
    summary_path = results / "experiment_summary.csv"
    summary.to_csv(summary_path, index=False)

    # Proportion comparison for unauthorized successes.
    table = [
        [(unauthorized_b.actual == "allow").sum(), (unauthorized_b.actual != "allow").sum()],
        [(unauthorized_p.actual == "allow").sum(), (unauthorized_p.actual != "allow").sum()],
    ]
    odds_ratio, fisher_p = fisher_exact(table)
    lat_b = fb[fb.status_code.between(200,299)]["latency_ms"].astype(float)
    lat_p = fp[fp.status_code.between(200,299)]["latency_ms"].astype(float)
    u_stat, mw_p = mannwhitneyu(lat_b, lat_p, alternative="two-sided")
    stats = pd.DataFrame([
        {"test": "Fisher exact - unauthorized flow success", "statistic": odds_ratio, "p_value": fisher_p},
        {"test": "Mann-Whitney U - latency", "statistic": u_stat, "p_value": mw_p},
    ])
    stats.to_csv(results / "statistical_tests.csv", index=False)

    plot_df = summary[summary.metric.isin(["authorized_flow_success_rate", "unauthorized_flow_success_rate", "cross_business_db_reachability_rate"])].set_index("metric")[["baseline","proposed"]]
    ax = plot_df.plot(kind="bar", figsize=(9, 5))
    ax.set_ylabel("Rate")
    ax.set_ylim(0, 1.05)
    plt.xticks(rotation=15, ha="right")
    plt.tight_layout()
    plt.savefig(results / "security_effectiveness.png", dpi=200, bbox_inches="tight")
    plt.close()

    perf = pd.DataFrame({"baseline": lat_b.reset_index(drop=True), "proposed": lat_p.reset_index(drop=True)})
    ax = perf.boxplot(figsize=(7, 5))
    ax.set_ylabel("Latency (ms)")
    plt.tight_layout()
    plt.savefig(results / "latency_boxplot.png", dpi=200, bbox_inches="tight")
    plt.close()
    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
