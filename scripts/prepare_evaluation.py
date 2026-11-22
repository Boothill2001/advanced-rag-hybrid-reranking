"""Isolated evaluation corpus; does not mutate the serving registry or historical reports."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT))
from rag.lifecycle import Lifecycle
from scripts.bootstrap_lifecycle import bootstrap,CURATOR

def prepare(output):
    output=Path(output).resolve()
    manager=Lifecycle(output/'registry')
    if not manager.snapshot()['generation']:
        bootstrap(manager)
        rev=manager.stage('refund-withdrawn','# SYNTHETIC withdrawn policy\nRefund within 99 days. This fixture must never be evidence.',CURATOR,corpus='policy',family='withdrawn-fixture',start='2026-01-01',revision='withdrawn-v1')['revision']
        manager.publish(manager.build('refund-withdrawn',rev,CURATOR),CURATOR)
        manager.withdraw('refund-withdrawn',rev,CURATOR)
    snap=manager.snapshot(); gen=snap['generation']
    manifest=manager.validate_generation(gen)
    record={'generation':gen,'registry_epoch':snap['epoch'],'registry_root':str(manager.root),
            'generation_manifest':manifest,'suite_sha256':sha256((ROOT/'evaluation/suite-v1.json').read_bytes()).hexdigest(),
            'notice':'Evaluation-only registry; real official snapshots plus explicitly synthetic policy/ACL fixtures; not the serving generation.'}
    output.mkdir(parents=True,exist_ok=True)
    (output/'snapshot.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps({'generation':gen,'registry_root':str(manager.root),'chunks':manifest['rows']}),flush=True)
    return record

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default=str(ROOT/'var/evaluation-v1'));args=p.parse_args();prepare(args.output)
