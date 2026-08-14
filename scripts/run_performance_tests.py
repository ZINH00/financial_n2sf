from __future__ import annotations

import argparse
import time

import httpx

from common import MODES, RESULTS, percentile, timestamp, write_csv

# 논문 3.3절 정상 업무흐름 중 loan이 시작점인 4개 흐름을 모두 측정한다(승인자가
# loan을 조회하는 역방향 흐름은 정책·기능 검증에서 이미 다루므로 성능측정은
# 심사 단계에 집중한다). 각 흐름은 --batches개의 측정 배치로 나뉘며(프로세스를
# 재기동하지는 않는다), 배치별 요약값(median 등)을 통계 단위로 사용해 반복 측정을
# 유사-독립 표본처럼 다루지 않는다.
FLOWS = [
    {"name": "loan_to_customer", "destination": "customer", "method": "GET", "path": "/customer-profile/CASE-0001", "purpose": "loan_screening"},
    {"name": "loan_to_credit", "destination": "credit", "method": "POST", "path": "/credit-assessment", "purpose": "loan_screening"},
    {"name": "loan_to_aml", "destination": "aml", "method": "POST", "path": "/aml-screening", "purpose": "loan_screening"},
    {"name": "loan_to_approval", "destination": "approval", "method": "POST", "path": "/approval-requests", "purpose": "loan_approval"},
]
SOURCE_APP_URL = "http://127.0.0.1:18002"  # loan_app


def call_flow(client: httpx.Client, flow: dict, scenario_id: str, experiment_run_id: str) -> tuple[int, float, str]:
    headers = {
        "x-user": "perf-user",
        "x-role": "loan_reviewer",
        "x-device-trust": "trusted",
        "x-scenario-id": scenario_id,
        "x-experiment-run-id": experiment_run_id,
    }
    payload = {
        "destination": flow["destination"],
        "path": flow["path"],
        "method": flow["method"],
        "purpose": flow["purpose"],
        "data_grade": "S",
        "body": None,
    }
    started = time.perf_counter()
    try:
        response = client.post(f"{SOURCE_APP_URL}/call", json=payload, headers=headers)
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            return response.status_code, latency_ms, ""
        inner_status = int(response.json().get("status_code", 0))
        return inner_status, latency_ms, ""
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        return 0, latency_ms, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure PEP/OPA overhead for the four loan-initiated business flows.")
    parser.add_argument("--mode", choices=list(MODES), required=True)
    parser.add_argument("--batches", type=int, default=30)
    parser.add_argument("--per-batch", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    args = parser.parse_args()
    if args.batches < 1 or args.per_batch < 1 or args.warmup < 0:
        parser.error("invalid batch configuration")

    experiment_run_id = f"perf-{args.mode}-{timestamp()}"
    rows: list[dict] = []
    with httpx.Client(timeout=10.0) as client:
        for flow in FLOWS:
            for _ in range(args.warmup):
                call_flow(client, flow, f"PERF-{flow['name']}-warmup", experiment_run_id)
            for batch_id in range(1, args.batches + 1):
                scenario_id = f"PERF-{flow['name']}-b{batch_id:03d}"
                for request_id in range(1, args.per_batch + 1):
                    status_code, latency_ms, error = call_flow(client, flow, scenario_id, experiment_run_id)
                    rows.append({
                        "mode": args.mode,
                        "experiment_run_id": experiment_run_id,
                        "flow": flow["name"],
                        "batch_id": batch_id,
                        "request_id": request_id,
                        "status_code": status_code,
                        "latency_ms": f"{latency_ms:.6f}",
                        "error": error,
                    })
    output = RESULTS / f"raw_performance_{args.mode}_{timestamp()}.csv"
    write_csv(output, list(rows[0].keys()), rows)
    print(output)
    print(f"experiment_run_id={experiment_run_id}")
    for flow in FLOWS:
        successful = [float(r["latency_ms"]) for r in rows if r["flow"] == flow["name"] and 200 <= int(r["status_code"]) < 300]
        total = [r for r in rows if r["flow"] == flow["name"]]
        print(f"{flow['name']}: successful={len(successful)}/{len(total)} p50_ms={percentile(successful,0.50):.3f} p95_ms={percentile(successful,0.95):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
