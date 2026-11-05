import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.conversation import (
    TECHNICAL_ERROR_TEXTS,
    TurnFailed,
    handle_message,
    start_application,
)
from backend.llm import ENV_FILE, DeferredLLM, ScreeningLLM
from backend.rules import (
    COVERAGE_CATALOG,
    CandidateFields,
    CandidateSnapshot,
    CatalogError,
    NonBlank,
    Outcome,
    StrictModel,
)
from backend.storage import (
    ApplicationClosed,
    StorageFailure,
    TurnConflict,
    TurnPending,
    UnknownApplication,
    init_db,
    list_applications,
    load_application,
    load_application_with_turn,
    reminder_due_at,
    send_reminder,
    set_recruiter_decision,
)

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent

# Status and localized notice for each public error code. Nothing else reaches the browser.
PUBLIC_ERRORS = {
    "invalid_request": (
        422,
        {
            "es": "No hemos podido leer tu mensaje. Revísalo e inténtalo de nuevo.",
            "en": "We could not read your message. Please check it and try again.",
        },
    ),
    "application_not_found": (
        404,
        {
            "es": "No encontramos esta conversación. Puedes empezar una nueva.",
            "en": "We could not find this conversation. You can start a new one.",
        },
    ),
    "request_conflict": (
        409,
        {
            "es": "Este mensaje no coincide con el guardado. Revisa la conversación.",
            "en": "This message does not match the saved one. Please review the conversation.",
        },
    ),
    "turn_pending": (
        409,
        {
            "es": "Hay un mensaje pendiente de confirmar. Recupéralo antes de escribir otro.",
            "en": "A message is waiting for confirmation. Recover it before writing another.",
        },
    ),
    "application_closed": (
        409,
        {
            "es": "Esta conversación ya ha terminado.",
            "en": "This conversation has already ended.",
        },
    ),
    "service_configuration": (
        503,
        {
            "es": "El servicio no está disponible ahora mismo. Puedes volver a intentarlo más tarde.",
            "en": "The service is not available right now. You can try again later.",
        },
    ),
    "service_unavailable": (503, TECHNICAL_ERROR_TEXTS),
    "internal_error": (500, TECHNICAL_ERROR_TEXTS),
}

# Recruiter routes share the codes and statuses, with their own Spanish notices.
RECRUITER_NOTICES = {
    "invalid_request": "No hemos podido procesar esta petición. Revisa los datos e inténtalo de nuevo.",
    "application_not_found": "No encontramos esta candidatura.",
    "service_unavailable": "El servicio no está disponible ahora mismo. Inténtalo de nuevo.",
    "internal_error": "Ha habido un problema técnico. Inténtalo de nuevo.",
}

RecruiterDecision = Literal["unreviewed", "advance", "rejected"]


class MessageRequest(StrictModel):
    request_id: NonBlank
    text: NonBlank


class DecisionRequest(StrictModel):
    recruiter_decision: RecruiterDecision


def resolve_db_path() -> Path:
    load_dotenv(ENV_FILE, override=False)
    configured = os.environ.get("SCREENING_DB", "").strip()
    if not configured:
        return APP_DIR / "data" / "screening.db"
    return APP_DIR / configured  # An absolute path replaces APP_DIR.


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    if not COVERAGE_CATALOG:
        raise CatalogError("the coverage catalog is missing or empty")
    db_path = resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    fastapi_app.state.db_path = db_path
    yield


app = FastAPI(lifespan=lifespan)


def get_llm() -> ScreeningLLM:
    return DeferredLLM()


def _public_state(request: Request, application_id: str) -> dict:
    """An explicit allowlist: the summary, fields and recruiter decision never reach the browser."""
    stored, turn = load_application_with_turn(request.app.state.db_path, application_id)
    due_at = reminder_due_at(stored, turn)
    return {
        "id": stored.snapshot.id,
        "messages": [
            {
                "sequence": message.sequence,
                "role": message.role,
                "text": message.text,
                "created_at": message.created_at,
            }
            for message in stored.messages
        ],
        "language": stored.snapshot.language,
        "closed": stored.snapshot.closed_at is not None,
        "pending": turn
        and {"request_id": turn.request_id, "candidate_sequence": turn.candidate_sequence},
        "reminder_due_at": due_at and due_at.isoformat(),
    }


def _recruiter_item(snapshot: CandidateSnapshot) -> dict:
    fields = snapshot.fields
    return {
        "id": snapshot.id,
        "created_at": snapshot.created_at,
        "outcome": snapshot.outcome,
        "recruiter_decision": snapshot.recruiter_decision,
        "full_name": fields.full_name.normalized,
        "work_locations": fields.work_locations.model_dump(),
        "resolved_fields": sum(
            getattr(fields, name).status == "resolved" for name in CandidateFields.model_fields
        ),
    }


def _active_language(request: Request) -> str:
    """Spanish when the application cannot be read: a notice must never fail itself."""
    application_id = request.path_params.get("application_id")
    if application_id is None:
        return "es"
    try:
        return load_application(request.app.state.db_path, application_id).snapshot.language
    except Exception:
        return "es"


def _error(request: Request, code: str) -> JSONResponse:
    status, notices = PUBLIC_ERRORS[code]
    if request.url.path.startswith("/api/recruiter/"):
        notice = RECRUITER_NOTICES[code]
    else:
        notice = notices[_active_language(request)]
    return JSONResponse({"code": code, "notice": notice}, status_code=status)


def _handler_for(code: str):
    def handle(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("request_refused code=%s cause=%s", code, type(exc).__name__)
        return _error(request, code)

    return handle


for exception, public_code in {
    RequestValidationError: "invalid_request",
    UnknownApplication: "application_not_found",
    TurnConflict: "request_conflict",
    TurnPending: "turn_pending",
    ApplicationClosed: "application_closed",
    StorageFailure: "service_unavailable",
}.items():
    app.add_exception_handler(exception, _handler_for(public_code))


@app.exception_handler(TurnFailed)
def turn_failed(request: Request, exc: TurnFailed):
    logger.warning("turn_failed code=%s phase=%s", exc.code, exc.phase)
    code = "service_configuration" if exc.code == "llm_permanent" else "service_unavailable"
    return _error(request, code)


@app.exception_handler(StarletteHTTPException)
async def body_parsing_failure(request: Request, exc: StarletteHTTPException):
    """FastAPI answers 400 to a body it cannot decode, such as invalid UTF-8."""
    if exc.status_code == 400:
        return _error(request, "invalid_request")
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
def unexpected_failure(request: Request, exc: Exception):
    return _error(request, "internal_error")  # The server logs the traceback after this reply.


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/applications", status_code=201)
def create_application(request: Request):
    snapshot = start_application(request.app.state.db_path)
    return _public_state(request, snapshot.id)


@app.get("/api/applications/{application_id}")
def read_application(application_id: str, request: Request):
    return _public_state(request, application_id)


@app.post("/api/applications/{application_id}/messages")
def send_message(
    application_id: str,
    body: MessageRequest,
    request: Request,
    llm: ScreeningLLM = Depends(get_llm),
):
    handle_message(request.app.state.db_path, application_id, body.request_id, body.text, llm)
    return _public_state(request, application_id)


@app.post("/api/applications/{application_id}/reminder")
def request_reminder(application_id: str, request: Request):
    send_reminder(request.app.state.db_path, application_id)
    return _public_state(request, application_id)


@app.get("/api/recruiter/applications")
def recruiter_list(
    request: Request,
    outcome: Outcome | None = None,
    recruiter_decision: RecruiterDecision | None = None,
):
    snapshots = list_applications(request.app.state.db_path, outcome, recruiter_decision)
    return {"items": [_recruiter_item(snapshot) for snapshot in snapshots]}


@app.get("/api/recruiter/applications/{application_id}")
def recruiter_detail(application_id: str, request: Request):
    stored = load_application(request.app.state.db_path, application_id)
    snapshot = stored.snapshot
    return {
        "id": snapshot.id,
        "created_at": snapshot.created_at,
        "updated_at": snapshot.updated_at,
        "closed_at": snapshot.closed_at,
        "outcome": snapshot.outcome,
        "outcome_reasons": snapshot.outcome_reasons,
        "recruiter_decision": snapshot.recruiter_decision,
        "fields": snapshot.fields.model_dump(),
        "pending_questions": snapshot.pending_questions,
        "summary": snapshot.summary,
        "messages": [message.model_dump() for message in stored.messages],
    }


@app.patch("/api/recruiter/applications/{application_id}/decision")
def recruiter_decide(application_id: str, body: DecisionRequest, request: Request):
    saved = set_recruiter_decision(
        request.app.state.db_path, application_id, body.recruiter_decision
    )
    return {"id": saved.id, "recruiter_decision": saved.recruiter_decision}


frontend_dist = APP_DIR / "frontend" / "dist"

if (frontend_dist / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/recruiter", include_in_schema=False)
    def index():
        return FileResponse(frontend_dist / "index.html")
else:
    logger.warning(
        "Frontend build missing. Run 'npm ci' and 'npm run build' in frontend/, "
        "then restart Uvicorn. The API remains available at /api/health."
    )

    @app.get("/", include_in_schema=False)
    @app.get("/recruiter", include_in_schema=False)
    def index():
        raise HTTPException(
            status_code=503,
            detail="Frontend not built. Run npm ci and npm run build in frontend/, "
            "then restart Uvicorn.",
        )
