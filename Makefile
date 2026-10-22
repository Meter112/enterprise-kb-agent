# kb-agent 常用命令（Windows Git Bash 兼容）
# 完整容器化：make up（qdrant+redis+api+web 一键起）；本机开发：make infra + make api + make web

.PHONY: infra up down api web web-ui web-ui-build test lint

infra:  ## 只起基础设施（qdrant + redis），api/web 本机跑
	docker compose up -d qdrant redis

up:  ## 一键起全套（容器化：基础设施 + API + Web UI）
	docker compose up -d --build

down:
	docker compose down

api:  ## 本机起 FastAPI（依赖 infra）
	uv run uvicorn kb_agent.api.main:app --port 8000

web:  ## 本机起 Streamlit UI（依赖 api）
	uv run streamlit run src/kb_agent/web/app.py

web-ui-build:  ## host 构建 React 静态产物（api 镜像内无 node，构建只能在 host）
	cd frontend && npm ci && npm run build

web-ui: web-ui-build  ## 起 React 前端（nginx :8080，先构建再启动）
	docker compose --profile webui up -d web-ui

test:
	uv run pytest

lint:
	uv run ruff check src/ tests/ scripts/
