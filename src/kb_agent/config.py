"""全局配置：pydantic-settings 从 .env / 环境变量读取。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM（DeepSeek，OpenAI 兼容） ---
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-chat"
    deepseek_flash_model: str = "deepseek-flash"

    # --- 基础设施 ---
    qdrant_url: str = "http://localhost:6333"
    redis_url: str = "redis://localhost:6379"

    # --- 路径 ---
    data_dir: Path = PROJECT_ROOT / "data"
    models_dir: Path = PROJECT_ROOT / "data" / "models"
    bge_m3_dir: Path = PROJECT_ROOT / "data" / "models" / "BAAI--bge-m3"
    reranker_dir: Path = PROJECT_ROOT / "data" / "models" / "BAAI--bge-reranker-base"
    corpus_dir: Path = PROJECT_ROOT / "data" / "corpus"

    # --- 索引与检索 ---
    collection_name: str = "kb_chunks"
    embed_threads: int = 8
    chunk_size: int = 500
    chunk_overlap: int = 50


settings = Settings()
