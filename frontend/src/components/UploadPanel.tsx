import { useCallback, useEffect, useState } from "react";
import { deleteDocument, listDocuments, uploadDocument } from "../lib/api";
import { ROLES, ROLE_LABELS, type DocInfo, type Role } from "../types";

const ACCEPT = ".md,.markdown,.txt,.pdf";

export function UploadPanel() {
  const [role, setRole] = useState<Role>("employee");
  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [file, setFile] = useState<File | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocuments());
    } catch {
      // 列表拉取失败不阻塞上传主流程
    }
  }, []);

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- 挂载时拉取文档列表（fetch-on-mount 标准模式）
    refresh();
  }, [refresh]);

  const onUpload = async () => {
    if (!file || busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      const r = await uploadDocument(file, role);
      setFeedback({
        kind: "ok",
        text: `已索引 ${r.chunks} chunks（kb_version: ${r.kb_version}）`,
      });
      setFile(null);
      await refresh();
    } catch (e) {
      setFeedback({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async (docId: string, title: string) => {
    setBusy(true);
    setFeedback(null);
    try {
      await deleteDocument(docId);
      setFeedback({ kind: "ok", text: `已删除《${title}》` });
      await refresh();
    } catch (e) {
      setFeedback({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="upload-panel">
      <input
        type="file"
        accept={ACCEPT}
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
      />
      <select value={role} onChange={(e) => setRole(e.target.value as Role)} title="文档可见角色">
        {ROLES.map((r) => (
          <option key={r} value={r}>
            {ROLE_LABELS[r]}
          </option>
        ))}
      </select>
      <button type="button" className="btn" onClick={onUpload} disabled={!file || busy}>
        {busy ? "上传 + 索引中…" : "上传并索引"}
      </button>
      <p className="upload-hint">支持 md / markdown / txt / pdf</p>
      {feedback && <p className={`upload-feedback ${feedback.kind}`}>{feedback.text}</p>}
      {docs.length > 0 && (
        <ul className="doc-list">
          {docs.map((d) => (
            <li key={d.doc_id} className="doc-item" title={d.doc_id}>
              <span className="doc-title">
                {d.title}
                <span className={`doc-role role-${d.role}`}>{d.role}</span>
                {!d.indexed && <span className="doc-unindexed">未索引</span>}
              </span>
              <button
                type="button"
                className="doc-delete"
                onClick={() => onDelete(d.doc_id, d.title)}
                disabled={busy}
                aria-label={`删除 ${d.title}`}
              >
                🗑
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
