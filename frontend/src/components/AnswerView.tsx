import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { MessageMeta } from "../types";

interface AnswerViewProps {
  content: string;
  meta: MessageMeta | null;
  /** 用户原始问题（改写徽章比对用） */
  question: string;
  /** 流式构建中：显示游标与状态行 */
  streaming?: boolean;
  status?: string | null;
  statusKind?: "info" | "success" | "warning";
}

export function AnswerView({ content, meta, question, streaming, status, statusKind }: AnswerViewProps) {
  if (meta?.error) {
    return <div className="msg-error">⚠️ 生成中断：{meta.error}</div>;
  }
  return (
    <div>
      {status && (
        <div className={`status-line status-${statusKind ?? "info"}`}>{status}</div>
      )}
      {meta?.refused ? (
        <div className="msg-refused">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      ) : (
        <div className="markdown-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {content + (streaming && !meta ? " ▍" : "")}
          </ReactMarkdown>
        </div>
      )}
      {meta && <Badges meta={meta} question={question} />}
    </div>
  );
}

function Badges({ meta, question }: { meta: MessageMeta; question: string }) {
  const badges: string[] = [];
  if (meta.cached) badges.push("⚡ 缓存命中");
  if (meta.rewritten_query && meta.rewritten_query !== question) {
    badges.push(`🔍 改写：${meta.rewritten_query}`);
  }
  if (meta.latency > 0) badges.push(`⏱ ${meta.latency.toFixed(1)}s`);
  for (const c of meta.citations) badges.push(`📖 ${c}`);
  if (badges.length === 0) return null;
  return <div className="badges">{badges.join("  |  ")}</div>;
}
