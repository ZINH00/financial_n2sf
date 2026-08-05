from __future__ import annotations

import hashlib
import json
import os
import re
import time
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
BLOCK_PATTERNS = [
    re.compile(r"\b\d{6}-?[1-4]\d{6}\b"),
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    re.compile(r"(?i)(resident[_ -]?id|credit[_ -]?card|personal[_ -]?credit[_ -]?info)"),
]

app = FastAPI(title="Financial Institution A Transfer CDS Prototype")


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
) -> dict[str, Any]:
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
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=5.0) as client:
        opa_response = await client.post(OPA_URL, json={"input": input_doc})
    decision_ms = (time.perf_counter() - started) * 1000
    result = (opa_response.json().get("result") if opa_response.status_code == 200 else None) or {}
    allow = bool(result.get("allow", False))
    reason = result.get("reason", "policy_denied")
    write_audit({
        "input": input_doc,
        "allow": allow,
        "reason": reason,
        "content_hash": digest,
        "pattern_hits": pattern_hits,
        "decision_ms": decision_ms,
    })
    if not allow:
        raise HTTPException(status_code=403, detail={"reason": reason, "pattern_hits": pattern_hits})
    target = PUBLIC_URL if request.destination == "public" else AI_URL
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(f"{target}/ingest", json={
            "source_object_id": request.source_object_id,
            "content": request.content,
            "content_hash": digest,
            "purpose": request.purpose,
        })
    return {"status": "transferred", "destination": request.destination, "upstream_status": response.status_code, "content_hash": digest}
