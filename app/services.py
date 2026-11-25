"""The set of long-lived collaborators the API works with, built once at startup.

Tests build the same container with in-memory Qdrant, a fake embedder, a fake
parser and a fake LLM.
"""

import threading
from dataclasses import dataclass, field

from qdrant_client import QdrantClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import create_session_factory
from app.embeddings import Embedder, FastEmbedEmbedder
from app.ingestion import DoclingParser, ParserOptions, PdfParser
from app.llm import LLMClient, OllamaClient
from app.vector_store import VectorStore


@dataclass
class Services:
    settings: Settings
    sessions: sessionmaker[Session]
    store: VectorStore
    embedder: Embedder
    llm: LLMClient
    parser: PdfParser
    ingestion_lock: threading.Lock = field(default_factory=threading.Lock)


def build_services(settings: Settings) -> Services:
    """Wire the production implementations from settings."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    return Services(
        settings=settings,
        sessions=create_session_factory(settings.resolved_database_url),
        store=VectorStore(QdrantClient(url=settings.qdrant_url), settings.qdrant_collection),
        embedder=FastEmbedEmbedder(
            settings.dense_model,
            settings.sparse_model,
            query_prefix=settings.dense_query_prefix,
            passage_prefix=settings.dense_passage_prefix,
            cache_dir=settings.models_cache_dir,
            threads=settings.embedding_threads,
            device=settings.embedding_device,
        ),
        llm=OllamaClient(
            settings.ollama_base_url,
            settings.llm_model,
            num_ctx=settings.llm_num_ctx,
            timeout_seconds=settings.llm_timeout_seconds,
            think=settings.llm_think,
        ),
        parser=DoclingParser(
            ParserOptions(
                threads=settings.parse_threads,
                table_mode=settings.parse_table_mode,
                device=settings.parse_device,
            ),
            cache_dir=settings.parse_cache_dir if settings.parse_cache else None,
        ),
    )
