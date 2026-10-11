# kb-agent：API（FastAPI）+ Web（Streamlit）共用镜像，CPU 推理
#
# torch 处理说明：uv.lock 里 torch 2.14.1 在 linux marker 下会拉 cuda-toolkit、
# cuda-bindings、triton 及 15 个 nvidia-* CUDA 依赖（合计 ~7GB，镜像爆炸；
# --no-install-package 只排除命名包本体、不剪其依赖，所以 GPU 系包名要逐个排除）。
# 容器只做 CPU 推理，因此 sync 时排除 torch + nvidia-*/cuda-*/triton（sed 从
# uv.lock 动态提取，lock 更新时自动跟随），改从 pytorch CPU index 装 2.6.0
# （该 index 当前 cp313 最新版，--no-deps：其真实依赖已由 sync 装好）。
# 2.6.0 与新 transformers 的兼容问题由 docker/sitecustomize.py 打补丁解决。
# uv 版本与 lock 生成环境（host uv 0.11.16）钉死，避免 uv:latest 行为漂移。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app/src:/app

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv

# 依赖层（利用 build cache：pyproject/uv.lock 不变时跳过重装）
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-package torch \
       $(sed -n 's/^name = "\(nvidia-[^"]*\|cuda-[^"]*\|triton\)"$/\1/p' uv.lock | sort -u | sed 's/^/--no-install-package /') \
    && uv pip install --python /usr/local/bin/python --no-deps torch==2.6.0 \
       --index-url https://download.pytorch.org/whl/cpu \
    && python -c "import torch; torch._C._accelerator_getAccelerator = lambda: None; \
from transformers import AutoModel; print('torch', torch.__version__, 'ml stack ok')"

# 代码层
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY docker/sitecustomize.py /app/sitecustomize.py

# 数据（模型权重/语料/索引缓存）由 compose 挂载 host 目录，不进镜像
EXPOSE 8000 8501

# 默认起 API；web 服务在 compose 里覆盖启动命令
CMD ["uvicorn", "kb_agent.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
