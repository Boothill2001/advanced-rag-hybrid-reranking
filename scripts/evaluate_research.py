"""Dev-only evidence gate calibration; frozen held-out retrieval ablation."""
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from rag.research import ResearchIndex

USER = {"sub": "researcher", "role": "researcher", "workspace": "research"}
DEV = [
    ("How does reciprocal rank fusion combine rankings?", "rrf"),
    ("How do LangGraph interrupts pause for human input?", "interrupts"),
    ("What is a checkpoint thread in LangGraph persistence?", "persistence"),
    ("What are MCP tools input schemas?", "mcp-tools"),
    ("How does a cross encoder rerank retrieved passages?", "reranking"),
    ("What is the current live price of Bitcoin right now?", None),
    ("What is the precise blood glucose level of patient Maria?", None),
    ("What was the final score in yesterday's baseball game?", None),
]
TEST = [
    ("What role does rank constant k play in RRF?", "rrf"),
    ("Does reciprocal rank fusion use raw relevance scores from each retriever?", "rrf"),
    ("How can I merge lexical and dense rankings?", "rrf"),
    ("How do I resume a graph after an interrupt?", "interrupts"),
    ("Why must side effects before interrupts be idempotent?", "interrupts"),
    ("How is human input returned to a paused graph node?", "interrupts"),
    ("What does a LangGraph checkpointer save?", "persistence"),
    ("Why use a thread ID for checkpointed agent execution?", "persistence"),
    ("What is a StateSnapshot?", "persistence"),
    ("What are the host, client and server in MCP?", "mcp-architecture"),
    ("What does MCP tool discovery return?", "mcp-architecture"),
    ("How are tools called in the model context protocol?", "mcp-tools"),
    ("How do MCP tools report execution errors?", "mcp-tools"),
    ("What is inputSchema in an MCP tool definition?", "mcp-tools"),
    ("Why retrieve candidates before cross-encoder reranking?", "reranking"),
    ("How does a bi-encoder differ from a cross-encoder?", "reranking"),
    ("What was my credit card balance this morning?", None),
    ("What is the latest airline ticket price from Hanoi to Tokyo?", None),
]


def evaluate():
    idx = ResearchIndex()
    dev = []
    for q, source in DEV:
        result = idx.search(q, USER)
        ranked = result["trace"]["reranked"]
        score = ranked[0]["rerank_score"] if ranked else -100.
        dev.append({"query": q, "expected_source": source, "top_score": score})
    scores = sorted({d["top_score"] for d in dev})
    candidates = [scores[0] - 1, *[(a + b) / 2 for a, b in zip(scores, scores[1:])], scores[-1] + 1]
    def counts(threshold):
        tp = sum(d["top_score"] >= threshold and d["expected_source"] is not None for d in dev)
        fp = sum(d["top_score"] >= threshold and d["expected_source"] is None for d in dev)
        fn = sum(d["top_score"] < threshold and d["expected_source"] is not None for d in dev)
        return tp, fp, fn
    def key(threshold):
        tp, fp, fn = counts(threshold)
        return (2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0., -fp, threshold)
    threshold = max(candidates, key=key)
    selection = {"threshold": threshold, "rule": "maximize dev answerability F1, fewer false accepts, higher threshold on tie",
                 "dev": dev, "dev_tp_fp_fn": counts(threshold), "notice": "Raw cross-encoder scores are not probabilities. No test questions select the threshold."}
    (ROOT / "research-gate.json").write_text(json.dumps(selection, indent=2), encoding="utf-8")
    idx.threshold = threshold
    result = {"manifest_sha256": sha256((ROOT / "research-manifest.json").read_bytes()).hexdigest(), "selection": selection, "test": {}}
    for mode in ("dense", "hybrid", "rerank"):
        cases = []
        for q, source in TEST:
            out = idx.search(q, USER, mode=mode)
            retrieved = out["evidence"]
            rank = next((i for i, e in enumerate(retrieved, 1) if e["source_id"] == source), None) if source else None
            cases.append({"query": q, "expected_source": source, "status": out["status"], "source_hit_at_5": rank is not None,
                          "reciprocal_rank": 1 / rank if rank else 0., "sources": [e["source_id"] for e in retrieved],
                          "latency_ms": out["trace"]["total_ms"]})
        answerable = [c for c in cases if c["expected_source"]]
        result["test"][mode] = {"n": len(cases), "answerable_n": len(answerable),
            "source_hit_at_5": sum(c["source_hit_at_5"] for c in answerable) / len(answerable),
            "source_mrr_at_5": sum(c["reciprocal_rank"] for c in answerable) / len(answerable),
            "out_of_scope_false_accepts": sum(c["status"] == "ok" for c in cases if not c["expected_source"]), "cases": cases}
    (ROOT / "research-evaluation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"threshold": threshold, "metrics": {m: {k: v for k, v in s.items() if k != "cases"} for m, s in result["test"].items()}}, indent=2))


if __name__ == "__main__":
    evaluate()
