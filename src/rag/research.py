"""Permission-first retrieval with inspectable candidate lists and an evidence gate."""
from hashlib import sha256
import json
from pathlib import Path
import re
from time import perf_counter

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from .access import allowed
from .embeddings import embed_query
from .retrieval import _get_reranker

ROOT = Path(__file__).resolve().parents[2]


class ResearchIndex:
    def __init__(self, location=None):
        self.location = Path(location or ROOT / "data/research")
        self.chunks = json.loads((self.location / "chunks.json").read_text(encoding="utf-8"))
        self.vectors = np.load(self.location / "embeddings.npy")
        self.manifest_hash = sha256((ROOT / "research-manifest.json").read_bytes()).hexdigest()
        self.threshold = json.loads((ROOT / "research-gate.json").read_text()).get("threshold", 0.) if (ROOT / "research-gate.json").exists() else 0.

    def search(self, query, principal, source_id=None, mode="rerank", candidate_ids=None):
        if mode not in ("dense", "hybrid", "rerank"):
            raise ValueError("Invalid retrieval mode")
        start = perf_counter()
        ids = [i for i, c in enumerate(self.chunks) if (candidate_ids is None or i in candidate_ids) and allowed(c, principal) and (not source_id or c["source_id"] == source_id)]
        if source_id and not ids:
            return {"status": "forbidden", "evidence": [], "trace": {}, "message": "Requested source is not accessible."}
        if not ids:
            return {"status": "insufficient_evidence", "evidence": [], "trace": {}}
        # Both retrievers operate exclusively on authorized rows, never post-filtered top-k.
        index = faiss.IndexFlatIP(self.vectors.shape[1])
        index.add(self.vectors[ids])
        vector = embed_query(query)
        scores, positions = index.search(vector, min(20, len(ids)))
        dense = [(ids[int(i)], float(s)) for i, s in zip(positions[0], scores[0])]
        tokenize = lambda text: re.findall(r"[a-z0-9]+", text.lower())
        tokenized = [tokenize(self.chunks[i]["text"]) for i in ids]
        sparse_scores = BM25Okapi(tokenized).get_scores(tokenize(query))
        sparse = [(ids[int(i)], float(sparse_scores[i])) for i in np.argsort(sparse_scores)[::-1][:20] if sparse_scores[i] > 0]
        retrieval_ms = (perf_counter() - start) * 1000
        fused = {}
        for name, results in (("dense", dense), ("bm25", sparse if mode != "dense" else [])):
            for rank, (i, score) in enumerate(results, 1):
                entry = fused.setdefault(i, {"chunk_id": i, "rrf_score": 0., "ranks": {}})
                entry["rrf_score"] += 1 / (60 + rank)
                entry["ranks"][name] = {"rank": rank, "raw_score": score}
        selected = sorted(fused.values(), key=lambda x: x["rrf_score"], reverse=True)
        before = [dict(v) for v in selected]
        rerank_start = perf_counter()
        if selected and mode == "rerank":
            values = _get_reranker().predict([(query, self.chunks[v["chunk_id"]]["text"]) for v in selected])
            for v, s in zip(selected, values):
                v["rerank_score"] = float(s)
            selected.sort(key=lambda x: x["rerank_score"], reverse=True)
        evidence = [{**self.chunks[v["chunk_id"]], **v} for v in selected[:5]]
        accepted = bool(evidence) and (mode != "rerank" or evidence[0]["rerank_score"] >= self.threshold)
        return {"status": "ok" if accepted else "insufficient_evidence", "evidence": evidence if accepted else [],
                "trace": {"authorized_candidates": len(ids), "dense": dense, "bm25": sparse,
                          "fusion": before, "reranked": selected[:5], "gate_threshold": self.threshold,
                          "gate_passed": accepted, "mode": mode, "manifest_sha256": self.manifest_hash,
                          "retrieval_ms": retrieval_ms, "rerank_ms": (perf_counter() - rerank_start) * 1000,
                          "total_ms": (perf_counter() - start) * 1000}}
