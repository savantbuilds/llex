"""The local language model contract.

LLex is local-first: no document text ever leaves the machine. This module
isolates the language-model contract so swapping the backend -- Ollama,
llama.cpp's server, LM Studio, or an in-process model -- is a configuration
change rather than a refactor.

Two backends are provided
-------------------------
:class:`LocalLLMBridge`
    Talks to any **OpenAI-compatible** ``/v1/chat/completions`` endpoint over
    plain HTTP. This covers Ollama, llama.cpp, LM Studio, vLLM and most local
    runners, and needs no third-party HTTP dependency.

:class:`HeuristicEngine`
    A dependency-free, fully deterministic fallback used when no local endpoint
    is reachable. It performs extractive work only -- it never invents prose --
    so an offline LLex still summarises and outlines predictably instead of
    silently returning a canned string.

Configuration
-------------
``LLEX_LOCAL_MODEL``
    Base URL of the endpoint, e.g. ``http://127.0.0.1:11434/v1``. If unset the
    bridge stays offline and the heuristic engine answers.
``LLEX_LOCAL_MODEL_NAME``
    Model identifier to request. Defaults to ``local-model``.
``LLEX_LOCAL_MODEL_TIMEOUT``
    Per-request timeout in seconds. Defaults to ``120``.

The UI must never block on the network, so :meth:`LocalLLMBridge.generate` is
a single call with an internal timeout and a clean fallback rather than a
long-lived streaming session.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Final, Protocol

__all__ = [
    "AssistantError",
    "AssistantResponseError",
    "AssistantUnavailableError",
    "Engine",
    "HeuristicEngine",
    "LocalLLMBridge",
    "Tone",
    "extractive_summary",
    "outline_from_text",
    "split_sentences",
    "strip_inference_noise",
]

DEFAULT_ENDPOINT: Final = ""
DEFAULT_MODEL: Final = "local-model"
DEFAULT_TIMEOUT: Final = 120.0

#: Sentence boundary that tolerates abbreviations, decimals and initials.
_SENTENCE_RE: Final = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_WHITESPACE_RE: Final = re.compile(r"\s+")

#: A local runner that is starting up answers slowly; retry once.
_RETRY_ATTEMPTS: Final = 2


class AssistantError(RuntimeError):
    """Base class for assistant failures."""


class AssistantUnavailableError(AssistantError):
    """No backend could be reached, so a fallback may be attempted.

    Raised for a transport problem: nothing is listening, the runner has not
    finished loading, or the request timed out. :meth:`LocalLLMBridge.generate`
    responds to this by falling back to the deterministic engine.
    """


class AssistantResponseError(AssistantError):
    """A backend replied, but its reply could not be used.

    Raised for an HTTP error status or a malformed payload. These are *not*
    retried with heuristics: a wrong model name or a broken runner is a real
    configuration problem, and silently substituting different output would
    hide it from the user.
    """


class Tone:
    """Named rewriting tones offered by the assistant sidebar."""

    FRIENDLY: Final = "friendly"
    TECHNICAL: Final = "technical"
    PROFESSIONAL: Final = "professional"
    CONCISE: Final = "concise"
    PERSUASIVE: Final = "persuasive"

    ALL: Final = (FRIENDLY, TECHNICAL, PROFESSIONAL, CONCISE, PERSUASIVE)


class Engine(Protocol):
    """The contract any assistant backend must satisfy.

    This is the whole assistant surface the UI can reach. ``generate`` is the
    primitive; the rest are the named operations the sidebar, the context menu
    and the scaffold pipeline call. A backend that cannot genuinely perform a
    task must raise :class:`AssistantUnavailableError` rather than return a
    placeholder, so the UI can tell the user instead of inserting fiction into
    their document.
    """

    @property
    def name(self) -> str:
        """Human-readable backend identifier, surfaced in the UI."""
        ...

    def generate(self, task: str, source: str, *, instruction: str = "") -> str:
        """Return text for ``task`` given ``source``, or raise on failure."""
        ...

    def summarize(self, text: str, max_sentences: int = 3) -> str:
        """Condense ``text``."""
        ...

    def rewrite_with_tone(self, text: str, tone: str) -> str:
        """Rewrite ``text`` in the named ``tone``."""
        ...

    def outline(self, text: str) -> str:
        """Produce an outline of ``text``."""
        ...

    def answer(self, question: str, context: str) -> str:
        """Answer ``question`` about the document body ``context``."""
        ...

    def execute_instruction(self, text: str, instruction: str) -> str:
        """Fulfil ``instruction`` for the selected fragment ``text``."""
        ...

    def describe(self) -> dict[str, Any]:
        """Serialisable status for the assistant sidebar."""
        ...


# --------------------------------------------------------------------------- #
# Shared text helpers
# --------------------------------------------------------------------------- #


def split_sentences(text: str) -> list[str]:
    """Split text into sentences, tolerating common abbreviation and ellipsis."""
    cleaned = _WHITESPACE_RE.sub(" ", (text or "").replace("\n", " ")).strip()
    if not cleaned:
        return []
    parts = [part.strip() for part in _SENTENCE_RE.split(cleaned) if part.strip()]
    return parts or [cleaned]


#: Reasoning models emit a scratchpad before the answer; drop it entirely.
_THINK_RE: Final = re.compile(r"<(think|thinking|scratchpad)\b[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)

#: An opening or closing code fence, including an optional language tag.
_FENCE_RE: Final = re.compile(r"^\s*```[a-zA-Z0-9_+.-]*\s*$", re.MULTILINE)

#: Standalone chat interjections. The trailing sentence punctuation is
#: required so that a real opening like "Sure thing, this is ..." is left alone;
#: only the punctuated "Sure!" / "Certainly." forms are removed.
_INTERJECTION_RE: Final = re.compile(
    r"^\s*(?:sure|certainly|of course|absolutely|okay|ok|got it|no problem|"
    r"great|understood|alright)\b[.!?]+:?\s+",
    re.IGNORECASE,
)

#: "Here is the summary:" style preambles. Requires a trailing colon so that a
#: legitimate sentence such as "Here is the problem." survives intact.
_HERE_RE: Final = re.compile(r"^\s*here\b[^:\n]{0,60}:\s*", re.IGNORECASE)

#: Any leftover fence markers, including one glued to the end of a line.
_FENCE_MARKER_RE: Final = re.compile(r"```[a-zA-Z0-9_+.-]*")

#: Do not strip a preamble that would consume the whole reply.
_MIN_REMAINDER: Final = 8

_MAX_PREAMBLES: Final = 3


def strip_inference_noise(text: str) -> str:
    """Clean raw model output for display in an editor.

    Removes reasoning scratchpads, markdown code fences, and chat preambles
    such as "Sure! Here is ...", so the result can be inserted into a document
    as-is. A preamble is only removed when a substantial reply remains, so a
    short answer is never reduced to nothing.
    """
    cleaned = _THINK_RE.sub("", text or "").strip()
    if not cleaned:
        return ""

    for pattern in (_INTERJECTION_RE, _HERE_RE):
        for _ in range(_MAX_PREAMBLES):
            candidate = pattern.sub("", cleaned, count=1).strip()
            if candidate == cleaned or len(candidate) < _MIN_REMAINDER:
                break
            cleaned = candidate

    cleaned = _FENCE_RE.sub("", cleaned)
    cleaned = _FENCE_MARKER_RE.sub("", cleaned)
    return cleaned.strip()


# --------------------------------------------------------------------------- #
# Deterministic extractive engine
# --------------------------------------------------------------------------- #

_STOPWORDS: Final = frozenset(
    (
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for",
        "from", "has", "have", "he", "her", "his", "i", "if", "in", "into", "is",
        "it", "its", "of", "on", "or", "our", "that", "the", "their", "them",
        "there", "these", "they", "this", "to", "was", "were", "will", "with",
        "would", "you", "your", "do", "does", "did", "can", "could", "should",
        "shall", "may", "might", "must", "not", "no", "so", "such", "than",
        "then", "when", "where", "who", "whom", "which", "what", "while",
        "about", "after", "before", "because", "between", "during", "over",
        "under", "again", "further", "once", "all", "any", "both", "each",
        "few", "more", "most", "other", "some", "only", "own", "same", "too",
        "very", "just", "also", "here",
    )
)

#: Cues that a sentence carries more topical weight than a connective one.
_SIGNAL_RE: Final = re.compile(
    r"\b(because|therefore|thus|hence|consequently|essential|crucial|key|important|"
    r"significant|primary|central|goal|objective|result|conclusion|evidence|"
    r"demonstrat\w*|proves?|enables?|provides?|ensures?|requires?|focus)\w*\b",
    re.IGNORECASE,
)


def _tokenize(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9']+", text.lower()) if word]


def _score_sentence(sentence: str, frequencies: dict[str, int], total: int) -> float:
    """Score a sentence by keyword density plus explicit signal cues."""
    words = _tokenize(sentence)
    if not words:
        return 0.0
    content = [word for word in words if word not in _STOPWORDS and len(word) > 2]
    if not content:
        return 0.0
    density = sum(frequencies.get(word, 0) for word in content) / total
    return density + (0.35 if _SIGNAL_RE.search(sentence) else 0.0)


def extractive_summary(text: str, max_sentences: int = 3) -> str:
    """Summarise by selecting the highest-scoring sentences, in original order.

    Extractive on purpose: it cannot hallucinate, so it is safe as an offline
    default and produces identical output for identical input.
    """
    sentences = split_sentences(text)
    if not sentences:
        return ""
    if len(sentences) <= max_sentences:
        return " ".join(sentences)

    frequencies: dict[str, int] = {}
    for sentence in sentences:
        for word in _tokenize(sentence):
            if word not in _STOPWORDS and len(word) > 2:
                frequencies[word] = frequencies.get(word, 0) + 1
    total = sum(frequencies.values()) or 1

    ranked = sorted(
        range(len(sentences)),
        key=lambda index: (-_score_sentence(sentences[index], frequencies, total), index),
    )
    keep = sorted(ranked[:max_sentences])
    return " ".join(sentences[index] for index in keep)


def outline_from_text(text: str, max_items: int = 8) -> str:
    """Build a flat outline from the most representative sentences.

    Used as the offline assistant outline. The editor's *structural* outline
    (real headings) is produced in the browser and is always preferable; this
    exists so the assistant has something meaningful to return with no model.
    """
    sentences = split_sentences(text)
    if not sentences:
        return ""
    picked = extractive_summary(text, max_sentences=max_items)
    return "\n".join(
        f"{index}. {sentence}"
        for index, sentence in enumerate(split_sentences(picked), start=1)
    )


class HeuristicEngine:
    """Deterministic, dependency-free fallback engine.

    Covers the extractive tasks well and *declines* the generative ones by
    raising :class:`AssistantUnavailableError`. That is deliberate: the
    alternative -- returning a plausible-looking string -- would let the
    frontend insert invented prose into a user's document while implying a
    language model had produced it.
    """

    name: Final = "heuristic"

    def generate(self, task: str, source: str, *, instruction: str = "") -> str:
        handlers = {
            "summarize": lambda: extractive_summary(source),
            "outline": lambda: outline_from_text(source),
        }
        handler = handlers.get(task)
        if handler is not None:
            return handler() or _EMPTY_RESULT[task]
        raise AssistantUnavailableError(
            f"'{task}' needs a local model; the offline engine only covers "
            f"summarize and outline. Set LLEX_LOCAL_MODEL to enable it."
        )

    def summarize(self, text: str, max_sentences: int = 3) -> str:
        if not text.strip():
            return "Nothing to summarize."
        return extractive_summary(text, max_sentences=max_sentences) or "Nothing to summarize."

    def outline(self, text: str) -> str:
        if not text.strip():
            return "Add more text to outline."
        return outline_from_text(text) or "Add more text to outline."

    def rewrite_with_tone(self, text: str, tone: str) -> str:
        if not text.strip():
            return "Select text to rewrite."
        raise AssistantUnavailableError(
            "rewriting in a named tone needs a local model. "
            "Set LLEX_LOCAL_MODEL to enable it."
        )

    def answer(self, question: str, context: str) -> str:
        raise AssistantUnavailableError(
            "answering questions needs a local model. "
            "Set LLEX_LOCAL_MODEL to enable it."
        )

    def execute_instruction(self, text: str, instruction: str) -> str:
        if not text.strip():
            raise ValueError("scaffold instruction requires some selected text")
        raise AssistantUnavailableError(
            f"running the instruction {instruction.strip()!r} needs a local model. "
            "Set LLEX_LOCAL_MODEL to enable it."
        )

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "endpoint": None,
            "model": None,
            "online": False,
            "error": None,
            "offline_capabilities": ["summarize", "outline"],
        }


#: What to return when an extractive task is handed empty input.
_EMPTY_RESULT: Final = {
    "summarize": "Nothing to summarize.",
    "outline": "Add more text to outline.",
}


# --------------------------------------------------------------------------- #
# OpenAI-compatible local endpoint
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Message:
    role: str
    content: str


_SYSTEM_PROMPTS: Final[dict[str, str]] = {
    "summarize": (
        "You are a writing assistant. Summarise the supplied text in three "
        "sentences or fewer. Reply with the summary only, no preamble."
    ),
    "rewrite": (
        "You are a writing assistant. Rewrite the supplied text in the "
        "requested tone. Preserve the original meaning and length. Reply with "
        "the rewritten text only, no preamble."
    ),
    "outline": (
        "You are a writing assistant. Produce a concise outline of the "
        "supplied text as a numbered list. Reply with the outline only."
    ),
    "scaffold": (
        "You are a writing assistant embedded in a word processor. A user has "
        "selected a fragment and given an instruction. Produce text that "
        "replaces the fragment and fulfils the instruction, matching the "
        "surrounding document's register. Reply with the replacement text "
        "only, no preamble, no quotation marks."
    ),
    "ask": (
        "You are a writing assistant embedded in a word processor. Answer the "
        "user's question about the supplied document text. Be concise and "
        "practical."
    ),
}

_MAX_SOURCE_CHARS: Final = 24_000


class LocalLLMBridge:
    """Bridge to a local, OpenAI-compatible chat-completions endpoint.

    Constructed with no arguments it is immediately usable: with no endpoint
    configured every generative task raises :class:`AssistantUnavailableError`
    and the caller falls back to :class:`HeuristicEngine`.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        *,
        fallback: Engine | None = None,
    ) -> None:
        self.endpoint = _clean_endpoint(
            endpoint if endpoint is not None else os.environ.get("LLEX_LOCAL_MODEL", DEFAULT_ENDPOINT)
        )
        self.model = model or os.environ.get("LLEX_LOCAL_MODEL_NAME", DEFAULT_MODEL)
        self.timeout = float(
            timeout
            if timeout is not None
            else os.environ.get("LLEX_LOCAL_MODEL_TIMEOUT", DEFAULT_TIMEOUT)
        )
        self.fallback = fallback or HeuristicEngine()
        #: Last transport error, for the UI to surface instead of a bare failure.
        self.last_error: str | None = None

    # -- Introspection ----------------------------------------------------- #

    @property
    def name(self) -> str:
        """Backend identifier, satisfying the :class:`Engine` contract."""
        return self.backend

    @property
    def is_online(self) -> bool:
        """True when a local endpoint is configured. Does not perform I/O."""
        return bool(self.endpoint)

    @property
    def backend(self) -> str:
        return f"local:{self.model}" if self.is_online else self.fallback.name

    def describe(self) -> dict[str, Any]:
        """Serialisable status for the assistant sidebar and the API."""
        return {
            "backend": self.backend,
            "endpoint": self.endpoint or None,
            "model": self.model if self.is_online else None,
            "online": self.is_online,
            "error": self.last_error,
            "offline_capabilities": ["summarize", "outline"],
        }

    # -- Generation -------------------------------------------------------- #

    def generate(self, task: str, source: str, *, instruction: str = "") -> str:
        """Run ``task`` against the local model, or fall back when offline.

        Only :class:`AssistantUnavailableError` triggers the fallback, because
        it means the model could not be reached at all. A
        :class:`AssistantResponseError` propagates: the backend answered and
        the answer was unusable, which is a configuration problem the user needs
        to see rather than have papered over.
        """
        if task not in _SYSTEM_PROMPTS:
            raise AssistantUnavailableError(f"unknown assistant task: {task!r}")

        if not self.is_online:
            return self._fallback(task, source, instruction)

        prompt = _build_user_prompt(task, source, instruction)
        try:
            return strip_inference_noise(self._complete(_SYSTEM_PROMPTS[task], prompt))
        except AssistantUnavailableError as exc:
            self.last_error = str(exc)
            return self._fallback(task, source, instruction)

    def _fallback(self, task: str, source: str, instruction: str) -> str:
        try:
            return self.fallback.generate(task, source, instruction=instruction)
        except AssistantUnavailableError as exc:
            self.last_error = str(exc)
            raise

    # -- Typed conveniences ------------------------------------------------ #

    def summarize(self, text: str, max_sentences: int = 3) -> str:
        """Summarise ``text``. Deterministic while offline."""
        if not text.strip():
            return "Nothing to summarize."
        if self.is_online:
            return self.generate("summarize", text)
        return extractive_summary(text, max_sentences=max_sentences) or "Nothing to summarize."

    def rewrite_with_tone(self, text: str, tone: str = Tone.PROFESSIONAL) -> str:
        """Rewrite ``text`` in ``tone``.

        Requires a local model: tone transfer is generative, and a heuristic
        cannot do it honestly. Raises :class:`AssistantUnavailableError` when
        offline so the UI can say so instead of quietly mangling the user's
        prose.
        """
        if not text.strip():
            return "Select text to rewrite."
        return self.generate("rewrite", text, instruction=tone.lower())

    def outline(self, text: str) -> str:
        """Outline ``text``. Deterministic while offline."""
        if not text.strip():
            return "Add more text to outline."
        return self.generate("outline", text)

    def execute_instruction(self, text: str, instruction: str) -> str:
        """Fulfil ``instruction`` for the selected fragment ``text``.

        This is the scaffold pipeline: an inline prompt attached to a
        highlighted span, resolved when the user runs the batch.
        """
        if not text.strip():
            raise ValueError("scaffold instruction requires some selected text")
        return self.generate("scaffold", text, instruction=instruction)

    def answer(self, question: str, context: str) -> str:
        """Answer ``question`` about the document body ``context``."""
        if not question.strip():
            raise ValueError("question must not be empty")
        return self.generate("ask", context, instruction=question)

    # -- Transport --------------------------------------------------------- #

    def _complete(self, system_prompt: str, user_prompt: str) -> str:
        """POST a chat completion and return the assistant's text.

        Retries once on connection errors, since a local runner that has just
        been launched often refuses the first request while it loads weights.
        """
        if not self.is_online:
            raise AssistantUnavailableError("no local model endpoint configured")

        url = f"{self.endpoint}/chat/completions"
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "temperature": 0.3,
            }
        ).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - endpoint is operator-configured
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        last_transport_error: Exception | None = None
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                    return _extract_message(response.read())
            except urllib.error.HTTPError as exc:
                # The runner answered, so this is a real configuration problem
                # (wrong path, wrong model) rather than an outage.
                raise AssistantResponseError(
                    f"local model returned HTTP {exc.code}: {_read_http_error(exc)}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_transport_error = exc
                if attempt < _RETRY_ATTEMPTS:
                    continue
        raise AssistantUnavailableError(
            f"could not reach the local model at {self.endpoint}: {last_transport_error}"
        ) from last_transport_error


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _clean_endpoint(value: str) -> str:
    """Normalise a base URL to a bare ``scheme://host[:port][/prefix]``."""
    endpoint = (value or "").strip().rstrip("/")
    if not endpoint:
        return ""
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"http://{endpoint}"
    return endpoint


def _build_user_prompt(task: str, source: str, instruction: str) -> str:
    """Assemble the user turn, truncating the source to a sane budget."""
    body = source.strip()
    truncated = False
    if len(body) > _MAX_SOURCE_CHARS:
        body = body[:_MAX_SOURCE_CHARS]
        truncated = True

    if task == "rewrite":
        return (
            f"Tone: {instruction or Tone.PROFESSIONAL}\n\n"
            f"Text to rewrite:\n{body}"
        )
    if task == "scaffold":
        return (
            f"Instruction: {instruction.strip()}\n\n"
            f"Selected fragment to replace:\n{body}"
        )
    if task == "ask":
        return f"Question: {instruction.strip()}\n\nDocument text:\n{body}"

    suffix = "\n\n[truncated]" if truncated else ""
    return f"{body}{suffix}"


def _extract_message(raw: bytes) -> str:
    """Pull the assistant text out of an OpenAI-shaped chat completion."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AssistantResponseError(
            f"local model returned a non-JSON response: {raw[:200]!r}"
        ) from exc

    if not isinstance(payload, dict):
        raise AssistantResponseError("local model returned an unexpected payload")

    if "error" in payload:
        error = payload["error"]
        detail = error.get("message") if isinstance(error, dict) else error
        raise AssistantResponseError(f"local model error: {detail}")

    choices = payload.get("choices")
    if not isinstance(choices, list):
        if "choices" in payload:
            raise AssistantResponseError("local model returned a malformed payload: choices is not a list")
        raise AssistantResponseError("local model returned no choices")
    if not choices:
        raise AssistantResponseError("local model returned no choices")

    first = choices[0]
    if not isinstance(first, dict):
        raise AssistantResponseError("local model returned a malformed choice")

    message = first.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        # Some runners expose a flat ``text`` field instead of ``message``.
        content = first.get("text")
    if not isinstance(content, str):
        raise AssistantResponseError("local model returned no message content")

    stripped = content.strip()
    if not stripped:
        raise AssistantResponseError("local model returned an empty response")
    return stripped


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    """Best-effort human-readable detail from an HTTP error response."""
    try:
        raw = exc.read()
    except Exception:  # pragma: no cover - body may already be consumed
        return exc.reason or "no detail"
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw[:200].decode("utf-8", "replace")
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and "message" in error:
            return str(error["message"])[:200]
        if "message" in payload:
            return str(payload["message"])[:200]
    return str(payload)[:200]
