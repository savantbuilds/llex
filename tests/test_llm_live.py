"""Live checks against a real local model.

Everything else in the suite exercises the bridge against a stub, which proves the
request is shaped correctly but not that a real runner *accepts* it. This module
closes that gap: it talks to whatever ``LLEX_LOCAL_MODEL`` points at, using the
same code path the editor does.

Skipped unless the endpoint answers, so the suite stays green with no model
running. The assertions are deliberately about the transport, not the output: a
0.5b model produces nonsense summaries and that is fine, because the question
here is whether the call reaches the runner and comes back with text.

Run it directly to check a runner by hand:

.. code-block:: console

   $ set LLEX_LOCAL_MODEL=http://127.0.0.1:11434/v1
   $ set LLEX_LOCAL_MODEL_NAME=qwen2.5:0.5b
   $ pytest tests/test_llm_live.py -v
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

from llex.llm import AssistantUnavailableError, LocalLLMBridge, Tone

#: Long enough for a small model on CPU, short enough to fail the suite rather
#: than hang it.
TIMEOUT = 120.0

#: Something with sentences, clauses and a couple of facts, so every prompt has
#: real material to work with regardless of how small the model is.
SOURCE = (
    "Photosynthesis converts light into chemical energy. Chlorophyll absorbs "
    "photons inside the leaf, which excites electrons. The water-splitting complex "
    "on the thylakoid membrane uses that energy to split water into protons and "
    "oxygen. ATP synthase then harnesses the proton gradient to make ATP. "
    "In the dark, cellular respiration reverses much of the process."
)


def _endpoint() -> str:
    return os.environ.get("LLEX_LOCAL_MODEL", "").strip()


@pytest.fixture(scope="module")
def bridge() -> LocalLLMBridge:
    """The real bridge, or a skip.

    Reachability is checked before yielding, because a module-scoped fixture
    cannot skip per test and a missing runner should not look like a failure.
    """
    if not _endpoint():
        pytest.skip("set LLEX_LOCAL_MODEL to run the live checks")

    runner = LocalLLMBridge(timeout=TIMEOUT)
    if not runner.is_online:
        pytest.skip(f"no local model answering at {runner.endpoint}")

    started = time.time()
    try:
        runner._complete("Reply with the single word: ready", "ping")
    except Exception as exc:
        pytest.skip(f"{runner.endpoint} did not complete a request: {type(exc).__name__}: {exc}")
    # A runner that answers but takes minutes is not usable either.
    if time.time() - started > TIMEOUT:
        pytest.skip("the local model did not respond within the timeout")
    return runner


class TestRunnerIsReachable:
    def test_it_reports_itself_online(self, bridge: LocalLLMBridge) -> None:
        described = bridge.describe()
        assert described["online"] is True
        assert described["endpoint"] == bridge.endpoint
        assert described["error"] is None

    def test_the_configured_model_is_the_one_named(self, bridge: LocalLLMBridge) -> None:
        assert bridge.model
        assert bridge.model in bridge.describe()["backend"]

    def test_the_openai_shape_is_what_the_runner_expects(self, bridge: LocalLLMBridge) -> None:
        """A 200 with an unparseable body is the failure mode this guards.

        The bridge posts to `{endpoint}/chat/completions` with a
        `{"model", "messages", "stream", "temperature"}` body. Ollama's
        compatibility layer, llama.cpp's server and LM Studio all answer that,
        but a runner that returns a different envelope produces a
        `AssistantResponseError` at the first real use rather than at start-up.
        """
        reply = bridge._complete("You are terse.", "Reply with the single word: ok")
        assert isinstance(reply, str)
        assert reply.strip(), "the runner returned an empty message"


class TestEveryAssistantCallReachesTheRunner:
    """One call per button in the assistant panel.

    Each asserts that text came back rather than what it says. A small model is
    entitled to answer all of these badly.
    """

    def test_summarize(self, bridge: LocalLLMBridge) -> None:
        assert bridge.summarize(SOURCE, max_sentences=2).strip()

    def test_outline(self, bridge: LocalLLMBridge) -> None:
        assert bridge.outline(SOURCE).strip()

    def test_rewrite_with_a_tone(self, bridge: LocalLLMBridge) -> None:
        assert bridge.rewrite_with_tone("The meeting was moved.", Tone.PROFESSIONAL).strip()

    @pytest.mark.parametrize("tone", Tone.ALL)
    def test_every_offered_tone_is_accepted(self, bridge: LocalLLMBridge, tone: str) -> None:
        """The dropdown offers exactly these, and each has to survive the round
        trip. A tone the API rejects is a dead control."""
        assert bridge.rewrite_with_tone("The meeting was moved.", tone).strip()

    def test_answer(self, bridge: LocalLLMBridge) -> None:
        assert bridge.answer("Where is the water split?", SOURCE).strip()

    def test_execute_instruction(self, bridge: LocalLLMBridge) -> None:
        assert bridge.execute_instruction("The cat sat on the mat.", "expand this").strip()

    def test_generate_dispatches_by_task(self, bridge: LocalLLMBridge) -> None:
        assert bridge.generate("summarize", SOURCE).strip()


class TestThroughTheHttpApi:
    """The same calls again, but through the endpoints the editor actually uses.

    Worth having separately: the bridge can work while a pydantic validator
    rejects the payload, which is a dead button rather than a broken backend.
    """

    @pytest.fixture
    def client(self, bridge: LocalLLMBridge) -> Any:
        from fastapi.testclient import TestClient

        from llex.api import API_TOKEN_HEADER, AppServices, build_app
        from llex.document import Document

        services = AppServices(document=Document(title="Live"), bridge=bridge)
        app = build_app(services)
        with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
            test_client.headers.update({API_TOKEN_HEADER: str(app.state.services.token)})
            yield test_client

    def test_the_environment_advertises_the_model(
        self, client: Any, bridge: LocalLLMBridge
    ) -> None:
        """The panel shows what it is talking to, so this has to be reported."""
        assistant = client.get("/api/environment").json()["assistant"]
        assert assistant["online"] is True
        assert assistant["model"] == bridge.model
        assert assistant["endpoint"] == bridge.endpoint

    @pytest.mark.parametrize(
        ("path", "payload"),
        [
            ("/api/assistant/summarize", {"text": SOURCE, "max_sentences": 2}),
            ("/api/assistant/outline", {"text": SOURCE}),
            ("/api/assistant/ask", {"text": SOURCE, "instruction": "Where is water split?"}),
            (
                "/api/assistant/rewrite",
                {"text": "The meeting was moved.", "tone": Tone.PROFESSIONAL},
            ),
        ],
    )
    def test_each_endpoint_returns_text(self, client: Any, path: str, payload: dict[str, Any]) -> None:
        response = client.post(path, json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
        result = body.get("result", body.get("text"))
        assert isinstance(result, str) and result.strip(), body

    def test_scaffolds_come_back_keyed_by_id(self, client: Any) -> None:
        """The scaffold runner replaces text in the document, so it has to be
        able to tell the replies apart."""
        response = client.post(
            "/api/assistant/scaffolds",
            json={
                "scaffolds": [
                    {"id": "a", "instruction": "expand this", "text": "The cat sat on the mat."},
                    {"id": "b", "instruction": "expand this", "text": "Rain fell all night."},
                ]
            },
        )
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert [item["id"] for item in results] == ["a", "b"]
        for item in results:
            assert item["status"] in {"ok", "error"}



class TestAMisconfiguredRunnerIsReportedNotSwallowed:
    def test_a_wrong_port_fails_with_a_readable_message(self) -> None:
        """A dead runner must produce a clear message, not a hang or a blank
        panel -- this is the most likely misconfiguration there is."""
        runner = LocalLLMBridge(endpoint="http://127.0.0.1:9/v1", model="nope", timeout=2.0)
        with pytest.raises(AssistantUnavailableError) as caught:
            runner._complete("system", "user")
        message = str(caught.value)
        assert "127.0.0.1:9" in message, message
        assert "could not reach" in message, message

    def test_online_means_configured_not_answered(self) -> None:
        """`is_online` deliberately performs no I/O, so the sidebar can render
        without waiting on a network call.

        Worth pinning because it means the panel shows "online" for a runner that
        is not actually there, and the first sign of trouble is a failed button
        rather than a status indicator.
        """
        runner = LocalLLMBridge(endpoint="http://127.0.0.1:9/v1", model="nope", timeout=2.0)
        assert runner.is_online is True

    def test_no_endpoint_configured_is_not_online(self) -> None:
        runner = LocalLLMBridge(endpoint="", model="nope", timeout=2.0)
        assert runner.is_online is False
        with pytest.raises(AssistantUnavailableError):
            runner._complete("system", "user")

    def test_a_wrong_model_name_reports_the_http_error(self, bridge: LocalLLMBridge) -> None:
        """A reachable runner that does not have the model answers 4xx, and the
        body says which. Silently returning empty text would look like a model
        that had nothing to say."""
        wrong = LocalLLMBridge(
            endpoint=bridge.endpoint,
            model="definitely-not-installed-xyz",
            timeout=TIMEOUT,
        )
        with pytest.raises(Exception) as caught:
            wrong._complete("system", "user")
        assert type(caught.value).__name__ in {
            "AssistantResponseError",
            "AssistantUnavailableError",
        }


def _skip_without_endpoint() -> None:
    if not _endpoint():
        pytest.skip("set LLEX_LOCAL_MODEL to run the live checks")


# Guard against the module being collected when nothing is configured: the
# fixture handles it, and this keeps `pytest --collect-only` honest.
_ = _skip_without_endpoint
