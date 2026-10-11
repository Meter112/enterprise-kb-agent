"""下载 BGE-M3 与 bge-reranker-base 到 data/models/。

国内网络通过 hf-mirror 镜像加速；支持断点续传，重复运行只校验不重复下载。
用法: uv run python scripts/download_models.py
"""
import os
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# hf-mirror 不支持 Xet CAS 分块重建（401），禁用后走传统 LFS 下载
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import snapshot_download  # noqa: E402

MODELS = ["BAAI/bge-m3", "BAAI/bge-reranker-base"]


def weights_exist(dest: Path) -> bool:
    """权重文件是否完整（config.json 不足以判断——首次失败可能只下到配置）。"""
    return (
        (dest / "config.json").exists()
        and (any(dest.glob("*.safetensors")) or any(dest.glob("pytorch_model*.bin")))
    )


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "data" / "models"
    for model in MODELS:
        dest = target / model.replace("/", "--")
        if weights_exist(dest):
            print(f"[skip] {model} 已完整: {dest}")
            continue
        print(f"[download] {model} -> {dest}")
        # imgs/、onnx/ 为仓库附带的演示图片与导出格式，推理不需要；.DS_Store 会被镜像站 403
        snapshot_download(
            repo_id=model,
            local_dir=str(dest),
            ignore_patterns=["imgs/*", "onnx/*", "*.DS_Store"],
        )


if __name__ == "__main__":
    main()
