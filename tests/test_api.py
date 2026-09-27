"""Tests for the local HTTP API.

The security tests are the important ones: LLex runs an unauthenticated HTTP
server on loopback, which is not isolation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from llex.api import API_TOKEN_HEADER, AppServices, build_app
from llex.document import Document

from .conftest import SAMPLE_HTML, StubEngine

#: The webview always addresses the server as loopback; the API rejects any
#: other ``Host`` header to block DNS rebinding, so ad-hoc clients must too.
_LOOPBACK = "http://127.0.0.1:8765"


class TestSecurity:
    def test_api_requires_a_token(self, client: TestClient) -> None:
        response = client.get("/api/document")
        assert response.status_code == 401
        assert "token" in response.json()["detail"].lower()

    def test_wrong_token_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/document", headers={API_TOKEN_HEADER: "nope"})
        assert response.status_code == 401

    def test_empty_token_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/document", headers={API_TOKEN_HEADER: ""})
        assert response.status_code == 401

    def test_token_may_be_supplied_as_a_query_parameter(self, client: TestClient, token: str) -> None:
        assert client.get(f"/api/document?token={token}").status_code == 200

    def test_non_loopback_host_is_rejected(self, client: TestClient, auth: dict[str, str]) -> None:
        """Blocks DNS rebinding: the attacker controls the resolving name."""
        response = client.get("/api/document", headers={**auth, "Host": "evil.example.com"})
        assert response.status_code == 403
        assert "loopback" in response.json()["detail"]

    def test_loopback_hosts_are_accepted(self, client: TestClient, auth: dict[str, str]) -> None:
        for host in ("127.0.0.1:8000", "localhost:8000", "[::1]:8000"):
            assert client.get("/api/document", headers={**auth, "Host": host}).status_code == 200

    def test_no_wildcard_cors(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.get("/api/document", headers={**auth, "Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in response.headers

    def test_responses_are_not_cacheable(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.get("/api/document", headers=auth)
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_shell_delivers_the_token(self, client: TestClient, token: str) -> None:
        body = client.get("/").text
        assert token in body
        assert "__LLEX_API_TOKEN__" not in body

    def test_each_app_gets_a_distinct_token(self, document: Document, bridge: StubEngine) -> None:
        first = build_app(AppServices(document=document, bridge=bridge)).state.services.token
        second = build_app(AppServices(document=document, bridge=bridge)).state.services.token
        assert first != second
        assert len(first) >= 32


class TestEnvironment:
    def test_reports_assistant_and_formats(self, client: TestClient, auth: dict[str, str]) -> None:
        payload = client.get("/api/environment", headers=auth).json()
        assert payload["assistant"]["backend"] == "stub"
        assert {entry["suffix"] for entry in payload["formats"]} == {
            ".txt", ".md", ".html", ".rtf", ".docx", ".odt", ".epub", ".zip",
        }
        assert payload["document"]["title"] == "Test Document"
        assert payload["page"]["width"] == 8.5

    def test_health(self, client: TestClient, auth: dict[str, str]) -> None:
        assert client.get("/api/health", headers=auth).json() == {"status": "ok"}


class TestDocumentLifecycle:
    def test_read_returns_the_current_document(self, client: TestClient, auth: dict[str, str]) -> None:
        payload = client.get("/api/document", headers=auth).json()
        assert payload["html"] == SAMPLE_HTML
        assert payload["document"]["word_count"] > 0

    def test_update_content_marks_dirty(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.put(
            "/api/document/content",
            json={"html": '<div class="page"><p>changed</p></div>'},
            headers=auth,
        )
        assert response.status_code == 200
        assert response.json()["document"]["dirty"] is True

    def test_blank_content_is_rejected(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.put("/api/document/content", json={"html": "   "}, headers=auth)
        assert response.status_code == 422

    def test_rename(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.put(
            "/api/document/content", json={"html": SAMPLE_HTML, "title": "  Renamed  "}, headers=auth
        )
        assert response.json()["document"]["title"] == "Renamed"

    def test_new_document_resets_state(self, client: TestClient, auth: dict[str, str]) -> None:
        payload = client.post("/api/document/new", headers=auth).json()
        assert payload["document"]["title"] == "Untitled Document"
        assert payload["document"]["path"] is None

    def test_save_writes_atomically(self, auth: dict[str, str], tmp_path: Path) -> None:
        target = tmp_path / "nested" / "out.llex"
        with TestClient(build_app(_services_with_path(target)), base_url=_LOOPBACK) as local:
            headers = {API_TOKEN_HEADER: local.app.state.services.token}  # type: ignore[attr-defined]
            response = local.post(
                "/api/document/save", json={"html": '<div class="page"><p>saved</p></div>'}, headers=headers
            )
        assert response.status_code == 200
        assert response.json()["status"] == "saved"
        assert target.is_file()
        assert Document.load(target).content == '<div class="page"><p>saved</p></div>'

    def test_save_without_a_window_is_rejected(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.post("/api/document/save", json={"html": SAMPLE_HTML}, headers=auth)
        assert response.status_code == 501
        assert "desktop shell" in response.json()["detail"]

    def test_open_without_a_window_is_rejected(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.post("/api/document/open", headers=auth)
        assert response.status_code == 501

    def test_open_with_a_cancelled_dialog(self, client: TestClient, auth: dict[str, str], bridge: StubEngine) -> None:
        _attach_window(client.app.state.services, choice=None)
        response = client.post("/api/document/open", headers=auth)
        assert response.json() == {"status": "cancelled"}

    def test_open_loads_the_chosen_file(
        self, client: TestClient, auth: dict[str, str], bridge: StubEngine, tmp_path: Path
    ) -> None:
        source = tmp_path / "existing.llex"
        Document(title="Existing", content='<div class="page"><p>from disk</p></div>').save(source)
        services = client.app.state.services
        _attach_window(services, choice=source)
        payload = client.post("/api/document/open", headers=auth).json()
        assert payload["status"] == "opened"
        assert payload["document"]["title"] == "Existing"
        assert "from disk" in payload["html"]

    def test_open_reports_a_corrupt_file(
        self, client: TestClient, auth: dict[str, str], bridge: StubEngine, tmp_path: Path
    ) -> None:
        broken = tmp_path / "broken.llex"
        broken.write_text("{ not json", encoding="utf-8")
        _attach_window(client.app.state.services, choice=broken)
        response = client.post("/api/document/open", headers=auth)
        assert response.status_code == 422
        assert "not a valid LLex document" in response.json()["detail"]


class TestAssistant:
    def test_summarize(self, client: TestClient, auth: dict[str, str], bridge: StubEngine) -> None:
        payload = client.post("/api/assistant/summarize", json={"text": "hello"}, headers=auth).json()
        assert payload["status"] == "ok"
        assert payload["result"] == "<summarize>:hello</summarize>"
        assert bridge.calls[-1][0] == "summarize"

    def test_summarize_falls_back_to_the_document(
        self, client: TestClient, auth: dict[str, str], bridge: StubEngine
    ) -> None:
        client.post("/api/assistant/summarize", json={"text": ""}, headers=auth)
        assert "bold" in bridge.calls[-1][1]

    def test_rewrite_passes_the_tone(self, client: TestClient, auth: dict[str, str], bridge: StubEngine) -> None:
        client.post("/api/assistant/rewrite", json={"text": "x", "tone": "friendly"}, headers=auth)
        assert bridge.calls[-1] == ("rewrite", "x", "friendly")

    def test_unknown_tone_is_rejected(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.post("/api/assistant/rewrite", json={"text": "x", "tone": "sarcastic"}, headers=auth)
        assert response.status_code == 422

    def test_ask(self, client: TestClient, auth: dict[str, str], bridge: StubEngine) -> None:
        client.post("/api/assistant/ask", json={"text": "ctx", "instruction": "why?"}, headers=auth)
        assert bridge.calls[-1] == ("ask", "ctx", "why?")

    def test_outline(self, client: TestClient, auth: dict[str, str], bridge: StubEngine) -> None:
        client.post("/api/assistant/outline", json={"text": "ctx"}, headers=auth)
        assert bridge.calls[-1][0] == "outline"

    def test_unavailable_backend_is_503(self, client: TestClient, auth: dict[str, str], bridge: StubEngine) -> None:
        bridge.fail = True
        response = client.post("/api/assistant/rewrite", json={"text": "x"}, headers=auth)
        assert response.status_code == 503
        assert "stub is offline" in response.json()["detail"]


class TestScaffolds:
    def test_batch_is_resolved_per_item(
        self, client: TestClient, auth: dict[str, str], bridge: StubEngine
    ) -> None:
        payload = client.post(
            "/api/assistant/scaffolds",
            json={
                "scaffolds": [
                    {"id": "a", "text": "first", "instruction": "expand"},
                    {"id": "b", "text": "second", "instruction": "shorten"},
                ]
            },
            headers=auth,
        ).json()
        assert [item["id"] for item in payload["results"]] == ["a", "b"]
        assert all(item["status"] == "ok" for item in payload["results"])
        assert "expand" in payload["results"][0]["text"]

    def test_one_failure_does_not_discard_the_batch(
        self, client: TestClient, auth: dict[str, str], bridge: StubEngine
    ) -> None:
        bridge.fail = True
        payload = client.post(
            "/api/assistant/scaffolds",
            json={"scaffolds": [{"id": "a", "text": "t", "instruction": "i"}]},
            headers=auth,
        ).json()
        assert payload["status"] == "ok"
        assert payload["results"][0]["status"] == "unavailable"
        assert payload["results"][0]["message"] == "stub is offline"

    def test_empty_batch_is_rejected(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.post("/api/assistant/scaffolds", json={"scaffolds": []}, headers=auth)
        assert response.status_code == 422

    def test_missing_instruction_is_rejected(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.post(
            "/api/assistant/scaffolds", json={"scaffolds": [{"id": "a", "text": "t"}]}, headers=auth
        )
        assert response.status_code == 422

    def test_scaffold_ids_may_be_strings(self, client: TestClient, auth: dict[str, str]) -> None:
        """The old model typed ids as int, so ``Date.now()`` ids could collide."""
        response = client.post(
            "/api/assistant/scaffolds",
            json={"scaffolds": [{"id": "1700000000000000", "text": "t", "instruction": "i"}]},
            headers=auth,
        )
        assert response.status_code == 200


class TestExport:
    def test_export_writes_a_file(
        self, client: TestClient, auth: dict[str, str], bridge: StubEngine, tmp_path: Path
    ) -> None:
        target = tmp_path / "out.md"
        _attach_window(client.app.state.services, choice=target)
        payload = client.post(
            "/api/export", json={"html": '<div class="page"><h1>T</h1></div>', "format": "md"}, headers=auth
        ).json()
        assert payload["status"] == "exported"
        assert target.read_text(encoding="utf-8") == "# T\n"

    def test_unsupported_format_is_rejected(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.post(
            "/api/export", json={"html": SAMPLE_HTML, "format": "pdf"}, headers=auth
        )
        assert response.status_code == 422
        assert "unsupported format" in str(response.json())

    def test_cancelled_dialog(self, client: TestClient, auth: dict[str, str], bridge: StubEngine) -> None:
        _attach_window(client.app.state.services, choice=None)
        payload = client.post(
            "/api/export", json={"html": SAMPLE_HTML, "format": "txt"}, headers=auth
        ).json()
        assert payload == {"status": "cancelled"}

    def test_export_without_a_window_is_rejected(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.post("/api/export", json={"html": SAMPLE_HTML, "format": "txt"}, headers=auth)
        assert response.status_code == 501


class TestBundleRoute:
    def test_missing_bundle_explains_how_to_build_it(
        self, app: Any, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from llex import api as api_module

        monkeypatch.setattr(api_module, "BUNDLE_PATH", api_module.STATIC_DIR / "absent.js")
        with TestClient(app, base_url=_LOOPBACK) as local:
            response = local.get("/static/editor.bundle.js")
        assert response.status_code == 500
        assert "npm run build" in response.json()["detail"]

    def test_static_files_are_served(self, client: TestClient) -> None:
        assert client.get("/static/styles.css").status_code == 200


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _services_with_path(path: Path) -> AppServices:
    services = AppServices(document=Document())
    services.document.path = path
    services.document.mark_clean()
    return services


class _FakeWindow:
    """Stands in for a pywebview window in dialog-driven tests."""

    def __init__(self, choice: Path | None) -> None:
        self.choice = choice
        self.titles: list[str] = []

    def create_file_dialog(self, *_args: Any, **_kwargs: Any) -> list[str] | None:
        return [str(self.choice)] if self.choice else None

    def set_title(self, title: str) -> None:
        self.titles.append(title)


def _attach_window(services: AppServices, *, choice: Path | None) -> None:
    services.window = _FakeWindow(choice)
