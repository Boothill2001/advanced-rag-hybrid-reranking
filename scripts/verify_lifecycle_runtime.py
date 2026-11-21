"""Actual CPU embeddings + running HTTP service. No paid API key; not a quality benchmark."""
import json
from pathlib import Path
import sys
import time
import requests
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from rag.access import secret
# The local companion issues trusted demo identities. No credentials are printed.
sys.path.insert(0,str(ROOT.parent/'AI_AGENT_LANGGHAPH/src'))
from copilot.security import USERS,issue
BASE='http://127.0.0.1:8011'
def call(path, payload=None, account='curator'):
    r=requests.request('POST' if payload is not None else 'GET',BASE+path,json=payload,
        headers={'Authorization':'Bearer '+issue(USERS[account])},timeout=180)
    r.raise_for_status()
    return r.json()
def main():
    stamp=str(time.time_ns())
    sid='runtime-update-demo'
    revisions=[]; generations=[]; outputs=[]
    for budget in (137,193):
        text=f'# SYNTHETIC lifecycle integration fixture\nThe current demo review budget is {budget} units. Verification run {stamp}. This is fictional learning data.'
        staged=call('/research/curator/refresh',{'source_id':sid,'text':text})
        assert staged['changed']
        same=call('/research/curator/refresh',{'source_id':sid,'text':text})
        assert not same['changed'] and same['revision']==staged['revision']
        published=call('/research/curator/publish',{'source_id':sid,'revision':staged['revision']})
        result=call('/search',{'query':'current demo review budget','source_id':sid,'mode':'hybrid'},'researcher')
        assert result['status']=='ok' and all(str(budget) in e['text'] for e in result['evidence'])
        revisions.append(staged['revision']);generations.append(published['generation']);outputs.append(result['evidence'][0]['text'])
        print('Published and queried budget',budget,flush=True)
    assert generations[0]!=generations[1]
    historic=call('/search',{'query':'demo review budget','source_id':sid,'revision':revisions[0],'mode':'hybrid'},'researcher')
    assert historic['status']=='ok' and '137' in historic['evidence'][0]['text']
    call('/research/curator/withdraw',{'source_id':sid,'revision':revisions[1]})
    withdrawn=call('/search',{'query':'current demo review budget','source_id':sid,'mode':'hybrid'},'researcher')
    assert withdrawn['evidence']==[]
    refs=call('/research/validate',{'evidence':historic['evidence'],'context':historic['trace']['context']},'researcher')
    assert refs['valid'] # Explicit historical revision survives default replacement and withdrawal of v2.
    policy=[]
    for day,expected in [('2026-10-25','refund_v1'),('2026-10-31','refund_v1'),('2026-11-01','refund_v2')]:
        r=call('/search',{'query':'refund days','corpus':'policy','purchase_date':day,'mode':'hybrid'},'researcher')
        assert {e['revision'] for e in r['evidence']}=={expected}
        policy.append({'purchase_date':day,'selected_revision':expected})
    out={'kind':'Live local runtime safety checks; not a quality benchmark','created':time.time(),'source':sid,
         'generations':generations,'revisions':revisions,'outputs':outputs,'service_restart_requested':False,
         'unchanged_refresh_created_revision':False,'withdrawn_default_evidence':withdrawn['evidence'],
         'explicit_historical_revision_valid':True,'policy_boundaries':policy}
    (ROOT/'artifacts/lifecycle-runtime.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    print('Live publication, withdrawal and policy checks passed',flush=True)
if __name__=='__main__':
    main()
