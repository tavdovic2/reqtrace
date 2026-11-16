"""Pydantic request and response models for the HTTP API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
        description="The evidence passage exactly as it appears in the page text, if found.",
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

    code: str
    kind: Literal["obligation", "limit"]
    quote: str = Field(description="Verbatim span of the page text that states the requirement.")
    paraphrase: str = Field(
        description="The model's rewording of the quote; empty when it was not trustworthy "
        "or paraphrasing is turned off."
    )
    type: str
    page: int
    verified: bool


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
    requirements: list[RequirementOut]


class HealthResponse(BaseModel):
    api: Literal["ok"]
    qdrant: bool
    ollama: bool
    llm_model: str
    llm_model_available: bool
