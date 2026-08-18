from __future__ import annotations

import argparse

import httpx

from common import MODES, RESULTS, join_audit_decision, read_csv, read_jsonl, timestamp, write_csv
from run_policy_tests import call_via_workload

# 논문 3.4.1의 정책경로 구성: 3.3에서 정의한 역할·출발업무·목적업무·요청행위·
# 업무목적을 대상으로 허용 여부를 확인한다. 요청행위는 목적업무가 노출하는
# 엔드포인트(business_endpoints.csv)로 결정되므로 자유변수가 아니다. 역할·목적은
# policies/data.json의 permissions에서 실제 쓰이는 값만 추출한다(정책이 바뀌어도
# 스크립트가 깨지지 않게). 5개 출발업무 x 4개 목적업무(자기 제외) x 역할 x 목적
# 조합을 전부 실제 /call -> HMAC -> PEP -> OPA 경로로 호출한다.


def load_actions() -> dict[str, dict]:
    rows = read_csv("business_endpoints.csv")
    return {row["destination"]: row for row in rows}


def load_role_purpose_domains() -> tuple[list[str], list[str]]:
    import json
    from pathlib import Path

    data_path = Path(__file__).resolve().parents[1] / "policies" / "data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    permissions = data["financial"]["permissions"]
    roles = sorted({p["role"] for p in permissions})
    purposes = sorted({p["purpose"] for p in permissions})
    return roles, purposes


def main() -> int:
    parser = argparse.ArgumentParser(description="Exhaustively probe the policy decision space (source_business x destination x role x purpose) via the real /call -> PEP -> OPA path.")
    parser.add_argument("--mode", choices=list(MODES), required=True)
    parser.add_argument("--data-grade", default="S")
    parser.add_argument("--experiment-run-id", default=None)
    args = parser.parse_args()
    experiment_run_id = args.experiment_run_id or f"policyspace-{args.mode}-{timestamp()}"

    actions = load_actions()
    businesses = sorted(actions.keys())
    roles, purposes = load_role_purpose_domains()

    combos = []
    for source_business in businesses:
        for destination in businesses:
            if destination == source_business:
                continue
            for role in roles:
                for purpose in purposes:
                    combos.append((source_business, destination, role, purpose))

    rows: list[dict] = []
    with httpx.Client(timeout=10.0) as client:
        for source_business, destination, role, purpose in combos:
            endpoint = actions[destination]
            attempt_id = f"PS-{source_business}-{destination}-{role}-{purpose}"
            scenario = {
                "role": role,
                "device_trust": "trusted",
                "destination": destination,
                "path": endpoint["path"],
                "method": endpoint["method"],
                "purpose": purpose,
                "data_grade": args.data_grade,
            }
            status_code, error, latency_ms = call_via_workload(client, source_business, scenario, experiment_run_id, attempt_id)
            actual = "allow" if 200 <= status_code < 300 else "deny"
            rows.append({
                "mode": args.mode,
                "experiment_run_id": experiment_run_id,
                "attempt_id": attempt_id,
                "source_business": source_business,
                "destination": destination,
                "role": role,
                "purpose": purpose,
                "action": endpoint["action"],
                "path": endpoint["path"],
                "method": endpoint["method"],
                "status_code": status_code,
                "actual": actual,
                "latency_ms": f"{latency_ms:.6f}",
                "error": error,
            })

    # HTTP status만으로는 정책적 deny와 opa_transport_error 같은 실행 오류가
    # 구분되지 않으므로(논문 3.4.2), PEP 감사로그의 실제 decision을 attempt_id+
    # experiment_run_id로 조인해 최종 판단 근거로 삼는다. build_effective_graph.py는
    # 이 audit_decision/is_execution_error 컬럼을 사용한다.
    audit_rows = read_jsonl(RESULTS / f"pep_audit_{args.mode}.jsonl")
    rows = join_audit_decision(rows, audit_rows, id_field="attempt_id")
    execution_errors = sum(1 for r in rows if r["is_execution_error"] == "true")

    output = RESULTS / f"raw_policy_space_{args.mode}_{timestamp()}.csv"
    write_csv(output, list(rows[0].keys()), rows)
    print(output)
    print(f"combinations_tested={len(rows)} (expected {len(businesses) * (len(businesses) - 1) * len(roles) * len(purposes)}), execution_errors={execution_errors}")
    return 1 if execution_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
