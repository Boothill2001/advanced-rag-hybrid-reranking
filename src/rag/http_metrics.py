"""Bounded per-workspace HTTP service-time samples. No request text or identities stored."""
from collections import deque
from threading import Lock
from time import perf_counter
from .evaluation import percentile

class HttpMetrics:
    def __init__(self, capacity=4096):
        self.samples=deque(maxlen=capacity)
        self.lock=Lock()
    def record(self,workspace,route,generation,ms,status):
        with self.lock:
            self.samples.append((workspace,route,generation,ms,status))
    def report(self, workspace):
        with self.lock:
            rows=[r for r in self.samples if r[0]==workspace]
        groups={}
        for _,route,generation,ms,status in rows:
            groups.setdefault((route,generation),[]).append((ms,status))
        return {'scope':'Bounded in-process HTTP service times; resets on restart; not client round-trip latency',
                'workspace':workspace,'groups':[{'route':route,'generation':gen,'n':len(v),
                    'p50_ms':percentile([x[0] for x in v],.5),'p95_ms':percentile([x[0] for x in v]),
                    'errors':sum(x[1]>=400 for x in v)} for (route,gen),v in groups.items()]}

metrics=HttpMetrics()

def install(app):
    @app.middleware('http')
    async def measure(request, call_next):
        start=perf_counter()
        status=500; response=None
        try:
            response=await call_next(request)
            status=response.status_code
            return response
        finally:
            user=getattr(request.state,'principal',None)
            if user and request.url.path in ('/search','/research/data','/research/validate'):
                metrics.record(user['workspace'],request.url.path,
                    response.headers.get('X-Research-Generation','unknown') if response else 'unknown',
                    (perf_counter()-start)*1000,status)
