"""Structural parsing of the editor's HTML fragment into a document tree.

The browser is the single source of truth for document content: TipTap emits
HTML that LLex persists verbatim in the ``.llex`` container. Everything the
Python side needs to do with a document -- deriving plain text for the LLM
bridge, or rendering ``.docx``/``.odt``/``.rtf``/``.epub`` exports -- requires
the same thing: a structural view of that HTML rather than a flat string.

This module is deliberately dependency-free and pure. It has no knowledge of
FastAPI, pywebview or Qt, which keeps it exhaustively testable and reusable.

Design notes
------------
* The parser is *tolerant*. Editor content is real-world HTML produced by a
  rich text engine and round-tripped through the clipboard, so unbalanced and
  unclosed tags are expected rather than exceptional. Nothing here raises on
  malformed input.
* The parser is *safe*. ``<script>`` and ``<style>`` bodies are discarded along
  with event-handler attributes, so nothing executable can survive into an
  exported artifact.
* Presentation is captured at a useful but shallow level: the inline marks
  (bold/italic/underline/strike) and text colour that document formats can
  actually represent. CSS is never interpreted.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final

__all__ = [
    "Block",
    "InlineStyle",
    "ParsedDocument",
    "TextRun",
    "count_words",
    "parse_document",
    "to_plain_text",
]


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

#: Block-level kinds. ``page`` wraps a physical page emitted by the pagination
#: engine; it is transparent to every consumer and retained only so page
#: boundaries survive a round trip.
BLOCK_KINDS: Final = frozenset(
    {
        "paragraph",
        "heading",
        "bulletList",
        "orderedList",
        "listItem",
        "codeBlock",
        "blockquote",
        "horizontalRule",
        "page",
    }
)

#: Tags whose *content* is never document text.
_SKIP_CONTENT: Final = frozenset({"script", "style", "head", "title", "noscript"})

#: Tags with no closing tag and no content. ``hr`` is deliberately absent: it
#: is a *block* that the writer wants as a real node, not a stray marker.
_VOID_TAGS: Final = frozenset(
    {"br", "img", "input", "meta", "link", "source", "wbr", "col", "area"}
)

_HEADING_TAGS: Final = {f"h{level}": level for level in range(1, 7)}

#: Tags that only ever carry inline formatting, so their end tag pops a style.
_INLINE_TAGS: Final = frozenset(
    {
        "strong", "b", "em", "i", "u", "ins", "s", "del", "strike",
        "code", "mark", "span", "a", "font", "sub", "sup", "small",
    }
)

_ALIGNMENTS: Final = frozenset({"left", "center", "right", "justify"})

_COLOR_RE: Final = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

#: Named colours worth carrying into an export. Anything else is dropped so an
#: invalid colour value can never reach a document format.
_NAMED_COLORS: Final = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "maroon": "#800000",
    "olive": "#808000",
    "navy": "#000080",
    "purple": "#800080",
    "teal": "#008080",
    "orange": "#ffa500",
}


@dataclass(frozen=True, slots=True)
class InlineStyle:
    """Inline formatting applied to a span of text.

    Immutable and hashable so identical adjacent runs can be coalesced during
    parsing instead of producing a heavily fragmented run list.
    """

    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False
    color: str | None = None
    highlight: str | None = None

    @property
    def is_plain(self) -> bool:
        """True when this style carries no formatting at all."""
        return self == PLAIN_STYLE


PLAIN_STYLE: Final = InlineStyle()


@dataclass(frozen=True, slots=True)
class TextRun:
    """A contiguous stretch of text sharing a single :class:`InlineStyle`."""

    text: str
    style: InlineStyle = PLAIN_STYLE


@dataclass(frozen=True, slots=True)
class Block:
    """A block-level node.

    ``runs`` holds inline content owned directly by this block; ``children``
    holds nested blocks. Most blocks use one or the other, but ``listItem`` uses
    both: a label plus a nested sub-list.
    """

    kind: str = "paragraph"
    runs: tuple[TextRun, ...] = ()
    children: tuple[Block, ...] = ()
    level: int | None = None
    align: str | None = None
    language: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in BLOCK_KINDS:
            raise ValueError(f"unknown block kind: {self.kind!r}")

    @property
    def text(self) -> str:
        """Plain text of this block and, recursively, of its children."""
        parts = [run.text for run in self.runs]
        parts.extend(child.text for child in self.children)
        return "".join(parts)

    def iter_blocks(self) -> Iterator[Block]:
        """Depth-first iteration over this block and all of its descendants."""
        yield self
        for child in self.children:
            yield from child.iter_blocks()


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """The full parse result: a flat tuple of top-level blocks."""

    blocks: tuple[Block, ...] = ()
    title: str = ""
    meta: dict[str, str] = field(default_factory=dict)

    def iter_blocks(self) -> Iterator[Block]:
        for block in self.blocks:
            yield from block.iter_blocks()

    def headings(self) -> tuple[Block, ...]:
        """Every heading block, in document order. Backs the outline view."""
        return tuple(b for b in self.iter_blocks() if b.kind == "heading")

    def outline(self) -> tuple[tuple[int, str], ...]:
        """``(level, text)`` pairs for every heading, for navigation UIs."""
        return tuple((b.level or 1, b.text.strip()) for b in self.headings())


# --------------------------------------------------------------------------- #
# Style extraction
# --------------------------------------------------------------------------- #


def normalize_color(value: str | None) -> str | None:
    """Coerce a CSS colour to ``#rrggbb``, or ``None`` if it is unusable."""
    if not value:
        return None
    candidate = value.strip().lower()
    if not candidate or candidate in {"inherit", "initial", "unset", "currentcolor"}:
        return None
    if candidate.startswith("rgb"):
        numbers = re.findall(r"\d+", candidate)
        if len(numbers) >= 3:
            red, green, blue = (min(255, int(n)) for n in numbers[:3])
            return f"#{red:02x}{green:02x}{blue:02x}"
        return None
    if _COLOR_RE.match(candidate):
        if len(candidate) == 4:  # #abc -> #aabbcc
            candidate = "#" + "".join(ch * 2 for ch in candidate[1:])
        return candidate.lower()
    return _NAMED_COLORS.get(candidate)


def _declarations(style_attr: str | None) -> dict[str, str]:
    """Parse an inline ``style`` attribute into a ``{property: value}`` map."""
    if not style_attr:
        return {}
    declarations: dict[str, str] = {}
    for chunk in style_attr.split(";"):
        name, sep, value = chunk.partition(":")
        if sep:
            declarations[name.strip().lower()] = value.strip()
    return declarations


def _style_from_tags(tag: str, declarations: dict[str, str]) -> InlineStyle:
    """Combine a tag's semantics with its inline style attribute."""
    bold = tag in {"strong", "b"}
    italic = tag in {"em", "i"}
    underline = tag in {"u", "ins"}
    strike = tag in {"s", "del", "strike"}
    code = tag == "code"

    weight = declarations.get("font-weight", "").lower()
    if weight in {"bold", "bolder"} or (weight.isdigit() and int(weight) >= 600):
        bold = True

    if declarations.get("font-style", "").lower() in {"italic", "oblique"}:
        italic = True

    decoration = (
        f"{declarations.get('text-decoration', '')} "
        f"{declarations.get('text-decoration-line', '')}"
    )
    if "underline" in decoration:
        underline = True
    if "line-through" in decoration:
        strike = True

    if tag == "mark":
        highlight: str | None = normalize_color(declarations.get("background-color")) or "#ffff00"
    else:
        highlight = normalize_color(declarations.get("background-color"))

    return InlineStyle(
        bold=bold,
        italic=italic,
        underline=underline,
        strike=strike,
        code=code,
        color=normalize_color(declarations.get("color")),
        highlight=highlight,
    )


def _align_from(attrs: dict[str, str | None]) -> str | None:
    """Resolve text alignment from ``text-align``, the legacy ``align``
    attribute, or an inline ``style`` declaration -- in that order."""
    for candidate in (
        attrs.get("text-align"),
        attrs.get("align"),
        _declarations(attrs.get("style")).get("text-align"),
    ):
        value = (candidate or "").strip().lower()
        if value in _ALIGNMENTS:
            return value
    return None


def _merge_style(base: InlineStyle, extra: InlineStyle) -> InlineStyle:
    """Combine an outer style with an inner one; the inner style wins."""
    if extra.is_plain:
        return base
    if base.is_plain:
        return extra
    return InlineStyle(
        bold=base.bold or extra.bold,
        italic=base.italic or extra.italic,
        underline=base.underline or extra.underline,
        strike=base.strike or extra.strike,
        code=base.code or extra.code,
        color=extra.color or base.color,
        highlight=extra.highlight or base.highlight,
    )


# --------------------------------------------------------------------------- #
# Frame model
# --------------------------------------------------------------------------- #

#: How each block-level tag is represented.
@dataclass(frozen=True, slots=True)
class _TagSpec:
    kind: str | None  # ``None`` for a transparent structural wrapper
    holds_blocks: bool
    accepts_inline: bool
    heading_level: int | None = None


_TAG_SPECS: Final[dict[str, _TagSpec]] = {
    "p": _TagSpec("paragraph", holds_blocks=False, accepts_inline=True),
    **{tag: _TagSpec("heading", False, True, level) for tag, level in _HEADING_TAGS.items()},
    "ul": _TagSpec("bulletList", holds_blocks=True, accepts_inline=False),
    "ol": _TagSpec("orderedList", holds_blocks=True, accepts_inline=False),
    # A list item owns its own label *and* may contain a nested sub-list, so it
    # is the one block that both holds blocks and accepts inline text.
    "li": _TagSpec("listItem", holds_blocks=True, accepts_inline=True),
    "blockquote": _TagSpec("blockquote", holds_blocks=True, accepts_inline=False),
    "pre": _TagSpec("codeBlock", holds_blocks=False, accepts_inline=True),
    "hr": _TagSpec("horizontalRule", holds_blocks=False, accepts_inline=False),
    "div": _TagSpec("paragraph", holds_blocks=True, accepts_inline=False),
    "section": _TagSpec("paragraph", holds_blocks=True, accepts_inline=False),
    "article": _TagSpec("paragraph", holds_blocks=True, accepts_inline=False),
    "main": _TagSpec("paragraph", holds_blocks=True, accepts_inline=False),
}


class _Frame:
    """Mutable builder state for one open block element."""

    __slots__ = (
        "align",
        "children",
        "implicit",
        "is_page",
        "language",
        "runs",
        "spec",
        "tag",
    )

    def __init__(self, spec: _TagSpec, tag: str, *, is_page: bool = False, implicit: bool = False) -> None:
        self.spec = spec
        self.tag = tag
        self.is_page = is_page
        #: True when this paragraph was synthesised to hold stray text rather
        #: than appearing in the source as an explicit ``<p>``.
        self.implicit = implicit
        self.align: str | None = None
        self.language: str | None = None
        self.runs: list[TextRun] = []
        self.children: list[_Frame] = []

    @property
    def holds_blocks(self) -> bool:
        return self.spec.holds_blocks

    @property
    def accepts_inline(self) -> bool:
        return self.spec.accepts_inline

    @property
    def is_empty(self) -> bool:
        return not self.runs and not self.children

    @property
    def text(self) -> str:
        if self.accepts_inline:
            return "".join(run.text for run in self.runs)
        return "".join(child.text for child in self.children)

    def add_text(self, text: str, style: InlineStyle) -> None:
        if not text:
            return
        if self.runs and self.runs[-1].style == style:
            previous = self.runs[-1]
            self.runs[-1] = TextRun(text=previous.text + text, style=style)
        else:
            self.runs.append(TextRun(text=text, style=style))

    def to_block(self) -> Block | None:
        """Materialise this frame, or return ``None`` if it carries nothing.

        A paragraph *synthesised* to capture stray text between two block
        elements is dropped when it holds only whitespace, so pretty-printed
        HTML does not gain an empty paragraph before every heading. An
        explicit ``<p>   </p>`` is a deliberate empty paragraph in a word
        processor, so it is always kept.
        """
        if self.implicit and not self.text.strip():
            return None
        children = tuple(
            child for block in map(_Frame.to_block, self.children) if (child := block)
        )
        if not self.runs and not children and self.spec.holds_blocks:
            # A container that never received content. Leaf blocks are exempt:
            # an empty `<p>`, `<h1>` or `<hr>` is meaningful content.
            return None
        kind = "page" if self.is_page else self.spec.kind
        if kind is None:  # pragma: no cover - defensive
            kind = "paragraph"
        return Block(
            kind=kind,
            runs=tuple(self.runs),
            children=children,
            level=self.spec.heading_level,
            align=self.align,
            language=self.language,
        )


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


class _DocumentBuilder(HTMLParser):
    """Builds a :class:`ParsedDocument` from a possibly-malformed fragment.

    The builder keeps an explicit stack of open elements. HTML permits implicit
    closes (``<li>`` directly inside ``<li>``, ``<p>`` inside ``<p>``), so a tag
    that was never explicitly closed is simply unwound when an ancestor or the
    end of input is reached. Inline text is flushed whenever a block boundary is
    crossed, which keeps run content in the correct block regardless of how
    sloppy the input is.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self._roots: list[list[_Frame]] = [[]]
        self._stack: list[_Frame] = []
        self._styles: list[InlineStyle] = [PLAIN_STYLE]
        self._pending = ""
        self._skip_depth = 0
        self._in_title = False

    # -- placement helpers ------------------------------------------------- #

    def _block_parent(self) -> list[_Frame]:
        """The child list that a new block element should be appended to."""
        for frame in reversed(self._stack):
            if frame.holds_blocks:
                return frame.children
        return self._roots[-1]

    def _inline_target(self) -> _Frame:
        """The frame that pending inline text belongs to.

        When no open element accepts inline text -- bare text directly inside a
        ``<ul>`` or a page wrapper, say -- an implicit paragraph is created so
        the text is never dropped.
        """
        for frame in reversed(self._stack):
            if frame.accepts_inline:
                return frame
        parent = self._stack[-1] if self._stack and self._stack[-1].holds_blocks else None
        implicit = _Frame(_TAG_SPECS["p"], "p", implicit=True)
        if parent is not None:
            parent.children.append(implicit)
        else:
            self._roots[-1].append(implicit)
        return implicit

    def _flush_inline(self) -> None:
        if self._pending:
            self._inline_target().add_text(self._pending, self._styles[-1])
            self._pending = ""

    # -- HTMLParser hooks -------------------------------------------------- #

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs = {key.lower(): value for key, value in attrs_list}

        if self._skip_depth:
            if tag in _SKIP_CONTENT and tag not in _VOID_TAGS:
                self._skip_depth += 1
            return
        if tag in _SKIP_CONTENT:
            if tag == "title":
                self._in_title = True
            self._skip_depth = 1
            return

        if tag == "br":
            self._pending += "\n"
            return
        if tag in _VOID_TAGS:
            return

        spec = _TAG_SPECS.get(tag)
        if spec is None:
            # An inline formatting element. Commit any text buffered so far
            # under the *outer* style before pushing this one, otherwise
            # ``a<strong>b</strong>`` would retroactively bold the ``a``.
            self._flush_inline()
            if (
                tag == "code"
                and self._stack
                and self._stack[-1].tag == "pre"
                and self._stack[-1].language is None
            ):
                # TipTap and most highlighters put the language on the <code>
                # inside <pre>, so pick it up from there.
                self._stack[-1].language = attrs.get("class")
            self._styles.append(
                _merge_style(
                    self._styles[-1],
                    _style_from_tags(tag, _declarations(attrs.get("style"))),
                )
            )
            return

        self._flush_inline()
        frame = _Frame(spec, tag)
        frame.align = _align_from(attrs)
        if tag == "pre" and attrs.get("class"):
            frame.language = attrs["class"]
        frame.is_page = tag == "div" and "page" in (attrs.get("class") or "").split()
        self._imply_close(tag)
        self._block_parent().append(frame)
        self._stack.append(frame)
        if tag == "hr":
            # `<hr>` is a leaf that is never explicitly closed.
            self._close_top()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle ``<tag/>`` as an immediate open-then-close."""
        tag = tag.lower()
        if tag in _SKIP_CONTENT:
            return
        if tag in _TAG_SPECS:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if self._skip_depth:
            if tag in _SKIP_CONTENT:
                if tag == "title":
                    self._in_title = False
                self._skip_depth -= 1
            return
        if tag in _VOID_TAGS:
            return
        if tag == "hr":
            # `<hr>` self-closes when it is opened, so there is nothing to pop.
            return
        if tag not in _TAG_SPECS:
            # Inline formatting element: commit its text under the style it
            # established, then pop the style for whatever follows.
            if tag in _INLINE_TAGS:
                self._flush_inline()
                if len(self._styles) > 1:
                    self._styles.pop()
            return

        self._flush_inline()
        self._close_top(expecting=tag)

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        if self._in_title:
            self.title += data.strip()
            return
        self._pending += data

    # -- lifecycle --------------------------------------------------------- #

    def _imply_close(self, tag: str) -> None:
        """Apply HTML5's optional-end-tag rule for list items.

        ``<li>a<li>b`` means two sibling list items, not a nested one, so a new
        ``li`` whose immediate parent is an open ``li`` closes that parent. The
        rule is deliberately limited to the *immediate* parent: when something
        sits between the two items (``<li>a<ul><li>b</li></ul><li>c``) the
        intervening ``<ul>`` must stay on the stack so its own ``</ul>`` still
        matches it instead of closing the outer list.
        """
        if tag == "li" and self._stack and self._stack[-1].tag == "li":
            self._stack.pop()
            while len(self._styles) > 1:
                self._styles.pop()

    def _close_top(self, expecting: str | None = None) -> None:
        """Close the innermost open element, tolerating mismatched tags.

        ``expecting`` unwinds the stack up to and including the nearest matching
        element. A closing tag with no matching opener is discarded rather than
        allowed to corrupt the tree.
        """
        if not self._stack:
            return
        if expecting is not None:
            depth = next(
                (i for i in range(len(self._stack) - 1, -1, -1) if self._stack[i].tag == expecting),
                -1,
            )
            if depth < 0:
                return
            del self._stack[depth:]
        else:
            self._stack.pop()
        while len(self._styles) > 1:
            self._styles.pop()

    def close(self) -> None:
        super().close()
        self._skip_depth = 0
        self._in_title = False
        self._flush_inline()
        while self._stack:
            self._close_top()
        # Inline text that was never wrapped in a block element at all.
        if self._pending:
            self._roots[-1].append(_Frame(_TAG_SPECS["p"], "p"))
            self._flush_inline()

    def result(self) -> ParsedDocument:
        blocks = tuple(
            block
            for root in self._roots
            for block in map(_Frame.to_block, root)
            if block is not None
        )
        return ParsedDocument(blocks=blocks, title=self.title, meta=dict(self.meta))


def parse_document(html: str, *, title: str = "") -> ParsedDocument:
    """Parse an HTML fragment into a :class:`ParsedDocument`.

    Never raises: malformed input yields a best-effort tree, and empty input
    yields an empty document.
    """
    if not html or not html.strip():
        return ParsedDocument(blocks=(), title=title)

    builder = _DocumentBuilder()
    # ``HTMLParser`` is extraordinarily tolerant, but a caller must never be
    # able to crash document loading with hand-crafted content.
    with contextlib.suppress(Exception):
        builder.feed(html)
    with contextlib.suppress(Exception):
        builder.close()
    return builder.result()


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #


def _render_blocks(blocks: tuple[Block, ...], indent: str = "") -> list[str]:
    lines: list[str] = []
    for block in blocks:
        lines.extend(_render_block(block, indent))
    return lines


def _render_block(block: Block, indent: str = "") -> list[str]:
    if block.kind == "horizontalRule":
        return [f"{indent}---"]
    if block.kind in {"bulletList", "orderedList"}:
        lines: list[str] = []
        for index, item in enumerate(block.children, start=1):
            marker = "- " if block.kind == "bulletList" else f"{index}. "
            own = item.runs and "".join(run.text for run in item.runs).strip()
            lines.append(f"{indent}{marker}{own}".rstrip())
            # Anything below the item's own label is its content, indented.
            lines.extend(_render_blocks(item.children, indent + "  "))
        return lines
    if block.kind == "listItem":
        own = block.text.strip()
        nested = _render_blocks(block.children, indent + "  ")
        return ([f"{indent}{own}"] if own else []) + nested
    if block.kind in {"page", "blockquote"}:
        return _render_blocks(block.children, indent)
    if block.kind == "codeBlock":
        return [f"{indent}{line}" for line in (block.text.rstrip("\n").splitlines() or [""])]
    text = block.text
    return [f"{indent}{text}"] if text else []


def to_plain_text(html: str) -> str:
    """Return the readable text of an HTML fragment.

    Block boundaries become newlines, list items are prefixed, and inline marks
    are discarded. This is what the LLM bridge and the ``.txt`` export consume.
    """
    parsed = parse_document(html)
    lines = [line.rstrip() for line in _render_blocks(parsed.blocks)]
    # Keep a single blank line where the document had a deliberate empty
    # paragraph, but never a run of them.
    collapsed: list[str] = []
    for line in lines:
        if line or (collapsed and collapsed[-1]):
            collapsed.append(line)
    while collapsed and not collapsed[-1]:
        collapsed.pop()
    return "\n".join(collapsed)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

#: A word is a run of word characters, optionally joined by an internal
#: apostrophe so ``don't`` counts once rather than twice. Underscores are word
#: characters, matching how mainstream word processors count ``under_score``.
_WORD_RE: Final = re.compile(r"\w+(?:['\u2019]\w+)*", re.UNICODE)


def count_words(text: str) -> int:
    """Count words the way a word processor status bar does."""
    return len(_WORD_RE.findall(text))
