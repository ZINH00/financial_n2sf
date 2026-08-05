from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

SERVICE_NAME = os.getenv("SERVICE_NAME", "external")
app = FastAPI(title=f"Financial Institution A - {SERVICE_NAME} stub")


class IngestRequest(BaseModel):
    source_object_id: str
    content: dict[str, Any]
    content_hash: str
    purpose: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "zone": "O"}


@app.post("/ingest")
def ingest(request: IngestRequest) -> dict[str, str]:
    return {"status": "accepted", "service": SERVICE_NAME, "source_object_id": request.source_object_id, "content_hash": request.content_hash}
