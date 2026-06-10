from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.app.storage.db import init_db
from backend.app.api.runs import router as runs_router
from backend.app.api.evidence import router as evidence_router
from backend.app.api.reviews import router as reviews_router
from backend.app.api.reports import router as reports_router
from backend.app.api.traces import router as traces_router
from backend.app.api.metrics import router as metrics_router
from backend.app.api.projects import router as projects_router
from backend.app.api.workflow import router as workflow_router
from backend.app.api.research_plans import router as research_plans_router
from backend.app.api.system import router as system_router


app = FastAPI(title="ProductInsight Agent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(runs_router)
app.include_router(evidence_router)
app.include_router(reviews_router)
app.include_router(reports_router)
app.include_router(traces_router)
app.include_router(metrics_router)
app.include_router(projects_router)
app.include_router(workflow_router)
app.include_router(research_plans_router)
app.include_router(system_router)

# Mount static files for serving reports (CSS, JS, images)
reports_dir = Path(__file__).parent.parent.parent / "data" / "reports"
reports_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/reports", StaticFiles(directory=str(reports_dir)), name="reports")
