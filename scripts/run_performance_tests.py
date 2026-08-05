from __future__ import annotations

import argparse
import time

import httpx

from common import RESULTS, timestamp, write_csv


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = (len(ordered) - 1) * p
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    fraction = index - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure PEP/OPA overhead for one approved business flow.")
    parser.add_argument("--mode", choices=["baseline", "proposed"], required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=50)
    args = parser.parse_args()
    if args.requests < 1 or args.warmup < 0:
        parser.error("invalid request counts")
    headers = {
        "x-user": "perf-user", "x-role": "analyst", "x-device-trust": "trusted",
        "x-source-service": "loan", "x-source-business": "loan",
        "x-purpose": "risk_review", "x-data-grade": "S", "x-transfer-approved": "false",
    }
    url = f"{args.base_url}/proxy/credit/records?limit=1"
    rows = []
    with httpx.Client(timeout=10.0) as client:
        for _ in range(args.warmup):
            client.get(url, headers=headers)
        for request_id in range(1, args.requests + 1):
            started = time.perf_counter()
            try:
                response = client.get(url, headers=headers)
                status = response.status_code
                error = ""
            except Exception as exc:
                status = 0
                error = f"{type(exc).__name__}: {exc}"
            latency_ms = (time.perf_counter() - started) * 1000
            rows.append({"mode": args.mode, "request_id": request_id, "status_code": status, "latency_ms": f"{latency_ms:.6f}", "error": error})
    output = RESULTS / f"raw_performance_{args.mode}_{timestamp()}.csv"
    write_csv(output, list(rows[0].keys()), rows)
    successful = [float(r["latency_ms"]) for r in rows if 200 <= int(r["status_code"]) < 300]
    print(output)
    print(f"successful={len(successful)}/{len(rows)} p50_ms={percentile(successful,0.50):.3f} p95_ms={percentile(successful,0.95):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
