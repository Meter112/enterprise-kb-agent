import { describe, expect, it } from "vitest";
import { parseSSE } from "./sse";

function respFrom(chunks: string[], init: ResponseInit = {}): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      c.close();
    },
  });
  return new Response(stream, init);
}

async function collect(gen: AsyncGenerator<unknown>): Promise<unknown[]> {
  const out: unknown[] = [];
  for await (const x of gen) out.push(x);
  return out;
}

const TOKEN_FRAME = 'event: token\ndata: {"type":"token","text":"你","attempt":1}\n\n';

describe("parseSSE", () => {
  it("解析完整单帧", async () => {
    const evs = await collect(parseSSE(respFrom([TOKEN_FRAME])));
    expect(evs).toEqual([{ type: "token", text: "你", attempt: 1 }]);
  });

  it("半帧跨 chunk 边界", async () => {
    const mid = TOKEN_FRAME.indexOf('"你"') + 2;
    const evs = await collect(parseSSE(respFrom([TOKEN_FRAME.slice(0, mid), TOKEN_FRAME.slice(mid)])));
    expect(evs).toHaveLength(1);
    expect(evs[0]).toMatchObject({ type: "token", text: "你" });
  });

  it("单 chunk 多帧", async () => {
    const evs = await collect(parseSSE(respFrom([TOKEN_FRAME + TOKEN_FRAME])));
    expect(evs).toHaveLength(2);
  });

  it("CRLF 帧分隔", async () => {
    const frame = 'event: token\r\ndata: {"type":"token","text":"好","attempt":1}\r\n\r\n';
    const evs = await collect(parseSSE(respFrom([frame])));
    expect(evs).toEqual([{ type: "token", text: "好", attempt: 1 }]);
  });

  it("末尾无换行的残留帧", async () => {
    const frame = 'event: done\ndata: {"type":"token","text":"末","attempt":1}';
    const evs = await collect(parseSSE(respFrom([frame])));
    expect(evs).toEqual([{ type: "token", text: "末", attempt: 1 }]);
  });

  it("忽略无 data 的心跳帧", async () => {
    const evs = await collect(parseSSE(respFrom(["event: ping\n\n", TOKEN_FRAME])));
    expect(evs).toHaveLength(1);
    expect(evs[0]).toMatchObject({ type: "token" });
  });

  it("HTTP 非 2xx 抛错并带状态码", async () => {
    await expect(collect(parseSSE(respFrom([], { status: 503 })))).rejects.toThrow(/HTTP 503/);
  });
});
