from __future__ import annotations

import argparse
import time

import httpx

from common import MODES, RESULTS, percentile, timestamp, write_csv

# 논문 3.4.2 성능 평가: 여신심사에서 시작하는 네 개 정상 업무흐름을 측정한다
# (승인자가 loan을 조회하는 역방향 흐름은 정책·기능 검증에서 이미 다루므로
# 성능측정은 심사 단계에 집중한다 — 흐름을 5개로 늘리지 않는다). 이 스크립트는
# 12개 독립 반복 라운드 중 하나를 담당하며(run_experiment.py가 라운드마다 이
# 스크립트를 재기동한다), 각 흐름은 --batches(기본 3)개의 측정 배치로 나뉜다.
# 배치별 요약값(median 등)을 통계 단위로 사용해 반복 측정을 유사-독립 표본처럼
# 다루지 않는다.
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
    parser.add_argument("--batches", type=int, default=3)
    parser.add_argument("--per-batch", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--round-id", type=int, default=1, help="12개 독립 반복 라운드 중 몇 번째인지(run_experiment.py가 지정)")
    parser.add_argument("--order-position", type=int, default=1, help="해당 라운드에서 이 조건이 실행된 순번(1~4, 균형화 순서 추적용)")
    args = parser.parse_args()
    if args.batches < 1 or args.per_batch < 1 or args.warmup < 0:
        parser.error("invalid batch configuration")

    experiment_run_id = f"perf-{args.mode}-r{args.round_id:03d}-{timestamp()}"
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
                        "round_id": args.round_id,
                        "order_position": args.order_position,
                        "experiment_run_id": experiment_run_id,
                        "flow": flow["name"],
                        "batch_id": batch_id,
                        "request_id": request_id,
                        "status_code": status_code,
                        "latency_ms": f"{latency_ms:.6f}",
                        "error": error,
                    })
    output = RESULTS / f"raw_performance_{args.mode}_r{args.round_id:03d}_{timestamp()}.csv"
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
