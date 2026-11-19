"""Structured, authenticated search endpoint; legacy /ask stays available separately."""
from functools import lru_cache

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from rag.access import verify
from rag.access import allowed
from rag.research import ResearchIndex

router = APIRouter()


def principal(authorization: str = Header(default="")):
    try:
        if not authorization.startswith("Bearer "):
            raise ValueError("Missing bearer token")
        return verify(authorization[7:])
    except (ValueError, OSError, KeyError):
        raise HTTPException(401, "Invalid or expired identity")


@lru_cache(maxsize=1)
def index():
    from pathlib import Path
    if not (Path(__file__).resolve().parents[1] / "research-gate.json").exists():
        raise FileNotFoundError("Calibrate the development evidence gate before serving")
    return ResearchIndex()


@router.get("/research/data")
def data(user=Depends(principal)):
    idx = index()
    return {"chunks": [c for c in idx.chunks if allowed(c, user)], "dimension": int(idx.vectors.shape[1])}


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    source_id: str | None = None
    mode: str = "rerank"


@router.get("/research/health")
def health():
    try:
        idx = index()
        return {"status": "ready", "chunks": len(idx.chunks), "manifest_sha256": idx.manifest_hash}
    except (OSError, ValueError):
        raise HTTPException(503, "Run scripts/ingest_research.py first")


@router.post("/search")
async def search(request: SearchRequest, user=Depends(principal)):
    if request.mode not in ("dense", "hybrid", "rerank"):
        raise HTTPException(422, "Unknown retrieval mode")
    return await run_in_threadpool(index().search, request.query, user, request.source_id, request.mode)
