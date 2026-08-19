from __future__ import annotations

import argparse
import time

import httpx

from common import MODES, RESULTS, as_bool, join_audit_decision, parse_json, read_csv, read_jsonl, timestamp, write_csv

# 논문 Table 3: S/O Boundary(Transfer CDS)는 2x2의 "Experimental Factors"(Network,
# Access Policy)에 포함되지 않는 고정 Security Control이다. 네 실험조건 모두
# 동일한 policies/cds.rego를 사용하므로(compose.*.yml) 기대값도 모드와 무관하게
# 하나만 존재한다 — --mode는 그 조건에서 CDS가 정상 배포·동작하는지 확인하는
# 사전검증(preflight) 대상을 고르는 용도로만 남겨둔다.


def main() -> int:
    parser = argparse.ArgumentParser(description="Run S/O Transfer CDS validation scenarios (fixed Security Control, identical policy across all modes).")
    parser.add_argument("--mode", choices=list(MODES), required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18090")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--experiment-run-id", default=None)
    parser.add_argument("--fail-on-mismatch", action="store_true", help="사전검증(preflight) 모드: 기대값과 다르거나 실행 오류인 요청이 하나라도 있으면 exit 1")
    args = parser.parse_args()
    experiment_run_id = args.experiment_run_id or f"{args.mode}-{timestamp()}"

    rows: list[dict] = []
    with httpx.Client(timeout=10.0) as client:
        for scenario in read_csv("cds_flows.csv"):
            expected = scenario["expected"]
            payload = {
                "destination": scenario["destination"],
                "data_grade": scenario["data_grade"],
                "approved": as_bool(scenario["approved"]),
                "purpose": scenario["purpose"],
                "content": parse_json(scenario["content_json"]),
                "source_object_id": scenario["source_object_id"],
            }
            for run_id in range(1, args.repeat + 1):
                # 요청마다 고유한 attempt_id를 CDS의 scenario_id로 전달해, 반복된
                # 동일 시나리오라도 감사로그와 1:1로 상관관계를 확인할 수 있게 한다.
                attempt_id = f"{scenario['scenario_id']}-r{run_id:03d}"
                headers = {
                    "x-user": "lab-user",
                    "x-role": scenario["role"],
                    "x-device-trust": scenario["device_trust"],
                    "x-scenario-id": attempt_id,
                    "x-experiment-run-id": experiment_run_id,
                }
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
                    "experiment_run_id": experiment_run_id,
                    "run_id": run_id,
                    "attempt_id": attempt_id,
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

    if args.fail_on_mismatch:
        audit_rows = read_jsonl(RESULTS / f"cds_audit_{args.mode}.jsonl")
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
