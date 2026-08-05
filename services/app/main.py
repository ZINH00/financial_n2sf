from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psycopg
from fastapi import FastAPI, Header, HTTPException
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
AUDIT_FILE = Path(os.getenv("AUDIT_FILE", "/tmp/service_audit.jsonl"))

app = FastAPI(title=f"Financial Institution A - {SERVICE_NAME}")


class WorkRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)
    operation: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class CrossServiceRequest(BaseModel):
    destination: str
    path: str = "/records"
    method: str = "GET"
    data_grade: str = "S"
    purpose: str = "approved_workflow"
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "role": BUSINESS_ROLE, "zone": ZONE}


@app.get("/records")
def records(limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    try:
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT record_id, case_id, data_grade, payload, created_at "
                "FROM business_records ORDER BY record_id LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    except Exception as exc:  # pragma: no cover - lab runtime path
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
    audit({"event": "records_read", "count": len(rows)})
    return {
        "service": SERVICE_NAME,
        "records": [
            {
                "record_id": row[0],
                "case_id": row[1],
                "data_grade": row[2],
                "payload": row[3],
                "created_at": row[4].isoformat() if row[4] else None,
            }
            for row in rows
        ],
    }


@app.post("/work")
def work(request: WorkRequest) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO business_records(case_id, data_grade, payload) VALUES (%s, %s, %s::jsonb) RETURNING record_id",
                (request.case_id, "S" if ZONE == "S" else "O", json.dumps({"operation": request.operation, **request.payload})),
            )
            record_id = cur.fetchone()[0]
            conn.commit()
    except Exception as exc:  # pragma: no cover - lab runtime path
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000
    audit({"event": "work_write", "record_id": record_id, "operation": request.operation, "elapsed_ms": elapsed_ms})
    return {"service": SERVICE_NAME, "record_id": record_id, "elapsed_ms": elapsed_ms}


@app.post("/call")
async def call_service(
    request: CrossServiceRequest,
    x_user: str = Header(default="lab-user"),
    x_role: str = Header(default="analyst"),
    x_device_trust: str = Header(default="trusted"),
) -> dict[str, Any]:
    headers = {
        "x-user": x_user,
        "x-role": x_role,
        "x-device-trust": x_device_trust,
        "x-source-service": SERVICE_NAME,
        "x-source-business": BUSINESS_ROLE,
        "x-purpose": request.purpose,
        "x-data-grade": request.data_grade,
        "x-transfer-approved": str(request.transfer_approved).lower(),
    }
    url = f"{PEP_URL}/proxy/{request.destination}{request.path}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.request(request.method.upper(), url, headers=headers, json=request.body)
    audit({"event": "cross_service_call", "destination": request.destination, "status": response.status_code})
    try:
        body = response.json()
    except ValueError:
        body = {"text": response.text}
    return {"status_code": response.status_code, "response": body}
