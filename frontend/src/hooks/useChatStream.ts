import { useCallback, useEffect, useReducer, useRef } from "react";
import { chatStream } from "../lib/api";
import { initialState, reducer } from "../state/chatReducer";
import type { Role } from "../types";

/** 与 Streamlit 版一致：首次问答后固定 session，之后进入多轮模式 */
function genSessionId(): string {
  return `web-${Math.floor(Date.now() / 1000)}`;
}

export function useChatStream() {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);
  // 最新 state 的镜像：ask 是事件回调（晚于 commit/effect），读 stateRef 不会拿到过期值
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);
  const abortRef = useRef<AbortController | null>(null);

  const ask = useCallback(async (question: string, role: Role) => {
    if (stateRef.current.streaming) return;
    dispatch({ type: "ask", question, role });
    const t0 = performance.now();
    const ac = new AbortController();
    abortRef.current = ac;
    let gotDone = false;
    let gotError = false;
    try {
      for await (const ev of chatStream(question, role, stateRef.current.sessionId, ac.signal)) {
        if (ev.type === "done") {
          gotDone = true;
          const sid = stateRef.current.sessionId ?? genSessionId();
          dispatch({ type: "done", event: ev, latencyMs: performance.now() - t0, sessionId: sid });
        } else if (ev.type === "error") {
          // 服务端 error 事件后无 done：立即落失败态（修复 Streamlit 版静默无输出的缺口）
          gotError = true;
          const sid = stateRef.current.sessionId ?? genSessionId();
          dispatch({
            type: "eventFailed",
            detail: ev.detail,
            latencyMs: performance.now() - t0,
            sessionId: sid,
          });
        } else {
          dispatch({ type: "event", event: ev });
        }
      }
      if (!gotDone && !gotError) {
        // 流正常关闭但既无 done 也无 error：断流兜底
        const sid = stateRef.current.sessionId ?? genSessionId();
        dispatch({ type: "streamEnded", latencyMs: performance.now() - t0, sessionId: sid });
      }
    } catch (e) {
      // fetch 网络错误 / HTTP 非 2xx / JSON 解析错误
      const sid = stateRef.current.sessionId ?? genSessionId();
      dispatch({
        type: "streamFailed",
        detail: e instanceof Error ? e.message : String(e),
        latencyMs: performance.now() - t0,
        sessionId: sid,
      });
    }
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  return { state, dispatch, ask };
}
