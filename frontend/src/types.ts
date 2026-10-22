/** SSE 事件联合类型。服务端帧格式 `event: {type}\ndata: {json}\n\n`，
 *  type 已含在 data JSON 内（src/kb_agent/api/chat.py:92），前端只解析 data: 行。
 */

export type Role = "public" | "contractor" | "employee" | "admin";

export const ROLES: Role[] = ["public", "contractor", "employee", "admin"];

export const ROLE_LABELS: Record<Role, string> = {
  public: "公众（公开文档）",
  contractor: "外包人员",
  employee: "正式员工",
  admin: "管理员（全部文档）",
};

export interface RetrievedCandidate {
  citation: string;
  rerank_score: number;
  text: string; // 截断文本（≤120 字），仅 retrieved 事件携带
}

export interface ToolCallEvent {
  type: "tool_call";
  tools: string[]; // 可能为空数组（路由判定无需工具）
  query: string;
}

export interface RetrievedEvent {
  type: "retrieved";
  query: string;
  count: number;
  top1: string | null;
  candidates: RetrievedCandidate[];
}

export interface TokenEvent {
  type: "token";
  text: string;
  attempt: number; // 引用校验重试轮次，递增时前端重置累积
}

export interface CitationsEvent {
  type: "citations";
  citations: string[];
}

export interface RefusedEvent {
  type: "refused";
  reason: string;
}

export interface CachedEvent {
  type: "cached";
  latency_ms: number;
}

export interface LatencyEvent {
  type: "latency";
  breakdown: Record<string, number>;
}

export interface DoneRetrieved {
  chunk_id: string;
  citation: string;
  title: string;
  rerank_score: number;
}

export interface DoneEvent {
  type: "done";
  answer: string;
  citations: string[];
  refused: boolean;
  refuse_reason: string;
  rewritten_query: string;
  trace: Record<string, unknown>[];
  cached: boolean;
  /** 缓存命中路径无此字段 */
  latency_breakdown?: Record<string, number>;
  /** 缓存命中路径无此字段 */
  retrieved?: DoneRetrieved[] | null;
}

export interface ErrorEvent {
  type: "error";
  detail: string;
}

export type ChatEvent =
  | ToolCallEvent
  | RetrievedEvent
  | TokenEvent
  | CitationsEvent
  | RefusedEvent
  | CachedEvent
  | LatencyEvent
  | DoneEvent
  | ErrorEvent;

// ---------- 前端消息模型 ----------

export interface TraceData {
  /** 客户端累积的事件类型序列（token 除外），与 Streamlit 版口径一致 */
  eventTypes: string[];
  /** retrieved 事件候选累积（对比题多路检索都保留，Streamlit 是 last-wins） */
  candidates: RetrievedCandidate[];
  toolCalls: { tools: string[]; query: string }[];
  latencyBreakdown: Record<string, number>;
}

export interface MessageMeta {
  cached: boolean;
  refused: boolean;
  citations: string[];
  rewritten_query: string | null;
  /** 客户端计时（s） */
  latency: number;
  trace: TraceData | null;
  /** 失败态：error 事件 detail / 网络中断 */
  error: string | null;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  /** assistant 消息关联的用户问题 */
  question?: string;
  meta?: MessageMeta;
}

export interface DocInfo {
  doc_id: string;
  title: string;
  role: string;
  size_bytes: number;
  indexed: boolean;
}

export interface Metrics {
  window: number;
  count: number;
  p50_s: number | null;
  p95_s: number | null;
}
