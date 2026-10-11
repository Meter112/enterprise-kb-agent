"""文档管理 API：上传（带角色标签）/ 列表 / 删除。

上传即入库：文件落盘到 data/corpus/<role>/ 后触发增量索引（hash 跳过未变更），
上传成功后该文档立即可被检索。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from kb_agent.api.schemas import DeleteResponse, DocumentInfo, UploadResponse
from kb_agent.config import settings
from kb_agent.ingest.indexer import SUPPORTED_SUFFIXES, Indexer
from kb_agent.ingest.loaders import doc_id_for_path
from kb_agent.models.embedder import Embedder
from kb_agent.store.qdrant_store import QdrantStore

ROLES = ("public", "contractor", "employee", "admin")
router = APIRouter(prefix="/documents", tags=["documents"])


@lru_cache(maxsize=1)
def _get_indexer() -> Indexer:
    """进程内单例：Embedder 加载约 2.5s，避免每个请求重建。"""
    return Indexer(QdrantStore(), Embedder())


def _iter_corpus_files() -> list[Path]:
    return sorted(
        p for p in settings.corpus_dir.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES
    )


@router.post("", response_model=UploadResponse)
async def upload_document(
    file: Annotated[UploadFile, File()],
    role: Annotated[
        str, Form(description="文档可见角色 public/contractor/employee/admin")
    ],
) -> UploadResponse:
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role 必须是 {list(ROLES)} 之一")
    name = Path(file.filename or "").name  # 仅取 basename，防路径穿越
    if not name or Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400, detail=f"仅支持 {sorted(SUPPORTED_SUFFIXES)} 格式"
        )
    dest = settings.corpus_dir / role / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())

    indexer = _get_indexer()
    report = indexer.index_directory()
    if report.errors:
        raise HTTPException(status_code=500, detail=f"索引失败: {report.errors}")
    doc_id = doc_id_for_path(dest)
    chunks = len(indexer.store.get_document_chunks(doc_id, roles=list(ROLES)))
    return UploadResponse(doc_id=doc_id, chunks=chunks, kb_version=report.kb_version)


@router.get("", response_model=list[DocumentInfo])
async def list_documents() -> list[DocumentInfo]:
    indexer = _get_indexer()
    known = indexer.known_file_names()
    return [
        DocumentInfo(
            doc_id=doc_id_for_path(p),
            title=p.name,
            role=p.parent.name,
            size_bytes=p.stat().st_size,
            indexed=p.name in known,
        )
        for p in _iter_corpus_files()
    ]


@router.delete("/{doc_id}", response_model=DeleteResponse)
async def delete_document(doc_id: str) -> DeleteResponse:
    indexer = _get_indexer()
    files = [p for p in _iter_corpus_files() if doc_id_for_path(p) == doc_id]
    if not files:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    for p in files:
        p.unlink()
    indexer.delete_document(doc_id)  # 删点 + 清 hash + kb_version+1
    return DeleteResponse(doc_id=doc_id, kb_version=indexer.current_version())
