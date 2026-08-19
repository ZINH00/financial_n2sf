from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx

from common import MODE_AXES, MODES, RESULTS, read_csv, write_csv
from plotting import MODE_LABELS_SHORT, PALETTE, bar_labels, style_axes

# 논문 3.4.2: 각 실험조건의 직접 노출범위는 Network/Policy/Effective 세 계층에서
# 각각 AOD(Average Out-Degree, Basta et al., NOMS 2022, references [25], 식 (1))로
# 평가하고, 다단계 도달범위는 Effective Graph에서 MPL(식 (2))·TINR(식 (3))로
# 평가한다. Network/Effective AOD는 10개 업무자산, Policy AOD는 5개 업무
# 애플리케이션을 대상으로 하므로 계층 간 절대값을 직접 비교하지 않고, 각 지표를
# 동일 계층 내 4개 실험조건 간 절대값으로 비교한다(축별 평균·Baseline 대비
# 감소율은 3.4.2가 정의한 평가 방법이 아니므로 주 결과표에 포함하지 않는다).
# 이 지표들은 정의된 자산관계와 정책조합을 전수평가하여 산출한 결정론적 값이므로
# 별도의 유의성 검정을 적용하지 않는다(논문 3.4.2).


def load_graph(mode: str, layer: str, results: Path) -> nx.DiGraph:
    nodes = read_csv("assets.csv")
    if layer == "policy":
        nodes = [n for n in nodes if n["tier"] == "app"]
    graph = nx.DiGraph()
    for node in nodes:
        graph.add_node(node["asset_id"], business=node["business"], tier=node["tier"])
    edge_path = results / f"{layer}_edges_{mode}.csv"
    if not edge_path.exists():
        raise SystemExit(f"missing {edge_path} (run build_effective_graph.py first)")
    with edge_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            graph.add_edge(row["source"], row["target"])
    return graph


def aod(graph: nx.DiGraph) -> float:
    """식 (1): AOD_C = (1/|V|) * sum(OD(v))."""
    n = graph.number_of_nodes()
    if n == 0:
        raise ValueError("graph has no nodes")
    return sum(dict(graph.out_degree()).values()) / n


def mpl(graph: nx.DiGraph) -> float:
    """식 (2): MPL_C = (1/|LSP_C|) * sum(|p|). |p|는 최단경로 p에 포함되는
    정점의 수다 — NetworkX의 shortest_path_length는 간선 수(hop)를 반환하므로
    정점 수로 맞추려면 +1이 필요하다(A->B 직접연결이면 hop=1, 논문 기준
    |p|=2). 도달 불가능한 자산쌍은 평균에서 제외한다(전체 도달범위는 TINR로
    별도 반영)."""
    path_vertex_counts = []
    for source in graph.nodes:
        for target, hops in nx.single_source_shortest_path_length(graph, source).items():
            if source != target:
                path_vertex_counts.append(hops + 1)
    if not path_vertex_counts:
        return float("nan")
    return statistics.mean(path_vertex_counts)


def tinr(graph: nx.DiGraph) -> int:
    """식 (3): TINR_C = |A^T| (전이폐쇄 간선 집합의 크기)."""
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
    parser = argparse.ArgumentParser(description="Compute Network/Policy/Effective AOD, Effective MPL and Effective TINR from the communication graphs.")
    parser.add_argument("--results-dir", type=Path, default=RESULTS)
    args = parser.parse_args()
    results = args.results_dir

    network_graphs = {mode: load_graph(mode, "network", results) for mode in MODES}
    policy_graphs = {mode: load_graph(mode, "policy", results) for mode in MODES}
    effective_graphs = {mode: load_graph(mode, "effective", results) for mode in MODES}

    # ---- graph_metrics.csv: 4개 모드 x 5개 지표 = 20개 행, 절대값만 ----
    summary_rows = []
    per_mode_values: dict[tuple[str, str, str], float] = {}
    for mode in MODES:
        ng, pg, eg = network_graphs[mode], policy_graphs[mode], effective_graphs[mode]
        metrics = [
            ("network", "aod", aod(ng), ng),
            ("policy", "aod", aod(pg), pg),
            ("effective", "aod", aod(eg), eg),
            ("effective", "mpl", mpl(eg), eg),
            ("effective", "tinr", tinr(eg), eg),
        ]
        for layer, metric, value, graph in metrics:
            per_mode_values[(layer, metric, mode)] = value
            summary_rows.append({
                "layer": layer,
                "metric": metric,
                "mode": mode,
                "policy_axis": MODE_AXES[mode]["policy"],
                "network_axis": MODE_AXES[mode]["network"],
                "value": value,
                "node_count": graph.number_of_nodes(),
                "edge_count": graph.number_of_edges(),
            })

    fieldnames = ["layer", "metric", "mode", "policy_axis", "network_axis", "value", "node_count", "edge_count"]
    write_csv(results / "graph_metrics.csv", fieldnames, summary_rows)

    # ---- node_metrics.csv (Effective Graph 기준, 4.1/4.2절 해석 보조자료) ----
    all_node_rows = []
    for mode in MODES:
        all_node_rows.extend(node_metrics(mode, effective_graphs[mode]))
    write_csv(results / "node_metrics.csv", list(all_node_rows[0].keys()), all_node_rows)

    # ---- plots: 계층별 AOD는 정점 수가 달라 절대값을 직접 비교하지 않으므로
    # 하나의 그래프에 합치지 않고 5개 그림으로 분리한다 ----
    plot_metric({m: per_mode_values[("network", "aod", m)] for m in MODES}, "Network AOD", "AOD (10 assets)", "network_aod.png", results, lambda h: f"{h:.2f}")
    plot_metric({m: per_mode_values[("policy", "aod", m)] for m in MODES}, "Policy AOD", "AOD (5 applications)", "policy_aod.png", results, lambda h: f"{h:.2f}")
    plot_metric({m: per_mode_values[("effective", "aod", m)] for m in MODES}, "Effective AOD", "AOD (10 assets)", "effective_aod.png", results, lambda h: f"{h:.2f}")
    plot_metric({m: per_mode_values[("effective", "mpl", m)] for m in MODES}, "Effective MPL", "MPL (vertices)", "effective_mpl.png", results, lambda h: f"{h:.3f}")
    plot_metric({m: per_mode_values[("effective", "tinr", m)] for m in MODES}, "Effective TINR", "TINR (edges)", "effective_tinr.png", results, lambda h: f"{int(h)}")

    print(results / "graph_metrics.csv")
    for mode in MODES:
        print(
            f"{mode}: network_aod={per_mode_values[('network','aod',mode)]:.3f} "
            f"policy_aod={per_mode_values[('policy','aod',mode)]:.3f} "
            f"effective_aod={per_mode_values[('effective','aod',mode)]:.3f} "
            f"effective_mpl={per_mode_values[('effective','mpl',mode)]:.3f} "
            f"effective_tinr={per_mode_values[('effective','tinr',mode)]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
