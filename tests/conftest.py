"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from llex.api import API_TOKEN_HEADER, AppServices, build_app
from llex.document import Document
from llex.llm import AssistantUnavailableError, Engine

SAMPLE_HTML = (
    '<div class="page">'
    "<h1>Title</h1>"
    "<h3>Section</h3>"
    "<p>Some <strong>bold</strong> text for the assistant.</p>"
    "</div>"
)


class StubEngine(Engine):
    """A deterministic assistant backend for tests.

    Implements the full :class:`~llex.llm.Engine` contract so the API can be
    exercised without a model, and records every call for assertions.
    """

    name = "stub"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    def _record(self, task: str, source: str, instruction: str) -> str:
        self.calls.append((task, source, instruction))
        if self.fail:
            raise AssistantUnavailableError("stub is offline")
        return f"<{task}>{instruction}:{source[:12]}</{task}>"

    def generate(self, task: str, source: str, *, instruction: str = "") -> str:
        return self._record(task, source, instruction)

    def summarize(self, text: str, max_sentences: int = 3) -> str:
        return self._record("summarize", text, "")

    def rewrite_with_tone(self, text: str, tone: str) -> str:
        return self._record("rewrite", text, tone)

    def outline(self, text: str) -> str:
        return self._record("outline", text, "")

    def answer(self, question: str, context: str) -> str:
        return self._record("ask", context, question)

    def execute_instruction(self, text: str, instruction: str) -> str:
        return self._record("scaffold", text, instruction)

    def describe(self) -> dict[str, Any]:
        return {"backend": self.name, "online": not self.fail, "error": None}


@pytest.fixture
def bridge() -> StubEngine:
    return StubEngine()


@pytest.fixture
def document() -> Document:
    return Document(title="Test Document", content=SAMPLE_HTML)


@pytest.fixture
def services(document: Document, bridge: StubEngine) -> AppServices:
    return AppServices(document=document, bridge=bridge)


@pytest.fixture
def app(services: AppServices) -> Any:
    return build_app(services)


@pytest.fixture
def token(app: Any) -> str:
    return str(app.state.services.token)


@pytest.fixture
def client(app: Any) -> Iterator[TestClient]:
    # The webview always addresses the server as loopback, and the API rejects
    # any other Host header to block DNS rebinding. A default TestClient would
    # send "testserver" and be refused, so the base URL mirrors the real one.
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


@pytest.fixture
def auth(token: str) -> dict[str, str]:
    return {API_TOKEN_HEADER: token}


@pytest.fixture
def api_client(app: Any, auth: dict[str, str]) -> Iterator[TestClient]:
    """A client that already carries the token.

    Every endpoint requires it, and the security tests deliberately omit it, so
    the ordinary tests use this rather than repeating a header per call. It still
    shares the app's services, so a test can reach in and set up state.
    """
    with TestClient(app, base_url="http://127.0.0.1:8765", headers=auth) as test_client:
        yield test_client
