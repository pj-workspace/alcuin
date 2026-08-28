from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


app = FastAPI(
    title="Alcuin Records API",
    description="A small domain-neutral service for validating governed OpenAPI tools.",
    version="0.1.0",
    servers=[{"url": "http://127.0.0.1:9411"}],
)


class Record(BaseModel):
    id: str
    title: str
    status: Literal["open", "monitoring", "closed"]


class RecordPatch(BaseModel):
    status: Literal["open", "monitoring", "closed"]


records = {
    "checkout-latency": Record(
        id="checkout-latency",
        title="Checkout latency",
        status="monitoring",
    )
}


@app.get("/health", operation_id="healthCheck", tags=["system"])
def health_check() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/records/{record_id}", operation_id="getRecord", tags=["records"])
def get_record(record_id: str) -> Record:
    record = records.get(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@app.patch("/records/{record_id}", operation_id="updateRecord", tags=["records"])
def update_record(record_id: str, patch: RecordPatch) -> Record:
    record = records.get(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    updated = record.model_copy(update={"status": patch.status})
    records[record_id] = updated
    return updated
