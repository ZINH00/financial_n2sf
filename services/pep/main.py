from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

OPA_URL = os.getenv("OPA_URL", "http://opa:8181/v1/data/financial/access/decision")
AUDIT_FILE = Path(os.getenv("AUDIT_FILE", "/logs/pep_audit.jsonl"))
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "5.0"))
SERVICE_MAP = {
    "customer": "http://customer_app:8000",
    "loan": "http://loan_app:8000",
    "credit": "http://credit_app:8000",
    "aml": "http://aml_app:8000",
    "approval": "http://approval_app:8000",
}

app = FastAPI(title="Financial Institution A Policy Enforcement Point")


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
    x_purpose: str = Header(default="unspecified"),
    x_data_grade: str = Header(default="S"),
    x_transfer_approved: str = Header(default="false"),
) -> Any:
    if destination not in SERVICE_MAP:
        raise HTTPException(status_code=404, detail="unknown destination")
    input_doc = {
        "user": x_user,
        "role": x_role,
        "device_trust": x_device_trust,
        "source_service": x_source_service,
        "source_business": x_source_business,
        "destination": destination,
        "method": request.method,
        "path": "/" + path,
        "purpose": x_purpose,
        "data_grade": x_data_grade,
        "transfer_approved": x_transfer_approved.lower() == "true",
    }
    decision_started = time.perf_counter()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        opa_response = await client.post(OPA_URL, json={"input": input_doc})
    decision_ms = (time.perf_counter() - decision_started) * 1000
    if opa_response.status_code != 200:
        write_audit({"input": input_doc, "allow": False, "reason": "opa_error", "decision_ms": decision_ms})
        raise HTTPException(status_code=503, detail="policy engine unavailable")
    result = opa_response.json().get("result") or {}
    allow = bool(result.get("allow", False))
    reason = result.get("reason", "unspecified")
    write_audit({"input": input_doc, "allow": allow, "reason": reason, "decision_ms": decision_ms})
    if not allow:
        raise HTTPException(status_code=403, detail={"reason": reason, "decision_ms": decision_ms})
    body = await request.body()
    target = f"{SERVICE_MAP[destination]}/{path}"
    forward_headers = {"x-policy-user": x_user, "x-policy-role": x_role}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        upstream = await client.request(request.method, target, params=request.query_params, content=body, headers=forward_headers)
    content_type = upstream.headers.get("content-type", "")
    if "application/json" in content_type:
        return upstream.json()
    return {"status_code": upstream.status_code, "text": upstream.text}
