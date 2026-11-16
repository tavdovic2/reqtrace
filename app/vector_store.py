"""Qdrant collection management: one collection with named dense and BM25 sparse vectors."""

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Literal

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.embeddings import EmbeddedText

logger = logging.getLogger(__name__)

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "bm25"
UPSERT_BATCH = 64

SearchMode = Literal["dense", "bm25", "hybrid"]


class VectorStoreUnavailableError(RuntimeError):
    """Qdrant could not be reached or returned an error."""


@dataclass(frozen=True)
class ChunkPayload:
    """What is stored next to every vector; enough to cite and display the chunk."""

    doc_id: int
    drawer_id: int
    filename: str
    page: int
    section: str
    kind: str
    text: str
    chunk_index: int


@dataclass(frozen=True)
class ScoredChunk:
    payload: ChunkPayload
    score: float


@contextmanager
def _qdrant_errors() -> Iterator[None]:
    try:
        yield
    except (ResponseHandlingException, UnexpectedResponse, ConnectionError, OSError) as exc:
        logger.warning("Qdrant call failed: %s", exc)
        raise VectorStoreUnavailableError(f"The vector database is not reachable ({exc}).") from exc


class VectorStore:
    """Thin wrapper over QdrantClient that knows reqTrace's collection layout."""

    def __init__(self, client: QdrantClient, collection: str) -> None:
        self._client = client
        self._collection = collection

    def ensure_collection(self, dense_size: int) -> None:
        """Create the collection and payload indexes if they do not exist yet."""
        with _qdrant_errors():
            if self._client.collection_exists(self._collection):
                self._check_dense_size(dense_size)
                return
            self._client.create_collection(
                self._collection,
                vectors_config={
                    DENSE_VECTOR: models.VectorParams(
                        size=dense_size, distance=models.Distance.COSINE
                    )
                },
                sparse_vectors_config={
                    SPARSE_VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF)
                },
            )
            for field in ("doc_id", "drawer_id"):
                self._client.create_payload_index(
                    self._collection, field, field_schema=models.PayloadSchemaType.INTEGER
                )
        logger.info("Created Qdrant collection %s (dense size %d)", self._collection, dense_size)

    def _check_dense_size(self, dense_size: int) -> None:
        info = self._client.get_collection(self._collection)
        vectors = info.config.params.vectors
        existing = vectors[DENSE_VECTOR].size if isinstance(vectors, dict) else None
        if existing != dense_size:
            raise RuntimeError(
                f"Collection {self._collection!r} has dense size {existing}, but the configured "
                f"model produces {dense_size}. Use a new QDRANT_COLLECTION or re-ingest."
            )

    def drop_collection(self) -> None:
        """Delete the whole collection (used by the evaluation to start clean)."""
        with _qdrant_errors():
            if self._client.collection_exists(self._collection):
                self._client.delete_collection(self._collection)

    def ping(self) -> bool:
        """True when Qdrant answers."""
        try:
            self._client.get_collections()
        except Exception:
            return False
        return True

    def upsert(self, payloads: list[ChunkPayload], vectors: list[EmbeddedText]) -> None:
        """Store chunks with their vectors."""
        points = [
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    DENSE_VECTOR: vector.dense,
                    SPARSE_VECTOR: models.SparseVector(
                        indices=vector.sparse.indices, values=vector.sparse.values
                    ),
                },
                payload=asdict(payload),
            )
            for payload, vector in zip(payloads, vectors, strict=True)
        ]
        with _qdrant_errors():
            for start in range(0, len(points), UPSERT_BATCH):
                self._client.upsert(self._collection, points[start : start + UPSERT_BATCH])

    def delete_document(self, doc_id: int) -> None:
        """Remove every vector of one document."""
        self._delete(models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)))

    def delete_drawer(self, drawer_id: int) -> None:
        """Remove every vector of every document in a drawer."""
        self._delete(
            models.FieldCondition(key="drawer_id", match=models.MatchValue(value=drawer_id))
        )

    def _delete(self, condition: models.FieldCondition) -> None:
        with _qdrant_errors():
            self._client.delete(
                self._collection,
                points_selector=models.FilterSelector(filter=models.Filter(must=[condition])),
            )

    def count(self, doc_id: int | None = None) -> int:
        """Number of stored chunks, optionally for one document."""
        query_filter = None
        if doc_id is not None:
            query_filter = models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
            )
        with _qdrant_errors():
            return self._client.count(self._collection, count_filter=query_filter).count

    def document_chunks(self, doc_id: int) -> list[ChunkPayload]:
        """All chunks of a document in reading order."""
        query_filter = models.Filter(
            must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
        )
        chunks: list[ChunkPayload] = []
        offset = None
        with _qdrant_errors():
            while True:
                points, offset = self._client.scroll(
                    self._collection,
                    scroll_filter=query_filter,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                chunks.extend(ChunkPayload(**p.payload) for p in points if p.payload)
                if offset is None:
                    break
        return sorted(chunks, key=lambda c: c.chunk_index)

    def search(
        self,
        queries: list[EmbeddedText],
        mode: SearchMode,
        query_filter: models.Filter | None,
        limit: int,
        prefetch_limit: int,
    ) -> list[ScoredChunk]:
        """Search by dense vector, BM25, or both, for one or more phrasings of a question.

        Every (phrasing, vector type) pair is one prefetch; several prefetches are fused
        with Reciprocal Rank Fusion inside Qdrant. A Bosnian question and its English
        translation are searched together this way.
        """
        prefetches = []
        for query in queries:
            sparse = models.SparseVector(indices=query.sparse.indices, values=query.sparse.values)
            if mode in ("hybrid", "dense"):
                prefetches.append(
                    models.Prefetch(
                        query=query.dense,
                        using=DENSE_VECTOR,
                        filter=query_filter,
                        limit=prefetch_limit,
                    )
                )
            if mode in ("hybrid", "bm25"):
                prefetches.append(
                    models.Prefetch(
                        query=sparse, using=SPARSE_VECTOR, filter=query_filter, limit=prefetch_limit
                    )
                )
        with _qdrant_errors():
            if len(prefetches) == 1:
                single = prefetches[0]
                response = self._client.query_points(
                    self._collection,
                    query=single.query,
                    using=single.using,
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                )
            else:
                response = self._client.query_points(
                    self._collection,
                    prefetch=prefetches,
                    query=models.RrfQuery(rrf=models.Rrf()),
                    limit=limit,
                    with_payload=True,
                )
        return [
            ScoredChunk(ChunkPayload(**point.payload), point.score)
            for point in response.points
            if point.payload
        ]
