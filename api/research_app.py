"""Copilot v2 search service. Legacy api.main /ask remains unchanged on its own port."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fastapi import FastAPI
from api.research import router

app = FastAPI(title="Research Evidence API", version="2.0")
app.include_router(router)

from rag.http_metrics import install
install(app)
