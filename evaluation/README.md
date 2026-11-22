# Evidence Evaluation v1 — measurement contract

This evaluation separates finding evidence, using it correctly, enforcing eligibility and serving it promptly. It uses the real six-page technical snapshot and explicitly fictional Policy Lab/withdrawal fixtures. Questions are self-authored, relevance labels are AI-assisted and answer judging uses the same model family as generation. There is no independent human reviewer.

## Dataset and labels

The frozen suite has 20 development and 40 test questions. Twelve development questions form the pilot. The pilot validates coverage, schema, quote grounding, metric arithmetic and judge fixtures; test results do not tune retrieval, thresholds or prompts.

Every technical answerable question labels **all eligible chunks**, not just the union of search results. A chunk is relevant if it directly supports at least one required fact. Alternative valid sources count. Negative labels are included in coverage. Exact source quotes validate positive annotations; long exact quotes are shortened mechanically for display without changing relevance. Unsupported live/private questions use an explicit author assessment of the fixed corpus scope. Policy and denied/withdrawn cases use deterministic applicability/ACL rules.

Chunks can overlap and repeat facts: chunk recall is not the same as distinct-fact coverage. Reference-claim answer correctness provides a separate view. Labels, generation, gate identity and suite hash are pinned. The external generator/judge API alias and returned model names are recorded, but its weights are not pinned. Full publisher text remains in ignored local storage; public qrels identify relevant chunk UIDs.

## Metrics and denominators

| Metric | Definition |
|---|---|
| Source Hit@5 | Whether an expected source occurs in the first five returned chunks |
| Source MRR@5 | Reciprocal rank of the first expected-source chunk, or zero; rank is within chunks, not deduplicated sources |
| Candidate union recall | Relevant chunks in the complete fused candidate set / all labeled relevant eligible chunks |
| RRF Recall@20 | Relevant chunks in the first 20 RRF results / all labeled relevant eligible chunks |
| Recall@5 | Relevant chunks in the final five / all labeled relevant eligible chunks |
| Precision@5 | Relevant chunks in the final five / **5**, even if fewer were returned; technical answerable queries only |
| Policy correctness | Returned revision matches the purchase-date policy on answerable Policy Lab cases |
| Expected status match | Actual status matches ok, needs_context, context_conflict or insufficient_evidence as specified |

The reranker still scores the existing full candidate union (up to 40). Measuring its RRF prefix at 20 does not silently change that input budget. Pre-gate and post-gate recall distinguish candidate/ranking failures from false rejection. Recall is N/A when no eligible relevant evidence should exist. Those cases remain in status, false-accept and safety denominators.

For example, retrieving one of two gold chunks gives Recall@5=0.5. If that is the only relevant result in the five slots, Precision@5=0.2. Source Hit@5 can still be 1. The three values answer different questions.

## Answer assessment

Only the production-style rerank pipeline generates the direct RAG answers. Retrieval ablations compare all three modes without changing the generator per mode. Faithfulness is the fraction of factual domain claims supported by the actual context. Requests, refusals and meta-statements are not assigned a fake perfect faithfulness score. Correctness checks reference-claim coverage and business context; citation support checks entailment as well as existence.

A statement accurately describing v2 may be faithful to a supplied v2 excerpt while failing to answer a question requiring v1. A statement falsely applying v2 to an October purchase can instead be unfaithful too: implicit scope matters. The pilot fixtures make this distinction explicit. Judge errors/uncertainty are unscored, with coverage shown. Repeated judgments measure same-model consistency, not independent correctness.

## Timing and operational behavior

The new percentile convention is linear interpolation at `(n-1)*p`; historical reports keep their original convention. Search benchmarks measure client HTTP round trip with ten warm-ups excluded and 200 attempts each at concurrency 1 and 3. The same frozen workload is used at both levels. A separate cold process provides one cold-start sample, never a cold p95. Timeouts/errors remain in attempt counts; successful-only and all-attempt p95 are labeled separately. Corpus-level breakdowns expose the workload mix.

Agent timing covers real LangGraph, metered DeepSeek and MCP stdio. Each test case is attempted twice. Reviewer decisions are scripted fixtures, not real human reviews, and their wait time is excluded. Runtime HTTP monitoring stores bounded, in-process, per-workspace route/generation samples with no query text; it resets on restart and is not client round-trip latency.

## Reproduce

With both sibling repositories and their environments configured:

```powershell
# RAG repository: isolated registry, no historical artifact rewrite
.venv\Scripts\python.exe scripts/prepare_evaluation.py

# Companion Copilot repository: paid, cached and resumable phases
.venv\Scripts\python.exe scripts/evaluate_evidence.py --phase pilot
.venv\Scripts\python.exe scripts/evaluate_evidence.py --phase all
```

The evaluation uses loopback ports 8012 and 8013, separate registry/checkpoint storage and the existing shared signing secret. It never withdraws documents from the serving registry. Model calls obey a $1 evaluation estimate cap and the existing $5 shared cumulative cap; unresolved dispatched calls keep reservations. Estimates use conservative peak cache-miss/output rates, not billing invoices. [Official pricing](https://api-docs.deepseek.com/quick_start/pricing/).

Results are in `artifacts/evaluation-v1.json`; failed/missing judgments are not fabricated. Historical benchmark files remain unchanged. These small local measurements establish neither a production SLO nor measured business savings.
