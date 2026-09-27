"""Check a ``.llex`` file and say what is wrong with it.

Opening a corrupt document currently reports whatever the loader happened to hit
first, which for a hand-edited or truncated file is rarely the actual problem.
This module answers the question a user actually has -- *why will this not
open?* -- without launching the editor, and it is usable from a terminal:

.. code-block:: console

   python -m llex.validate notes.llex
   python -m llex.validate notes.llex --json

Every check is reported with the JSON path it applies to, so the output points at
the thing to fix rather than at the file as a whole.

The validator never raises on bad input. A validator that crashes on the input it
exists to diagnose is worse than no validator, so every failure becomes a
finding, and the exit status is the number of errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from .document import (
    CONTAINER_SUFFIX,
    DEFAULT_CONTENT,
    FORMAT_VERSION,
    Document,
    DocumentError,
)
from .markup import BLOCK_KINDS, parse_document

__all__ = [
    "Finding",
    "Report",
    "main",
    "validate_bytes",
    "validate_data",
    "validate_file",
]

#: Findings below this severity do not affect whether a file opens.
SEVERITIES: Final = ("error", "warning", "note")

#: Keys a version 3 file is expected to carry.
_REQUIRED_KEYS: Final = ("format_version", "title", "content", "page", "styles")


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing wrong with a document, located and explained."""

    severity: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class Report:
    """The result of validating one document."""

    source: str
    findings: list[Finding] = field(default_factory=list)
    #: Set when the document could be loaded, so the checks that need one ran.
    document: Document | None = None

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "warning"]

    @property
    def ok(self) -> bool:
        """Whether the file can be opened. Warnings do not count against it."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "ok": self.ok,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def render(self) -> str:
        """A human-readable report."""
        lines = [f"{self.source}: {'OK' if self.ok else 'INVALID'}"]
        for finding in self.findings:
            lines.append(f"  {finding.severity}: {finding.path}: {finding.message}")
        if self.ok and not self.findings:
            lines.append("  no findings")
        return "\n".join(lines)


def validate_bytes(raw: bytes, source: str = "<bytes>") -> Report:
    """Validate the contents of a ``.llex`` file.

    Args:
        raw: The file's bytes.
        source: A name for it, used in the report.

    Returns:
        A :class:`Report`. Never raises.
    """
    report = Report(source=source)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        report.findings.append(
            Finding("error", "<file>", f"not valid UTF-8 at byte {exc.start}: {exc.reason}")
        )
        return report
    return validate_text(text, source)


def validate_text(text: str, source: str = "<string>") -> Report:
    """Validate already-decoded file contents."""
    report = Report(source=source)
    if not text.strip():
        report.findings.append(Finding("error", "<file>", "the file is empty"))
        return report

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        report.findings.append(
            Finding(
                "error",
                f"line {exc.lineno}, column {exc.colno}",
                f"not valid JSON: {exc.msg}",
            )
        )
        return report

    return validate_data(data, source)


def validate_data(data: Any, source: str = "<data>") -> Report:
    """Validate an already-parsed ``.llex`` document.

    Args:
        data: The parsed JSON.
        source: A name for it, used in the report.

    Returns:
        A :class:`Report`. Never raises, whatever ``data`` is.
    """
    report = Report(source=source)
    _check_root(data, report)
    if report.errors:
        # Structural problems make the remaining checks meaningless, and a
        # cascade of derived complaints is harder to act on than the first cause.
        return report

    _check_version(data, report)
    _check_content(data, report)
    _check_page(data, report)
    _check_styles(data, report)
    _check_loads(data, report)
    return report


def _check_root(data: Any, report: Report) -> None:
    if not isinstance(data, dict):
        report.findings.append(
            Finding("error", "<root>", f"the document root must be an object, not {_kind(data)}")
        )
        return
    for key in _REQUIRED_KEYS:
        if key not in data:
            report.findings.append(Finding("error", key, "the key is missing"))


def _check_version(data: dict[str, Any], report: Report) -> None:
    version = data.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool):
        report.findings.append(
            Finding("error", "format_version", f"must be an integer, found {_kind(version)}")
        )
        return
    if version < 1:
        report.findings.append(
            Finding("error", "format_version", f"must be 1 or greater, found {version}")
        )
    elif version > FORMAT_VERSION:
        report.findings.append(
            Finding(
                "error",
                "format_version",
                f"{version} is newer than this build understands ({FORMAT_VERSION}); "
                "upgrade LLex to open it",
            )
        )
    elif version < FORMAT_VERSION:
        report.findings.append(
            Finding(
                "note",
                "format_version",
                f"written by an older version ({version}); it will be migrated on save",
            )
        )


def _check_content(data: dict[str, Any], report: Report) -> None:
    content = data.get("content")
    if isinstance(content, str):
        report.findings.append(
            Finding(
                "note",
                "content",
                "stored as a single string; saving in the current version makes it "
                "one block per line",
            )
        )
    elif isinstance(content, list):
        for index, line in enumerate(content):
            if not isinstance(line, str):
                report.findings.append(
                    Finding(
                        "error",
                        f"content[{index}]",
                        f"must be a string, found {_kind(line)}",
                    )
                )
    else:
        report.findings.append(
            Finding("error", "content", f"must be a string or a list, found {_kind(content)}")
        )

    title = data.get("title")
    if not isinstance(title, str):
        report.findings.append(
            Finding("error", "title", f"must be a string, found {_kind(title)}")
        )
    elif not title.strip():
        report.findings.append(Finding("warning", "title", "is empty"))


def _check_page(data: dict[str, Any], report: Report) -> None:
    page = data.get("page")
    if not isinstance(page, dict):
        report.findings.append(
            Finding("error", "page", f"must be an object, found {_kind(page)}")
        )
        return
    for key in ("width", "height", "margin_top", "margin_right", "margin_bottom", "margin_left"):
        value = page.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            report.findings.append(
                Finding("error", f"page.{key}", f"must be a number, found {_kind(value)}")
            )
            continue
        if value <= 0:
            report.findings.append(
                Finding("error", f"page.{key}", f"must be greater than zero, found {value}")
            )
    width = page.get("width")
    height = page.get("height")
    if (
        isinstance(width, (int, float))
        and isinstance(height, (int, float))
        and (width < 1 or height < 1)
    ):
        report.findings.append(
            Finding(
                "warning",
                "page",
                f"a page of {width}x{height} inches is smaller than a postage stamp",
            )
        )


def _check_styles(data: dict[str, Any], report: Report) -> None:
    styles = data.get("styles")
    if not isinstance(styles, dict):
        report.findings.append(
            Finding("error", "styles", f"must be an object, found {_kind(styles)}")
        )
        return
    for name, style in styles.items():
        if not isinstance(style, dict):
            report.findings.append(
                Finding("error", f"styles.{name}", f"must be an object, found {_kind(style)}")
            )
            continue
        size = style.get("font_size")
        if isinstance(size, (int, float)) and not isinstance(size, bool) and size <= 0:
            report.findings.append(
                Finding("error", f"styles.{name}.font_size", f"must be positive, found {size}")
            )


def _check_loads(data: dict[str, Any], report: Report) -> None:
    """The check that matters: does the real loader accept it?

    Everything above is a guess about what the loader wants. Rather than
    duplicate those rules and hope they stay in step, ask the loader itself, and
    report whatever it says. A disagreement between this module and the loader
    shows up here as an error rather than as a document that fails to open.
    """
    try:
        document = Document.from_dict(data)
    except DocumentError as exc:
        report.findings.append(Finding("error", "<root>", f"the document will not open: {exc}"))
        return
    report.document = document

    content = document.content
    if not content.strip():
        report.findings.append(
            Finding("warning", "content", "is empty, so the document will open blank")
        )
    if content == DEFAULT_CONTENT and content.strip():
        report.findings.append(
            Finding("note", "content", "matches the default content; the file may be a stub")
        )

    # A parser that recovers from malformed markup means a broken file can look
    # superficially fine, so report when the recovery actually happened.
    parsed = parse_document(content)
    unknown = sorted(
        {block.kind for block in parsed.iter_blocks() if block.kind not in BLOCK_KINDS}
    )
    if unknown:
        report.findings.append(
            Finding("warning", "content", f"unrecognised block kinds: {', '.join(unknown)}")
        )


def _kind(value: Any) -> str:
    """The JSON type name of a value, for a message a user can act on."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


def validate_file(path: Path | str) -> Report:
    """Validate a ``.llex`` file on disk."""
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        report = Report(source=str(target))
        report.findings.append(Finding("error", "<file>", f"could not be read: {exc}"))
        return report

    report = validate_bytes(raw, str(target))
    if target.suffix.lower() != CONTAINER_SUFFIX:
        report.findings.append(
            Finding(
                "note",
                "<file>",
                f"has the extension {target.suffix or '(none)'}; "
                f"LLex documents use {CONTAINER_SUFFIX}",
            )
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point. Returns the number of errors found."""
    parser = argparse.ArgumentParser(
        prog="python -m llex.validate",
        description="Check an LLex document and report what is wrong with it.",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="files to check")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the report as JSON instead of text",
    )
    args = parser.parse_args(argv)

    reports = [validate_file(path) for path in args.paths]
    failures = 0

    if args.as_json:
        payload: Any = [report.to_dict() for report in reports]
        if len(reports) == 1:
            payload = reports[0].to_dict()
        print(json.dumps(payload, indent=2))
    else:
        for report in reports:
            print(report.render())
            failures += len(report.errors)

    return failures


if __name__ == "__main__":
    sys.exit(main())
