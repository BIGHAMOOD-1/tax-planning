"""FastAPI 适配层（Update 4.1 · Phase 0）。

仅做适配：把既有产物（outputs / profile / evidence.db / KB）以 View Model 形式暴露给前端。
业务逻辑仍在既有后端模块中，不在此复制。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.routers import (analyses, companies, evidence, ingest, jobs, kb, meta,
                             plans, review, settings)

app = FastAPI(title="智能税务筹划 API", version="4.6")

# 开发期 React(Vite :5173) 跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (companies, analyses, plans, kb, meta, evidence, jobs, review, ingest, settings):
    app.include_router(r.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 交付/比赛：托管构建后的 React（单源、无 CORS）
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="ui")
