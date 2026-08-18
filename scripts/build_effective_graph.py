from __future__ import annotations

import argparse
import csv
import glob
from pathlib import Path

import networkx as nx

from common import MODES, RESULTS, read_csv, write_csv

# 논문 3.4.1: 유효 통신 그래프 C_m = (A_m, V). 정점집합 V는 10개 업무자산(5개
# 업무 x app/db)이며 PDP/PEP는 제외한다. 간선 A_m은 "두 업무자산 사이에 직접
# TCP 통신이 가능하거나(Network Graph) PEP를 경유한 업무통신이 정책에 의해
# 허용되는 경우(Policy Graph)"의 합집합이다. Policy Graph는 /call이 항상 App
# 컨테이너만을 대상으로 하므로 앱 노드 사이에서만 간선을 가질 수 있다 — DB는
# 네트워크 계층에서만(직접 TCP로만) 도달 가능하다.


def latest(pattern: str) -> Path | None:
    matches = sorted(Path(p) for p in glob.glob(pattern))
    return matches[-1] if matches else None


def load_network_edges(mode: str, results: Path) -> list[tuple[str, str]]:
    path = latest(str(results / f"raw_network_edges_{mode}_*.csv"))
    if path is None:
        raise SystemExit(f"missing raw_network_edges_{mode}_*.csv under {results} (run run_reachability_tests.py --mode {mode} first)")
    edges = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["stable"] != "true":
                raise SystemExit(f"{path} contains an unstable pair ({row['source_asset']}->{row['target_asset']}); re-run reachability tests before building the graph")
            if row["reachable"] == "true":
                edges.append((row["source_asset"], row["target_asset"]))
    return edges


def load_policy_edges(mode: str, results: Path) -> list[tuple[str, str]]:
    path = latest(str(results / f"raw_policy_space_{mode}_*.csv"))
    if path is None:
        raise SystemExit(f"missing raw_policy_space_{mode}_*.csv under {results} (run run_policy_space.py --mode {mode} first)")
    allowed_pairs: set[tuple[str, str]] = set()
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["is_execution_error"] == "true":
                raise SystemExit(f"{path} contains execution errors (e.g. opa_transport_error); re-run policy space tests before building the graph")
            # source_business/destination은 업무 단위이며, 그래프 노드는 자산
            # 단위(<business>_app)다 — /call은 항상 출발/목적 업무의 App 컨테이너를
            # 통해 이뤄지므로 자산 ID는 "<business>_app"로 결정론적으로 매핑된다.
            if row["audit_decision"] == "allow":
                allowed_pairs.add((f"{row['source_business']}_app", f"{row['destination']}_app"))
    return sorted(allowed_pairs)


def build_graph(nodes: list[dict], edges: list[tuple[str, str]]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for node in nodes:
        graph.add_node(node["asset_id"], business=node["business"], tier=node["tier"])
    for source, target in edges:
        graph.add_edge(source, target)
    return graph


def write_graph(graph: nx.DiGraph, results: Path, name: str, mode: str) -> None:
    nx.write_graphml(graph, results / f"{name}_graph_{mode}.graphml")
    edge_rows = [{"source": u, "target": v} for u, v in graph.edges()]
    if edge_rows:
        write_csv(results / f"{name}_edges_{mode}.csv", ["source", "target"], edge_rows)
    else:
        write_csv(results / f"{name}_edges_{mode}.csv", ["source", "target"], [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Network/Policy/Effective communication graphs for each ablation mode.")
    parser.add_argument("--results-dir", type=Path, default=RESULTS)
    parser.add_argument("--modes", nargs="+", choices=list(MODES), default=list(MODES))
    args = parser.parse_args()
    results = args.results_dir
    nodes = read_csv("assets.csv")

    for mode in args.modes:
        network_edges = load_network_edges(mode, results)
        policy_edges = load_policy_edges(mode, results)

        network_graph = build_graph(nodes, network_edges)
        policy_graph = build_graph(nodes, policy_edges)

        effective_graph = build_graph(nodes, [])
        for u, v in network_edges:
            effective_graph.add_edge(u, v, direct_tcp=True, policy_mediated=False)
        for u, v in policy_edges:
            if effective_graph.has_edge(u, v):
                effective_graph[u][v]["policy_mediated"] = True
            else:
                effective_graph.add_edge(u, v, direct_tcp=False, policy_mediated=True)

        write_graph(network_graph, results, "network", mode)
        write_graph(policy_graph, results, "policy", mode)
        write_graph(effective_graph, results, "effective", mode)

        print(f"{mode}: network_edges={network_graph.number_of_edges()} policy_edges={policy_graph.number_of_edges()} effective_edges={effective_graph.number_of_edges()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
