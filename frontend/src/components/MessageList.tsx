import { useEffect, useRef } from "react";
import type { ChatMessage } from "../types";
import type { Pending } from "../state/chatReducer";
import { AnswerView } from "./AnswerView";
import { ToolTrace } from "./ToolTrace";

interface MessageListProps {
  messages: ChatMessage[];
  pending: Pending | null;
}

export function MessageList({ messages, pending }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, pending?.acc.length]);

  return (
    <div className="message-list">
      {messages.map((m) =>
        m.role === "user" ? (
          <div key={m.id} className="bubble bubble-user">
            {m.content}
          </div>
        ) : (
          <div key={m.id} className="bubble bubble-assistant">
            <AnswerView
              content={m.content}
              meta={m.meta ?? null}
              question={m.question ?? ""}
            />
            {m.meta?.trace && <ToolTrace trace={m.meta.trace} />}
          </div>
        ),
      )}
      {pending && (
        <div className="bubble bubble-assistant">
          <AnswerView
            content={pending.acc}
            meta={null}
            question={pending.question}
            streaming
            status={pending.status}
            statusKind={pending.statusKind}
          />
        </div>
      )}
      {messages.length === 0 && !pending && (
        <div className="empty-hint">
          <p>输入问题开始，如「年假有几天」「内部订单API和公开API的鉴权有什么区别」</p>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
