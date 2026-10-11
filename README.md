# enterprise-kb-agent

企业级智能知识库问答 Agent（RAG + Agent）。求职作品集项目：从语料生成、混合检索、拒答阈值标定到手写 Agent 状态机、权限隔离、缓存加速、端到端评估，四周完整走通一个生产形态的知识库问答系统。

**一句话**：52 篇企业文档（制度/API/FAQ，含角色权限标签）→ 混合检索（BGE-M3 + 手写 BM25 + RRF + rerank）→ 手写 StateGraph Agent（工具路由 / 拒答 / 引用校验重试）→ 带 [n] 引用的流式回答，权限隔离 0 泄漏，L1 缓存命中 0.001s。

## 架构

```mermaid
flowchart LR
    subgraph 前端
        UI[Streamlit UI<br/>流式渲染 / 角色切换 / 轨迹面板]
    end
    subgraph 服务层
        API[FastAPI /chat SSE<br/>事件流: tool_call → retrieved → token → citations]
        A[Agent StateGraph]
    end
    subgraph Agent 内部
        R1[route] --> RW[rewrite<br/>多轮指代消解]
        RW --> TR[tool_router<br/>function calling]
        TR --> ET[exec_tools<br/>query_kb / summarize_doc<br/>对比题确定性拆分]
        ET --> GN[generate<br/>带 n 引用生成]
        GN --> VA[validate<br/>引用校验, 失败重试≤2]
        VA -->|通过| CACHE
        VA -->|重试耗尽| RF[refuse<br/>分场景话术]
        ET -->|全拒| RF
    end
    subgraph 检索链路
        DENSE[BGE-M3 dense] --> QD[(Qdrant<br/>RRF 融合<br/>role 权限过滤)]
        SPARSE[手写 jieba BM25] --> QD
        QD --> RR[bge-reranker-base<br/>重排 + 拒答阈值 0.7]
        RR --> ET
    end
    subgraph 存储
        RD[(Redis<br/>checkpoint 多轮记忆<br/>L1 精确缓存)]
        CACHE[L1 Cache] --> GN
        API <--> RD
    end
    UI -->|SSE| API
    API --> A
    A --> DENSE
    A --> SPARSE
    A --> CACHE
```

## 核心特性

| 能力 | 实现 |
|---|---|
| 混合检索 | dense（BGE-M3）+ sparse（手写 jieba BM25）双路召回，Qdrant RRF 融合，bge-reranker-base 重排 |
| 权限隔离 | 语料带 role 标签，检索期 `expand_roles` 向上兼容过滤；端到端评估 0 权限泄漏 |
| 拒答机制 | 三层：检索阈值（离线标定 0.7）→ Agent 全拒判定 → 生成后引用校验失败重试（≤2）→ 拒答 |
| Agent 状态机 | 手写 langgraph StateGraph，7 节点 2 工具；多轮指代消解；对比题自动拆分多路检索（确定性正则兜底 LLM 路由） |
| 引用可追溯 | 每个事实句末尾 [n] 引用，程序化校验引用编号存在性，失败自动重试 |
| 流式体验 | FastAPI /chat SSE 事件流（检索轨迹 / token 增量 / 引用 / 拒答），Streamlit 逐 token 渲染 |
| 缓存加速 | Redis L1 精确缓存（query+role+语料版本 → 回答），warm 命中 0.001s/条；L2 checkpoint 多轮记忆 24h TTL |
| 文档摘要 | summarize_doc 工具：标题归一化反查 + map-reduce 长文档摘要 |

## 快速开始

### 方式一：Docker Compose 一键启动（推荐）

```bash
cp .env.example .env        # 填入 DEEPSEEK_API_KEY
docker compose up -d --build
# Web UI:  http://localhost:8501
# API:     http://localhost:8000/docs   （/health 检查服务与索引状态）
```

四容器（qdrant + redis-stack + api + web），语料与模型权重随仓库 `data/` 挂载。新环境首次启动时 Qdrant 索引为空，需先执行一次索引（容器内已具备依赖）：

```bash
docker compose exec api python scripts/index_corpus.py   # 增量索引 52 篇语料 → 370 chunks
```

或直接使用本机开发流程（方式二）完成索引后再 `docker compose up -d`。

### 方式二：本机开发

```bash
uv sync                                  # Python 3.13，依赖见 pyproject.toml
cp .env.example .env                     # 填入 DEEPSEEK_API_KEY
make infra                               # 起 qdrant + redis
uv run python scripts/download_models.py # BGE-M3 + reranker 权重 → data/models/
uv run python scripts/index_corpus.py    # 增量索引（--rebuild 全量重建）
make api                                 # FastAPI :8000
make web                                 # Streamlit :8501
```

> 模型权重约 2.3GB，国内网络经 hf-mirror 镜像下载，支持断点续传。

## Web UI 使用

- **问答**：输入问题 → 流式逐 token 渲染 + 引用高亮 → 展开工具轨迹面板（router 选择了哪些工具、每次检索的候选与 rerank 分数）
- **角色切换**：public / contractor / employee / admin 四档，权限隔离实时生效（如 contractor 问年假会被拒答）
- **多轮对话**：同一 session 内支持指代（「那病假呢？」）
- **文档上传**：拖入 PDF/MD → 自动 chunk + 增量索引

## 评估结果

> 完整报告：[docs/eval_report.md](docs/eval_report.md)（检索基线 v1 + Agent 端到端 §7）。评估集：32 条 QA × 4 场景（单跳 16 / 跨文档 5 / 语料外 5 / 权限 6）。

| 指标 | 数值 |
|---|---|
| 答案正确率（LLM judge + 多数表决） | **0.952**（21 条判分） |
| 拒答准确率（应拒 9/11 + 应答 21/21） | **0.938** |
| 实质泄漏（judge_leak 口径） | **1 条**（qa-029，主题错位非权限越界，已披露） |
| 权限隔离 | **0 泄漏** |
| 引用覆盖 | **21/21** |
| L1 缓存 | **32/32 命中，0.001s/条**（warm 轮） |
| 检索 Hit@5 / MRR（RAG 基线） | 0.741 / 0.560 |
| 端到端延迟 | 14.3s/条 cold（D26 rerank 截断 -32%；检索 7.9s/条，零指标损失，见报告 §7.4） |

复现评估：

```bash
uv run python scripts/eval_retrieval.py   # Week 2 检索基线（Hit@5/MRR/拒答双面）
uv run python scripts/eval_agent.py       # Week 4 Agent 端到端（cold+warm 两轮 + judge）
```

## 项目结构

```
src/kb_agent/
├── agent/          # 手写 StateGraph：graph / prompts / tools / llm / state / cache
├── retrieval/      # hybrid_retriever / refusal(阈值) / citations / reranker 封装
├── ingest/         # chunker(结构感知) / sparse_bm25(手写) / indexer(增量 hash)
├── store/          # Qdrant 封装（RRF prefetch + role 过滤）
├── models/         # BGE-M3 embedder / bge-reranker-base
├── api/            # FastAPI：/chat SSE 事件流 / /documents 上传 / /health
├── web/            # Streamlit UI
└── eval/           # QA 集 / 指标 / flash judge / AgentEvalRunner
data/
├── corpus/         # 52 篇生成语料（MD/PDF/TXT × 4 类 × role 标签）
├── qa_sets/        # 32 条评估题（gold chunk + 答案要点 + 权限期望）
├── index_cache/    # BM25 参数 + kb_version
└── reports/        # 评估报告 JSON
docs/               # plan（四周计划）/ eval_report / manual_test_report / benchmarks
```

## 技术栈

Python 3.13 / uv · langgraph（手写状态机）· Qdrant（RRF 混合检索）· BGE-M3 + bge-reranker-base（FlagEmbedding）· jieba（手写 BM25）· FastAPI + SSE · Streamlit · Redis（checkpoint + L1 缓存）· DeepSeek（chat 生成 / flash 路由与 judge）· Docker Compose · pytest + ruff

## 面试亮点索引

开发过程中真实踩过并修复的问题（均可展开讲，细节见 docs/plan.md 进度快照）：

1. **对比题拆分检索**：LLM 路由不遵守拆分规则 → exec 层确定性正则兜底（`A和B的X有什么区别` → 两侧独立 query），多路结果按 chunk_id 去重合并
2. **reranker 对约束词不敏感**：正确答案 RRF 排第 2 却被 rerank 压到第 6 截断 → 候选数 5→8 兜底
3. **拒答阈值标定方法**：离线重算 top-5 分数扫描阈值（12 阈值 × 32 题 1 秒完成），选帕累托最优下界
4. **langgraph last-write-wins 陷阱**：validate 通过后残留 retry_feedback 导致死循环 → 通过分支显式清空
5. **Redis 重启后 RediSearch 索引丢失**：checkpoint 数据在但模块索引不持久化 → 服务启动显式 `saver.setup()` 幂等重建
6. **Docker 镜像 18GB→4.68GB**：`--no-install-package` 不剪传递依赖（nvidia-*/cuda-* 逐个排除）；torch 2.6 CPU 与 transformers 5.x 兼容 bug → sitecustomize 补丁
7. **评估口径设计**：漏拒用「是否实质回答」而非 refused 标志（软拒答话术不算泄漏）；judge 边界噪音用多数表决
