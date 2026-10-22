import type { TraceData } from "../types";

export function ToolTrace({ trace }: { trace: TraceData }) {
  if (!trace || (trace.eventTypes.length === 0 && trace.candidates.length === 0)) {
    return null;
  }
  const breakdown = Object.entries(trace.latencyBreakdown).sort((a, b) => b[1] - a[1]);
  return (
    <details className="trace">
      <summary>🔎 工具轨迹</summary>
      {trace.eventTypes.length > 0 && (
        <p className="trace-line">
          事件序列：<code>{trace.eventTypes.join(" → ")}</code>
        </p>
      )}
      {trace.toolCalls.length > 0 && (
        <div className="trace-block">
          <strong>工具调用：</strong>
          <ul>
            {trace.toolCalls.map((t, i) => (
              <li key={i}>
                <code>{t.tools.join(", ") || "（无工具）"}</code> — {t.query}
              </li>
            ))}
          </ul>
        </div>
      )}
      {breakdown.length > 0 && (
        <div className="trace-block">
          <strong>延迟拆解：</strong>
          <ul>
            {breakdown.map(([label, secs]) => (
              <li key={label}>
                {label} <span className="trace-secs">{secs.toFixed(2)}s</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {trace.candidates.length > 0 && (
        <div className="trace-block">
          <strong>检索候选：</strong>
          <ul>
            {trace.candidates.map((c, i) => (
              <li key={i}>
                <strong>{c.citation}</strong>（rerank {c.rerank_score.toFixed(2)}）
                <div className="trace-excerpt">{c.text}…</div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </details>
  );
}
