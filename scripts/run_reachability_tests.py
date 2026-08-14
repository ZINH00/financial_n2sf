from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from common import MODES, RESULTS, ROOT, read_csv, timestamp, write_csv

REACHABLE_RE = re.compile(r"reachable=(true|false)\s+elapsed_ms=([0-9.]+)\s+error=(.*)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure direct service (app) and database reachability from each business service.")
    parser.add_argument("--mode", choices=list(MODES), required=True)
    args = parser.parse_args()
    compose = ROOT / f"compose.{args.mode}.yml"
    rows = []
    for scenario in read_csv("reachability_matrix.csv"):
        cmd = [
            "docker", "compose", "-f", str(compose), "exec", "-T",
            scenario["source_container"], "python", "/app/probe.py",
            scenario["target_host"], scenario["target_port"],
        ]
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False, timeout=20)
        output = (proc.stdout or proc.stderr).strip().replace("\n", " | ")
        match = REACHABLE_RE.search(output)
        reachable = match.group(1) if match else "unknown"
        elapsed_ms = match.group(2) if match else ""
        error = match.group(3) if match else output
        rows.append({**scenario, "mode": args.mode, "reachable": reachable, "elapsed_ms": elapsed_ms, "returncode": proc.returncode, "error": error})
    output_path = RESULTS / f"raw_reachability_{args.mode}_{timestamp()}.csv"
    write_csv(output_path, list(rows[0].keys()) if rows else [], rows)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
