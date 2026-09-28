"""Tests for the local LLM bridge and its deterministic fallback."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from llex import settings as llex_settings
from llex.llm import (
    AssistantResponseError,
    AssistantUnavailableError,
    HeuristicEngine,
    LocalLLMBridge,
    Tone,
    extractive_summary,
    outline_from_text,
    split_sentences,
    strip_inference_noise,
)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real user settings file out of these tests, in both directions.

    The bridge deliberately prefers the model the user chose in the app over the
    environment. That is the intended behaviour, and it also means these tests
    only pass on a machine that has never opened the model picker: a saved
    endpoint makes every "offline by default" assertion fail, and every
    environment-only assertion read the saved model instead of the one the test
    set. Reading the developer's own settings would make the suite depend on
    what they happen to have configured, and writing to it would be worse.
    """
    monkeypatch.setattr(
        llex_settings, "default_settings_path", lambda: tmp_path / "settings.json"
    )

FENCE = "```"
PROSE = (
    "Pagination is the core of a word processor. "
    "Without pagination, students cannot print assignments correctly. "
    "The engine therefore measures rendered page height. "
    "This is essential because layout is only knowable in the browser. "
    "Because of this, the backend stores opaque HTML. "
    "Someone ate lunch."
)


class TestSplitSentences:
    def test_basic(self) -> None:
        assert split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]

    def test_empty(self) -> None:
        assert split_sentences("") == []
        assert split_sentences("   \n  ") == []

    def test_newlines_collapse(self) -> None:
        assert split_sentences("One\n\nTwo") == ["One Two"]

    def test_lowercase_continuation_stays_joined(self) -> None:
        """A sentence boundary requires a capital, so ``e.g.`` is not split."""
        assert split_sentences("Use e.g. this value. Then stop.") == [
            "Use e.g. this value.",
            "Then stop.",
        ]


class TestExtractiveSummary:
    def test_is_deterministic(self) -> None:
        assert extractive_summary(PROSE) == extractive_summary(PROSE)

    def test_short_text_passes_through(self) -> None:
        assert extractive_summary("Only one sentence.") == "Only one sentence."

    def test_preserves_original_order(self) -> None:
        summary = extractive_summary(PROSE, max_sentences=2)
        assert summary in PROSE
        position = PROSE.index(summary[:20])
        assert position >= 0

    def test_selects_sentences_from_the_source(self) -> None:
        for sentence in split_sentences(extractive_summary(PROSE, max_sentences=2)):
            assert sentence in PROSE

    def test_empty_input(self) -> None:
        assert extractive_summary("") == ""


class TestOutline:
    def test_numbered_list(self) -> None:
        outline = outline_from_text(PROSE, max_items=3)
        lines = outline.splitlines()
        assert len(lines) == 3
        assert lines[0].startswith("1. ")

    def test_empty(self) -> None:
        assert outline_from_text("") == ""


class TestStripInferenceNoise:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", ""),
            ("Just the answer.", "Just the answer."),
            (f"{FENCE}json\n{{}}\n{FENCE}", "{}"),
            ("<think>reasoning</think>Answer", "Answer"),
            ("<thinking>a</thinking><thinking>b</thinking>Answer", "Answer"),
            ("Sure! Here it is: The answer.", "The answer."),
            ("Certainly. Rewritten text.", "Rewritten text."),
            ("Of course! Certainly! Here are the steps: 1. Do it.", "1. Do it."),
            ("Here is the summary:\n\nThe text.", "The text."),
        ],
    )
    def test_cleans_model_output(self, raw: str, expected: str) -> None:
        assert strip_inference_noise(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "Here is the problem.",
            "Sure thing, this is a real answer that is long enough.",
            "The answer starts with Here we go.",
            "Sure.",
        ],
    )
    def test_keeps_legitimate_openings(self, raw: str) -> None:
        assert strip_inference_noise(raw) == raw

    def test_never_reduces_a_reply_to_nothing(self) -> None:
        assert strip_inference_noise("Sure!") == "Sure!"
        assert strip_inference_noise("Here: x") == "Here: x"


class TestHeuristicEngine:
    def test_satisfies_the_engine_contract(self) -> None:
        engine = HeuristicEngine()
        assert engine.name == "heuristic"
        assert engine.describe()["online"] is False

    def test_summarize_and_outline_work_offline(self) -> None:
        engine = HeuristicEngine()
        assert engine.summarize(PROSE)
        assert engine.outline(PROSE)

    @pytest.mark.parametrize(
        "call",
        [
            lambda e: e.rewrite_with_tone("x", Tone.FRIENDLY),
            lambda e: e.answer("q", "ctx"),
            lambda e: e.execute_instruction("x", "do more"),
        ],
    )
    def test_generative_tasks_decline_honestly(self, call: Any) -> None:
        with pytest.raises(AssistantUnavailableError, match="local model"):
            call(HeuristicEngine())

    def test_empty_scaffold_input_is_a_value_error(self) -> None:
        with pytest.raises(ValueError, match="selected text"):
            HeuristicEngine().execute_instruction("  ", "instruction")


class TestBridgeConfiguration:
    def test_defaults_to_offline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLEX_LOCAL_MODEL", raising=False)
        bridge = LocalLLMBridge()
        assert bridge.is_online is False
        assert bridge.backend == "heuristic"
        assert bridge.describe()["endpoint"] is None

    def test_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLEX_LOCAL_MODEL", "http://127.0.0.1:11434/v1")
        monkeypatch.setenv("LLEX_LOCAL_MODEL_NAME", "llama3")
        bridge = LocalLLMBridge()
        assert bridge.is_online
        assert bridge.model == "llama3"
        assert bridge.describe()["backend"] == "local:llama3"

    def test_endpoint_is_normalised(self) -> None:
        assert LocalLLMBridge(endpoint="localhost:1234").endpoint == "http://localhost:1234"
        assert LocalLLMBridge(endpoint="http://h/v1/").endpoint == "http://h/v1"
        assert LocalLLMBridge(endpoint="   ").endpoint == ""

    def test_timeout_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLEX_LOCAL_MODEL_TIMEOUT", "5")
        assert LocalLLMBridge().timeout == 5.0

    def test_satisfies_the_engine_contract(self) -> None:
        bridge = LocalLLMBridge()
        assert bridge.name == bridge.backend
        for member in ("generate", "summarize", "rewrite_with_tone", "outline", "answer",
                       "execute_instruction", "describe"):
            assert callable(getattr(bridge, member))


class TestOfflineBehaviour:
    def test_summarize_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLEX_LOCAL_MODEL", raising=False)
        assert LocalLLMBridge().summarize(PROSE) == extractive_summary(PROSE)

    def test_rewrite_raises_offline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLEX_LOCAL_MODEL", raising=False)
        with pytest.raises(AssistantUnavailableError):
            LocalLLMBridge().rewrite_with_tone("x", Tone.PROFESSIONAL)

    def test_empty_inputs_are_handled_without_a_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLEX_LOCAL_MODEL", raising=False)
        bridge = LocalLLMBridge()
        assert bridge.summarize("  ") == "Nothing to summarize."
        assert bridge.outline("") == "Add more text to outline."
        assert bridge.rewrite_with_tone("", Tone.PROFESSIONAL) == "Select text to rewrite."

    def test_unknown_task_is_rejected(self) -> None:
        with pytest.raises(AssistantUnavailableError, match="unknown assistant task"):
            LocalLLMBridge().generate("translate", "x")

    def test_empty_question_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            LocalLLMBridge().answer("   ", "ctx")


# --------------------------------------------------------------------------- #
# A real local endpoint, to exercise the transport
# --------------------------------------------------------------------------- #


class _StubHandler(BaseHTTPRequestHandler):
    """Mimics an OpenAI-compatible local runner."""

    reply: str = "The answer."
    status: int = 200
    calls: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).calls.append(payload)
        body = json.dumps(
            {"choices": [{"message": {"content": type(self).reply}}]}
        ).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        """Silence the default stderr access log."""


@pytest.fixture
def stub_server() -> Any:
    _StubHandler.calls = []
    _StubHandler.status = 200
    _StubHandler.reply = "The answer."
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()


class TestOnlineTransport:
    def test_generate_posts_a_chat_completion(self, stub_server: str) -> None:
        bridge = LocalLLMBridge(endpoint=stub_server, model="test-model", timeout=5)
        assert bridge.summarize(PROSE) == "The answer."
        payload = _StubHandler.calls[0]
        assert payload["model"] == "test-model"
        assert payload["stream"] is False
        assert payload["messages"][0]["role"] == "system"
        assert "Summarise" in payload["messages"][0]["content"]
        assert PROSE[:40] in payload["messages"][1]["content"]

    def test_rewrite_sends_the_tone(self, stub_server: str) -> None:
        bridge = LocalLLMBridge(endpoint=stub_server, timeout=5)
        _StubHandler.reply = "Rewritten."
        assert bridge.rewrite_with_tone("text", Tone.FRIENDLY) == "Rewritten."
        assert "friendly" in _StubHandler.calls[0]["messages"][1]["content"]

    def test_scaffold_sends_the_instruction(self, stub_server: str) -> None:
        LocalLLMBridge(endpoint=stub_server, timeout=5).execute_instruction("frag", "expand this")
        user_turn = _StubHandler.calls[0]["messages"][1]["content"]
        assert "expand this" in user_turn
        assert "frag" in user_turn

    def test_noise_is_stripped_from_the_reply(self, stub_server: str) -> None:
        _StubHandler.reply = f"Sure! Here it is:\n{FENCE}\nReal answer.\n{FENCE}"
        assert LocalLLMBridge(endpoint=stub_server, timeout=5).summarize(PROSE) == "Real answer."

    def test_ask_sends_the_question(self, stub_server: str) -> None:
        LocalLLMBridge(endpoint=stub_server, timeout=5).answer("Why?", "context text")
        user_turn = _StubHandler.calls[0]["messages"][1]["content"]
        assert "Why?" in user_turn
        assert "context text" in user_turn

    def test_source_is_truncated_to_a_prompt_budget(self, stub_server: str) -> None:
        LocalLLMBridge(endpoint=stub_server, timeout=5).summarize("word " * 20_000)
        assert len(_StubHandler.calls[0]["messages"][1]["content"]) < 30_000

    def test_long_source_is_marked_truncated(self, stub_server: str) -> None:
        LocalLLMBridge(endpoint=stub_server, timeout=5).summarize("word " * 20_000)
        assert "[truncated]" in _StubHandler.calls[0]["messages"][1]["content"]

    def test_http_error_is_surfaced_not_swallowed(self, stub_server: str) -> None:
        _StubHandler.status = 404
        bridge = LocalLLMBridge(endpoint=stub_server, timeout=5)
        with pytest.raises(AssistantResponseError, match="HTTP 404"):
            bridge.summarize(PROSE)
        # A well-formed error is a real configuration problem, so it must not
        # silently degrade to the offline heuristic.

    def test_unreachable_endpoint_falls_back(self) -> None:
        bridge = LocalLLMBridge(endpoint="http://127.0.0.1:1/v1", timeout=1)
        assert bridge.summarize(PROSE) == extractive_summary(PROSE)
        assert "could not reach" in (bridge.last_error or "")

    def test_rewrite_unreachable_falls_back_and_raises(self) -> None:
        bridge = LocalLLMBridge(endpoint="http://127.0.0.1:1/v1", timeout=1)
        with pytest.raises(AssistantUnavailableError):
            bridge.rewrite_with_tone("x", Tone.PROFESSIONAL)


class TestMalformedModelResponses:
    """A backend that answers badly must be reported, not silently replaced."""

    @pytest.fixture
    def bridge(self, stub_server: str) -> LocalLLMBridge:
        return LocalLLMBridge(endpoint=stub_server, timeout=5)

    @pytest.mark.parametrize("reply", ["", "   "])
    def test_empty_reply_is_surfaced(self, bridge: LocalLLMBridge, reply: str) -> None:
        _StubHandler.reply = reply
        with pytest.raises(AssistantResponseError, match="empty response"):
            bridge.generate("summarize", "text")

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            (b"not json at all", "non-JSON"),
            (b"[1,2,3]", "unexpected payload"),
            (b'{"choices": []}', "no choices"),
            (b'{"choices": "x"}', "malformed payload"),
            (b'{"choices": [{}]}', "no message content"),
            (b'{"choices": [{"message": {}}]}', "no message content"),
            (b'{"error": {"message": "bad model"}}', "model error"),
            (b'{"error": "plain string error"}', "model error"),
        ],
    )
    def test_malformed_payloads_are_surfaced(
        self, bridge: LocalLLMBridge, raw: bytes, message: str
    ) -> None:
        from urllib.request import urlopen

        import llex.llm as module

        def fake_urlopen(request: Any, timeout: float) -> Any:
            return _FakeResponse(raw)

        original = module.urllib.request.urlopen
        module.urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            with pytest.raises(AssistantResponseError, match=message):
                bridge.generate("summarize", "text")
        finally:
            module.urllib.request.urlopen = original  # type: ignore[assignment]
        del urlopen

    def test_transport_failure_still_falls_back(self) -> None:
        """The distinction matters: an outage may be worked around, a bad
        reply may not."""
        bridge = LocalLLMBridge(endpoint="http://127.0.0.1:1/v1", timeout=1)
        assert bridge.summarize(PROSE) == extractive_summary(PROSE)


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class TestMessageExtraction:
    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            (b'{"error": {"message": "bad model"}}', "model error"),
            (b"not json", "non-JSON"),
            (b"[1,2,3]", "unexpected payload"),
            (b'{"choices": []}', "no choices"),
        ],
    )
    def test_extraction_failures(self, raw: bytes, message: str) -> None:
        from llex.llm import _extract_message

        with pytest.raises(AssistantResponseError, match=message):
            _extract_message(raw)

    def test_flat_text_field_is_accepted(self) -> None:
        from llex.llm import _extract_message

        assert _extract_message(json.dumps({"choices": [{"text": "flat"}]}).encode()) == "flat"

    def test_message_content_wins_over_flat_text(self) -> None:
        from llex.llm import _extract_message

        payload = json.dumps({"choices": [{"message": {"content": "a"}, "text": "b"}]}).encode()
        assert _extract_message(payload) == "a"
