import { useCallback, useEffect, useState } from "react";
import { fetchMetrics } from "../lib/api";
import { ROLES, ROLE_LABELS, type Metrics, type Role } from "../types";
import { UploadPanel } from "./UploadPanel";

interface SidebarProps {
  role: Role;
  streaming: boolean;
  requests: number;
  cacheHits: number;
  totalLatency: number;
  onRoleChange: (r: Role) => void;
  onNewSession: () => void;
}

/** 服务端延迟统计（进程内窗口 P50/P95），30s 轮询 + 手动刷新 */
function useMetrics() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const refresh = useCallback(async () => {
    try {
      setMetrics(await fetchMetrics());
    } catch {
      // API 未启动时不打扰主流程
    }
  }, []);
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- 挂载时拉取 metrics（fetch-on-mount 标准模式）
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);
  return { metrics, refresh };
}

export function Sidebar(props: SidebarProps) {
  const { metrics, refresh } = useMetrics();
  const avg =
    props.requests > 0 ? (props.totalLatency / props.requests).toFixed(1) : "0.0";

  return (
    <aside className="sidebar">
      <section>
        <h3>⚙️ 设置</h3>
        <div className="role-group" role="radiogroup" aria-label="当前角色">
          {ROLES.map((r) => (
            <label key={r} className={`role-item${props.role === r ? " active" : ""}`}>
              <input
                type="radio"
                name="role"
                value={r}
                checked={props.role === r}
                onChange={() => props.onRoleChange(r)}
              />
              {ROLE_LABELS[r]}
            </label>
          ))}
        </div>
        <p className="hint">角色决定可见文档范围（public &lt; contractor &lt; employee &lt; admin）</p>
        <button
          type="button"
          className="btn btn-block"
          onClick={props.onNewSession}
          disabled={props.streaming}
        >
          🆕 新会话
        </button>
      </section>

      <section>
        <h3>📊 统计</h3>
        <div className="stats">
          <div className="stat">
            <span className="stat-value">{props.requests}</span>
            <span className="stat-label">请求数</span>
          </div>
          <div className="stat">
            <span className="stat-value">{props.cacheHits}</span>
            <span className="stat-label">缓存命中</span>
          </div>
          <div className="stat">
            <span className="stat-value">{avg}s</span>
            <span className="stat-label">平均耗时</span>
          </div>
        </div>
        <div className="server-stats">
          <span className="server-stats-title">
            服务端 P50/P95{" "}
            <button type="button" className="link-btn" onClick={refresh}>
              刷新
            </button>
          </span>
          {metrics && metrics.count > 0 ? (
            <span className="hint">
              {metrics.p50_s?.toFixed(1)}s / {metrics.p95_s?.toFixed(1)}s（近 {metrics.count} 条）
            </span>
          ) : (
            <span className="hint">暂无请求</span>
          )}
        </div>
      </section>

      <section>
        <h3>📤 上传文档</h3>
        <UploadPanel />
      </section>
    </aside>
  );
}
