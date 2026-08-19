from __future__ import annotations

import argparse
import csv
import glob
from pathlib import Path

import networkx as nx

from common import MODES, RESULTS, read_csv, write_csv

# 논문 3.4.1/3.4.2: 각 실험조건 m에 대해 네트워크 그래프 G_m^N, 정책 그래프
# G_m^P, 유효 통신 그래프 G_m^E 세 종류의 방향성 그래프를 구성한다.
# - Network Graph: 10개 업무자산(5개 업무 x app/db, PDP/PEP 제외)이 정점이며,
#   두 자산 사이에 직접 TCP 통신이 가능하면 간선이다.
# - Policy Graph: 정책집행 대상인 5개 업무 애플리케이션만 정점이다(/call은
#   항상 App 컨테이너만을 대상으로 하므로 PEP를 경유한 정책 허용 관계는 앱
#   노드 사이에서만 존재한다 — DB는 정책 그래프의 정점이 아니다).
# - Effective Graph: 10개 업무자산이 정점이며, 두 그래프의 간선을 합집합한다.
#   Network/Effective AOD는 10개 자산을 대상으로, Policy AOD는 5개
#   애플리케이션을 대상으로 하므로 서로 다른 계층의 절대값을 직접 비교하지
#   않는다(analyze_graph_metrics.py, README §11).


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
    # network_graph/policy_graph 간선에는 direct_tcp/policy_mediated 속성이 없고
    # effective_graph 간선에만 있다 — 존재하는 속성 키만 동적으로 컬럼에 반영해서
    # 4.2 분석 시 이 edge가 직접 TCP 때문인지 정책 허용 때문인지(또는 둘 다인지)
    # effective_edges_<mode>.csv만 보고도 바로 추적할 수 있게 한다.
    attr_keys: list[str] = []
    for _, _, data in graph.edges(data=True):
        for key in data:
            if key not in attr_keys:
                attr_keys.append(key)
    fieldnames = ["source", "target", *attr_keys]
    edge_rows = [{"source": u, "target": v, **data} for u, v, data in graph.edges(data=True)]
    write_csv(results / f"{name}_edges_{mode}.csv", fieldnames, edge_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Network/Policy/Effective communication graphs for each ablation mode.")
    parser.add_argument("--results-dir", type=Path, default=RESULTS)
    parser.add_argument("--modes", nargs="+", choices=list(MODES), default=list(MODES))
    args = parser.parse_args()
    results = args.results_dir
    nodes = read_csv("assets.csv")
    app_nodes = [n for n in nodes if n["tier"] == "app"]

    for mode in args.modes:
        network_edges = load_network_edges(mode, results)
        policy_edges = load_policy_edges(mode, results)

        network_graph = build_graph(nodes, network_edges)
        policy_graph = build_graph(app_nodes, policy_edges)

        effective_graph = build_graph(nodes, [])
        for u, v in network_edges:
            effective_graph.add_edge(u, v, direct_tcp=True, policy_mediated=False)
        for u, v in policy_edges:
            if effective_graph.has_edge(u, v):
                effective_graph[u][v]["policy_mediated"] = True
            else:
                effective_graph.add_edge(u, v, direct_tcp=False, policy_mediated=True)

        # 논문 3.4.2의 정점 수 정의를 그대로 방어적으로 검증한다: Network/
        # Effective Graph는 10개 업무자산, Policy Graph는 5개 App만이어야 한다.
        if network_graph.number_of_nodes() != 10:
            raise RuntimeError(f"{mode}: Network Graph must contain 10 assets, got {network_graph.number_of_nodes()}")
        if policy_graph.number_of_nodes() != 5:
            raise RuntimeError(f"{mode}: Policy Graph must contain 5 application nodes, got {policy_graph.number_of_nodes()}")
        if effective_graph.number_of_nodes() != 10:
            raise RuntimeError(f"{mode}: Effective Graph must contain 10 assets, got {effective_graph.number_of_nodes()}")

        write_graph(network_graph, results, "network", mode)
        write_graph(policy_graph, results, "policy", mode)
        write_graph(effective_graph, results, "effective", mode)

        print(f"{mode}: network_edges={network_graph.number_of_edges()} policy_edges={policy_graph.number_of_edges()} effective_edges={effective_graph.number_of_edges()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
