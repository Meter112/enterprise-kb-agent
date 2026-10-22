import type { ChatEvent, DocInfo, Metrics, Role } from "../types";
import { parseSSE } from "./sse";

/** 同源（vite proxy / nginx 反代）时留空；直连 API（vite preview + CORS）时配 VITE_API_URL */
export const API_BASE: string = import.meta.env.VITE_API_URL ?? "";

export async function* chatStream(
  question: string,
  role: Role,
  sessionId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const payload: Record<string, string> = { question, role };
  if (sessionId) payload.session_id = sessionId;
  const resp = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  yield* parseSSE(resp);
}

export interface UploadResult {
  doc_id: string;
  chunks: number;
  kb_version: number;
}

export async function uploadDocument(file: File, role: Role): Promise<UploadResult> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("role", role);
  const resp = await fetch(`${API_BASE}/documents`, { method: "POST", body: fd });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`上传失败（HTTP ${resp.status}）${text.slice(0, 200)}`);
  }
  return resp.json();
}

export async function listDocuments(): Promise<DocInfo[]> {
  const resp = await fetch(`${API_BASE}/documents`);
  if (!resp.ok) throw new Error(`获取文档列表失败（HTTP ${resp.status}）`);
  return resp.json();
}

export async function deleteDocument(docId: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/documents/${docId}`, { method: "DELETE" });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`删除失败（HTTP ${resp.status}）${text.slice(0, 200)}`);
  }
}

export async function fetchMetrics(): Promise<Metrics> {
  const resp = await fetch(`${API_BASE}/chat/metrics`);
  if (!resp.ok) throw new Error(`metrics 获取失败（HTTP ${resp.status}）`);
  return resp.json();
}
