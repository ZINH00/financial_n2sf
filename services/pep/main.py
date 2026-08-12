from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

OPA_URL = os.getenv("OPA_URL", "http://opa:8181/v1/data/financial/access/decision")
OPA_MODEL_VERSION_URL = OPA_URL.split("/v1/data/")[0] + "/v1/data/financial/model_version"
AUDIT_FILE = Path(os.getenv("AUDIT_FILE", "/logs/pep_audit.jsonl"))
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "5.0"))
WORKLOAD_SIGNATURE_WINDOW_SECONDS = 30
SERVICE_MAP = {
    "customer": "http://customer_app:8000",
    "loan": "http://loan_app:8000",
    "credit": "http://credit_app:8000",
    "aml": "http://aml_app:8000",
    "approval": "http://approval_app:8000",
}
# 업무 워크로드 신원 검증용 비밀키. 키가 곧 검증된 출발 업무명이며, 클라이언트가
# 보낸 x-source-business 헤더는 신뢰하지 않고 서명이 검증된 서비스명으로 대체한다.
WORKLOAD_SECRETS: dict[str, str] = json.loads(os.getenv("WORKLOAD_SECRETS", "{}"))

# 논문 3.3절의 "요청 행위(Action)"를 클라이언트 자기신고가 아니라 PEP가 HTTP
# method+path로부터 서버 측에서 결정한다. 매핑되지 않는 경로는 unsupported_action으로
# 표시되어 OPA의 기본거부(default-deny)로 자연스럽게 막힌다.
ACTION_ROUTES: list[tuple[str, re.Pattern[str], str]] = [
    ("GET", re.compile(r"^/customer-profile/[^/]+$"), "read_customer_profile"),
    ("POST", re.compile(r"^/credit-assessment$"), "request_credit_assessment"),
    ("POST", re.compile(r"^/aml-screening$"), "request_aml_screening"),
    ("POST", re.compile(r"^/approval-requests$"), "submit_for_approval"),
    ("GET", re.compile(r"^/loan-review/[^/]+$"), "read_review_package"),
]


def derive_action(method: str, path: str) -> str:
    for route_method, pattern, action in ACTION_ROUTES:
        if method == route_method and pattern.match(path):
            return action
    return "unsupported_action"


def verify_workload(source_service: str, timestamp: str, signature: str) -> tuple[bool, str, str | None]:
    """PEP가 자체적으로 출발 업무 신원을 검증한다(NIST SP 800-207A의 서비스 신원
    기반 정책 취지). 클라이언트가 자칭한 x-source-business는 여기서 검증에 성공한
    경우에만, 그것도 서명에 쓰인 서비스명으로 대체되어 신뢰된다."""
    secret = WORKLOAD_SECRETS.get(source_service)
    if not secret:
        return False, "unregistered_workload", None
    if not timestamp or not signature:
        return False, "workload_signature_invalid", None
    try:
        ts = int(timestamp)
    except ValueError:
        return False, "workload_signature_invalid", None
    if abs(time.time() - ts) > WORKLOAD_SIGNATURE_WINDOW_SECONDS:
        return False, "workload_signature_invalid", None
    expected = hmac.new(secret.encode(), f"{source_service}:{timestamp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False, "workload_signature_invalid", None
    return True, "ok", source_service


async def refresh_policy_version(app: FastAPI, attempts: int = 1, delay_seconds: float = 1.0) -> None:
    """docker compose의 short-form depends_on은 OPA가 "시작되었다"는 순서만 보장할
    뿐 요청을 받을 준비가 됐다는 것까지 보장하지 않는다(OPA는 -static 이미지라 셸이
    없어 Docker 레벨 healthcheck를 붙이기 어렵다). PEP 기동 시 여러 번 재시도하고,
    그래도 실패해 policy_version이 "unknown"으로 남아 있으면 이후 요청이 들어올 때
    한 번씩 다시 시도해 자연스럽게 회복한다."""
    for attempt in range(attempts):
        try:
            response = await app.state.http_client.get(OPA_MODEL_VERSION_URL)
            if response.status_code == 200:
                app.state.policy_version = response.json().get("result", "unknown")
                return
        except httpx.HTTPError:
            pass
        if attempt < attempts - 1:
            await asyncio.sleep(delay_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=TIMEOUT)
    app.state.policy_version = "unknown"
    await refresh_policy_version(app, attempts=8, delay_seconds=1.0)
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(title="Financial Institution A Policy Enforcement Point", lifespan=lifespan)


def write_audit(record: dict[str, Any]) -> None:
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with AUDIT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "component": "pep"}


@app.api_route("/proxy/{destination}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(
    destination: str,
    path: str,
    request: Request,
    x_user: str = Header(default="anonymous"),
    x_role: str = Header(default="none"),
    x_device_trust: str = Header(default="untrusted"),
    x_source_service: str = Header(default="external"),
    x_source_business: str = Header(default="external"),
    x_workload_timestamp: str = Header(default=""),
    x_workload_signature: str = Header(default=""),
    x_purpose: str = Header(default="unspecified"),
    x_data_grade: str = Header(default="S"),
    x_transfer_approved: str = Header(default="false"),
    x_scenario_id: str = Header(default=""),
    x_experiment_run_id: str = Header(default="adhoc"),
) -> Any:
    total_started = time.perf_counter()
    request_id = str(uuid.uuid4())
    if request.app.state.policy_version == "unknown":
        await refresh_policy_version(request.app)
    path_with_slash = "/" + path
    action = derive_action(request.method, path_with_slash)
    verified, identity_reason, verified_business = verify_workload(x_source_service, x_workload_timestamp, x_workload_signature)

    # audit_base를 먼저 만들어두고, 이후 모든 거부·오류 경로(미등록 목적지 포함)가
    # write_audit을 거쳐 예외를 던지도록 한다. README가 "허용/거부 모든 경로에서
    # 동일한 필드 집합을 남긴다"고 명시하므로, 요청/응답 트랜스포트 실패나 목적지
    # 오기입도 감사로그 없이 조용히 FastAPI 기본 오류로 빠지면 안 된다.
    audit_base = {
        "request_id": request_id,
        "experiment_run_id": x_experiment_run_id or "adhoc",
        "scenario_id": x_scenario_id,
        "user_role": x_role,
        "claimed_source_service": x_source_service,
        "verified_workload_identity": verified,
        "destination": destination,
        "action": action,
        "purpose": x_purpose,
        "data_grade": x_data_grade,
        "policy_version": request.app.state.policy_version,
    }

    def deny(reason: str, status_code: int, source_business: str | None, decision_ms: float = 0.0, upstream_ms: float = 0.0, upstream_status: int | None = None) -> None:
        total_ms = (time.perf_counter() - total_started) * 1000
        write_audit({
            **audit_base,
            "source_business": source_business,
            "decision": "deny",
            "reason": reason,
            "decision_ms": decision_ms,
            "upstream_ms": upstream_ms,
            "total_ms": total_ms,
            "upstream_status": upstream_status,
        })
        raise HTTPException(status_code=status_code, detail={"reason": reason, "request_id": request_id})

    if destination not in SERVICE_MAP:
        deny("unknown_destination", 404, None)

    if not verified:
        deny(identity_reason, 403, None)

    input_doc = {
        "user": x_user,
        "role": x_role,
        "device_trust": x_device_trust,
        "source_service": x_source_service,
        "source_business": verified_business,
        "destination": destination,
        "method": request.method,
        "path": path_with_slash,
        "action": action,
        "purpose": x_purpose,
        "data_grade": x_data_grade,
        "transfer_approved": x_transfer_approved.lower() == "true",
    }
    client: httpx.AsyncClient = request.app.state.http_client
    decision_started = time.perf_counter()
    try:
        opa_response = await client.post(OPA_URL, json={"input": input_doc})
    except httpx.HTTPError:
        deny("opa_transport_error", 503, verified_business, decision_ms=(time.perf_counter() - decision_started) * 1000)
    decision_ms = (time.perf_counter() - decision_started) * 1000
    if opa_response.status_code != 200:
        deny("opa_error", 503, verified_business, decision_ms=decision_ms)
    result = opa_response.json().get("result") or {}
    allow = bool(result.get("allow", False))
    reason = result.get("reason", "unspecified")

    if not allow:
        deny(reason, 403, verified_business, decision_ms=decision_ms)

    body = await request.body()
    target = f"{SERVICE_MAP[destination]}/{path}"
    forward_headers = {"x-policy-user": x_user, "x-policy-role": x_role, "x-request-id": request_id}
    upstream_started = time.perf_counter()
    try:
        upstream = await client.request(request.method, target, params=request.query_params, content=body, headers=forward_headers)
    except httpx.HTTPError:
        deny("upstream_transport_error", 502, verified_business, decision_ms=decision_ms, upstream_ms=(time.perf_counter() - upstream_started) * 1000)
    upstream_ms = (time.perf_counter() - upstream_started) * 1000
    total_ms = (time.perf_counter() - total_started) * 1000
    write_audit({
        **audit_base,
        "source_business": verified_business,
        "decision": "allow",
        "reason": reason,
        "decision_ms": decision_ms,
        "upstream_ms": upstream_ms,
        "total_ms": total_ms,
        "upstream_status": upstream.status_code,
    })
    # 업스트림 업무 서비스가 실제로 반환한 HTTP status를 그대로 보존한다. PEP가
    # 항상 200으로 감싸버리면(예: 업무 DB 장애로 upstream이 503을 반환해도) 테스트
    # 하네스가 status_code만으로 성공 여부를 판정하므로 장애가 성공으로 기록된다.
    content_type = upstream.headers.get("content-type", "")
    if "application/json" in content_type:
        return JSONResponse(status_code=upstream.status_code, content=upstream.json())
    return JSONResponse(status_code=upstream.status_code, content={"status_code": upstream.status_code, "text": upstream.text})
