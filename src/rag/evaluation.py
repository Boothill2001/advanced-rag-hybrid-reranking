"""Pure evaluation math; units, cutoffs and missing labels are explicit."""
import math


def percentile(values, p=.95):
    if not 0 <= p <= 1:
        raise ValueError('Percentile must be between zero and one')
    values = sorted(float(v) for v in values)
    if not values:
        return None
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('Latency samples must be finite and nonnegative')
    x = (len(values)-1)*p
    lo = int(x)
    return values[lo] + (values[min(lo+1,len(values)-1)]-values[lo])*(x-lo)


def unique(values):
    return list(dict.fromkeys(values))


def recall(ranked, relevant, k):
    gold = set(relevant)
    return len(set(unique(ranked)[:k]) & gold)/len(gold) if gold else None


def precision(ranked, relevant, k):
    if k <= 0:
        raise ValueError('K must be positive')
    return len(set(unique(ranked)[:k]) & set(relevant))/k


def source_metrics(sources, expected, k=5):
    if not expected:
        return {'source_hit_at_5':None, 'source_mrr_at_5':None}
    rank = next((i for i,s in enumerate(sources[:k],1) if s in expected),None)
    return {'source_hit_at_5':int(rank is not None), 'source_mrr_at_5':1/rank if rank else 0}


def claim_score(claims):
    if not claims:
        return None
    if any(c.get('verdict') not in ('supported','unsupported','uncertain') for c in claims):
        raise ValueError('Invalid claim verdict')
    if any(c['verdict']=='uncertain' for c in claims):
        return None
    return sum(c['verdict']=='supported' for c in claims)/len(claims)


def mean(values):
    known=[v for v in values if v is not None]
    return {'value':sum(known)/len(known) if known else None,'scored_n':len(known),'total_n':len(values)}


def retrieval_scores(case, result, relevant):
    stages=result.get('trace',{}).get('stages',{})
    ids=lambda name:[e['chunk_uid'] for e in stages.get(name,[])]
    final=[e['chunk_uid'] for e in result.get('evidence',[])]
    gold=set(relevant)
    answerable=case['expected_status']=='ok'
    metrics=source_metrics([e['source_id'] for e in result.get('evidence',[])],case.get('expected_sources',[]))
    metrics.update({'candidate_union_recall':recall(ids('rrf'),gold,10000) if answerable else None,
        'rrf_recall_at_20':recall(ids('rrf'),gold,20) if answerable else None,
        'pre_gate_recall_at_5':recall(ids('pre_gate'),gold,5) if answerable else None,
        'recall_at_5':recall(final,gold,5) if answerable else None,
        'precision_at_5':precision(final,gold,5) if answerable and case['corpus']=='technical' else None,
        'returned_n':len(final), 'candidate_n':len(ids('rrf')),
        'expected_status_match':result['status']==case['expected_status'],
        'false_accept':not answerable and bool(result.get('evidence')),
        'false_reject':answerable and result['status']!='ok',
        'policy_revision_correct':(bool(result.get('evidence')) and all(e['revision']==case['expected_revision'] for e in result['evidence'])) if case.get('expected_revision') else None})
    return metrics

class SnapshotChanged(ValueError):
    pass


def ensure_snapshot(trace, generation, epoch):
    if trace.get('generation')!=generation or trace.get('registry_epoch')!=epoch:
        raise SnapshotChanged('Evaluation registry/generation changed; stop instead of mixing measurements')
