"""Authenticated research API. Legacy tutorial /ask is outside this lifecycle contract."""
from functools import lru_cache
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from rag.access import verify, allowed
from rag.service import ResearchService

router = APIRouter()


def principal(request: Request, authorization: str = Header(default='')):
    try:
        if not authorization.startswith('Bearer '):
            raise ValueError('Missing bearer token')
        user=verify(authorization[7:])
        request.state.principal=user
        return user
    except (ValueError, OSError, KeyError):
        raise HTTPException(401, 'Invalid or expired identity')


@lru_cache(maxsize=1)
def service():
    return ResearchService()


def guarded(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except (ValueError, KeyError) as exc:
        raise HTTPException(409, str(exc))
    except OSError:
        raise HTTPException(503, 'Generation unavailable; bootstrap lifecycle or retry')


@router.get('/research/data')
def data(response: Response, corpus: str = 'technical', revision: str | None = None, purchase_date: str | None = None, user=Depends(principal)):
    result=guarded(service().data, user, corpus=corpus, revision=revision, purchase_date=purchase_date)
    response.headers['X-Research-Generation']=result.get('generation','unknown')
    return result


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    source_id: str | None = None
    mode: str = 'rerank'
    corpus: str = 'technical'
    revision: str | None = None
    purchase_date: str | None = None


@router.get('/research/health')
def health():
    snap = service().manager.snapshot()
    if not snap['generation']:
        raise HTTPException(503, 'Run scripts/bootstrap_lifecycle.py')
    return {'status': 'ready', 'generation': snap['generation'], 'registry_epoch': snap['epoch']}


@router.post('/search')
async def search(request: SearchRequest, response: Response, user=Depends(principal)):
    result=await run_in_threadpool(guarded, service().search, user=user, **request.model_dump())
    response.headers['X-Research-Generation']=result.get('trace',{}).get('generation','unknown')
    return result


class EvidenceRequest(BaseModel):
    evidence: list[dict] = Field(max_length=100)
    context: dict = Field(default_factory=dict)


@router.post('/research/validate')
def validate(request: EvidenceRequest, response: Response, user=Depends(principal)):
    allowed_context = {'corpus', 'revision', 'purchase_date', 'source_id'}
    if set(request.context) - allowed_context:
        raise HTTPException(422, 'Unknown evidence context')
    result=guarded(service().manager.validate_evidence, request.evidence, user, request.context)
    generations={r.get('generation') for r in request.evidence if r.get('generation')}
    response.headers['X-Research-Generation']=next(iter(generations)) if len(generations)==1 else 'multiple' if generations else 'none'
    return result


class RefreshRequest(BaseModel):
    source_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    text: str | None = Field(default=None, max_length=2_000_000)
    corpus: str = 'technical'
    family: str | None = None
    start: str | None = None
    end: str | None = None
    revision: str | None = None


def curator(user):
    if user['role'] != 'curator':
        raise HTTPException(403, 'Curator role required; report review does not grant publication rights')


@router.post('/research/curator/refresh')
def refresh(request: RefreshRequest, user=Depends(principal)):
    curator(user)
    m = service().manager
    rows = [r for r in m.snapshot()['rows'] if r['source'] == request.source_id]
    url = None
    if request.text is None:
        if not rows:
            raise HTTPException(404, 'Source unavailable')
        row = rows[-1]
        guarded(m.manage, row, user)
        url = row['url']
        if not url:
            raise HTTPException(422, 'Inline fixture requires source text')
        # URLs only from the trusted existing registry, never arbitrary caller URLs (SSRF).
        import requests
        from scripts.ingest_research import clean
        response = requests.get(url, timeout=40)
        response.raise_for_status()
        text = clean(response.content)
        if len(text) < 300:
            raise HTTPException(422, 'Empty or blocked upstream source')
    else:
        text = request.text
    return guarded(m.stage, request.source_id, text, user, corpus=request.corpus, family=request.family,
                   start=request.start, end=request.end, revision=request.revision, url=url or (rows[-1]['url'] if rows else None))


class RevisionRequest(BaseModel):
    source_id: str
    revision: str


@router.post('/research/curator/publish')
def publish(request: RevisionRequest, user=Depends(principal)):
    curator(user)
    m = service().manager
    generation = guarded(m.build, request.source_id, request.revision, user)
    return guarded(m.publish, generation, user)


@router.post('/research/curator/withdraw')
def withdraw(request: RevisionRequest, user=Depends(principal)):
    curator(user)
    guarded(service().manager.withdraw, request.source_id, request.revision, user)
    return {'status': 'withdrawn'}


@router.get('/research/lifecycle')
def lifecycle(user=Depends(principal)):
    m = service().manager
    snap = m.snapshot()
    rows = [{k: v for k, v in r.items() if k not in ('content',)} for r in snap['rows'] if allowed(r, user)]
    sources = {r['source'] for r in rows}
    with m.db() as c:
        audit = [dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC') if r['source'] in sources]
        builds = [dict(r) for r in c.execute('SELECT * FROM builds ORDER BY created DESC') if user['role'] == 'curator' and any(json_source(r['detail']) == s for s in sources)]
    return {'generation': snap['generation'], 'epoch': snap['epoch'], 'revisions': rows, 'audit': audit[:100], 'builds': builds[:50]}


def json_source(detail):
    import json
    try:
        return json.loads(detail).get('source')
    except (ValueError, AttributeError):
        return None


class RollbackRequest(BaseModel):
    generation: str
    expected_generation: str


@router.post('/research/curator/rollback')
def rollback(request: RollbackRequest, user=Depends(principal)):
    curator(user)
    return guarded(service().manager.rollback, request.generation, user, request.expected_generation)


@router.get('/research/metrics')
def http_metrics(user=Depends(principal)):
    from rag.http_metrics import metrics
    return metrics.report(user['workspace'])
