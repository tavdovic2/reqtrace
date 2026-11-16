"""SQLite models (drawers, documents, requirements) and session handling."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String, Text, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


def utcnow() -> datetime:
    """Current time as a naive UTC datetime (SQLite stores no timezone)."""
    return datetime.now(UTC).replace(tzinfo=None)


class DocumentStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class ExtractionStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Base(DeclarativeBase):
    pass


class Drawer(Base):
    __tablename__ = "drawers"
    # Ids are never reused, so vectors and files keyed by id cannot attach to a new row.
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    is_temporary: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="drawer", cascade="all, delete-orphan", passive_deletes=True
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(primary_key=True)
    drawer_id: Mapped[int] = mapped_column(ForeignKey("drawers.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    page_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default=DocumentStatus.PROCESSING)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(default=None, index=True)

    # Pages parsed so far while the document is processing, for the progress display.
    ingest_pages_done: Mapped[int] = mapped_column(default=0, server_default="0")

    extraction_status: Mapped[str] = mapped_column(String(20), default=ExtractionStatus.IDLE)
    extraction_error: Mapped[str | None] = mapped_column(Text, default=None)
    extraction_done_chunks: Mapped[int] = mapped_column(default=0)
    extraction_total_chunks: Mapped[int] = mapped_column(default=0)
    requirements_rejected: Mapped[int] = mapped_column(default=0)
    rejected_quote_not_found: Mapped[int] = mapped_column(default=0, server_default="0")
    # Candidates that are neither an obligation nor a limit. The column keeps the name it
    # had in fix round 1 ("unsupported") so existing databases work.
    rejected_not_requirement: Mapped[int] = mapped_column(
        "rejected_unsupported", default=0, server_default="0"
    )
    extraction_skipped_sections: Mapped[int] = mapped_column(default=0, server_default="0")
    paraphrases_removed: Mapped[int] = mapped_column(default=0, server_default="0")
    extraction_started_at: Mapped[datetime | None] = mapped_column(default=None)
    extraction_finished_at: Mapped[datetime | None] = mapped_column(default=None)

    drawer: Mapped[Drawer] = relationship(back_populates="documents")
    requirements: Mapped[list["Requirement"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Requirement.id",
    )


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(20))
    # The model's rewording; the column keeps its original name so existing databases work.
    paraphrase: Mapped[str] = mapped_column("text", Text)
    kind: Mapped[str] = mapped_column(String(20), default="obligation", server_default="obligation")
    type: Mapped[str] = mapped_column(String(30))
    page: Mapped[int]
    quote: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    document: Mapped[Document] = relationship(back_populates="requirements")


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# Columns added after the first release. create_all() does not alter existing tables,
# so databases created by an older version get them here.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "documents": {
        "rejected_quote_not_found": "INTEGER NOT NULL DEFAULT 0",
        "rejected_unsupported": "INTEGER NOT NULL DEFAULT 0",
        "extraction_skipped_sections": "INTEGER NOT NULL DEFAULT 0",
        "paraphrases_removed": "INTEGER NOT NULL DEFAULT 0",
        "extraction_started_at": "DATETIME",
        "extraction_finished_at": "DATETIME",
        "ingest_pages_done": "INTEGER NOT NULL DEFAULT 0",
    },
    "requirements": {"kind": "VARCHAR(20) NOT NULL DEFAULT 'obligation'"},
}


def add_missing_columns(engine: Engine) -> None:
    """Add columns that newer versions introduced to tables created by older ones."""
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in ADDED_COLUMNS.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    """Create the engine, ensure the schema exists and return a session factory."""
    is_sqlite = database_url.startswith("sqlite")
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    if is_sqlite:
        _enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Unit of work: commit on success, roll back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
