"""Bootstrap once from the frozen local snapshot; never rewrite benchmark inputs."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag.lifecycle import Lifecycle, ROOT, hashed, now

CURATOR = {'sub': 'curator', 'role': 'curator', 'workspace': 'research'}


def bootstrap(manager=None, embed=None):
    m = manager or Lifecycle()
    if m.snapshot()['generation']:
        return m.snapshot()['generation']
    old = ROOT / 'data/research'
    chunks = json.loads((old / 'chunks.json').read_text(encoding='utf-8'))
    with m.db() as c:
        c.execute('BEGIN IMMEDIATE')
        for sid in dict.fromkeys(ch['source_id'] for ch in chunks):
            group = [ch for ch in chunks if ch['source_id'] == sid]
            snapshot = old / (sid + '.json')
            text = json.loads(snapshot.read_text(encoding='utf-8'))['text'] if snapshot.exists() else '\n\n'.join(ch['text'] for ch in group)
            c.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (sid, 'snapshot-v1', group[0]['workspace'], 'technical', None, None, None,
                       group[0].get('url'), text, hashed(text.encode()), 'published', now(), 'research'))
            c.execute('INSERT OR IGNORE INTO defaults VALUES(?,?)', (sid, 'snapshot-v1'))
        for rev, start, end, days in [('refund_v1', '2026-01-01', '2026-11-01', 30), ('refund_v2', '2026-11-01', None, 7)]:
            text = f'# SYNTHETIC Policy Lab: {rev}\nFictional refund policy for learning only. Applies by PURCHASE DATE, from {start} inclusive to {end or "no upper limit"} exclusive. Refund requests are allowed within {days} days of purchase. Query date does not select the policy version.'
            c.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      ('refund', rev, 'research', 'policy', 'refund', start, end, None, text, hashed(text.encode()), 'published', now(), 'research'))
        m.bump(c)
        m.event(c, 'migration', 'import_frozen_snapshot', None, {'legacy_manifest_sha256': hashed((ROOT / 'research-manifest.json').read_bytes())})
    generation = m.build(embed=embed)
    m.publish(generation, CURATOR)
    return generation

if __name__ == '__main__':
    print(bootstrap())
