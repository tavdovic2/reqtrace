"""Drawers and documents: create, list, rename, delete, upload, ingest and expire.

Deleting always removes all three traces of a document: the SQLite row (with its
requirements), the vectors in Qdrant and the stored PDF.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Document, DocumentStatus, Drawer, session_scope, utcnow
from app.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from app.ingestion import IngestionError, chunk_blocks, unique_chunks
from app.noise import NoiseSettings, filter_noise
from app.pdf import PDF_MAGIC, InvalidPdfError, read_page_count
from app.schemas import DocumentOut, DrawerOut
from app.services import Services
from app.vector_store import ChunkPayload, VectorStoreUnavailableError

logger = logging.getLogger(__name__)

READ_BLOCK = 1024 * 1024
EMBED_BATCH = 16


def _get_drawer(session: Session, drawer_id: int) -> Drawer:
    drawer = session.get(Drawer, drawer_id)
    if drawer is None:
        raise NotFoundError(f"Drawer {drawer_id} does not exist.")
    return drawer


def _get_document(session: Session, doc_id: int) -> Document:
    document = session.get(Document, doc_id)
    if document is None:
        raise NotFoundError(f"Document {doc_id} does not exist.")
    return document


def _ensure_unique_name(session: Session, name: str, exclude_id: int | None = None) -> None:
    query = select(Drawer.id).where(func.lower(Drawer.name) == name.lower())
    if exclude_id is not None:
        query = query.where(Drawer.id != exclude_id)
    if session.scalar(query) is not None:
        raise ConflictError(f"A drawer named {name!r} already exists.")


def _drawer_out(drawer: Drawer, document_count: int) -> DrawerOut:
    return DrawerOut(
        id=drawer.id,
        name=drawer.name,
        is_temporary=drawer.is_temporary,
        created_at=drawer.created_at,
        document_count=document_count,
    )


def file_path(services: Services, doc_id: int) -> Path:
    """Where the PDF of a document is stored."""
    return services.settings.uploads_dir / f"{doc_id}.pdf"


def create_drawer(services: Services, name: str, is_temporary: bool) -> DrawerOut:
    """Create a drawer; names are unique regardless of letter case."""
    with session_scope(services.sessions) as session:
        _ensure_unique_name(session, name)
        drawer = Drawer(name=name, is_temporary=is_temporary)
        session.add(drawer)
        session.flush()
        return _drawer_out(drawer, 0)


def list_drawers(services: Services) -> list[DrawerOut]:
    """All drawers with their document counts, temporary drawers first."""
    with session_scope(services.sessions) as session:
        counts = dict(
            session.execute(
                select(Document.drawer_id, func.count(Document.id)).group_by(Document.drawer_id)
            ).all()
        )
        drawers = session.scalars(
            select(Drawer).order_by(Drawer.is_temporary.desc(), Drawer.name)
        ).all()
        return [_drawer_out(d, counts.get(d.id, 0)) for d in drawers]


def rename_drawer(services: Services, drawer_id: int, name: str) -> DrawerOut:
    """Rename a drawer."""
    with session_scope(services.sessions) as session:
        drawer = _get_drawer(session, drawer_id)
        _ensure_unique_name(session, name, exclude_id=drawer_id)
        drawer.name = name
        count = session.scalar(
            select(func.count(Document.id)).where(Document.drawer_id == drawer_id)
        )
        return _drawer_out(drawer, count or 0)


def delete_drawer(services: Services, drawer_id: int) -> None:
    """Delete a drawer together with its documents, vectors and files."""
    with session_scope(services.sessions) as session:
        drawer = _get_drawer(session, drawer_id)
        doc_ids = [d.id for d in drawer.documents]
        services.store.delete_drawer(drawer_id)
        session.delete(drawer)
    for doc_id in doc_ids:
        file_path(services, doc_id).unlink(missing_ok=True)
    logger.info("Deleted drawer %d with %d documents", drawer_id, len(doc_ids))


def list_documents(services: Services, drawer_id: int) -> list[DocumentOut]:
    """Documents in a drawer, newest first."""
    with session_scope(services.sessions) as session:
        _get_drawer(session, drawer_id)
        documents = session.scalars(
            select(Document)
            .where(Document.drawer_id == drawer_id)
            .order_by(Document.created_at.desc(), Document.id.desc())
        ).all()
        return [DocumentOut.model_validate(d) for d in documents]


def get_document(services: Services, doc_id: int) -> DocumentOut:
    """One document's metadata."""
    with session_scope(services.sessions) as session:
        return DocumentOut.model_validate(_get_document(session, doc_id))


def get_document_file(services: Services, doc_id: int) -> tuple[Path, str]:
    """Path and original filename of a stored PDF."""
    with session_scope(services.sessions) as session:
        document = _get_document(session, doc_id)
        filename = document.filename
    path = file_path(services, doc_id)
    if not path.exists():
        raise NotFoundError(f"The file for document {doc_id} is missing.")
    return path, filename


def delete_document(services: Services, doc_id: int) -> None:
    """Delete a document's row, requirements, vectors and file."""
    with session_scope(services.sessions) as session:
        document = _get_document(session, doc_id)
        services.store.delete_document(doc_id)
        session.delete(document)
    file_path(services, doc_id).unlink(missing_ok=True)
    logger.info("Deleted document %d", doc_id)


async def read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an upload, stopping as soon as it exceeds the size limit."""
    data = bytearray()
    while block := await upload.read(READ_BLOCK):
        data.extend(block)
        if len(data) > max_bytes:
            raise PayloadTooLargeError(
                f"The file is larger than the limit of {max_bytes // (1024 * 1024)} MB."
            )
    return bytes(data)


def _clean_filename(filename: str | None) -> str:
    name = Path(filename or "document.pdf").name.strip() or "document.pdf"
    return name[:255]


def store_upload(services: Services, drawer_id: int, filename: str | None, data: bytes) -> int:
    """Validate a PDF upload, store it and create its document row (status: processing).

    Returns the new document id; ingestion runs separately in the background.
    """
    name = _clean_filename(filename)
    if not data:
        raise BadRequestError("The uploaded file is empty.")
    if not name.lower().endswith(".pdf") or not data.startswith(PDF_MAGIC):
        raise UnsupportedMediaTypeError("Only PDF files can be uploaded.")

    uploads = services.settings.uploads_dir
    uploads.mkdir(parents=True, exist_ok=True)
    staging = uploads / f"upload-{uuid.uuid4().hex}.tmp"
    staging.write_bytes(data)
    try:
        page_count = read_page_count(staging)
        with session_scope(services.sessions) as session:
            drawer = _get_drawer(session, drawer_id)
            expires_at = None
            if drawer.is_temporary:
                expires_at = utcnow() + timedelta(hours=services.settings.temporary_ttl_hours)
            document = Document(
                drawer_id=drawer_id,
                filename=name,
                page_count=page_count,
                status=DocumentStatus.PROCESSING,
                expires_at=expires_at,
            )
            session.add(document)
            session.flush()
            doc_id = document.id
        staging.replace(file_path(services, doc_id))
    except InvalidPdfError as exc:
        raise BadRequestError(str(exc)) from exc
    finally:
        staging.unlink(missing_ok=True)
    return doc_id


def ingest_document(services: Services, doc_id: int) -> None:
    """Parse, chunk, embed and index a stored PDF, then mark it ready or failed.

    Failures are recorded on the document (and partial vectors removed) instead of
    raised, because this runs in the background after the upload response.
    """
    with session_scope(services.sessions) as session:
        document = _get_document(session, doc_id)
        drawer_id, filename = document.drawer_id, document.filename
    try:
        with services.ingestion_lock:
            chunk_count = _index_document(services, doc_id, drawer_id, filename)
        if not _finish_ingestion(services, doc_id, DocumentStatus.READY, None):
            services.store.delete_document(doc_id)
            logger.info("Document %d was deleted during ingestion; vectors removed", doc_id)
            return
        logger.info("Ingested document %d (%s): %d chunks", doc_id, filename, chunk_count)
    except (IngestionError, VectorStoreUnavailableError) as exc:
        _fail_ingestion(services, doc_id, str(exc))
    except Exception as exc:
        logger.exception("Unexpected ingestion failure for document %d", doc_id)
        _fail_ingestion(services, doc_id, f"Unexpected error during ingestion: {exc}")


def _record_pages(services: Services, doc_id: int, done: int | None) -> None:
    """Store the parsing progress; None marks parsing as finished (all pages read)."""
    with session_scope(services.sessions) as session:
        document = session.get(Document, doc_id)
        if document is not None:
            document.ingest_pages_done = document.page_count if done is None else done


def warm_up(services: Services) -> None:
    """Load the parser and embedding models in the background after startup.

    Loading takes tens of seconds on CPU; without this the first upload after a
    container start pays for it. The models come from the volume cache, not the network.
    """
    started = time.monotonic()
    try:
        warm = getattr(services.parser, "warm_up", None)
        if warm is not None:
            warm()
        services.embedder.embed_query("warm-up")
    except Exception:
        logger.exception("Model warm-up failed; models will load on first use")
        return
    logger.info("Models loaded in %.1f s", time.monotonic() - started)


@dataclass
class IngestTimings:
    """Seconds per ingestion phase, for the log and the ingestion benchmark."""

    chunks: int = 0
    pages: int = 0
    parse: float = 0.0
    chunking: float = 0.0
    dense: float = 0.0
    sparse: float = 0.0
    upsert: float = 0.0
    total: float = 0.0

    def line(self) -> str:
        return (
            f"parse {self.parse:.1f} s ({self.pages} pages), chunking and filtering "
            f"{self.chunking:.1f} s, dense embedding {self.dense:.1f} s, sparse embedding "
            f"{self.sparse:.1f} s ({self.chunks} chunks), upsert {self.upsert:.1f} s, "
            f"total {self.total:.1f} s"
        )


def _index_document(services: Services, doc_id: int, drawer_id: int, filename: str) -> int:
    return index_document(services, doc_id, drawer_id, filename).chunks


def index_document(services: Services, doc_id: int, drawer_id: int, filename: str) -> IngestTimings:
    """Parse, filter, chunk, embed and upsert one stored PDF; returns the phase timings."""
    settings = services.settings
    timings = IngestTimings()
    started = time.monotonic()
    blocks = services.parser(
        file_path(services, doc_id),
        on_page=lambda done, _total: _record_pages(services, doc_id, done),
    )
    parsed = time.monotonic()
    timings.parse = parsed - started
    timings.pages = max((block.page for block in blocks), default=0)
    _record_pages(services, doc_id, None)
    noise = filter_noise(
        blocks,
        NoiseSettings(
            settings.ingest_min_words,
            settings.ingest_repeated_min_pages,
            settings.ingest_repeated_max_chars,
        ),
    )
    logger.info(
        "Document %d: dropped %d repeated header/footer blocks and %d blocks without words",
        doc_id,
        noise.repeated,
        noise.low_content,
    )
    chunks = unique_chunks(
        chunk_blocks(noise.blocks, settings.chunk_max_chars, settings.chunk_overlap_chars)
    )
    timings.chunking = time.monotonic() - parsed
    timings.chunks = len(chunks)
    if not chunks:
        raise IngestionError(
            "No extractable text was found. The PDF is probably scanned; OCR is not supported."
        )
    services.store.ensure_collection(services.embedder.dense_size)
    model_timings = getattr(services.embedder, "timings", None)
    if model_timings is not None:
        model_timings.dense = model_timings.sparse = 0.0
    embed_seconds = 0.0
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        step = time.monotonic()
        vectors = services.embedder.embed_passages([c.embedding_text for c in batch])
        embed_seconds += time.monotonic() - step
        payloads = [
            ChunkPayload(
                doc_id=doc_id,
                drawer_id=drawer_id,
                filename=filename,
                page=chunk.page,
                section=chunk.section,
                kind=chunk.kind,
                text=chunk.text,
                chunk_index=start + offset,
            )
            for offset, chunk in enumerate(batch)
        ]
        step = time.monotonic()
        services.store.upsert(payloads, vectors)
        timings.upsert += time.monotonic() - step
    if model_timings is not None:
        timings.dense, timings.sparse = model_timings.dense, model_timings.sparse
    else:
        timings.dense = embed_seconds
    timings.total = time.monotonic() - started
    logger.info("Document %d timing: %s", doc_id, timings.line())
    return timings


def _finish_ingestion(services: Services, doc_id: int, status: str, error: str | None) -> bool:
    with session_scope(services.sessions) as session:
        document = session.get(Document, doc_id)
        if document is None:
            return False
        document.status = status
        document.error = error
        return True


def _fail_ingestion(services: Services, doc_id: int, message: str) -> None:
    logger.warning("Ingestion of document %d failed: %s", doc_id, message)
    try:
        services.store.delete_document(doc_id)
    except VectorStoreUnavailableError:
        logger.warning("Could not remove partial vectors of document %d", doc_id)
    _finish_ingestion(services, doc_id, DocumentStatus.FAILED, message)


def delete_expired_documents(services: Services) -> int:
    """Delete documents whose expiry time has passed; returns how many were deleted."""
    with session_scope(services.sessions) as session:
        expired = session.scalars(
            select(Document.id).where(
                Document.expires_at.is_not(None), Document.expires_at <= utcnow()
            )
        ).all()
    deleted = 0
    for doc_id in expired:
        try:
            delete_document(services, doc_id)
            deleted += 1
        except NotFoundError:
            continue
        except VectorStoreUnavailableError:
            logger.warning("Cleanup postponed for document %d: Qdrant unavailable", doc_id)
    if deleted:
        logger.info("Cleanup removed %d expired documents", deleted)
    return deleted


def fail_interrupted_ingestions(services: Services) -> int:
    """Mark documents left in 'processing' by a previous run as failed."""
    with session_scope(services.sessions) as session:
        stuck = session.scalars(
            select(Document).where(Document.status == DocumentStatus.PROCESSING)
        ).all()
        for document in stuck:
            document.status = DocumentStatus.FAILED
            document.error = "Ingestion was interrupted by a restart. Delete and upload again."
        return len(stuck)
