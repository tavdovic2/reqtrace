"""Ingestion speed-ups: parse cache, parser options, length-sorted embedding batches."""

import os
from pathlib import Path

import pytest

from app import documents
from app.config import Settings
from app.embeddings import EmbeddedText
from app.ingestion import Block, DoclingParser, ParseCache, ParserOptions
from app.services import Services
from app.vector_store import ChunkPayload
from tests.conftest import HashEmbedder, make_pdf

BLOCKS = [Block("heading", "1 Features", 1), Block("text", "Use a 100-nF capacitor.", 2)]


class CountingParser(DoclingParser):
    """DoclingParser with the Docling call replaced, to test the cache around it."""

    def __init__(self, cache_dir: Path | None) -> None:
        super().__init__(ParserOptions(), cache_dir=cache_dir)
        self.parsed = 0

    def _parse(self, path: Path, on_page=None) -> list[Block]:  # type: ignore[no-untyped-def]
        self.parsed += 1
        return BLOCKS


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "a.pdf", [["1 Features", "Use a 100-nF capacitor."]])


def test_same_pdf_is_parsed_once_and_then_read_from_the_cache(tmp_path: Path, pdf: Path) -> None:
    parser = CountingParser(tmp_path / "cache")
    assert parser(pdf) == BLOCKS
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(pdf.read_bytes())  # same content, another name (another drawer)
    assert parser(copy) == BLOCKS
    assert parser.parsed == 1


def test_changed_content_or_settings_miss_the_cache(tmp_path: Path, pdf: Path) -> None:
    cache_dir = tmp_path / "cache"
    parser = CountingParser(cache_dir)
    parser(pdf)
    other = make_pdf(tmp_path / "b.pdf", [["2 Layout", "Place the sensor close."]])
    parser(other)
    assert parser.parsed == 2
    accurate = ParseCache(cache_dir, ParserOptions(table_mode="accurate").fingerprint())
    assert accurate.get(accurate.key(pdf)) is None


def test_cache_is_safe_to_delete_or_corrupt(tmp_path: Path, pdf: Path) -> None:
    cache_dir = tmp_path / "cache"
    parser = CountingParser(cache_dir)
    parser(pdf)
    for file in cache_dir.iterdir():
        file.write_text("{not json", encoding="utf-8")
    assert parser(pdf) == BLOCKS
    for file in cache_dir.iterdir():
        file.unlink()
    cache_dir.rmdir()
    assert parser(pdf) == BLOCKS
    assert parser.parsed == 3


def test_without_a_cache_directory_every_call_parses(pdf: Path) -> None:
    parser = CountingParser(None)
    parser(pdf)
    parser(pdf)
    assert parser.parsed == 2


def test_parser_options_resolve_threads_and_fall_back_to_the_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ParserOptions(threads=0).thread_count == (os.cpu_count() or 4)
    assert ParserOptions(threads=6).thread_count == 6
    assert ParserOptions(device="cpu").resolved_device() == "cpu"
    torch = pytest.importorskip("torch")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert ParserOptions(device="cuda").resolved_device() == "cpu"
    assert ParserOptions(device="auto").resolved_device() == "cpu"
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert ParserOptions(device="auto").resolved_device() == "cuda"


def test_settings_have_safe_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.parse_device == "cpu"
    assert settings.embedding_device == "cpu"
    assert settings.parse_cache_dir == settings.data_dir / "cache" / "parsed"


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


def test_embedding_on_the_cpu_names_the_cpu_provider_explicitly() -> None:
    from app.embeddings import FastEmbedEmbedder

    cpu = FastEmbedEmbedder("dense", "sparse", device="cpu")
    assert cpu._device_options() == {"providers": ["CPUExecutionProvider"]}
