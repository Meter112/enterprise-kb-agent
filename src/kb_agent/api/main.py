"""FastAPI 入口：文档管理 + 健康检查 + /chat SSE。

启动: uv run uvicorn kb_agent.api.main:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from kb_agent.api.chat import router as chat_router
from kb_agent.api.documents import router as documents_router
from kb_agent.api.schemas import HealthResponse
from kb_agent.config import settings
from kb_agent.store.qdrant_store import QdrantStore

app = FastAPI(title="kb-agent", version="0.1.0")
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.include_router(documents_router)
app.include_router(chat_router)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    version_file = settings.data_dir / "index_cache" / "kb_version.txt"
    kb_version = (
        int(version_file.read_text(encoding="utf-8").strip()) if version_file.exists() else 0
    )
    try:
        points = QdrantStore().count()
    except Exception as e:  # noqa: BLE001 - Qdrant 未启动时明确报 503
        raise HTTPException(status_code=503, detail=f"Qdrant 不可用: {e}") from e
    return HealthResponse(status="ok", qdrant_points=points, kb_version=kb_version)
