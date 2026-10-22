import { useState } from "react";
import { ErrorBanner } from "./components/ErrorBanner";
import { MessageList } from "./components/MessageList";
import { Sidebar } from "./components/Sidebar";
import { useChatStream } from "./hooks/useChatStream";
import type { Role } from "./types";

export default function App() {
  const { state, dispatch, ask } = useChatStream();
  const [input, setInput] = useState("");

  const submit = () => {
    const q = input.trim();
    if (!q || state.streaming) return;
    setInput("");
    ask(q, state.role);
  };

  return (
    <div className="layout">
      <Sidebar
        role={state.role}
        streaming={state.streaming}
        requests={state.requests}
        cacheHits={state.cacheHits}
        totalLatency={state.totalLatency}
        onRoleChange={(r: Role) => dispatch({ type: "setRole", role: r })}
        onNewSession={() => dispatch({ type: "newSession" })}
      />
      <main className="main">
        <header className="main-header">
          <h1>📚 云帆科技知识库问答 Agent</h1>
          <p className="subtitle">
            RAG + Agent：hybrid 检索 → rerank → 拒答阈值 → 工具调用 → 引用生成 → 多轮记忆 → L1 缓存
          </p>
        </header>
        {state.error && (
          <ErrorBanner detail={state.error} onDismiss={() => dispatch({ type: "dismissError" })} />
        )}
        <MessageList messages={state.messages} pending={state.pending} />
        <footer className="composer">
          <textarea
            value={input}
            rows={2}
            placeholder="输入问题，如「年假有几天」（Enter 发送，Shift+Enter 换行）"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <button
            type="button"
            className="btn btn-send"
            onClick={submit}
            disabled={state.streaming || !input.trim()}
          >
            {state.streaming ? "生成中…" : "发送"}
          </button>
        </footer>
      </main>
    </div>
  );
}
