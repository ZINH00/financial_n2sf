from __future__ import annotations

import argparse
import hashlib
import time

import httpx

from common import MODE_AXES, MODES, RESULTS, join_audit_decision, read_csv, read_jsonl, timestamp, write_csv

# 논문 3.3절의 신원 검증 구조를 실험에도 반영하기 위해, entry_point="call" 시나리오는
# 호스트에서 PEP를 직접 두드리지 않고 실제 출발 업무 컨테이너의 /call을 거친다.
# 출발 업무 자체를 위장하는 시나리오(entry_point="direct")만 예외적으로 PEP를 직접
# 호출하여, PEP가 자기선언을 신뢰하지 않는지 검증한다.
APP_PORTS = {
    "customer": 18001,
    "loan": 18002,
    "credit": 18003,
    "aml": 18004,
    "approval": 18005,
}


def call_via_workload(client: httpx.Client, source_service: str, scenario: dict, experiment_run_id: str, attempt_id: str) -> tuple[int, str, float]:
    base_url = f"http://127.0.0.1:{APP_PORTS[source_service]}"
    headers = {
        "x-user": "lab-user",
        "x-role": scenario["role"],
        "x-device-trust": scenario["device_trust"],
        "x-scenario-id": attempt_id,
        "x-experiment-run-id": experiment_run_id,
    }
    payload = {
        "destination": scenario["destination"],
        "path": scenario["path"],
        "method": scenario["method"],
        "purpose": scenario["purpose"],
        "data_grade": scenario["data_grade"],
        "body": None,
    }
    started = time.perf_counter()
    try:
        response = client.post(f"{base_url}/call", json=payload, headers=headers)
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            return response.status_code, "", latency_ms
        inner_status = int(response.json().get("status_code", 0))
        return inner_status, "", latency_ms
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        return 0, f"{type(exc).__name__}: {exc}", latency_ms


def call_pep_directly(client: httpx.Client, pep_url: str, scenario: dict, experiment_run_id: str, attempt_id: str) -> tuple[int, str, float]:
    headers = {
        "x-user": "lab-user",
        "x-role": scenario["role"],
        "x-device-trust": scenario["device_trust"],
        "x-source-service": scenario["claimed_source_service"],
        "x-source-business": scenario["claimed_source_business"],
        "x-purpose": scenario["purpose"],
        "x-data-grade": scenario["data_grade"],
        "x-transfer-approved": "false",
        "x-scenario-id": attempt_id,
        "x-experiment-run-id": experiment_run_id,
    }
    signature_mode = scenario["signature_mode"]
    if signature_mode == "invalid":
        # 등록된 업무명을 사칭하되, 그 업무의 비밀키를 모르는 공격자를 흉내낸다.
        forged_timestamp = str(int(time.time()))
        forged_signature = hashlib.sha256(f"forged:{scenario['claimed_source_service']}".encode()).hexdigest()
        headers["x-workload-timestamp"] = forged_timestamp
        headers["x-workload-signature"] = forged_signature
    # signature_mode == "missing"인 경우 서명 헤더를 아예 보내지 않는다.
    url = f"{pep_url}/proxy/{scenario['destination']}{scenario['path']}"
    started = time.perf_counter()
    try:
        response = client.request(scenario["method"], url, headers=headers)
        latency_ms = (time.perf_counter() - started) * 1000
        return response.status_code, "", latency_ms
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        return 0, f"{type(exc).__name__}: {exc}", latency_ms


def main() -> int:
    parser = argparse.ArgumentParser(description="Run approved and unauthorized business-flow policy tests.")
    parser.add_argument("--mode", choices=list(MODES), required=True)
    parser.add_argument("--pep-url", default="http://127.0.0.1:18080")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--experiment-run-id", default=None)
    parser.add_argument("--fail-on-mismatch", action="store_true", help="사전검증(preflight) 모드: 기대값과 다르거나 실행 오류인 요청이 하나라도 있으면 exit 1")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")
    experiment_run_id = args.experiment_run_id or f"{args.mode}-{timestamp()}"

    policy_axis = MODE_AXES[args.mode]["policy"]
    scenarios = read_csv("authorized_flows.csv") + read_csv("unauthorized_flows.csv")
    rows: list[dict] = []
    with httpx.Client(timeout=10.0) as client:
        for scenario in scenarios:
            for run_id in range(1, args.repeat + 1):
                # 요청마다 고유한 attempt_id를 PEP의 scenario_id로 전달해, 반복된
                # 동일 시나리오라도 감사로그와 1:1로 상관관계를 확인할 수 있게 한다.
                attempt_id = f"{scenario['scenario_id']}-r{run_id:03d}"
                if scenario["entry_point"] == "call":
                    status_code, error, latency_ms = call_via_workload(client, scenario["source_service"], scenario, experiment_run_id, attempt_id)
                else:
                    status_code, error, latency_ms = call_pep_directly(client, args.pep_url, scenario, experiment_run_id, attempt_id)
                actual = "allow" if 200 <= status_code < 300 else "deny"
                expected = scenario[f"expected_{policy_axis}_policy"]
                rows.append({
                    **scenario,
                    "mode": args.mode,
                    "run_id": run_id,
                    "experiment_run_id": experiment_run_id,
                    "attempt_id": attempt_id,
                    "status_code": status_code,
                    "actual": actual,
                    "expected": expected,
                    "matches_expected": str(actual == expected).lower(),
                    "latency_ms": f"{latency_ms:.6f}",
                    "error": error,
                })
    output = RESULTS / f"raw_policy_{args.mode}_{timestamp()}.csv"
    fields = list(rows[0].keys()) if rows else []
    write_csv(output, fields, rows)
    print(output)

    if args.fail_on_mismatch:
        audit_rows = read_jsonl(RESULTS / f"pep_audit_{args.mode}.jsonl")
        joined = join_audit_decision(rows, audit_rows, id_field="attempt_id")
        problems = [r for r in joined if r["is_execution_error"] == "true" or r["matches_expected"] != "true"]
        if problems:
            print(f"PREFLIGHT FAILED: {len(problems)}/{len(joined)} request(s) mismatched or hit an execution error:")
            for r in problems[:20]:
                print(f"  {r['scenario_id']} attempt={r['attempt_id']} expected={r['expected']} actual={r['actual']} is_execution_error={r['is_execution_error']} audit_reason={r['audit_reason']}")
            return 1
        print(f"PREFLIGHT OK: {len(joined)}/{len(joined)} requests matched expectations with no execution errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
