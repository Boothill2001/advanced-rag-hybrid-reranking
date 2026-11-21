"""Keyless lifecycle contracts: controlled vectors, no model downloads."""
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag.lifecycle import Lifecycle
from rag.service import ResearchService

C = {'sub': 'curator', 'role': 'curator', 'workspace': 'research'}
U = {'sub': 'researcher', 'role': 'researcher', 'workspace': 'research'}
O = {'sub': 'outsider', 'role': 'researcher', 'workspace': 'other'}
embed = lambda texts: np.asarray([[1., 0.] for _ in texts], dtype='float32')


def publish(m, sid, text='retrieval policy evidence', **kw):
    rev = m.stage(sid, text, C, **kw)['revision']
    gen = m.build(sid, rev, C, embed)
    m.publish(gen, C)
    return rev, gen


@pytest.fixture
def lab(tmp_path, monkeypatch):
    m = Lifecycle(tmp_path)
    publish(m, 'refund', 'Refund within 30 days.', corpus='policy', family='refund', start='2026-01-01', end='2026-11-01', revision='refund_v1')
    publish(m, 'refund', 'Refund within 7 days.', corpus='policy', family='refund', start='2026-11-01', revision='refund_v2')
    monkeypatch.setattr('rag.research.embed_query', lambda _: np.asarray([[1., 0.]], dtype='float32'))
    return m, ResearchService(m)


@pytest.mark.parametrize('purchase,expected', [('2026-10-25','refund_v1'),('2026-10-31','refund_v1'),('2026-11-01','refund_v2')])
def test_purchase_date_selects_version_before_both_retrievers(lab, purchase, expected):
    m, service = lab
    result = service.search('refund days', U, corpus='policy', purchase_date=purchase, mode='hybrid')
    assert {e['revision'] for e in result['evidence']} == {expected}
    assert result['trace']['authorized_candidates'] == 1
    assert len(result['trace']['dense']) == 1
    assert result['trace']['selection_basis'] == 'purchase_date'


@pytest.mark.parametrize('context,status', [({},'needs_context'),({'purchase_date':'2025-12-31'},'insufficient_evidence'),({'purchase_date':'2026-10-25','revision':'refund_v2'},'context_conflict')])
def test_no_implicit_latest_policy(lab, context, status):
    result = lab[1].search('refund policy', U, corpus='policy', mode='hybrid', **context)
    assert result['status'] == status and result['evidence'] == []


def test_policy_overlap_rejected_before_publish(lab):
    m, _ = lab
    r = m.stage('overlap', 'Conflicting refund policy', C, corpus='policy', family='refund', start='2026-10-01')['revision']
    original = m.snapshot()['generation']
    with pytest.raises(ValueError, match='Overlapping'):
        m.build('overlap', r, C, embed)
    assert m.snapshot()['generation'] == original


@pytest.mark.parametrize('mode',['dense','hybrid','rerank'])
def test_withdrawn_high_score_and_private_never_become_candidates(lab, mode):
    m, service = lab
    m.withdraw('refund','refund_v1',C)
    result = service.search('refund policy', U, corpus='policy', purchase_date='2026-10-25', mode=mode)
    assert result['evidence'] == [] and 'dense' not in result['trace']
    result = service.search('refund policy', O, corpus='policy', purchase_date='2026-11-01', mode=mode)
    assert result['evidence'] == [] and result['trace']['eligibility'] == []


def test_unchanged_refresh_no_duplicate_and_default_vs_historic(tmp_path, monkeypatch):
    m = Lifecycle(tmp_path)
    rev1, g1 = publish(m, 'rrf')
    assert not m.stage('rrf','retrieval policy evidence',C)['changed']
    rev2, g2 = publish(m,'rrf','Updated reciprocal rank fusion.')
    assert len(m.snapshot()['rows']) == 2
    service = ResearchService(m)
    assert {c['revision'] for c in service.data(U)['chunks']} == {rev2}
    assert {c['revision'] for c in service.data(U,revision=rev1)['chunks']} == {rev1}
    assert {c['generation'] for c in service.index(g1).chunks} == {g1}
    assert {c['generation'] for c in service.index(g2).chunks} == {g2}


def test_failed_and_stale_build_never_switch_pointer(tmp_path):
    m = Lifecycle(tmp_path)
    _, original = publish(m,'rrf')
    rev = m.stage('rrf','new evidence',C)['revision']
    with pytest.raises(ValueError):
        m.build('rrf',rev,C,lambda _: np.asarray([[np.nan, 1.]]))
    assert m.snapshot()['generation'] == original
    g1 = m.build('rrf',rev,C,embed)
    g2 = m.build('rrf',rev,C,embed)
    m.publish(g1,C)
    with pytest.raises(ValueError, match='Stale'):
        m.publish(g2,C)
    assert m.snapshot()['generation'] == g1


def test_concurrent_publishes_one_wins(tmp_path):
    m = Lifecycle(tmp_path)
    rev,_ = publish(m,'rrf')
    builds = [m.build('rrf',rev,C,embed) for _ in range(2)]
    def attempt(g):
        try:
            m.publish(g,C)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(attempt,builds)) == 1


def test_rollback_does_not_resurrect_and_refs_not_caller_text(lab):
    m, service = lab
    result = service.search('refund policy',U,corpus='policy',purchase_date='2026-10-25',mode='hybrid')
    refs = result['evidence']
    context = refs[0]['retrieval_context']
    refs[0]['text'] = 'Forged caller text'
    assert m.validate_evidence(refs,U,context)['evidence'][0]['text'] != 'Forged caller text'
    original = m.snapshot()['generation']
    m.withdraw('refund','refund_v1',C)
    m.rollback(original,C,original)
    assert not m.validate_evidence(refs,U,context)['valid']
    assert service.search('refund',U,corpus='policy',purchase_date='2026-10-25')['evidence'] == []


def test_registry_change_during_ranking_drops_entire_trace(lab,monkeypatch):
    m, service = lab
    def changed(_):
        m.withdraw('refund','refund_v1',C)
        return np.asarray([[1.,0.]],dtype='float32')
    monkeypatch.setattr('rag.research.embed_query', changed)
    result = service.search('refund policy',U,corpus='policy',purchase_date='2026-10-25',mode='hybrid')
    assert result['status'] == 'registry_changed' and result['evidence'] == []
    assert 'dense' not in result['trace']


def test_curator_workspace_and_reviewer_cannot_publish(lab):
    m,_ = lab
    for user in [U, {**C,'workspace':'other'}]:
        with pytest.raises(PermissionError):
            m.withdraw('refund','refund_v1',user)


def test_tampered_generation_rejected(lab):
    m,_ = lab
    generation = m.snapshot()['generation']
    (m.root/'generations'/generation/'chunks.json').write_text('[]')
    with pytest.raises(ValueError,match='hash'):
        m.validate_generation(generation)


def test_withdrawn_winner_absent_while_other_candidates_are_ranked(tmp_path, monkeypatch):
    m=Lifecycle(tmp_path)
    rev,_=publish(m,'bad','fraud fraud fraud specialneedle')
    publish(m,'good','permitted retrieval evidence')
    m.withdraw('bad',rev,C)
    monkeypatch.setattr('rag.research.embed_query', lambda _: np.asarray([[1.,0.]],dtype='float32'))
    service=ResearchService(m)
    result=service.search('fraud specialneedle',U,mode='hybrid')
    assert result['trace']['authorized_candidates'] == 1
    idx=service.index(result['trace']['generation'])
    for branch in ['dense','bm25']:
        assert all(idx.chunks[i]['source_id'] == 'good' for i,_ in result['trace'][branch])
    assert all(e['source_id']=='good' for e in result['evidence'])


def test_migration_leaves_frozen_benchmark_bytes_untouched(tmp_path):
    from scripts.bootstrap_lifecycle import bootstrap
    from rag.lifecycle import ROOT, hashed
    files=[ROOT/name for name in ['research-manifest.json','research-gate.json','research-evaluation.json']]
    before={f:hashed(f.read_bytes()) for f in files}
    if not (ROOT/'data/research/chunks.json').exists():
        # CI has no publisher corpus. Its fixture still exercises migration without a network call.
        import scripts.bootstrap_lifecycle as module
        import unittest.mock
        frozen=tmp_path/'snapshot'; (frozen/'data/research').mkdir(parents=True)
        chunk={'source_id':'fixture','text':'snapshot migration fixture','workspace':'public'}
        (frozen/'data/research/chunks.json').write_text(json.dumps([chunk]))
        (frozen/'research-manifest.json').write_text('{}')
        with unittest.mock.patch.object(module,'ROOT',frozen):
            bootstrap(Lifecycle(tmp_path/'registry'),embed)
    else:
        bootstrap(Lifecycle(tmp_path/'registry'),embed)
    assert all(hashed(f.read_bytes())==before[f] for f in files)
