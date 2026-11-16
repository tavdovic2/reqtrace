"""FastAPI application: routes only translate HTTP to service calls."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, File, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from app import documents, export, requirements
from app.config import Settings
from app.errors import ReqTraceError
from app.llm import LLMOutputError, LLMUnavailableError
from app.qa import answer_question
from app.schemas import (
    AskRequest,
    AskResponse,
    DocumentOut,
    DrawerCreate,
    DrawerOut,
    DrawerUpdate,
    ErrorResponse,
    HealthResponse,
    RequirementsResponse,
)
from app.services import Services, build_services
from app.vector_store import VectorStoreUnavailableError

logger = logging.getLogger("reqtrace")

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


def get_services(request: Request) -> Services:
    """FastAPI dependency returning the application's service container."""
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


async def _cleanup_loop(services: Services) -> None:
    interval = services.settings.cleanup_interval_minutes * 60
    while True:
        try:
            await asyncio.to_thread(documents.delete_expired_documents, services)
        except Exception:
            logger.exception("Cleanup of expired documents failed")
        await asyncio.sleep(interval)


def _prepare_storage(services: Services) -> None:
    interrupted = documents.fail_interrupted_ingestions(services)
    if interrupted:
        logger.warning("Marked %d interrupted ingestions as failed", interrupted)
    if requirements.fail_interrupted_extractions(services):
        logger.warning("Marked interrupted requirement extractions as failed")
    try:
        services.store.ensure_collection(services.embedder.dense_size)
    except VectorStoreUnavailableError:
        logger.warning("Qdrant is not reachable yet; the collection is created on first upload")


def create_app(services: Services | None = None, run_background_jobs: bool = True) -> FastAPI:
    """Build the FastAPI app; tests pass their own services and skip the cleanup loop."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if getattr(app.state, "services", None) is None:
            app.state.services = build_services(Settings())
        _prepare_storage(app.state.services)
        cleanup_task = warm_up_task = None
        if run_background_jobs:
            cleanup_task = asyncio.create_task(_cleanup_loop(app.state.services))
            warm_up_task = asyncio.create_task(
                asyncio.to_thread(documents.warm_up, app.state.services)
            )
        yield
        if warm_up_task is not None and not warm_up_task.done():
            warm_up_task.cancel()
        if cleanup_task is not None:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task

    app = FastAPI(
        title="reqTrace",
        description="Answers questions from technical documents with page citations and "
        "extracts verified requirements.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.services = services
    origins = services.settings.cors_origins if services else Settings().cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_error_handlers(app)
    _register_routes(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ReqTraceError)
    async def _domain_error(_request: Request, exc: ReqTraceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.exception_handler(VectorStoreUnavailableError)
    async def _qdrant_down(_request: Request, exc: VectorStoreUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(LLMUnavailableError)
    async def _llm_down(_request: Request, exc: LLMUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(LLMOutputError)
    async def _llm_bad_output(_request: Request, exc: LLMOutputError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})


def _register_routes(app: FastAPI) -> None:
    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health(services: ServicesDep) -> HealthResponse:
        """Status of the API, Qdrant and Ollama."""
        llm = services.llm.health()
        return HealthResponse(
            api="ok",
            qdrant=services.store.ping(),
            ollama=llm.reachable,
            llm_model=services.settings.llm_model,
            llm_model_available=llm.model_available,
        )

    @app.post(
        "/drawers",
        response_model=DrawerOut,
        status_code=status.HTTP_201_CREATED,
        responses={409: {"model": ErrorResponse}},
        tags=["drawers"],
    )
    def create_drawer(body: DrawerCreate, services: ServicesDep) -> DrawerOut:
        """Create a drawer."""
        return documents.create_drawer(services, body.name, body.is_temporary)

    @app.get("/drawers", response_model=list[DrawerOut], tags=["drawers"])
    def list_drawers(services: ServicesDep) -> list[DrawerOut]:
        """List drawers with document counts."""
        return documents.list_drawers(services)

    @app.patch(
        "/drawers/{drawer_id}",
        response_model=DrawerOut,
        responses={**ERROR_RESPONSES, 409: {"model": ErrorResponse}},
        tags=["drawers"],
    )
    def rename_drawer(drawer_id: int, body: DrawerUpdate, services: ServicesDep) -> DrawerOut:
        """Rename a drawer."""
        return documents.rename_drawer(services, drawer_id, body.name)

    @app.delete(
        "/drawers/{drawer_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=ERROR_RESPONSES,
        tags=["drawers"],
    )
    def delete_drawer(drawer_id: int, services: ServicesDep) -> None:
        """Delete a drawer with its documents, vectors and files."""
        documents.delete_drawer(services, drawer_id)

    @app.post(
        "/drawers/{drawer_id}/documents",
        response_model=DocumentOut,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            **ERROR_RESPONSES,
            400: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            415: {"model": ErrorResponse},
        },
        tags=["documents"],
    )
    async def upload_document(
        drawer_id: int,
        services: ServicesDep,
        background: BackgroundTasks,
        file: Annotated[UploadFile, File(description="A PDF file")],
    ) -> DocumentOut:
        """Upload a PDF; ingestion continues in the background (poll the document list)."""
        data = await documents.read_upload(file, services.settings.max_upload_bytes)
        doc_id = await asyncio.to_thread(
            documents.store_upload, services, drawer_id, file.filename, data
        )
        background.add_task(documents.ingest_document, services, doc_id)
        return documents.get_document(services, doc_id)

    @app.get(
        "/drawers/{drawer_id}/documents",
        response_model=list[DocumentOut],
        responses=ERROR_RESPONSES,
        tags=["documents"],
    )
    def list_documents(drawer_id: int, services: ServicesDep) -> list[DocumentOut]:
        """List documents in a drawer."""
        return documents.list_documents(services, drawer_id)

    @app.get(
        "/documents/{doc_id}/file",
        response_class=FileResponse,
        responses=ERROR_RESPONSES,
        tags=["documents"],
    )
    def get_document_file(doc_id: int, services: ServicesDep) -> FileResponse:
        """Return the stored PDF."""
        path, filename = documents.get_document_file(services, doc_id)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=filename,
            content_disposition_type="inline",
        )

    @app.delete(
        "/documents/{doc_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=ERROR_RESPONSES,
        tags=["documents"],
    )
    def delete_document(doc_id: int, services: ServicesDep) -> None:
        """Delete a document with its vectors and file."""
        documents.delete_document(services, doc_id)

    @app.post(
        "/ask",
        response_model=AskResponse,
        responses={**ERROR_RESPONSES, 502: {"model": ErrorResponse}},
        tags=["questions"],
    )
    def ask(body: AskRequest, services: ServicesDep) -> AskResponse:
        """Answer a question from the documents; no drawer_id or doc_ids searches everything."""
        return answer_question(services, body)

    @app.post(
        "/documents/{doc_id}/requirements",
        response_model=RequirementsResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={**ERROR_RESPONSES, 409: {"model": ErrorResponse}},
        tags=["requirements"],
    )
    def extract_requirements(
        doc_id: int, services: ServicesDep, background: BackgroundTasks
    ) -> RequirementsResponse:
        """Start requirement extraction; poll the GET endpoint for progress and results."""
        requirements.start_extraction(services, doc_id)
        background.add_task(requirements.run_extraction, services, doc_id)
        return requirements.get_requirements(services, doc_id)

    @app.get(
        "/documents/{doc_id}/requirements",
        response_model=RequirementsResponse,
        responses={
            **ERROR_RESPONSES,
            200: {
                "content": {
                    "text/csv": {},
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                }
            },
        },
        tags=["requirements"],
    )
    def get_requirements(
        doc_id: int,
        services: ServicesDep,
        format: Annotated[Literal["json", "csv", "xlsx"], Query()] = "json",
    ) -> RequirementsResponse | Response:
        """The requirement table as JSON, or as a CSV / Excel download."""
        table = requirements.get_requirements(services, doc_id)
        if format == "json":
            return table
        stem = Path(documents.get_document(services, doc_id).filename).stem
        if format == "csv":
            content: str | bytes = export.to_csv(table.requirements)
            media_type = "text/csv; charset=utf-8"
        else:
            content = export.to_xlsx(table.requirements)
            media_type = XLSX_MEDIA_TYPE
        return Response(
            content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{stem}_requirements.{format}"'},
        )


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
