"""Authorization must constrain both retrievers before candidate ranking."""
import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.access import allowed
from rag.research import ResearchIndex


def test_both_retrievers_exclude_unauthorized_candidate_before_fusion(monkeypatch):
    obj = ResearchIndex.__new__(ResearchIndex)
    obj.chunks = [{"chunk_id": 0, "source_id": "public", "workspace": "public", "text": "public retrieval research"},
                  {"chunk_id": 1, "source_id": "secret", "workspace": "research", "text": "ORCHID-742 secret budget 137"}]
    obj.vectors = np.asarray([[0., 1.], [1., 0.]], dtype="float32")
    obj.threshold, obj.manifest_hash = 0, "test"
    monkeypatch.setattr("rag.research.embed_query", lambda q: np.asarray([[1., 0.]], dtype="float32"))
    outsider = {"role": "researcher", "workspace": "other"}
    out = obj.search("ORCHID-742 secret budget", outsider, mode="hybrid")
    assert out["trace"]["authorized_candidates"] == 1
    assert all(i == 0 for i, _ in out["trace"]["dense"])
    assert all(i == 0 for i, _ in out["trace"]["bm25"])
    assert all(e["source_id"] != "secret" for e in out["evidence"])
    denied = obj.search("secret budget", outsider, source_id="secret")
    assert denied["status"] == "forbidden"
    assert denied["evidence"] == []


def test_acl_requires_matching_workspace_and_supported_role():
    private = {"workspace": "research"}
    assert allowed(private, {"workspace": "research", "role": "researcher"})
    assert not allowed(private, {"workspace": "other", "role": "reviewer"})
    assert not allowed(private, {"workspace": "research", "role": "unknown"})


def test_ingestion_honors_utf8_and_excludes_navigation():
    from scripts.ingest_research import clean
    document = '<html><head><meta charset="utf-8"></head><body><nav>unrelated navigation</nav><main><h1>RRF</h1><p>A document’s rank matters.</p></main></body></html>'
    result = clean(document.encode("utf-8"))
    assert "document’s rank" in result
    assert "unrelated navigation" not in result
