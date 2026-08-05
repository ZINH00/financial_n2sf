from __future__ import annotations

import argparse
import time
from pathlib import Path

import httpx

from common import RESULTS, read_csv, timestamp, write_csv


def main() -> int:
    parser = argparse.ArgumentParser(description="Run approved and unauthorized service-flow policy tests.")
    parser.add_argument("--mode", choices=["baseline", "proposed"], required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

    scenarios = read_csv("authorized_flows.csv") + read_csv("unauthorized_flows.csv")
    rows: list[dict] = []
    with httpx.Client(timeout=10.0) as client:
        for scenario in scenarios:
            for run_id in range(1, args.repeat + 1):
                headers = {
                    "x-user": "lab-user",
                    "x-role": scenario["role"],
                    "x-device-trust": scenario["device_trust"],
                    "x-source-service": scenario["source_service"],
                    "x-source-business": scenario["source_business"],
                    "x-purpose": scenario["purpose"],
                    "x-data-grade": scenario["data_grade"],
                    "x-transfer-approved": "false",
                }
                url = f"{args.base_url}/proxy/{scenario['destination']}{scenario['path']}"
                started = time.perf_counter()
                try:
                    response = client.request(scenario["method"], url, headers=headers)
                    status_code = response.status_code
                    error = ""
                except Exception as exc:
                    status_code = 0
                    error = f"{type(exc).__name__}: {exc}"
                latency_ms = (time.perf_counter() - started) * 1000
                actual = "allow" if 200 <= status_code < 300 else "deny"
                rows.append({
                    **scenario,
                    "mode": args.mode,
                    "run_id": run_id,
                    "status_code": status_code,
                    "actual": actual,
                    "latency_ms": f"{latency_ms:.6f}",
                    "matches_expected_proposed": str(actual == scenario["expected_proposed"]).lower() if args.mode == "proposed" else "",
                    "error": error,
                })
    output = RESULTS / f"raw_policy_{args.mode}_{timestamp()}.csv"
    fields = list(rows[0].keys()) if rows else []
    write_csv(output, fields, rows)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
