from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx

from common import MODE_AXES, MODES, RESULTS, read_csv, write_csv
from plotting import MODE_LABELS_SHORT, PALETTE, bar_labels, style_axes

# 논문 3.4.1/3.4.2: Effective Graph 기준으로 AVOD(Average Out-Degree)와
# TINR(Transitive Internal Network Reachability)를 계산한다(Basta et al., NOMS
# 2022, references [25]). AVOD_C = (1/|V|) * sum(OD(v)), TINR_C = |A^T|
# (전이폐쇄 간선 집합의 크기). 2x2 ablation에서 이미 검증된 축별 평균 분해 패턴
# (정책축 고정/네트워크축 고정 평균)을 그대로 적용해 "정책축만 반응/네트워크축만
# 반응" 패턴을 4.2절에서 바로 인용할 수 있게 한다.

AXIS_SENSITIVE_METRICS = ["avod", "tinr"]


def load_effective_graph(mode: str, results: Path) -> nx.DiGraph:
    nodes = read_csv("assets.csv")
    graph = nx.DiGraph()
    for node in nodes:
        graph.add_node(node["asset_id"], business=node["business"], tier=node["tier"])
    edge_path = results / f"effective_edges_{mode}.csv"
    if not edge_path.exists():
        raise SystemExit(f"missing {edge_path} (run build_effective_graph.py first)")
    with edge_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            graph.add_edge(row["source"], row["target"])
    return graph


def avod(graph: nx.DiGraph) -> float:
    n = graph.number_of_nodes()
    if n == 0:
        raise ValueError("graph has no nodes")
    return sum(dict(graph.out_degree()).values()) / n


def tinr(graph: nx.DiGraph) -> int:
    closure = nx.transitive_closure(graph, reflexive=None)
    return closure.number_of_edges()


def node_metrics(mode: str, graph: nx.DiGraph) -> list[dict]:
    closure = nx.transitive_closure(graph, reflexive=None)
    rows = []
    for node, attrs in graph.nodes(data=True):
        rows.append({
            "mode": mode,
            "node": node,
            "business": attrs.get("business", ""),
            "tier": attrs.get("tier", ""),
            "out_degree": graph.out_degree(node),
            "transitive_reachable_count": closure.out_degree(node),
        })
    return rows


def plot_metric(values_by_mode: dict[str, float], title: str, ylabel: str, filename: str, results: Path, fmt) -> None:
    fig, ax = plt.subplots(figsize=(7, 5))
    x = list(range(len(MODES)))
    bars = ax.bar(x, [values_by_mode[m] for m in MODES], color=[PALETTE[m] for m in MODES], width=0.6)
    bar_labels(ax, bars, fmt)
    ax.set_xticks(x)
    ax.set_xticklabels([MODE_LABELS_SHORT[m] for m in MODES], fontsize=9)
    ax.set_ylabel(ylabel)
    ax.set_title(title, color=PALETTE["text_primary"], fontsize=11)
    style_axes(ax)
    fig.tight_layout()
    fig.savefig(results / filename, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute AVOD/TINR structural exposure metrics from the effective communication graphs.")
    parser.add_argument("--results-dir", type=Path, default=RESULTS)
    args = parser.parse_args()
    results = args.results_dir

    graphs = {mode: load_effective_graph(mode, results) for mode in MODES}
    avod_by_mode = {mode: avod(g) for mode, g in graphs.items()}
    tinr_by_mode = {mode: tinr(g) for mode, g in graphs.items()}

    # ---- graph_metrics.csv: 모드별 절대값 + Baseline 대비 상대적 감소율 ----
    summary_rows = []
    for mode in MODES:
        g = graphs[mode]
        summary_rows.append({
            "metric": "avod",
            "mode": mode,
            "policy_axis": MODE_AXES[mode]["policy"],
            "network_axis": MODE_AXES[mode]["network"],
            "value": avod_by_mode[mode],
            "node_count": g.number_of_nodes(),
            "effective_edge_count": g.number_of_edges(),
            "axis_group": "",
        })
        summary_rows.append({
            "metric": "tinr",
            "mode": mode,
            "policy_axis": MODE_AXES[mode]["policy"],
            "network_axis": MODE_AXES[mode]["network"],
            "value": tinr_by_mode[mode],
            "node_count": g.number_of_nodes(),
            "effective_edge_count": g.number_of_edges(),
            "axis_group": "",
        })
    summary_df = {(r["metric"], r["mode"]): r["value"] for r in summary_rows}

    def relative_change(metric: str, mode: str) -> float:
        if mode == "baseline":
            return float("nan")
        base = summary_df[(metric, "baseline")]
        if not base:
            return float("nan")
        return (summary_df[(metric, mode)] - base) / base

    for row in summary_rows:
        row["relative_change_vs_baseline"] = relative_change(row["metric"], row["mode"])

    # ---- 축별 평균(다른 축은 평균으로 소거) 행: 2x2 ablation에서 검증된 패턴 재사용 ----
    axis_rows = []
    for metric in AXIS_SENSITIVE_METRICS:
        for axis_key, axis_values in (("policy", ("broad", "finegrained")), ("network", ("flat", "segmented"))):
            for axis_value in axis_values:
                modes_in_group = [m for m in MODES if MODE_AXES[m][axis_key] == axis_value]
                vals = [summary_df[(metric, m)] for m in modes_in_group]
                axis_rows.append({
                    "metric": metric,
                    "mode": "",
                    "policy_axis": axis_value if axis_key == "policy" else "",
                    "network_axis": axis_value if axis_key == "network" else "",
                    "value": statistics.mean(vals) if vals else float("nan"),
                    "node_count": "",
                    "effective_edge_count": "",
                    "relative_change_vs_baseline": float("nan"),
                    "axis_group": f"{axis_key}_axis={axis_value} (mean over {','.join(modes_in_group)})",
                })

    all_rows = summary_rows + axis_rows
    fieldnames = ["metric", "mode", "policy_axis", "network_axis", "value", "node_count", "effective_edge_count", "relative_change_vs_baseline", "axis_group"]
    write_csv(results / "graph_metrics.csv", fieldnames, all_rows)

    # ---- node_metrics.csv ----
    all_node_rows = []
    for mode in MODES:
        all_node_rows.extend(node_metrics(mode, graphs[mode]))
    write_csv(results / "node_metrics.csv", list(all_node_rows[0].keys()), all_node_rows)

    # ---- plots ----
    plot_metric(avod_by_mode, "Average Out-Degree (AVOD)", "AVOD", "graph_avod.png", results, lambda h: f"{h:.2f}")
    plot_metric({m: float(v) for m, v in tinr_by_mode.items()}, "Transitive Internal Network Reachability (TINR)", "TINR (edges)", "graph_tinr.png", results, lambda h: f"{int(h)}")

    print(results / "graph_metrics.csv")
    for mode in MODES:
        print(f"{mode}: avod={avod_by_mode[mode]:.3f} tinr={tinr_by_mode[mode]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
