import type {
  ChatEvent,
  ChatMessage,
  DoneEvent,
  MessageMeta,
  RetrievedCandidate,
  Role,
  TraceData,
} from "../types";

/** 构建中的 assistant 消息（流式中间态，done 后落为正式消息） */
export interface Pending {
  id: number;
  question: string;
  /** token 累积文本；done.answer 才是权威（重试轮 token 会混拼） */
  acc: string;
  attempt: number;
  status: string;
  statusKind: "info" | "success" | "warning";
  cached: boolean;
  refused: boolean;
  refuseReason: string | null;
  citations: string[];
  rewrittenQuery: string | null;
  /** retrieved 事件候选累积：对比题多路检索都保留（Streamlit 是 last-wins，会丢前一路） */
  candidates: RetrievedCandidate[];
  toolCalls: { tools: string[]; query: string }[];
  latencyBreakdown: Record<string, number>;
  eventTypes: string[];
  error: string | null;
}

export interface ChatState {
  messages: ChatMessage[];
  role: Role;
  sessionId: string | null;
  streaming: boolean;
  pending: Pending | null;
  nextId: number;
  requests: number;
  cacheHits: number;
  /** 客户端累计耗时（s） */
  totalLatency: number;
  /** 全局错误横幅（失败消息落定后展示） */
  error: string | null;
}

export type Action =
  | { type: "ask"; question: string; role: Role }
  | { type: "setRole"; role: Role }
  | { type: "event"; event: ChatEvent }
  | { type: "done"; event: DoneEvent; latencyMs: number; sessionId: string }
  | { type: "eventFailed"; detail: string; latencyMs: number; sessionId: string }
  | { type: "streamEnded"; latencyMs: number; sessionId: string }
  | { type: "streamFailed"; detail: string; latencyMs: number; sessionId: string }
  | { type: "newSession" }
  | { type: "dismissError" };

export function initialState(): ChatState {
  return {
    messages: [],
    role: "employee",
    sessionId: null,
    streaming: false,
    pending: null,
    nextId: 1,
    requests: 0,
    cacheHits: 0,
    totalLatency: 0,
    error: null,
  };
}

export function reducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case "ask": {
      if (state.streaming) return state;
      const userMsg: ChatMessage = {
        id: state.nextId,
        role: "user",
        content: action.question,
      };
      const pending: Pending = {
        id: state.nextId + 1,
        question: action.question,
        acc: "",
        attempt: 0,
        status: "",
        statusKind: "info",
        cached: false,
        refused: false,
        refuseReason: null,
        citations: [],
        rewrittenQuery: null,
        candidates: [],
        toolCalls: [],
        latencyBreakdown: {},
        eventTypes: [],
        error: null,
      };
      return {
        ...state,
        role: action.role,
        messages: [...state.messages, userMsg],
        pending,
        streaming: true,
        error: null,
        nextId: state.nextId + 2,
      };
    }
    case "setRole":
      return { ...state, role: action.role };
    case "newSession":
      // 统计保留（与 Streamlit 一致）；流式期间按钮禁用，不会触发
      return { ...state, messages: [], sessionId: null, pending: null, streaming: false };
    case "dismissError":
      return { ...state, error: null };
    case "event":
      return applyEvent(state, action.event);
    case "done":
      return finalize(state, applyDone(state.pending, action.event), action.latencyMs, action.sessionId);
    case "eventFailed":
      return finalize(state, failPending(state.pending, action.detail), action.latencyMs, action.sessionId);
    case "streamEnded":
      return finalize(
        state,
        failPending(state.pending, "连接中断（未收到 done 事件）"),
        action.latencyMs,
        action.sessionId,
      );
    case "streamFailed":
      return finalize(state, failPending(state.pending, action.detail), action.latencyMs, action.sessionId);
  }
}

function applyEvent(state: ChatState, ev: ChatEvent): ChatState {
  const p = state.pending;
  if (!p) return state; // error 事件由 hook 拦截转 eventFailed，不会走到这里
  switch (ev.type) {
    case "tool_call":
      return {
        ...state,
        pending: {
          ...p,
          status: `🔧 调用工具：${ev.tools.join(", ") || "（无工具，直接回答）"} — ${ev.query.slice(0, 40)}`,
          statusKind: "info",
          toolCalls: [...p.toolCalls, { tools: ev.tools, query: ev.query }],
          eventTypes: [...p.eventTypes, ev.type],
        },
      };
    case "retrieved":
      return {
        ...state,
        pending: {
          ...p,
          status: `📚 检索到 ${ev.count} 条候选${ev.top1 ? `（top1: ${ev.top1}）` : ""}`,
          statusKind: "info",
          candidates: [...p.candidates, ...ev.candidates],
          eventTypes: [...p.eventTypes, ev.type],
        },
      };
    case "token": {
      // attempt 递增 = 引用校验重试轮：重置累积，避免多轮 token 混拼
      const newRound = ev.attempt > p.attempt;
      return {
        ...state,
        pending: {
          ...p,
          acc: (newRound ? "" : p.acc) + ev.text,
          attempt: ev.attempt,
        },
      };
    }
    case "citations":
      return {
        ...state,
        pending: { ...p, citations: ev.citations, eventTypes: [...p.eventTypes, ev.type] },
      };
    case "refused":
      return {
        ...state,
        pending: {
          ...p,
          refused: true,
          refuseReason: ev.reason,
          status: "⚠️ 已拒答（证据不足或无权限）",
          statusKind: "warning",
          eventTypes: [...p.eventTypes, ev.type],
        },
      };
    case "cached":
      return {
        ...state,
        pending: {
          ...p,
          cached: true,
          status: "⚡ 命中缓存，直接返回",
          statusKind: "success",
          eventTypes: [...p.eventTypes, ev.type],
        },
      };
    case "latency":
      return {
        ...state,
        pending: { ...p, latencyBreakdown: ev.breakdown, eventTypes: [...p.eventTypes, ev.type] },
      };
    default:
      // done/error 由 hook 走专用 action
      return state;
  }
}

function applyDone(p: Pending | null, ev: DoneEvent): Pending | null {
  if (!p) return p;
  return {
    ...p,
    acc: ev.answer, // 权威答案覆盖 token 累积
    cached: ev.cached,
    citations: ev.citations,
    refused: ev.refused,
    refuseReason: ev.refuse_reason || null,
    rewrittenQuery: ev.rewritten_query || null,
    latencyBreakdown: ev.latency_breakdown ?? p.latencyBreakdown,
    eventTypes: [...p.eventTypes, "done"],
  };
}

function failPending(p: Pending | null, detail: string): Pending | null {
  return p ? { ...p, error: detail } : p;
}

function finalize(state: ChatState, p: Pending | null, latencyMs: number, sessionId: string): ChatState {
  if (!p) return { ...state, sessionId, error: null };
  const trace: TraceData = {
    eventTypes: p.eventTypes,
    candidates: p.candidates,
    toolCalls: p.toolCalls,
    latencyBreakdown: p.latencyBreakdown,
  };
  const meta: MessageMeta = {
    cached: p.cached,
    refused: p.refused,
    citations: p.citations,
    rewritten_query: p.rewrittenQuery,
    latency: latencyMs / 1000,
    trace,
    error: p.error,
  };
  const msg: ChatMessage = {
    id: p.id,
    role: "assistant",
    content: p.acc,
    question: p.question,
    meta,
  };
  return {
    ...state,
    messages: [...state.messages, msg],
    pending: null,
    streaming: false,
    sessionId,
    requests: state.requests + 1,
    cacheHits: state.cacheHits + (p.cached ? 1 : 0),
    totalLatency: state.totalLatency + latencyMs / 1000,
    error: p.error,
  };
}
