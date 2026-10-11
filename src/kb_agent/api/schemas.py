"""API 数据模型。"""
from pydantic import BaseModel


class UploadResponse(BaseModel):
    doc_id: str
    chunks: int
    kb_version: int


class DeleteResponse(BaseModel):
    doc_id: str
    kb_version: int


class DocumentInfo(BaseModel):
    doc_id: str
    title: str
    role: str
    size_bytes: int
    indexed: bool


class HealthResponse(BaseModel):
    status: str
    qdrant_points: int
    kb_version: int


class ChatRequest(BaseModel):
    question: str
    role: str  # public/contractor/employee/admin
    session_id: str | None = None  # 多轮记忆：同一 session 复用 checkpoint
