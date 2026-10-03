"""
Centralized application configuration.

All runtime configuration is sourced from environment variables (and an
optional .env file) using Pydantic Settings. This keeps secrets out of
source control and gives us validated, typed configuration objects that
can be safely injected throughout the application.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Strongly typed application settings loaded from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application metadata
    # ------------------------------------------------------------------
    app_name: str = Field(default="PDF-ChatGPT", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_version: str = Field(default="1.0.0", alias="APP_VERSION")
    debug: bool = Field(default=True, alias="DEBUG")

    # ------------------------------------------------------------------
    # Server
    # ------------------------------------------------------------------
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------
    openai_api_key: str = Field(
        default="", validation_alias=AliasChoices("OPENAI_API_KEY", "LLM_API_KEY")
    )
    openai_api_base: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("OPENAI_API_BASE", "OPENAI_BASE_URL"),
    )
    embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("EMBEDDING_API_KEY", "OPENAI_EMBEDDING_API_KEY"),
    )
    embedding_api_base: str = Field(
        default="",
        validation_alias=AliasChoices("EMBEDDING_API_BASE", "OPENAI_EMBEDDING_BASE_URL"),
    )
    openai_chat_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_CHAT_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL"
    )
    openai_request_timeout: int = Field(default=60, alias="OPENAI_REQUEST_TIMEOUT")
    openai_max_output_tokens: int = Field(default=1024, alias="OPENAI_MAX_OUTPUT_TOKENS")

    # ------------------------------------------------------------------
    # RAG / chunking
    # ------------------------------------------------------------------
    chunk_size: int = Field(default=1000, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=150, alias="CHUNK_OVERLAP")
    retrieval_top_k: int = Field(default=4, alias="RETRIEVAL_TOP_K")
    max_upload_mb: int = Field(default=25, alias="MAX_UPLOAD_MB")

    # ------------------------------------------------------------------
    # Storage paths
    # ------------------------------------------------------------------
    data_dir: Path = Field(default=BASE_DIR / "data", alias="DATA_DIR")
    upload_dir: Path = Field(default=BASE_DIR / "data" / "uploads", alias="UPLOAD_DIR")
    vector_store_dir: Path = Field(
        default=BASE_DIR / "data" / "vector_store", alias="VECTOR_STORE_DIR"
    )
    database_url: str = Field(
        default=f"sqlite:///{BASE_DIR / 'data' / 'app.db'}", alias="DATABASE_URL"
    )
    log_dir: Path = Field(default=BASE_DIR / "logs", alias="LOG_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("upload_dir", "vector_store_dir", "log_dir", "data_dir", mode="after")
    @classmethod
    def _ensure_directory_exists(cls, value: Path) -> Path:
        value.mkdir(parents=True, exist_ok=True)
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
