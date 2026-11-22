"""Generation-pinned retrieval and live registry eligibility."""
from functools import lru_cache
from hashlib import sha256
import json

from .lifecycle import Lifecycle
from .research import ResearchIndex


class ResearchService:
    def __init__(self, manager=None):
        self.manager = manager or Lifecycle()

    @lru_cache(maxsize=8)
    def index(self, generation):
        self.manager.validate_generation(generation)
        folder = self.manager.root / 'generations' / generation
        idx = ResearchIndex(folder)
        idx.threshold = json.loads((folder / 'gate.json').read_text(encoding='utf-8'))['threshold']
        idx.manifest_hash = sha256((folder / 'manifest.json').read_bytes()).hexdigest()
        return idx

    def select(self, user, context):
        snap = self.manager.snapshot()
        if not snap['generation']:
            raise FileNotFoundError('Run scripts/bootstrap_lifecycle.py')
        idx = self.index(snap['generation'])
        ids, status, reasons = self.manager.eligible(idx.chunks, user, snapshot=snap, **context)
        return snap, idx, ids, status, reasons

    def search(self, query, user, source_id=None, mode='rerank', corpus='technical', revision=None, purchase_date=None):
        context = dict(source_id=source_id, corpus=corpus, revision=revision, purchase_date=purchase_date)
        snap, idx, ids, status, reasons = self.select(user, context)
        trace = {'generation': snap['generation'], 'registry_epoch': snap['epoch'], 'eligibility': reasons,
                 'selection_basis': 'purchase_date' if corpus == 'policy' else 'explicit_revision' if revision else 'published_default',
                 'context': context}
        if status != 'ok':
            return {'status': status, 'evidence': [], 'trace': trace}
        result = idx.search(query, user, source_id, mode, candidate_ids=set(ids))
        # Withdrawal during a slow retrieval/rerank must not escape in results OR trace.
        if self.manager.snapshot()['epoch'] != snap['epoch']:
            return {'status': 'registry_changed', 'evidence': [], 'trace': {'generation': snap['generation']}, 'message': 'Retry against the current registry.'}
        for item in result['evidence']:
            item['retrieval_context'] = context
        result['trace'].update(trace)
        return result

    def data(self, user, **context):
        snap, idx, ids, status, reasons = self.select(user, context)
        if self.manager.snapshot()['epoch'] != snap['epoch']:
            return {'generation': snap['generation'], 'registry_epoch':snap['epoch'], 'status':'registry_changed','chunks':[], 'dimension':int(idx.vectors.shape[1]),'eligibility':[]}
        return {'generation': snap['generation'], 'registry_epoch':snap['epoch'], 'status': status, 'chunks': [idx.chunks[i] for i in ids],
                'dimension': int(idx.vectors.shape[1]), 'eligibility': reasons}
