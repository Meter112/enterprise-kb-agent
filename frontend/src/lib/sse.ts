import type { ChatEvent } from "../types";

/**
 * POST /chat 的 SSE 流解析器。EventSource 只支持 GET，本项目 /chat 是 POST，
 * 故用 fetch + ReadableStream 手写解析（纯函数生成器，可单测）。
 *
 * 服务端帧格式：`event: {type}\ndata: {json}\n\n`，type 已含在 data JSON 内，
 * 所以只解析 data: 行（与 Streamlit 版一致）。
 *
 * 处理的分帧边界：单 chunk 半帧 / 单 chunk 多帧 / CRLF / 末尾无换行残留帧。
 */
export async function* parseSSE(resp: Response): AsyncGenerator<ChatEvent> {
  if (!resp.ok) {
    let detail = "";
    try {
      detail = (await resp.text()).slice(0, 200);
    } catch {
      // body 读取失败时只报状态码
    }
    throw new Error(`HTTP ${resp.status}${detail ? `: ${detail}` : ""}`);
  }
  if (!resp.body) {
    throw new Error("响应无 body，无法读取 SSE 流");
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  const drainFrames = (final: boolean): ChatEvent[] => {
    const out: ChatEvent[] = [];
    // 帧分隔符：双换行（兼容 \n\n 与 \r\n\r\n）
    let idx: number;
    while ((idx = buf.search(/\r?\n\r?\n/)) !== -1) {
      const sepLen = buf[idx] === "\r" ? 4 : 2;
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + sepLen);
      out.push(...parseFrame(frame));
    }
    if (final && buf.trim()) {
      // 末尾无换行的残留帧：按行取 data: 段
      out.push(...parseFrame(buf));
      buf = "";
    }
    return out;
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      yield* drainFrames(false);
    }
    buf += decoder.decode();
    yield* drainFrames(true);
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(frame: string): ChatEvent[] {
  return frame
    .split(/\r?\n/)
    .filter((l) => l.startsWith("data:"))
    .map((l) => l.slice(5).trim())
    .filter((d) => d.length > 0)
    .map((d) => JSON.parse(d) as ChatEvent);
}
