# kb-agent React 前端

企业知识库问答 Agent 的 React + TypeScript 前端（与 `src/kb_agent/web/` 的 Streamlit 版并存）。

## 技术要点

- **手写 SSE 解析**（`src/lib/sse.ts`）：`/chat` 是 POST SSE，`EventSource` 只支持 GET 不可用——用 `fetch` + `ReadableStream` 按双换行切帧解析，处理半帧/CRLF/残留帧边界（vitest 单测覆盖）
- **流式状态机**（`src/state/chatReducer.ts`）：`useReducer` 维护"构建中"消息——`token` 事件按 `attempt` 编号重置累积（引用校验重试轮不混拼）、`done.answer` 覆盖流式文本、`error` 事件后无 `done` 时落可见失败态（修复 Streamlit 版静默无输出的缺口）
- **轨迹面板**：事件序列 + 延迟拆解（降序）+ 检索候选（引用/rerank 分/摘要），对比题多路检索候选**累积**展示（Streamlit 是 last-wins 会丢前一路）
- **部署**：dev 走 vite proxy（免 CORS）；生产 `make web-ui` 用 nginx 托管构建产物 + API 反代（`proxy_buffering off` 保 SSE 流式），后端 CORS 仅作 preview 直连兜底

## 命令

```bash
npm ci            # 安装依赖
npm run dev       # dev server :5173（代理 /chat /documents /health → :8000）
npm run build     # tsc 类型检查 + vite build → dist/
npm test          # vitest（sse 解析器 + chatReducer）
npm run lint      # oxlint
```

## 目录

```
src/
├── lib/          # sse.ts（流解析纯函数）+ api.ts（chatStream/上传/删除/metrics）
├── state/        # chatReducer（流式中间态 → 终态消息）
├── hooks/        # useChatStream（消费 SSE 逐事件 dispatch）
└── components/   # Sidebar / MessageList / AnswerView / ToolTrace / UploadPanel / ErrorBanner
```
