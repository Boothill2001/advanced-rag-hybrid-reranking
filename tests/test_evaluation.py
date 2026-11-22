"""Metric denominators and missing-label handling must be deterministic and offline."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rag.evaluation import percentile,recall,precision,source_metrics,claim_score,retrieval_scores
from rag.http_metrics import HttpMetrics

def test_known_rankings_and_duplicate_units():
    assert recall(['a','a','b'],['a','b','c'],2)==pytest.approx(2/3)
    assert precision(['a','a','b'],['a'],5)==.2
    assert source_metrics(['wrong','good','good'],['good'])['source_mrr_at_5']==.5
    assert recall([],[],5) is None
    assert recall([],['a'],5)==0

def test_linear_percentile_and_invalid_samples():
    assert percentile([0,10],.95)==9.5
    assert percentile([]) is None
    with pytest.raises(ValueError):percentile([float('nan')])

def test_refusal_and_uncertain_judge_not_perfect_scores():
    assert claim_score([]) is None
    assert claim_score([{'verdict':'uncertain'}]) is None
    assert claim_score([{'verdict':'supported'},{'verdict':'unsupported'}])==.5

def test_gate_rejection_does_not_hide_retrieval_success():
    case={'expected_status':'ok','expected_sources':['doc'],'corpus':'technical'}
    out={'status':'insufficient_evidence','evidence':[],'trace':{'stages':{'rrf':[{'chunk_uid':'a'}],'pre_gate':[{'chunk_uid':'a'}]}}}
    m=retrieval_scores(case,out,['a'])
    assert m['pre_gate_recall_at_5']==1 and m['recall_at_5']==0 and m['false_reject']

def test_http_samples_bounded_and_workspace_scoped():
    m=HttpMetrics(2)
    m.record('other','/search','g',1,200)
    m.record('research','/search','g',10,200)
    m.record('research','/search','g',20,500)
    assert m.report('other')['groups']==[]
    assert m.report('research')['groups'][0]['errors']==1

def test_frozen_suite_composition_and_no_split_group_overlap():
    import json
    suite=json.loads((Path(__file__).resolve().parents[1]/'evaluation/suite-v1.json').read_text())
    cases=suite['cases'];assert len(cases)==60
    assert sum(c['split']=='dev' for c in cases)==20
    assert len(suite['pilot_ids'])==12
    assert not ({c['family'] for c in cases if c['split']=='dev'} & {c['family'] for c in cases if c['split']=='test'})


def test_registry_changes_between_requests_abort_evaluation():
    from rag.evaluation import ensure_snapshot,SnapshotChanged
    ensure_snapshot({'generation':'g1','registry_epoch':4},'g1',4)
    with pytest.raises(SnapshotChanged):ensure_snapshot({'generation':'g1','registry_epoch':5},'g1',4)
    with pytest.raises(SnapshotChanged):ensure_snapshot({'generation':'g2','registry_epoch':4},'g1',4)
