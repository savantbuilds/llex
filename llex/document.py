"""The logical document model and its ``.llex`` persistence format.

Responsibilities
----------------
* Own document metadata (title, timestamps), page setup, and the style table.
* Own serialisation to and from the ``.llex`` JSON container.
* Stay completely free of UI, HTTP and editor concerns. Everything the
  frontend needs is derived here and nowhere else.

Format
------
``.llex`` is UTF-8 JSON with a ``format_version`` discriminator so future
releases can migrate old files instead of failing to open them. The canonical
key set is written by :meth:`Document.to_dict`; :meth:`Document.from_dict`
accepts a superset and tolerates missing or unknown keys so a file written by a
newer LLex still opens.

The editor's HTML lives in ``content``. It is the authoritative representation:
the browser renders and edits it directly, and the Python side treats it as
opaque except when a structured view is needed (see :mod:`llex.markup`).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from .markup import count_words, to_plain_text

__all__ = [
    "DEFAULT_CONTENT",
    "FORMAT_VERSION",
    "Document",
    "DocumentError",
    "DocumentFormatError",
    "DocumentNotFoundError",
    "PageSetup",
    "StyleDefinition",
]

#: Bumped whenever the on-disk shape changes in a way that needs migration.
FORMAT_VERSION: Final = 2

#: The HTML a brand-new document starts with. Matches the editor's own default
#: so a freshly opened window and a freshly created file look identical.
DEFAULT_CONTENT: Final = '<div class="page"><p></p></div>'

#: Legacy key from FORMAT_VERSION 1, still read for backwards compatibility.
_LEGACY_CONTENT_KEY: Final = "html_content"

#: 96 CSS pixels per inch, matching the browser's definition of 1in.
DPI: Final = 96.0

_DEFAULT_STYLE_NAMES: Final = ("Normal", "Heading")

#: The attributes of a :class:`StyleDefinition` that are persisted. Anything
#: else found in a file is dropped, so a newer build's extra fields do not make
#: an older LLex fail to open a document.
_STYLE_FIELDS: Final = (
    "name",
    "font_family",
    "font_size",
    "weight",
    "slant",
    "underline",
    "alignment",
    "color",
)

#: The numeric attributes of a :class:`PageSetup`.
_PAGE_FIELDS: Final = (
    "width",
    "height",
    "margin_top",
    "margin_right",
    "margin_bottom",
    "margin_left",
)


class DocumentError(Exception):
    """Base class for document-layer failures."""


class DocumentFormatError(DocumentError):
    """Raised when a file exists but is not a readable ``.llex`` document."""


class DocumentNotFoundError(DocumentError):
    """Raised when a document path does not exist."""


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #


def _utcnow() -> datetime:
    """Timezone-aware current time. ``datetime.utcnow()`` is deprecated."""
    return datetime.now(timezone.utc)


def _parse_timestamp(value: object, fallback: datetime) -> datetime:
    """Parse an ISO-8601 timestamp, normalising naive values to UTC.

    Files written by LLex 0.1 used naive ``datetime.utcnow()`` values, so a
    missing offset has to be interpreted as UTC rather than local time.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return fallback
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return fallback


@dataclass(frozen=True, slots=True)
class StyleDefinition:
    """A named character/paragraph style.

    Only attributes that are actually honoured by the editor and the exporters
    are modelled. Unknown keys found in a file are dropped rather than raising,
    so a document written by a newer build still opens.
    """

    name: str
    font_family: str = "Arial"
    font_size: float = 11.0
    weight: str = "normal"
    slant: str = "roman"
    underline: bool = False
    alignment: str = "left"
    color: str = "#000000"

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in _STYLE_FIELDS}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, name: str = "") -> StyleDefinition:
        """Build a style from untrusted data, ignoring anything unrecognised."""
        if not isinstance(data, Mapping):
            raise DocumentFormatError(f"style {name!r} is not an object")
        known = {key: data[key] for key in _STYLE_FIELDS if key in data}
        known.setdefault("name", name or str(data.get("name", "Untitled")))
        clean = cls(**known)
        return replace(clean, name=clean.name or name)


@dataclass(frozen=True, slots=True)
class PageSetup:
    """Physical page geometry, in inches.

    Letter at 1-inch margins is the US academic default and matches the
    816x1056 CSS pixel page the frontend renders at :data:`DPI`.
    """

    width: float = 8.5
    height: float = 11.0
    margin_top: float = 1.0
    margin_right: float = 1.0
    margin_bottom: float = 1.0
    margin_left: float = 1.0

    def __post_init__(self) -> None:
        for name in _PAGE_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ValueError(f"page setup {name} must be a number, got {value!r}")
            if value <= 0:
                raise ValueError(f"page setup {name} must be positive, got {value!r}")
        if self.margin_left + self.margin_right >= self.width:
            raise ValueError("horizontal margins leave no printable width")
        if self.margin_top + self.margin_bottom >= self.height:
            raise ValueError("vertical margins leave no printable height")

    def to_dict(self) -> dict[str, float]:
        return {name: float(getattr(self, name)) for name in _PAGE_FIELDS}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> PageSetup:
        """Build page setup from untrusted data, falling back per field.

        A single bad margin must not make a document unopenable, so invalid
        values degrade to the default instead of raising.
        """
        if not isinstance(data, Mapping):
            return cls()
        defaults = cls()
        values: dict[str, float] = {}
        for name in _PAGE_FIELDS:
            raw = data.get(name, getattr(defaults, name))
            try:
                number = float(raw)
            except (TypeError, ValueError):
                number = float(getattr(defaults, name))
            values[name] = number if number > 0 else float(getattr(defaults, name))
        try:
            return cls(**values)
        except ValueError:
            return defaults

    @property
    def pixel_width(self) -> int:
        return round(self.width * DPI)

    @property
    def pixel_height(self) -> int:
        return round(self.height * DPI)

    @property
    def margins_px(self) -> dict[str, int]:
        """Margins in CSS pixels, for the frontend's page geometry."""
        return {
            "top": round(self.margin_top * DPI),
            "right": round(self.margin_right * DPI),
            "bottom": round(self.margin_bottom * DPI),
            "left": round(self.margin_left * DPI),
        }


def _default_styles() -> dict[str, StyleDefinition]:
    """The style table a brand-new document starts with."""
    return {
        "Normal": StyleDefinition(name="Normal", font_family="Arial", font_size=11),
        "Heading": StyleDefinition(
            name="Heading",
            font_family="Arial",
            font_size=16,
            weight="bold",
            color="#0b3d91",
        ),
    }


# --------------------------------------------------------------------------- #
# Document
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Document:
    """The logical document model and metadata."""

    title: str = "Untitled Document"
    content: str = DEFAULT_CONTENT
    created_at: datetime = field(default_factory=_utcnow)
    modified_at: datetime = field(default_factory=_utcnow)
    page: PageSetup = field(default_factory=PageSetup)
    styles: dict[str, StyleDefinition] = field(default_factory=dict)
    path: Path | None = None

    #: Snapshot of the last persisted state, used to answer "is this dirty?".
    _saved_content: str = field(default="", repr=False, compare=False)
    _saved_title: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.styles:
            self.styles = _default_styles()
        if not self.title:
            self.title = "Untitled Document"
        self.mark_clean()

    # -- Backwards-compatible alias ---------------------------------------- #

    @property
    def html_content(self) -> str:
        """Deprecated alias for :attr:`content` (LLex 0.1 spelling)."""
        return self.content

    @html_content.setter
    def html_content(self, value: str) -> None:
        self.content = value

    # -- Derived views ----------------------------------------------------- #

    @property
    def text(self) -> str:
        """Readable plain text. Backs the LLM bridge and text-only exports."""
        return to_plain_text(self.content)

    @property
    def word_count(self) -> int:
        return count_words(self.text)

    @property
    def character_count(self) -> int:
        return len(self.text)

    @property
    def page_count(self) -> int:
        """Number of physical pages recorded in the saved HTML.

        The pagination engine is authoritative about page breaks -- it measures
        rendered layout, which Python cannot -- so this reflects what was last
        laid out rather than re-deriving it.
        """
        return self.content.count('class="page"') or 1

    @property
    def is_saved(self) -> bool:
        """True when this document has a location on disk."""
        return self.path is not None

    @property
    def is_dirty(self) -> bool:
        """True when the in-memory state differs from what was last persisted."""
        return self.content != self._saved_content or self.title != self._saved_title

    def mark_clean(self) -> None:
        """Record the current state as the persisted baseline."""
        self._saved_content = self.content
        self._saved_title = self.title

    def touch(self) -> None:
        """Mark the document as modified right now."""
        self.modified_at = _utcnow()

    def set_content(self, html: str) -> None:
        """Replace the document body and stamp the modification time."""
        self.content = html
        self.touch()

    def rename(self, title: str) -> None:
        cleaned = title.strip() or "Untitled Document"
        if cleaned != self.title:
            self.title = cleaned
            self.touch()

    def summary(self) -> dict[str, Any]:
        """Metadata for the status bar and the window title."""
        return {
            "title": self.title,
            "path": str(self.path) if self.path else None,
            "file_name": self.path.name if self.path else None,
            "dirty": self.is_dirty,
            "word_count": self.word_count,
            "character_count": self.character_count,
            "page_count": self.page_count,
            "created_at": self.created_at.isoformat(),
            "modified_at": self.modified_at.isoformat(),
        }

    # -- Serialisation ----------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": FORMAT_VERSION,
            "title": self.title,
            "created_at": self.created_at.isoformat(),
            "modified_at": self.modified_at.isoformat(),
            "content": self.content,
            "page": self.page.to_dict(),
            "styles": {name: style.to_dict() for name, style in self.styles.items()},
        }

    @classmethod
    def from_dict(cls, data: object) -> Document:
        """Rebuild a document from parsed JSON of unknown provenance."""
        if not isinstance(data, Mapping):
            raise DocumentFormatError("document root must be a JSON object")

        version = data.get("format_version", 1)
        if not isinstance(version, int) or version < 1:
            raise DocumentFormatError(f"unsupported format_version: {version!r}")
        if version > FORMAT_VERSION:
            raise DocumentFormatError(
                f"document was written by a newer LLex (format_version {version} > "
                f"{FORMAT_VERSION}); upgrade to open it"
            )

        now = _utcnow()
        content = data.get("content") or data.get(_LEGACY_CONTENT_KEY) or DEFAULT_CONTENT
        if not isinstance(content, str):
            content = DEFAULT_CONTENT

        raw_styles = data.get("styles")
        styles: dict[str, StyleDefinition] = {}
        if isinstance(raw_styles, Mapping):
            for name, style_data in raw_styles.items():
                try:
                    style = StyleDefinition.from_dict(style_data, name=str(name))
                except (DocumentFormatError, TypeError):
                    # A single malformed style must not make the file unopenable.
                    continue
                styles[style.name] = style
        if not styles:
            styles = _default_styles()

        title = data.get("title")
        document = cls(
            title=title.strip() if isinstance(title, str) and title.strip() else "Untitled Document",
            content=content,
            created_at=_parse_timestamp(data.get("created_at"), now),
            modified_at=_parse_timestamp(data.get("modified_at"), now),
            page=PageSetup.from_dict(data.get("page")),
            styles=styles,
        )
        # Loading is not an edit: a freshly opened file is clean.
        document.mark_clean()
        return document

    # -- Persistence ------------------------------------------------------- #

    def save(self, path: Path | str | None = None) -> Path:
        """Write the document to ``path`` (or its current path) atomically.

        The payload is written to a temporary file in the destination directory
        and then moved into place, so an interrupted save can never leave a
        half-written document where a valid one used to be.
        """
        target = Path(path) if path is not None else self.path
        if target is None:
            raise DocumentError("no path given and the document has never been saved")
        if target.suffix.lower() != ".llex":
            target = target.with_suffix(".llex")
        target = target.expanduser()

        self.touch()
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

        try:
            target.parent.mkdir(exist_ok=True, parents=True)
            handle, temp_name = tempfile.mkstemp(
                dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as writer:
                    writer.write(payload)
                os.replace(temp_name, target)
            except BaseException:
                # Never leave a stray temp file behind on failure.
                with contextlib.suppress(OSError):
                    Path(temp_name).unlink()
                raise
        except OSError as exc:
            raise DocumentError(f"could not save to {target}: {exc}") from exc

        self.path = target
        self.mark_clean()
        return target

    @classmethod
    def load(cls, path: Path | str) -> Document:
        """Read a ``.llex`` file from disk."""
        target = Path(path).expanduser()
        if not target.is_file():
            raise DocumentNotFoundError(f"no such document: {target}")
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise DocumentError(f"could not read {target}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise DocumentFormatError(f"{target} is not a UTF-8 text file") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DocumentFormatError(f"{target.name} is not a valid LLex document: {exc}") from exc

        document = cls.from_dict(data)
        document.path = target
        return document
