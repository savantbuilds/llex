"""The local HTTP API backing the editor window.

Threat model
------------
LLex runs a local HTTP server and renders it in a webview. Binding to
``127.0.0.1`` is **not** sufficient isolation: any web page the user visits in
another tab can issue requests to ``http://127.0.0.1:<port>``, and DNS
rebinding lets an attacker's domain resolve to the loopback address. Either
path would give a random web page the ability to read the open document or
write files to the user's disk.

Three defences apply to every route:

1. **Host header validation.** Requests whose ``Host`` is not a loopback name
   are rejected, which is what stops DNS rebinding.
2. **A per-session bearer token.** A 256-bit secret generated at startup is
   embedded into the served page and required on every ``/api`` call. Another
   process would have to read the user's memory to obtain it.
3. **No wildcard CORS.** Cross-origin JavaScript cannot read any response,
   because no ``Access-Control-Allow-Origin`` header is ever emitted.

Design
------
Services live on ``app.state`` rather than in module globals, so importing this
module has no side effects and tests can build an isolated app per case.
Failures raise :class:`fastapi.HTTPException` with a real status code instead
of being swallowed into ``200 OK`` with an error string, so the frontend can
distinguish "saved" from "failed" -- something it previously could not, which is
why the Save menu item reported "Saved!" even when the write failed.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import __version__
from .document import Document, DocumentError, DocumentFormatError, DocumentNotFoundError
from .export import SUPPORTED_FORMATS, Exporter, ExportError
from .llm import (
    AssistantResponseError,
    AssistantUnavailableError,
    Engine,
    LocalLLMBridge,
)

__all__ = ["API_TOKEN_HEADER", "AppServices", "build_app", "create_app"]

logger = logging.getLogger(__name__)

#: Header carrying the per-session bearer token. The name trips the
#: hardcoded-credential rule; no secret is embedded here.
API_TOKEN_HEADER: Final = "X-LLex-Token"  # noqa: S105

#: Hosts a request may legitimately arrive with. Anything else is a rebinding
#: attempt: the attacker controls the name that resolved to 127.0.0.1.
_ALLOWED_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})

#: Placeholder in the template, replaced with this session's token when the
#: shell is served. Not a credential.
_TOKEN_PLACEHOLDER: Final = "__LLEX_API_TOKEN__"  # noqa: S105

#: HTTP status codes used by this module, as plain integers. Starlette has
#: renamed several of these constants across releases, and a deprecation
#: warning here would fail the test suite for no benefit.
HTTP_BAD_REQUEST: Final = 400
HTTP_UNAUTHORIZED: Final = 401
HTTP_FORBIDDEN: Final = 403
HTTP_NOT_FOUND: Final = 404
HTTP_UNPROCESSABLE: Final = 422
HTTP_INTERNAL_ERROR: Final = 500
HTTP_NOT_IMPLEMENTED: Final = 501
HTTP_BAD_GATEWAY: Final = 502
HTTP_SERVICE_UNAVAILABLE: Final = 503

PACKAGE_DIR: Final = Path(__file__).resolve().parent
TEMPLATE_PATH: Final = PACKAGE_DIR / "templates" / "index.html"
STATIC_DIR: Final = PACKAGE_DIR / "static"
BUNDLE_PATH: Final = STATIC_DIR / "editor.bundle.js"

_BUILD_HINT: Final = (
    "The editor bundle has not been built. Run `npm ci && npm run build` "
    "in the repository root, then reload."
)


# --------------------------------------------------------------------------- #
# Services
# --------------------------------------------------------------------------- #


class AppServices:
    """The long-lived objects a request handler needs.

    Held on ``app.state`` so they are created once, are reachable from any
    route, and are trivially replaceable in tests.
    """

    def __init__(self, document: Document | None = None, bridge: Engine | None = None) -> None:
        self.document: Document = document if document is not None else Document()
        resolved_bridge: Engine = bridge if bridge is not None else LocalLLMBridge()
        self.bridge: Engine = resolved_bridge
        #: 256 bits of entropy, regenerated per launch.
        self.token = secrets.token_urlsafe(32)
        #: Remembers which window, if any, the shell owns. Absent in tests.
        self.window: Any | None = None

    def set_title(self, title: str) -> None:
        """Push a new title to the native window, if one is attached."""
        if self.window is None:
            return
        try:
            self.window.set_title(title)
        except Exception:
            logger.debug("could not set window title", exc_info=True)

    def title(self) -> str:
        """The native window title for the current document state."""
        document = self.document
        name = document.path.name if document.path else document.title
        marker = "*" if document.is_dirty else ""
        return f"{name}{marker} - LLex"


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #


class DocumentPayload(BaseModel):
    """The editor's document body, as sent on every save."""

    html: str = Field(max_length=8_000_000, description="Editor HTML for the whole document")
    title: str | None = Field(default=None, max_length=300)

    @field_validator("html")
    @classmethod
    def _must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document content must not be empty")
        return value


class ScaffoldItem(BaseModel):
    """One inline prompt attached to a highlighted span."""

    id: str = Field(max_length=64, description="Stable client-side identifier")
    text: str = Field(max_length=20_000, description="Currently selected text")
    instruction: str = Field(min_length=1, max_length=2_000)

    @field_validator("id")
    @classmethod
    def _identifier_is_plain(cls, value: str) -> str:
        if not value.strip() or len(value) > 64:
            raise ValueError("scaffold id must be a short non-empty token")
        return value.strip()


class ScaffoldPayload(BaseModel):
    scaffolds: list[ScaffoldItem] = Field(min_length=1, max_length=200)


class AssistantPayload(BaseModel):
    """A request to the local assistant."""

    text: str = Field(default="", max_length=60_000)
    instruction: str = Field(default="", max_length=4_000)
    tone: str = Field(default="professional", max_length=40)

    @field_validator("tone")
    @classmethod
    def _known_tone(cls, value: str) -> str:
        from .llm import Tone

        tone = value.strip().lower()
        if tone not in Tone.ALL:
            raise ValueError(f"unknown tone {value!r}; expected one of {', '.join(Tone.ALL)}")
        return tone


class ExportPayload(BaseModel):
    """A request to write the document to an interchange format."""

    html: str = Field(max_length=8_000_000)
    format: str = Field(max_length=10)

    @field_validator("format")
    @classmethod
    def _supported(cls, value: str) -> str:
        key = value if value.startswith(".") else f".{value}"
        if key.lower() not in SUPPORTED_FORMATS:
            supported = ", ".join(sorted(SUPPORTED_FORMATS))
            raise ValueError(f"unsupported format {value!r}; expected one of {supported}")
        return key.lower()


# --------------------------------------------------------------------------- #
# Shell helpers
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _render_shell() -> str:
    """Read the editor template, caching it for the process lifetime."""
    try:
        return TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - packaging failure
        raise RuntimeError(f"the editor template is missing: {TEMPLATE_PATH}") from exc


def _document_response(document: Document, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """The canonical document payload consumed by the frontend."""
    payload: dict[str, Any] = {
        "document": document.summary(),
        "html": document.content,
        "page": document.page.to_dict(),
        "styles": {name: style.to_dict() for name, style in document.styles.items()},
    }
    if extra:
        payload.update(extra)
    return payload


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #


def build_app(services: AppServices | None = None) -> FastAPI:
    """Construct the FastAPI application.

    Kept separate from module import so a test can build an isolated app with
    its own :class:`AppServices` and its own token.
    """
    resolved = services if services is not None else AppServices()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.services = resolved
        logger.debug("LLex API ready (token-protected, %d formats)", len(SUPPORTED_FORMATS))
        try:
            yield
        finally:
            app.state.services = None

    app = FastAPI(
        title="LLex",
        version=__version__,
        description="Local document API for the LLex editor.",
        lifespan=lifespan,
        # The API is single-user and token-guarded; a browsable schema is noise.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.services = resolved

    _register_middleware(app)
    _register_routes(app)
    return app


def _register_middleware(app: FastAPI) -> None:
    def session_token(request: Request) -> str:
        active: AppServices | None = getattr(request.app.state, "services", None)
        return active.token if active else ""

    @app.middleware("http")
    async def guard(
        request: Request,
        call_next: Callable[[Request], Awaitable[Any]],
    ) -> Any:
        """Reject rebinding attempts and unauthenticated API calls.

        The shell itself is exempt: it is what delivers the token, and it is
        only reachable after the ``Host`` check has passed.
        """
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip().lower()
        if host and host not in _ALLOWED_HOSTS:
            logger.warning("rejected request with non-loopback Host header: %r", host)
            return JSONResponse(
                status_code=HTTP_FORBIDDEN,
                content={"detail": "requests must originate from the loopback interface"},
            )

        if request.url.path.startswith("/api"):
            supplied = request.headers.get(API_TOKEN_HEADER) or request.query_params.get("token")
            if not secrets.compare_digest(supplied or "", session_token(request)):
                return JSONResponse(
                    status_code=HTTP_UNAUTHORIZED,
                    content={"detail": "missing or invalid API token"},
                    headers={"WWW-Authenticate": "Token"},
                )

        response = await call_next(request)
        # The document is private to this session; never let the webview or an
        # intermediary cache a stale copy.
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def _register_routes(app: FastAPI) -> None:

    def current() -> AppServices:
        active: AppServices | None = getattr(app.state, "services", None)
        if active is None:  # pragma: no cover - only after shutdown
            raise HTTPException(HTTP_SERVICE_UNAVAILABLE, "service is shutting down")
        return active

    # -- shell ------------------------------------------------------------- #

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def shell(request: Request) -> HTMLResponse:
        """Serve the editor with its session token injected."""
        token = current().token
        markup = _render_shell().replace(_TOKEN_PLACEHOLDER, token)
        response = HTMLResponse(content=markup)
        # The shell must never be cached: it carries this session's token.
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @app.get("/static/editor.bundle.js", include_in_schema=False)
    def bundle() -> Any:
        """Serve the built bundle, or explain how to build it.

        Declared before the ``/static`` mount so it takes precedence, turning a
        confusing 404 on the editor's own script into an actionable message.
        """
        if not BUNDLE_PATH.is_file():
            raise HTTPException(
                HTTP_INTERNAL_ERROR,
                detail=_BUILD_HINT,
            )
        return FileResponse(BUNDLE_PATH, media_type="application/javascript")

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # -- status ------------------------------------------------------------ #

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """Liveness probe. Also the only endpoint that needs no document."""
        return {"status": "ok"}

    @app.get("/api/environment")
    def environment() -> dict[str, Any]:
        """What the frontend needs to configure itself: assistant, formats."""
        active = current()
        bridge = active.bridge
        describe = bridge.describe() if hasattr(bridge, "describe") else {"backend": type(bridge).__name__}
        return {
            "document": active.document.summary(),
            "page": active.document.page.to_dict(),
            "styles": {name: style.to_dict() for name, style in active.document.styles.items()},
            "assistant": describe,
            "formats": [{"suffix": suffix, "label": label} for suffix, label in SUPPORTED_FORMATS.items()],
            "title": active.title(),
        }

    # -- document lifecycle ------------------------------------------------ #

    @app.get("/api/document")
    def read_document() -> dict[str, Any]:
        """The whole current document state."""
        return _document_response(current().document)

    @app.put("/api/document/content")
    def update_content(payload: DocumentPayload) -> dict[str, Any]:
        """Replace the document body. Used by autosave."""
        active = current()
        active.document.set_content(payload.html)
        if payload.title is not None:
            active.document.rename(payload.title)
        active.set_title(active.title())
        return _document_response(active.document)

    @app.post("/api/document/new")
    def new_document() -> dict[str, Any]:
        """Discard the current document and start an empty one."""
        active = current()
        active.document = Document()
        active.set_title(active.title())
        return _document_response(active.document)

    @app.post("/api/document/autosave")
    def autosave(payload: DocumentPayload) -> dict[str, Any]:
        """Persist the document in the background.

        Deliberately refuses to prompt: autosave that can raise a Save As dialog
        is worse than no autosave, because it would steal focus mid-sentence. A
        document that has never been saved is simply reported as unsaved.
        """
        active = current()
        active.document.set_content(payload.html)
        if payload.title is not None:
            active.document.rename(payload.title)
        if active.document.path is None:
            return {"status": "unsaved", "reason": "the document has no file yet"}
        try:
            target = active.document.save()
        except DocumentError as exc:
            raise HTTPException(HTTP_INTERNAL_ERROR, str(exc)) from exc
        return {
            "status": "saved",
            "path": str(target),
            "document": active.document.summary(),
        }

    @app.get("/api/document/conflict")
    def conflict() -> dict[str, Any]:
        """Report whether the file changed underneath the editor."""
        found = current().document.check_conflict()
        if found is None:
            return {"status": "clear"}
        return {"status": "conflict", **found.to_dict()}

    @app.post("/api/document/accept-disk")
    def accept_disk() -> dict[str, Any]:
        """Treat the file on disk as authoritative and reload it.

        The resolution when a conflict is reported and the user decides the other
        version is the one to keep.
        """
        active = current()
        path = active.document.path
        if path is None:
            raise HTTPException(HTTP_NOT_FOUND, "no document is open")
        try:
            active.document = Document.load(path)
        except DocumentFormatError as exc:
            raise HTTPException(HTTP_UNPROCESSABLE, str(exc)) from exc
        except DocumentError as exc:
            raise HTTPException(HTTP_INTERNAL_ERROR, str(exc)) from exc
        active.set_title(active.title())
        return {"status": "reloaded", **_document_response(active.document)}

    @app.post("/api/document/open")
    def open_document() -> dict[str, Any]:
        """Prompt for a file and load it.

        Requires a native window; a headless server reports 501 rather than
        pretending to have opened anything.
        """
        active = current()
        window = active.window
        if window is None:
            raise HTTPException(
                HTTP_NOT_IMPLEMENTED,
                "opening a file needs the desktop shell; pass a path on the command line instead",
            )
        chosen = _pick_file(window, save=False, file_types=("LLex Document (*.llex)",))
        if chosen is None:
            return {"status": "cancelled"}
        try:
            active.document = Document.load(chosen)
        except DocumentNotFoundError as exc:
            raise HTTPException(HTTP_NOT_FOUND, str(exc)) from exc
        except DocumentFormatError as exc:
            raise HTTPException(HTTP_UNPROCESSABLE, str(exc)) from exc
        except DocumentError as exc:
            raise HTTPException(HTTP_INTERNAL_ERROR, str(exc)) from exc
        active.set_title(active.title())
        return {"status": "opened", **_document_response(active.document)}

    @app.post("/api/document/save")
    def save_document(payload: DocumentPayload) -> dict[str, Any]:
        """Save to the current path, or prompt for one if there is none."""
        active = current()
        active.document.set_content(payload.html)
        if payload.title is not None:
            active.document.rename(payload.title)
        if active.document.path is None:
            return _save_as(active, payload.html)
        return _write(active)

    @app.post("/api/document/save-as")
    def save_document_as(payload: DocumentPayload) -> dict[str, Any]:
        """Always prompt for a destination."""
        active = current()
        active.document.set_content(payload.html)
        if payload.title is not None:
            active.document.rename(payload.title)
        return _save_as(active, payload.html)

    def _write(active: AppServices) -> dict[str, Any]:
        try:
            target = active.document.save()
        except DocumentError as exc:
            raise HTTPException(HTTP_INTERNAL_ERROR, str(exc)) from exc
        active.set_title(active.title())
        return {"status": "saved", "path": str(target), **_document_response(active.document)}

    def _save_as(active: AppServices, html: str) -> dict[str, Any]:
        window = active.window
        if window is None:
            raise HTTPException(
                HTTP_NOT_IMPLEMENTED,
                "saving a new file needs the desktop shell",
            )
        exporter = Exporter(active.document)
        suggested = active.document.path.name if active.document.path else exporter.suggested_filename(".llex")
        chosen = _pick_file(window, save=True, save_filename=suggested, file_types=("LLex Document (*.llex)",))
        if chosen is None:
            return {"status": "cancelled", "document": active.document.summary()}
        active.document.save(chosen)
        active.set_title(active.title())
        del html  # already applied to the document
        return {"status": "saved", "path": str(active.document.path), **_document_response(active.document)}

    # -- export ------------------------------------------------------------ #

    @app.post("/api/export")
    def export_document(payload: ExportPayload) -> dict[str, Any]:
        """Write the document to an interchange format via a save dialog."""
        active = current()
        active.document.set_content(payload.html)
        window = active.window
        if window is None:
            raise HTTPException(
                HTTP_NOT_IMPLEMENTED,
                "exporting needs the desktop shell for a destination dialog",
            )
        exporter = Exporter(active.document)
        chosen = _pick_file(
            window,
            save=True,
            save_filename=exporter.suggested_filename(payload.format),
            file_types=(f"{SUPPORTED_FORMATS[payload.format]} (*{payload.format})", "All files (*.*)"),
        )
        if chosen is None:
            return {"status": "cancelled"}
        try:
            result = exporter.write(chosen)
        except ExportError as exc:
            raise HTTPException(HTTP_INTERNAL_ERROR, str(exc)) from exc
        return {"status": "exported", **result.to_dict()}

    # -- assistant --------------------------------------------------------- #

    @app.post("/api/assistant/summarize")
    def summarize(payload: AssistantPayload) -> dict[str, str]:
        return _assistant("summarize", payload)

    @app.post("/api/assistant/rewrite")
    def rewrite(payload: AssistantPayload) -> dict[str, str]:
        return _assistant("rewrite", payload, instruction=payload.tone)

    @app.post("/api/assistant/outline")
    def outline(payload: AssistantPayload) -> dict[str, str]:
        return _assistant("outline", payload)

    @app.post("/api/assistant/ask")
    def ask(payload: AssistantPayload) -> dict[str, str]:
        return _assistant("ask", payload)

    def _assistant(task: str, payload: AssistantPayload, *, instruction: str = "") -> dict[str, str]:
        active = current()
        bridge = active.bridge
        text = payload.text.strip() or active.document.text
        try:
            if task == "summarize":
                result = bridge.summarize(text)
            elif task == "rewrite":
                result = bridge.rewrite_with_tone(text, payload.tone)
            elif task == "outline":
                result = bridge.outline(text)
            else:
                result = bridge.answer(payload.instruction, text)
        except AssistantUnavailableError as exc:
            # 503: the capability exists but nothing could serve it.
            raise HTTPException(HTTP_SERVICE_UNAVAILABLE, str(exc)) from exc
        except AssistantResponseError as exc:
            # 502: a backend answered with something unusable. Surfacing this
            # is deliberate -- falling back would hide a misconfiguration.
            raise HTTPException(HTTP_BAD_GATEWAY, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(HTTP_UNPROCESSABLE, str(exc)) from exc
        except Exception as exc:
            logger.exception("assistant task %s failed", task)
            raise HTTPException(HTTP_BAD_GATEWAY, f"assistant failed: {exc}") from exc
        return {"status": "ok", "task": task, "result": result}

    @app.post("/api/assistant/scaffolds")
    def run_scaffolds(payload: ScaffoldPayload) -> dict[str, Any]:
        """Resolve a batch of inline prompts into replacement text.

        One failing scaffold must not discard the rest of the batch, so each is
        reported individually and the response is always 200 with a per-item
        status.
        """
        active = current()
        bridge = active.bridge
        results: list[dict[str, Any]] = []
        for item in payload.scaffolds:
            try:
                output = bridge.execute_instruction(item.text, item.instruction)
                results.append({"id": item.id, "status": "ok", "text": output})
            except AssistantUnavailableError as exc:
                results.append({"id": item.id, "status": "unavailable", "message": str(exc)})
            except AssistantResponseError as exc:
                results.append({"id": item.id, "status": "error", "message": str(exc)})
            except Exception as exc:
                logger.exception("scaffold %s failed", item.id)
                results.append({"id": item.id, "status": "error", "message": str(exc)})
        return {"status": "ok", "results": results}


# --------------------------------------------------------------------------- #
# File dialogs
# --------------------------------------------------------------------------- #


def _pick_file(
    window: Any,
    *,
    save: bool,
    save_filename: str | None = None,
    file_types: tuple[str, ...] = ("All files (*.*)",),
) -> Path | None:
    """Show a native file dialog and return the chosen path, or ``None``.

    pywebview spells the dialog constants differently across versions and
    backends, so they are resolved defensively and any failure degrades to
    "no file chosen" rather than a 500.
    """
    import webview

    constant = "SAVE_DIALOG" if save else "OPEN_DIALOG"
    dialog_type = getattr(webview, constant, None)
    if dialog_type is None:  # pragma: no cover - very old pywebview
        logger.warning("pywebview has no %s constant; cannot prompt", constant)
        return None
    try:
        chosen = window.create_file_dialog(
            dialog_type,
            save_filename=save_filename,
            file_types=file_types,
        )
    except Exception:
        logger.exception("file dialog failed")
        return None
    if not chosen:
        return None
    if isinstance(chosen, str):
        return Path(chosen)
    if isinstance(chosen, (list, tuple)) and chosen:
        return Path(str(chosen[0]))
    return None


def create_app() -> FastAPI:
    """Module-level ASGI factory for ``uvicorn llex.api:create_app --factory``."""
    return build_app()


#: A default application instance, used by ``uvicorn llex.api:app``.
app = build_app()
