"""Search over indexed chunks, restricted to a drawer or a set of documents."""

from qdrant_client import models

from app.services import Services
from app.vector_store import ScoredChunk, SearchMode


def build_filter(drawer_id: int | None, doc_ids: list[int] | None) -> models.Filter | None:
    """Qdrant filter for the requested scope; None means the whole cabinet."""
    conditions: list[models.Condition] = []
    if drawer_id is not None:
        conditions.append(
            models.FieldCondition(key="drawer_id", match=models.MatchValue(value=drawer_id))
        )
    if doc_ids:
        conditions.append(
            models.FieldCondition(key="doc_id", match=models.MatchAny(any=sorted(set(doc_ids))))
        )
    return models.Filter(must=conditions) if conditions else None


def diversify(chunks: list[ScoredChunk], limit: int, max_per_page: int) -> list[ScoredChunk]:
    """The best `limit` chunks, with at most `max_per_page` from any one document page.

    Without this, several chunks of one long section (for example the device modes
    on page 14) can fill the context and push out the overview that states the
    headline value.
    """
    picked: list[ScoredChunk] = []
    per_page: dict[tuple[int, int], int] = {}
    for chunk in chunks:
        key = (chunk.payload.doc_id, chunk.payload.page)
        if per_page.get(key, 0) >= max_per_page:
            continue
        per_page[key] = per_page.get(key, 0) + 1
        picked.append(chunk)
        if len(picked) == limit:
            break
    return picked


def retrieve(
    services: Services,
    question: str | list[str],
    drawer_id: int | None = None,
    doc_ids: list[int] | None = None,
    mode: SearchMode = "hybrid",
    limit: int | None = None,
) -> list[ScoredChunk]:
    """Return the best chunks for a question (top_k from settings unless `limit` is given).

    `question` may be several phrasings of the same question (for example the original
    and an English translation); they are searched together. Hybrid mode fuses dense and
    BM25 results with Reciprocal Rank Fusion inside Qdrant: BM25 catches exact part
    numbers and values, dense search catches paraphrases and other languages.
    """
    settings = services.settings
    phrasings = [question] if isinstance(question, str) else question
    queries = [services.embedder.embed_query(text) for text in phrasings]
    top_k = limit or settings.retrieval_top_k
    candidates = services.store.search(
        queries,
        mode=mode,
        query_filter=build_filter(drawer_id, doc_ids),
        limit=top_k * 3,
        prefetch_limit=settings.retrieval_prefetch_limit,
    )
    return diversify(candidates, top_k, settings.retrieval_max_per_page)
