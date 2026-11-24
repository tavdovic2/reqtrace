"""Dense and sparse (BM25) embeddings with FastEmbed, on the CPU."""

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SparseVector:
    indices: list[int]
    values: list[float]


@dataclass(frozen=True)
class EmbeddedText:
    dense: list[float]
    sparse: SparseVector


@dataclass
class EmbedTimings:
    """Seconds spent in each model since the last reset (for the ingestion timing log)."""

    dense: float = 0.0
    sparse: float = 0.0


class Embedder(Protocol):
    """Anything that can produce a dense and a sparse vector for a text."""

    @property
    def dense_size(self) -> int: ...

    def embed_passages(self, texts: list[str]) -> list[EmbeddedText]: ...

    def embed_query(self, text: str) -> EmbeddedText: ...


class FastEmbedEmbedder:
    """FastEmbed models loaded lazily, so the API starts before the models are downloaded.

    E5 models expect "query: " / "passage: " prefixes and FastEmbed does not add them,
    so the prefixes are configurable and applied here.
    """

    def __init__(
        self,
        dense_model: str,
        sparse_model: str,
        query_prefix: str = "",
        passage_prefix: str = "",
        cache_dir: Path | None = None,
        threads: int | None = None,
        device: str = "cpu",
    ) -> None:
        self._dense_name = dense_model
        self._sparse_name = sparse_model
        self._query_prefix = query_prefix
        self._passage_prefix = passage_prefix
        self._cache_dir = str(cache_dir) if cache_dir else None
        self._threads = threads
        self._device = device
        self._dense: object | None = None
        self._sparse: object | None = None
        self._lock = threading.Lock()
        self.timings = EmbedTimings()

    def _models(self) -> tuple[object, object]:
        with self._lock:
            if self._dense is None or self._sparse is None:
                from fastembed import SparseTextEmbedding, TextEmbedding

                logger.info(
                    "Loading embedding models %s and %s", self._dense_name, self._sparse_name
                )
                self._dense = TextEmbedding(
                    self._dense_name,
                    cache_dir=self._cache_dir,
                    # ONNX Runtime's own default uses fewer threads than there are cores;
                    # all logical cores embedded TMP117 about 15% faster (README, Performance).
                    threads=self._threads or os.cpu_count(),
                    **self._device_options(),
                )
                self._sparse = SparseTextEmbedding(self._sparse_name, cache_dir=self._cache_dir)
        return self._dense, self._sparse

    def _device_options(self) -> dict[str, object]:
        """ONNX Runtime providers for the dense model: CUDA when asked for and installed."""
        if self._device != "cuda":
            return {}
        import onnxruntime

        if "CUDAExecutionProvider" not in onnxruntime.get_available_providers():
            logger.warning(
                "EMBEDDING_DEVICE=cuda but onnxruntime-gpu is not installed; embedding on the CPU"
            )
            return {}
        # The CUDA and cuDNN libraries come from the torch or nvidia-* wheels; ONNX Runtime
        # finds them only once they are loaded.
        preload = getattr(onnxruntime, "preload_dlls", None)
        if preload is not None:
            preload()
        return {"providers": ["CUDAExecutionProvider", "CPUExecutionProvider"]}

    @property
    def dense_size(self) -> int:
        """Dimension of the dense model, known without loading it."""
        from fastembed import TextEmbedding

        return TextEmbedding.get_embedding_size(self._dense_name)

    def embed_passages(self, texts: list[str]) -> list[EmbeddedText]:
        """Embed document chunks."""
        dense_model, sparse_model = self._models()
        started = time.monotonic()
        dense = list(dense_model.embed([self._passage_prefix + t for t in texts]))  # type: ignore[attr-defined]
        middle = time.monotonic()
        sparse = list(sparse_model.embed(texts))  # type: ignore[attr-defined]
        self.timings.dense += middle - started
        self.timings.sparse += time.monotonic() - middle
        return [
            EmbeddedText(d.tolist(), SparseVector(s.indices.tolist(), s.values.tolist()))
            for d, s in zip(dense, sparse, strict=True)
        ]

    def embed_query(self, text: str) -> EmbeddedText:
        """Embed a question; BM25 query vectors weight every term equally."""
        dense_model, sparse_model = self._models()
        dense = next(iter(dense_model.query_embed(self._query_prefix + text)))  # type: ignore[attr-defined]
        sparse = next(iter(sparse_model.query_embed(text)))  # type: ignore[attr-defined]
        return EmbeddedText(
            dense.tolist(), SparseVector(sparse.indices.tolist(), sparse.values.tolist())
        )
