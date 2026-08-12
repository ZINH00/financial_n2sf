from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psycopg
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown")
BUSINESS_ROLE = os.getenv("BUSINESS_ROLE", SERVICE_NAME)
ZONE = os.getenv("ZONE", "S")
DB_HOST = os.getenv("DB_HOST", f"{SERVICE_NAME}_db")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB", "n2sf")
DB_USER = os.getenv("POSTGRES_USER", "n2sf")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "n2sf_lab_pw")
PEP_URL = os.getenv("PEP_URL", "http://pep:8080")
WORKLOAD_SECRET = os.getenv("WORKLOAD_SECRET", "")
AUDIT_FILE = Path(os.getenv("AUDIT_FILE", "/tmp/service_audit.jsonl"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=5.0)
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(title=f"Financial Institution A - {SERVICE_NAME}", lifespan=lifespan)


class BusinessActionRequest(BaseModel):
    case_id: str = Field(default="CASE-0001", min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class CrossServiceRequest(BaseModel):
    destination: str
    path: str
    method: str = "GET"
    data_grade: str = "S"
    purpose: str = "loan_screening"
    transfer_approved: bool = False
    body: dict[str, Any] | None = None


def db_conn() -> psycopg.Connection[Any]:
    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=3,
    )


def audit(event: dict[str, Any]) -> None:
    event = {"ts": datetime.now(timezone.utc).isoformat(), "service": SERVICE_NAME, **event}
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def sign_workload(timestamp: str) -> str:
    if not WORKLOAD_SECRET:
        return ""
    return hmac.new(WORKLOAD_SECRET.encode(), f"{SERVICE_NAME}:{timestamp}".encode(), hashlib.sha256).hexdigest()


def _serialize_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "record_id": row[0],
        "case_id": row[1],
        "data_grade": row[2],
        "payload": row[3],
        "created_at": row[4].isoformat() if row[4] else None,
    }


def _read_records(case_id: str, action: str) -> dict[str, Any]:
    try:
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT record_id, case_id, data_grade, payload, created_at "
                "FROM business_records WHERE case_id = %s ORDER BY record_id DESC LIMIT 5",
                (case_id,),
            )
            rows = cur.fetchall()
    except Exception as exc:  # pragma: no cover - lab runtime path
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
    audit({"event": action, "case_id": case_id, "count": len(rows)})
    return {
        "service": SERVICE_NAME,
        "action": action,
        "case_id": case_id,
        "records": [_serialize_row(row) for row in rows],
    }


def _write_record(case_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO business_records(case_id, data_grade, payload) VALUES (%s, %s, %s::jsonb) RETURNING record_id",
                (case_id, "S" if ZONE == "S" else "O", json.dumps({"action": action, **payload})),
            )
            record_id = cur.fetchone()[0]
            conn.commit()
    except Exception as exc:  # pragma: no cover - lab runtime path
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000
    audit({"event": action, "case_id": case_id, "record_id": record_id, "elapsed_ms": elapsed_ms})
    return {"service": SERVICE_NAME, "action": action, "case_id": case_id, "record_id": record_id, "elapsed_ms": elapsed_ms}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "role": BUSINESS_ROLE, "zone": ZONE}


# 논문 3.3절의 업무 행위(Action) 단위 엔드포인트. 모든 업무 컨테이너가 동일한 이미지를
# 사용하므로 다섯 행위 모두를 동일하게 노출하고, 실제 어떤 업무가 응답하는지는 PEP의
# 목적지 라우팅(destination)으로 결정된다. 행위 자체의 허용 여부는 PEP가 파생한
# canonical action과 OPA 정책(permissions 튜플)이 판단한다.
@app.get("/customer-profile/{case_id}")
def read_customer_profile(case_id: str) -> dict[str, Any]:
    return _read_records(case_id, "read_customer_profile")


@app.post("/credit-assessment")
def request_credit_assessment(request: BusinessActionRequest) -> dict[str, Any]:
    return _write_record(request.case_id, "request_credit_assessment", request.payload)


@app.post("/aml-screening")
def request_aml_screening(request: BusinessActionRequest) -> dict[str, Any]:
    return _write_record(request.case_id, "request_aml_screening", request.payload)


@app.post("/approval-requests")
def submit_for_approval(request: BusinessActionRequest) -> dict[str, Any]:
    return _write_record(request.case_id, "submit_for_approval", request.payload)


@app.get("/loan-review/{case_id}")
def read_review_package(case_id: str) -> dict[str, Any]:
    return _read_records(case_id, "read_review_package")


@app.post("/call")
async def call_service(
    call: CrossServiceRequest,
    http_request: Request,
    x_user: str = Header(default="lab-user"),
    x_role: str = Header(default="loan_reviewer"),
    x_device_trust: str = Header(default="trusted"),
    x_scenario_id: str = Header(default=""),
    x_experiment_run_id: str = Header(default=""),
) -> dict[str, Any]:
    """실제 출발 업무 워크로드가 자기 신원(SERVICE_NAME/BUSINESS_ROLE)을 서명하여 PEP를
    호출한다. 호출자는 destination/path/method/purpose 등 업무 요청 내용만 지정할 수
    있으며, source_service/source_business와 워크로드 서명은 이 컨테이너의 환경설정과
    비밀키로만 생성되어 호출자가 덮어쓸 수 없다."""
    timestamp = str(int(time.time()))
    headers = {
        "x-user": x_user,
        "x-role": x_role,
        "x-device-trust": x_device_trust,
        "x-source-service": SERVICE_NAME,
        "x-source-business": BUSINESS_ROLE,
        "x-workload-timestamp": timestamp,
        "x-workload-signature": sign_workload(timestamp),
        "x-purpose": call.purpose,
        "x-data-grade": call.data_grade,
        "x-transfer-approved": str(call.transfer_approved).lower(),
        "x-scenario-id": x_scenario_id,
        "x-experiment-run-id": x_experiment_run_id,
    }
    url = f"{PEP_URL}/proxy/{call.destination}{call.path}"
    client: httpx.AsyncClient = http_request.app.state.http_client
    method = call.method.upper()
    # httpx의 json=None은 파라미터를 생략한 것과 같아 길이 0의 body가 전송된다.
    # POST/PUT/PATCH 목적지 엔드포인트는 Pydantic 모델 body를 요구하므로(필드에
    # 기본값이 있어도 body 자체가 비어 있으면 FastAPI가 JSON 파싱 실패로 422를
    # 반환한다), body가 없을 때는 빈 객체 {}를 명시적으로 보낸다.
    request_kwargs: dict[str, Any] = {}
    if method in {"POST", "PUT", "PATCH"}:
        request_kwargs["json"] = call.body if call.body is not None else {}
    response = await client.request(method, url, headers=headers, **request_kwargs)
    audit({"event": "cross_service_call", "destination": call.destination, "status": response.status_code, "scenario_id": x_scenario_id})
    try:
        body = response.json()
    except ValueError:
        body = {"text": response.text}
    return {"status_code": response.status_code, "response": body}
