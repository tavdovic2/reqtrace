"""Ingestion speed-ups: parse cache, parser options, length-sorted embedding batches."""

from pathlib import Path

import pytest

from app import documents
from app.config import Settings
from app.embeddings import EmbeddedText
from app.services import Services
from app.vector_store import ChunkPayload
from tests.conftest import HashEmbedder, make_pdf


def test_settings_have_safe_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.embedding_device == "cpu"


class RecordingEmbedder(HashEmbedder):
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed_passages(self, texts: list[str]) -> list[EmbeddedText]:
        self.batches.append(list(texts))
        return super().embed_passages(texts)


def test_embedding_batches_are_sorted_by_length_and_vectors_stay_with_their_chunk(
    services: Services, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = [f"Instruction {n}: " + "word " * (n * 7 % 23) for n in range(12)]
    pdf = make_pdf(tmp_path / "many.pdf", [[f"Page {p}", *lines] for p in range(1, 7)])
    embedder = RecordingEmbedder()
    services.embedder = embedder
    services.settings.embedding_batch_size = 2
    stored: list[tuple[ChunkPayload, EmbeddedText]] = []
    real_upsert = services.store.upsert

    def recording_upsert(payloads: list[ChunkPayload], vectors: list[EmbeddedText]) -> None:
        stored.extend(zip(payloads, vectors, strict=True))
        real_upsert(payloads, vectors)

    monkeypatch.setattr(services.store, "upsert", recording_upsert)
    drawer = documents.create_drawer(services, "Speed", False)
    doc_id = documents.store_upload(services, drawer.id, "many.pdf", pdf.read_bytes())
    timings = documents.index_document(services, doc_id, drawer.id, "many.pdf")

    sent = [text for batch in embedder.batches for text in batch]
    assert [len(t) for t in sent] == sorted(len(t) for t in sent)
    assert all(len(batch) <= 2 for batch in embedder.batches)
    assert [p.chunk_index for p, _ in stored] == list(range(timings.chunks))
    reference = HashEmbedder()
    for payload, vector in stored:
        text = f"{payload.section}\n{payload.text}" if payload.kind == "text" else payload.text
        assert vector.dense == reference.embed_query(text).dense
