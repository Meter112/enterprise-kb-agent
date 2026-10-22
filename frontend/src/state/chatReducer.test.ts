import { describe, expect, it } from "vitest";
import { initialState, reducer, type ChatState } from "./chatReducer";
import type { DoneEvent, RetrievedEvent, TokenEvent } from "../types";

const ASK = { type: "ask", question: "年假有几天", role: "employee" } as const;

const token = (text: string, attempt: number): TokenEvent => ({ type: "token", text, attempt });

const retrieved = (n: number, offset = 0): RetrievedEvent => ({
  type: "retrieved",
  query: "年假",
  count: n,
  top1: "考勤制度",
  candidates: Array.from({ length: n }, (_, i) => ({
    citation: `《考勤制度》§${i + offset}`,
    rerank_score: 0.9 - i * 0.1,
    text: `片段${i + offset}`,
  })),
});

const doneEv = (over: Partial<DoneEvent> = {}): DoneEvent => ({
  type: "done",
  answer: "最终答案",
  citations: ["《考勤制度》§2"],
  refused: false,
  refuse_reason: "",
  rewritten_query: "年假有几天",
  trace: [],
  cached: false,
  latency_breakdown: { retrieval_dense: 0.1 },
  retrieved: null,
  ...over,
});

const doneAction = (ev: DoneEvent, sessionId = "web-1") => ({
  type: "done" as const,
  event: ev,
  latencyMs: 1000,
  sessionId,
});

function askState(): ChatState {
  return reducer(initialState(), ASK);
}

describe("chatReducer token 流", () => {
  it("同 attempt 累积", () => {
    const s1 = reducer(askState(), { type: "event", event: token("A", 1) });
    const s2 = reducer(s1, { type: "event", event: token("B", 1) });
    expect(s2.pending?.acc).toBe("AB");
  });

  it("attempt 递增时重置累积（重试轮不混拼）", () => {
    const s1 = reducer(askState(), { type: "event", event: token("第一轮", 1) });
    const s2 = reducer(s1, { type: "event", event: token("第二轮", 2) });
    expect(s2.pending?.acc).toBe("第二轮");
    expect(s2.pending?.attempt).toBe(2);
  });
});

describe("chatReducer 终态", () => {
  it("done 以 answer 覆盖 token 累积并落为消息", () => {
    const s1 = reducer(askState(), { type: "event", event: token("流式文本", 1) });
    const s2 = reducer(s1, doneAction(doneEv({ answer: "权威答案" })));
    expect(s2.pending).toBeNull();
    expect(s2.streaming).toBe(false);
    expect(s2.messages).toHaveLength(2);
    const last = s2.messages[1];
    expect(last.content).toBe("权威答案");
    expect(last.meta?.error).toBeNull();
    expect(last.meta?.trace?.eventTypes).toContain("done");
  });

  it("error 事件落失败消息并置全局横幅", () => {
    const s = reducer(askState(), {
      type: "eventFailed",
      detail: "LLM 调用超时",
      latencyMs: 500,
      sessionId: "web-1",
    });
    expect(s.pending).toBeNull();
    expect(s.messages[1].meta?.error).toBe("LLM 调用超时");
    expect(s.error).toBe("LLM 调用超时");
  });

  it("无 done 无 error 的断流兜底", () => {
    const s = reducer(askState(), {
      type: "streamEnded",
      latencyMs: 500,
      sessionId: "web-1",
    });
    expect(s.messages[1].meta?.error).toContain("连接中断");
  });

  it("done 设置 sessionId", () => {
    const s = reducer(askState(), doneAction(doneEv(), "web-1729000000"));
    expect(s.sessionId).toBe("web-1729000000");
  });
});

describe("chatReducer 轨迹与统计", () => {
  it("retrieved 事件候选累积（对比题多路都保留）", () => {
    const s1 = reducer(askState(), { type: "event", event: retrieved(2) });
    const s2 = reducer(s1, { type: "event", event: retrieved(1, 2) });
    expect(s2.pending?.candidates).toHaveLength(3);
  });

  it("缓存命中计数", () => {
    const a1 = reducer(askState(), doneAction(doneEv({ cached: true })));
    expect(a1.requests).toBe(1);
    expect(a1.cacheHits).toBe(1);
    const a2 = reducer(reducer(a1, ASK), doneAction(doneEv({ cached: false })));
    expect(a2.requests).toBe(2);
    expect(a2.cacheHits).toBe(1);
    expect(a2.totalLatency).toBe(2);
  });
});

describe("chatReducer 会话", () => {
  it("newSession 清消息但保留统计", () => {
    const done = reducer(askState(), doneAction(doneEv({ cached: true })));
    const s = reducer(done, { type: "newSession" });
    expect(s.messages).toHaveLength(0);
    expect(s.sessionId).toBeNull();
    expect(s.requests).toBe(1);
    expect(s.cacheHits).toBe(1);
  });

  it("流式进行中忽略新的 ask", () => {
    const s = reducer(askState(), ASK);
    expect(s.messages).toHaveLength(1);
  });
});
