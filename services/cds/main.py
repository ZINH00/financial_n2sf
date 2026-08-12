from __future__ import annotations

import hashlib
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
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

OPA_URL = os.getenv("CDS_OPA_URL", "http://opa:8181/v1/data/financial/cds/decision")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://public_app:8000")
AI_URL = os.getenv("AI_URL", "http://external_ai:8000")
AUDIT_FILE = Path(os.getenv("AUDIT_FILE", "/logs/cds_audit.jsonl"))
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "5.0"))
BLOCK_PATTERNS = [
    re.compile(r"\b\d{6}-?[1-4]\d{6}\b"),
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    re.compile(r"(?i)(resident[_ -]?id|credit[_ -]?card|personal[_ -]?credit[_ -]?info)"),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=TIMEOUT)
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(title="Financial Institution A Transfer CDS Prototype", lifespan=lifespan)


class TransferRequest(BaseModel):
    destination: str = Field(pattern="^(public|external_ai)$")
    data_grade: str = Field(pattern="^[SO]$")
    approved: bool
    purpose: str
    content: dict[str, Any]
    source_object_id: str


def write_audit(record: dict[str, Any]) -> None:
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with AUDIT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def content_safe(content: dict[str, Any]) -> tuple[bool, list[str]]:
    text = json.dumps(content, ensure_ascii=False)
    hits = [p.pattern for p in BLOCK_PATTERNS if p.search(text)]
    return (len(hits) == 0, hits)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "component": "transfer-cds"}


@app.post("/transfer")
async def transfer(
    request: TransferRequest,
    x_user: str = Header(default="anonymous"),
    x_role: str = Header(default="none"),
    x_device_trust: str = Header(default="untrusted"),
    x_scenario_id: str = Header(default=""),
    x_experiment_run_id: str = Header(default="adhoc"),
) -> dict[str, Any]:
    total_started = time.perf_counter()
    request_id = str(uuid.uuid4())
    safe, pattern_hits = content_safe(request.content)
    digest = hashlib.sha256(json.dumps(request.content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    input_doc = {
        "user": x_user,
        "role": x_role,
        "device_trust": x_device_trust,
        "destination": request.destination,
        "data_grade": request.data_grade,
        "approved": request.approved,
        "purpose": request.purpose,
        "content_safe": safe,
        "source_object_id": request.source_object_id,
    }
    client: httpx.AsyncClient = app.state.http_client
    audit_prefix = {
        "request_id": request_id,
        "experiment_run_id": x_experiment_run_id or "adhoc",
        "scenario_id": x_scenario_id,
        "input": input_doc,
        "content_hash": digest,
        "pattern_hits": pattern_hits,
    }

    def deny(reason: str, status_code: int, decision_ms: float, upstream_ms: float = 0.0, upstream_status: int | None = None) -> None:
        total_ms = (time.perf_counter() - total_started) * 1000
        write_audit({**audit_prefix, "decision_ms": decision_ms, "decision": "deny", "reason": reason, "upstream_ms": upstream_ms, "total_ms": total_ms, "upstream_status": upstream_status})
        raise HTTPException(status_code=status_code, detail={"reason": reason, "pattern_hits": pattern_hits, "request_id": request_id})

    decision_started = time.perf_counter()
    try:
        opa_response = await client.post(OPA_URL, json={"input": input_doc})
    except httpx.HTTPError:
        deny("cds_opa_transport_error", 503, (time.perf_counter() - decision_started) * 1000)
    decision_ms = (time.perf_counter() - decision_started) * 1000
    result = (opa_response.json().get("result") if opa_response.status_code == 200 else None) or {}
    allow = bool(result.get("allow", False))
    reason = result.get("reason", "policy_denied")
    audit_base = {**audit_prefix, "decision_ms": decision_ms}
    if not allow:
        deny(reason, 403, decision_ms)
    target = PUBLIC_URL if request.destination == "public" else AI_URL
    upstream_started = time.perf_counter()
    try:
        response = await client.post(f"{target}/ingest", json={
            "source_object_id": request.source_object_id,
            "content": request.content,
            "content_hash": digest,
            "purpose": request.purpose,
        })
    except httpx.HTTPError:
        deny("cds_upstream_transport_error", 502, decision_ms, upstream_ms=(time.perf_counter() - upstream_started) * 1000)
    upstream_ms = (time.perf_counter() - upstream_started) * 1000
    total_ms = (time.perf_counter() - total_started) * 1000
    write_audit({**audit_base, "decision": "allow", "reason": reason, "upstream_ms": upstream_ms, "total_ms": total_ms, "upstream_status": response.status_code})
    return {"status": "transferred", "destination": request.destination, "upstream_status": response.status_code, "content_hash": digest, "request_id": request_id}
