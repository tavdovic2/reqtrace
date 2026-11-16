"""Shared fixtures: an app wired with in-memory Qdrant and offline fakes."""

import hashlib
import math
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from pydantic import BaseModel
from qdrant_client import QdrantClient

from app.config import Settings
from app.db import create_session_factory
from app.embeddings import EmbeddedText, SparseVector
from app.ingestion import Block
from app.llm import LLMClient, LLMHealth
from app.main import create_app
from app.pdf import extract_page_text
from app.services import Services
from app.vector_store import VectorStore

DENSE_SIZE = 64
TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def _bucket(token: str, size: int) -> int:
    return int(hashlib.md5(token.encode()).hexdigest(), 16) % size


class HashEmbedder:
    """Deterministic bag-of-words embedder: texts sharing words get similar vectors."""

    dense_size = DENSE_SIZE

    def _embed(self, text: str) -> EmbeddedText:
        dense = [0.0] * DENSE_SIZE
        counts: dict[int, float] = {}
        for token in _tokens(text):
            dense[_bucket(token, DENSE_SIZE)] += 1.0
            index = _bucket(token, 2**20)
            counts[index] = counts.get(index, 0.0) + 1.0
        norm = math.sqrt(sum(v * v for v in dense)) or 1.0
        dense = [v / norm for v in dense]
        if not any(dense):
            dense[0] = 1.0
        return EmbeddedText(dense, SparseVector(list(counts), list(counts.values())))

    def embed_passages(self, texts: list[str]) -> list[EmbeddedText]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> EmbeddedText:
        return self._embed(text)


def pdfium_parser(path: Path, on_page: Callable[[int, int], None] | None = None) -> list[Block]:
    """Offline stand-in for Docling: one heading per page plus the page's text."""
    from app.pdf import read_page_count

    blocks: list[Block] = []
    total = read_page_count(path)
    for page in range(1, total + 1):
        if on_page is not None:
            on_page(page, total)
        text = extract_page_text(path, page)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        blocks.append(Block("heading", lines[0], page, level=1))
        blocks.append(Block("text", "\n".join(lines[1:]) or lines[0], page))
    return blocks


class FakeLLM(LLMClient):
    """Returns whatever the test queues; records prompts so tests can inspect them."""

    def __init__(self) -> None:
        self.structured_replies: list[BaseModel | Callable[[str], BaseModel]] = []
        self.prompts: list[str] = []
        self.systems: list[str] = []
        self.text_reply = "ok"

    def generate(self, system: str, prompt: str, temperature: float = 0.0) -> str:
        self.prompts.append(prompt)
        return self.text_reply

    def generate_structured[T: BaseModel](self, system: str, prompt: str, schema: type[T]) -> T:
        self.prompts.append(prompt)
        self.systems.append(system)
        reply = self.structured_replies.pop(0) if self.structured_replies else None
        if callable(reply):
            reply = reply(prompt)
        if reply is None:
            raise AssertionError("FakeLLM received an unexpected call")
        return schema.model_validate(reply.model_dump())

    def health(self) -> LLMHealth:
        return LLMHealth(reachable=True, model_available=True)


def make_pdf(path: Path, pages: list[list[str]]) -> Path:
    """Write a simple PDF: each inner list holds the lines of one page."""
    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    for lines in pages:
        pdf.add_page()
        for line in lines:
            pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        qdrant_collection="test_chunks",
        max_upload_mb=1,
        chunk_max_chars=400,
        chunk_overlap_chars=50,
    )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def services(settings: Settings, fake_llm: FakeLLM) -> Services:
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    return Services(
        settings=settings,
        sessions=create_session_factory(settings.resolved_database_url),
        store=VectorStore(QdrantClient(":memory:"), settings.qdrant_collection),
        embedder=HashEmbedder(),
        llm=fake_llm,
        parser=pdfium_parser,
    )


@pytest.fixture
def client(services: Services) -> Iterator[TestClient]:
    app = create_app(services, run_background_jobs=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    return make_pdf(
        tmp_path / "sensor.pdf",
        [
            [
                "General Description",
                "The TH-200 is a digital temperature and humidity sensor.",
                "It communicates over an I2C interface at up to 400 kHz.",
            ],
            [
                "Absolute Maximum Ratings",
                "The maximum operating temperature is 85 C.",
                "The supply voltage shall not exceed 3.6 V.",
            ],
            [
                "Ordering Information",
                "The TH-200 is delivered in a 3 x 3 mm DFN package.",
            ],
        ],
    )


def upload(client: TestClient, drawer_id: int, path: Path) -> dict:
    with path.open("rb") as handle:
        response = client.post(
            f"/drawers/{drawer_id}/documents",
            files={"file": (path.name, handle, "application/pdf")},
        )
    assert response.status_code == 202, response.text
    return response.json()
