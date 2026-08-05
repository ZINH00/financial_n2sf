from __future__ import annotations

import argparse
import time

import httpx

from common import RESULTS, as_bool, parse_json, read_csv, timestamp, write_csv


def main() -> int:
    parser = argparse.ArgumentParser(description="Run S/O Transfer CDS validation scenarios.")
    parser.add_argument("--mode", choices=["baseline", "proposed"], required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18090")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    rows: list[dict] = []
    with httpx.Client(timeout=10.0) as client:
        for scenario in read_csv("cds_flows.csv"):
            expected = scenario[f"expected_{args.mode}"]
            payload = {
                "destination": scenario["destination"],
                "data_grade": scenario["data_grade"],
                "approved": as_bool(scenario["approved"]),
                "purpose": scenario["purpose"],
                "content": parse_json(scenario["content_json"]),
                "source_object_id": scenario["source_object_id"],
            }
            headers = {"x-user": "lab-user", "x-role": scenario["role"], "x-device-trust": scenario["device_trust"]}
            for run_id in range(1, args.repeat + 1):
                started = time.perf_counter()
                try:
                    response = client.post(f"{args.base_url}/transfer", json=payload, headers=headers)
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
                    "expected": expected,
                    "matches_expected": str(actual == expected).lower(),
                    "latency_ms": f"{latency_ms:.6f}",
                    "error": error,
                })
    output = RESULTS / f"raw_cds_{args.mode}_{timestamp()}.csv"
    write_csv(output, list(rows[0].keys()) if rows else [], rows)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
