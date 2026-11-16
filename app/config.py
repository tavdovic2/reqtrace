"""Application settings, read from environment variables (and an optional .env file)."""

from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.extraction_scope import DEFAULT_SKIP_SECTIONS


class Settings(BaseSettings):
    """All runtime configuration for reqTrace in one place."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: Path = Path("./data")
    database_url: str | None = Field(
        default=None, description="SQLAlchemy URL; defaults to SQLite inside DATA_DIR."
    )

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "reqtrace_chunks"

    dense_model: str = "intfloat/multilingual-e5-large"
    dense_query_prefix: str = "query: "
    dense_passage_prefix: str = "passage: "
    sparse_model: str = "Qdrant/bm25"
    embedding_threads: int | None = None
    models_cache_dir: Path | None = None

    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen3:4b"
    llm_num_ctx: int = 8192
    llm_timeout_seconds: float = 300.0
    llm_think: bool = False

    retrieval_top_k: int = Field(default=8, ge=1, le=10)
    retrieval_max_per_page: int = Field(default=2, ge=1)
    retrieval_prefetch_limit: int = 20

    chunk_max_chars: int = 1500
    chunk_overlap_chars: int = 200

    max_upload_mb: int = 50
    temporary_ttl_hours: int = 24
    cleanup_interval_minutes: int = 15

    quote_match_threshold: float = Field(default=90.0, ge=0, le=100)
    quote_min_chars: int = 15
    requirement_support_threshold: float = Field(default=0.75, ge=0, le=1)
    extraction_skip_sections: list[str] = list(DEFAULT_SKIP_SECTIONS)
    extraction_paraphrase: bool = False
    extraction_group_chars: int = Field(default=3000, ge=500)
    extraction_concurrency: int = Field(default=1, ge=1, le=4)
    dedup_threshold: float = Field(default=92.0, ge=0, le=100)

    cors_origins: list[str] = ["http://localhost:5173"]

    @cached_property
    def uploads_dir(self) -> Path:
        """Directory where uploaded PDFs are stored."""
        return self.data_dir / "uploads"

    @cached_property
    def resolved_database_url(self) -> str:
        """Database URL, falling back to a SQLite file in the data directory."""
        return self.database_url or f"sqlite:///{(self.data_dir / 'reqtrace.db').as_posix()}"

    @property
    def max_upload_bytes(self) -> int:
        """Upload size limit in bytes."""
        return self.max_upload_mb * 1024 * 1024
