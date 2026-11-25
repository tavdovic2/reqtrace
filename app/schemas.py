"""Pydantic request and response models for the HTTP API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RequirementType = Literal[
    "functional",
    "performance",
    "electrical",
    "safety",
    "interface",
    "environmental",
    "process",
    "other",
]


# A person's decision on a requirement. Independent of `verified`, which is automatic.
ReviewStatus = Literal["proposed", "accepted", "rejected"]
# Filters of the requirement table: a status, or the reviewed rows that the latest
# extraction did not find again.
StatusFilter = Literal["proposed", "accepted", "rejected", "not_found"]
EDITED_TEXT_MAX = 5000
NOTE_MAX = 2000


class ErrorResponse(BaseModel):
    detail: str


class DrawerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    is_temporary: bool = False

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class DrawerUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class DrawerOut(BaseModel):
    id: int
    name: str
    is_temporary: bool
    created_at: datetime
    document_count: int


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    drawer_id: int
    filename: str
    page_count: int
    status: Literal["processing", "ready", "failed"]
    # While processing: pages parsed so far; equal to page_count once indexing starts.
    ingest_pages_done: int = 0
    error: str | None
    created_at: datetime
    expires_at: datetime | None
    extraction_status: Literal["idle", "running", "done", "failed"]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    drawer_id: int | None = None
    doc_ids: list[int] | None = Field(default=None, max_length=200)
    debug: bool = Field(
        default=False, description="Return the retrieved chunks and discard reasons."
    )

    @field_validator("question")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class Source(BaseModel):
    filename: str
    doc_id: int
    page: int
    snippet: str
    highlight: str | None = Field(
        default=None,
        description="The first of `highlights`, or null (kept for older clients).",
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Every evidence passage the answer relies on from this page, each exactly "
        "as it appears in the page text; passages that could not be found are left out.",
    )


class AnswerLine(BaseModel):
    """One claim of an answer, with the sources (indexes into `sources`) that state it."""

    text: str
    sources: list[int]


class RetrievedChunk(BaseModel):
    rank: int
    filename: str
    doc_id: int
    page: int
    section: str
    score: float
    snippet: str


class DiscardedClaim(BaseModel):
    statement: str
    reason: str
    # For clients that show the reason in another language.
    code: Literal["no_citation", "number_missing"]
    numbers: list[str] = Field(default_factory=list, description="Numbers not found.")


class AskDebug(BaseModel):
    """Why an answer looks the way it does; returned only when the request asks for it."""

    queries: list[str] = Field(description="The phrasings that were searched.")
    retrieved: list[RetrievedChunk]
    model_answered: bool | None = Field(description="What the model said before verification.")
    discarded: list[DiscardedClaim]


class AskResponse(BaseModel):
    answer: str
    answered: bool
    sources: list[Source]
    lines: list[AnswerLine] = Field(default_factory=list)
    debug: AskDebug | None = None


class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str = Field(description="REQ-### code; stable across re-extraction, never reused.")
    kind: Literal["obligation", "limit"]
    quote: str = Field(description="Verbatim span of the page text that states the requirement.")
    paraphrase: str = Field(
        description="The model's rewording of the quote; empty when it was not trustworthy "
        "or paraphrasing is turned off."
    )
    type: str
    page: int
    verified: bool = Field(description="Automatic: the quote was found on the cited page.")
    status: ReviewStatus = Field(description="A person's decision, independent of `verified`.")
    edited_text: str | None = Field(
        default=None, description="The requirement as a reviewer rewrote it; the quote is kept."
    )
    reviewer_note: str | None = None
    reviewed_at: datetime | None = Field(
        default=None, description="UTC time of the last status decision; null while proposed."
    )
    not_found: bool = Field(
        default=False,
        description="A reviewed row that the latest extraction did not find again.",
    )
    requirement_text: str = Field(
        default="",
        description="The text to use: the edited text, else the paraphrase, else the quote.",
    )


class RequirementUpdate(BaseModel):
    """A review change to one requirement; fields that are left out stay as they are."""

    status: ReviewStatus | None = None
    edited_text: str | None = Field(
        default=None,
        max_length=EDITED_TEXT_MAX,
        description="New requirement text; null removes the edit and restores the original.",
    )
    reviewer_note: str | None = Field(
        default=None, max_length=NOTE_MAX, description="A note; null or blank removes it."
    )

    @field_validator("edited_text")
    @classmethod
    def _edited_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError(
                "edited text must not be empty; send null to go back to the original text"
            )
        return value

    @field_validator("reviewer_note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @model_validator(mode="after")
    def _something_to_change(self) -> "RequirementUpdate":
        if not self.model_fields_set:
            raise ValueError("nothing to change: send status, edited_text or reviewer_note")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status must be one of proposed, accepted, rejected")
        return self


class BulkReview(BaseModel):
    """The same status for several requirements of one document, in one transaction."""

    ids: list[int] = Field(min_length=1, max_length=10000)
    status: ReviewStatus


class ReviewCounts(BaseModel):
    """Rows per review status. `not_found` rows are also counted under their status."""

    total: int = 0
    proposed: int = 0
    accepted: int = 0
    rejected: int = 0
    not_found: int = 0


class RequirementsResponse(BaseModel):
    document_id: int
    status: Literal["idle", "running", "done", "failed"]
    error: str | None
    done_chunks: int
    total_chunks: int
    rejected: int
    rejected_quote_not_found: int
    rejected_not_requirement: int = Field(
        description="Candidates that are neither an obligation nor a limit."
    )
    skipped_sections: int = Field(description="Sections and tables skipped before any model call.")
    paraphrases_removed: int
    elapsed_seconds: float | None = Field(
        default=None, description="Time spent so far (running) or in total (done)."
    )
    kept: int = Field(default=0, description="Rows the latest extraction found again.")
    added: int = Field(default=0, description="New rows from the latest extraction.")
    no_longer_found: int = Field(
        default=0, description="Reviewed rows the latest extraction did not find again (kept)."
    )
    counts: ReviewCounts = Field(
        default_factory=ReviewCounts, description="Rows per review status, before filtering."
    )
    requirements: list[RequirementOut]


class HealthResponse(BaseModel):
    api: Literal["ok"]
    qdrant: bool
    ollama: bool
    llm_model: str
    llm_model_available: bool
