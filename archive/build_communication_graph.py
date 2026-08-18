from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a directed communication graph from policy test CSV.")
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path("results/communication_graph.png"))
    parser.add_argument("--allowed-only", action="store_true")
    args = parser.parse_args()
    df = pd.read_csv(args.input_csv)
    if args.allowed_only:
        df = df[df["actual"] == "allow"]
    graph = nx.DiGraph()
    for _, row in df.iterrows():
        graph.add_edge(str(row["source_business"]), str(row["destination"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(8, 6))
    positions = nx.spring_layout(graph, seed=7)
    nx.draw_networkx(graph, positions, with_labels=True, arrows=True, node_size=2200, font_size=9)
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(args.output, dpi=200, bbox_inches="tight")
    plt.close()
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
