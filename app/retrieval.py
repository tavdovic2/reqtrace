"""Search over indexed chunks, restricted to a drawer or a set of documents."""

from qdrant_client import models

from app.services import Services
from app.vector_store import ScoredChunk, SearchMode
from app.verification import normalize


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
    """The best `limit` distinct chunks, with at most `max_per_page` from any one page.

    Candidates arrive in fused rank order. A chunk is skipped when its point or its
    text was already picked: the same PDF uploaded to two drawers is indexed twice,
    and its identical chunks rank next to each other, so without this one passage can
    take three of the eight slots. The page cap then keeps several chunks of one long
    section (for example the device modes on page 14) from pushing out the overview
    that states the headline value. Skipped chunks are replaced by the next distinct
    candidates.
    """
    picked: list[ScoredChunk] = []
    seen: set[str] = set()
    per_page: dict[tuple[int, int], int] = {}
    for chunk in chunks:
        keys = {"text:" + normalize(chunk.payload.text)}
        if chunk.point_id:
            keys.add("id:" + chunk.point_id)
        if keys & seen:
            continue
        key = (chunk.payload.doc_id, chunk.payload.page)
        if per_page.get(key, 0) >= max_per_page:
            continue
        seen |= keys
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
    # Fetch every fused candidate (each prefetch returns at most prefetch_limit points),
    # so enough distinct chunks remain after duplicates and the page cap are removed.
    candidates = services.store.search(
        queries,
        mode=mode,
        query_filter=build_filter(drawer_id, doc_ids),
        limit=max(top_k * 3, settings.retrieval_prefetch_limit * 2 * len(queries)),
        prefetch_limit=settings.retrieval_prefetch_limit,
    )
    return diversify(candidates, top_k, settings.retrieval_max_per_page)
