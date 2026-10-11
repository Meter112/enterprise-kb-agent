# CLAUDE.md

企业级智能知识库问答 Agent（RAG + Agent），面试作品集项目。四周计划与进度快照见 `docs/plan.md`（已完成 D1-25，剩余 D26-28 收尾）。

## 架构导览

```
src/kb_agent/
├── agent/          # 手写 StateGraph：graph.py(节点+边) prompts.py tools.py llm.py state.py cache.py
├── retrieval/      # hybrid_retriever.py(RRF融合) refusal.py(阈值0.7) citations.py(格式+校验)
├── ingest/         # chunker.py sparse_bm25.py(手写) indexer.py(增量hash)
├── store/          # qdrant_store.py（query_hybrid + expand_roles 权限过滤）
├── models/         # embedder.py(BGE-M3) reranker.py(bge-reranker-base)
├── api/            # main.py /chat SSE 事件流；chat.py 构建单例 agent
├── web/            # app.py Streamlit UI
└── eval/           # qa_set.py metrics.py judge.py agent_runner.py
```

数据流：`query → route(输入校验) → rewrite(多轮指代) → tool_router(LLM 选工具) → exec_tools(query_kb/summarize_doc) → generate(带[n]引用) → validate(引用校验,失败重试≤2) → refuse(分场景话术)`。对比题在 exec_tools 用确定性正则拆成两侧独立检索（`_split_compare_query`），多路结果按 chunk_id 去重合并，「query_kb 全部拒绝才整体拒绝」。

关键设计点（改动前先读）：
- **依赖全注入**：graph 的 deps 在 build_graph 时固定；API 层 per-request 事件队列走 thread-local 桥接（`api/chat.py`）
- **缓存**：L1 ExactCache（query+role+kb_version → 回答，7 天 TTL，`agent/cache.py`）；L2 RedisSaver checkpoint（24h TTL）。改 prompt/检索逻辑后记得清 `answer_cache:*` key（kb_version 只跟语料走）
- **权限**：检索期 `expand_roles` 向上兼容过滤（public < contractor < employee < admin），生成层只见过滤后的候选
- **DeepSeek 模型分工**：deepseek-chat 生成（max_tokens 8192），deepseek-flash 路由/judge/改写（推理模型，max_tokens 留 4000 余量，见 memory）

## 常用命令

```bash
make infra           # 起 qdrant + redis（本机开发）
make api / make web  # FastAPI :8000 / Streamlit :8501
make up / make down  # compose 四容器一键起停
make test / make lint

uv run python scripts/index_corpus.py            # 增量索引（--rebuild 全量；--spot "查询" 抽查）
uv run python scripts/eval_retrieval.py          # Week 2 检索基线（Hit@5/MRR/拒答双面）
uv run python scripts/eval_agent.py              # Week 4 Agent 端到端（cold+warm+judge；--ids 单条；--no-judge 省钱）
uv run python scripts/scan_threshold.py          # 阈值离线扫描（改阈值前先跑）
```

## 环境与部署

- Python 3.13 + uv；`.env` 只需 `DEEPSEEK_API_KEY`（模板 `.env.example`）
- 模型权重在 `data/models/`（BGE-M3 + reranker，~2.3GB），`scripts/download_models.py` 走 hf-mirror 断点续传
- Docker：镜像构建见 `Dockerfile`（torch CPU 2.6.0 + GPU 依赖逐个排除 + `docker/sitecustomize.py` 补丁），api/web 共用镜像 `kb-agent-api:latest`，web 服务 `pull_policy: never`——**不要再给 web 加 `build: .`，会重复构建**
- 电脑重启后容器停，需 `docker compose up -d`

## 已知边界（报告已披露，勿重复"修复"）

- qa-029 fail-open 主题错位：权限没漏、用可见文档答了相邻主题问题；后续方向是生成前主题一致性校验（尚未实施）
- 冷路径延迟 D26 优化后 ~7.9s/条（rerank 候选 50→20，评估验证零指标损失）；剩余大头仍是 rerank（7.2s），进一步方向是 GPU 或蒸馏小模型
- flash judge 对表格类/边界答案判分有噪音，评估框架已有多数表决缓解
- `app/` 目录（若还在）是旧模型权重副本，与 `data/models/` 重复

## 坑记录（面试可讲，细节在 docs/plan.md 快照）

1. langgraph state 是 last-write-wins：validate 通过分支必须显式清空 retry_feedback，否则条件边死循环
2. LLM 提取的文档标题与索引标题有格式差异（书名号/连字符）→ `_norm` 归一化两级匹配
3. Redis 重启后 RediSearch 索引丢失（内存对象不随 RDB 持久化）→ 服务启动显式 `saver.setup()` 幂等重建
4. `RedisSaver.from_conn_string` 是 contextmanager 不是构造器，长生命周期服务用 `RedisSaver(redis_url=…)`
5. `--no-install-package torch` 不剪传递依赖，GPU 包要 sed 从 uv.lock 逐个提取排除
6. torch 2.6.0 CPU 在无 GPU 环境 `_accelerator_getAccelerator` 抛异常（2.7 修复），sentence-transformers 6.x 锁死 transformers≥5 无法降级 → sitecustomize 补丁（仅 2.6.x 触发）

## 测试约定

- 单测全部 fake 化（FakeLLM/FakeRetriever/FakeStore），不碰真实模型/索引/Redis
- 改检索或 Agent 逻辑后：`make lint && make test` 必须全绿；涉及排序/拒答/生成的改动，评估结果会变，需重跑对应 eval 脚本并更新 `docs/eval_report.md`
