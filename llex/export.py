"""Document export to interchange formats.

The editor is the browser, so the only thing the backend can export is the HTML
the editor produces. This module turns that HTML into real files using the
structural tree from :mod:`llex.markup`.

Supported formats
-----------------
=========  ==========================================================
``.txt``   Plain text, one block per line.
``.md``    CommonMark, with headings, emphasis, lists and code fences.
``.html``  A standalone, self-contained page with print-ready CSS.
``.rtf``   Rich Text Format, readable by every mainstream word processor.
``.docx``  Office Open XML via ``python-docx``.
``.odt``   OpenDocument Text via ``odfpy``.
``.epub``  EPUB 3, a zip container with navigation.
``.zip``   The standalone HTML page, for sharing.
=========  ==========================================================

PDF is intentionally absent. Producing a faithful PDF from HTML needs a layout
and rasterisation engine -- the very dependency the project moved away from --
and any pure-Python approximation would silently emit documents whose
pagination did not match the editor. Print to PDF from the editor window is the
honest route, and the editor is already laid out for it.

:func:`Exporter.render` is pure with respect to I/O: it returns ``bytes`` and
:meth:`Exporter.write` puts them on disk. That keeps every format testable
without touching the filesystem.
"""

from __future__ import annotations

import hashlib
import html as html_module
import re
import uuid
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Final

from .document import Document
from .markup import Block, InlineStyle, ParsedDocument, TextRun, parse_document, to_plain_text

__all__ = [
    "SUPPORTED_FORMATS",
    "ExportError",
    "ExportResult",
    "Exporter",
    "export_document",
]

#: Extensions this module can write, mapped to a human-readable name. The API
#: and the File > Download menu are both generated from this, so the offered
#: formats and the implemented formats cannot drift apart.
SUPPORTED_FORMATS: Final[dict[str, str]] = {
    ".txt": "Plain Text",
    ".md": "Markdown",
    ".html": "Web Page",
    ".rtf": "Rich Text Format",
    ".docx": "Microsoft Word",
    ".odt": "OpenDocument Text",
    ".epub": "EPUB Publication",
    ".zip": "Web Page Archive",
}

_LIST_KINDS: Final = frozenset({"bulletList", "orderedList"})

_ALIGN_TO_RTF: Final = {
    "left": r"\ql",
    "center": r"\qc",
    "right": r"\qr",
    "justify": r"\qj",
}

_ALIGN_TO_ODT: Final = {
    "left": "start",
    "center": "center",
    "right": "end",
    "justify": "justify",
}

#: RTF page geometry is expressed in twips (1/1440 inch).
_TWIPS_PER_INCH: Final = 1440

#: Heading font sizes in half-points, matching the editor's typographic scale.
_HEADING_HALF_POINTS: Final = {1: 52, 2: 30, 3: 40, 4: 32, 5: 28, 6: 24}

_INVALID_FILENAME_RE: Final = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class ExportError(RuntimeError):
    """Raised when a document cannot be rendered to the requested format."""


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _normalise_suffix(suffix: str) -> str:
    key = (suffix or "").strip().lower()
    if not key.startswith("."):
        key = f".{key}"
    return key


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    """``#abc``/``#aabbcc`` to an integer triple, falling back to black."""
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return 0, 0, 0
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return 0, 0, 0


def _escape_markdown(text: str) -> str:
    """Backslash-escape the characters that would otherwise be syntax."""
    return re.sub(r"([\\`*_\[\]])", r"\\\1", text)


def _iter_leaf_blocks(blocks: Sequence[Block]) -> Iterable[Block]:
    """Yield blocks with the ``page`` wrapper flattened away.

    ``page`` exists only to preserve physical page breaks for the editor. No
    interchange format needs it, and carrying it through would add a spurious
    level of nesting to every writer.
    """
    for block in blocks:
        if block.kind == "page":
            yield from _iter_leaf_blocks(block.children)
        else:
            yield block


# --------------------------------------------------------------------------- #
# Plain text
# --------------------------------------------------------------------------- #


def write_txt(document: Document, parsed: ParsedDocument) -> bytes:
    """Plain text: one block per line, list markers preserved."""
    return (to_plain_text(document.content) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #

_MD_ORDER: Final = ".,;:!?)"
_MD_CLOSE: Final = "*_"


def _md_emphasis(text: str, style: InlineStyle) -> str:
    """Wrap ``text`` in Markdown emphasis according to ``style``."""
    if not text:
        return ""
    for marker in (style.code and "`", style.bold and "**", style.italic and "*", style.strike and "~~"):
        if marker:
            text = f"{marker}{text}{marker}"
    return text


def _collapse_blank_lines(lines: Sequence[str]) -> list[str]:
    """Squash runs of blank lines down to a single separator.

    Every block appends its own trailing blank line, so nesting them would
    otherwise leave three or four blank lines between paragraphs.
    """
    out: list[str] = []
    for line in lines:
        if not line.strip() and (not out or not out[-1].strip()):
            continue
        out.append(line.rstrip())
    return out


def _code_language(raw: str | None) -> str:
    """Normalise a language hint to a bare token for a Markdown fence.

    Accepts ``python``, ``language-python`` and ``lang-python``, since the hint
    is carried on a ``class`` attribute whose format varies by highlighter.
    """
    if not raw:
        return ""
    token = raw.strip().split()[-1] if raw.strip() else ""
    for prefix in ("language-", "lang-"):
        if token.startswith(prefix):
            token = token[len(prefix) :]
    return token if token.isidentifier() else ""


def write_md(document: Document, parsed: ParsedDocument) -> bytes:
    """CommonMark, preserving the structure the editor models."""

    def inline(runs: Iterable[TextRun]) -> str:
        return "".join(_md_emphasis(_escape_markdown(run.text), run.style) for run in runs)

    def render(blocks: Sequence[Block], depth: int = 0, quote: str = "") -> list[str]:
        pad = "  " * depth
        lines: list[str] = []
        for block in _iter_leaf_blocks(blocks):
            kind = block.kind
            if kind == "horizontalRule":
                lines.extend([f"{quote}---", ""])
            elif kind in _LIST_KINDS:
                for index, item in enumerate(block.children, start=1):
                    marker = "-" if kind == "bulletList" else f"{index}."
                    lines.append(f"{quote}{pad}{marker} {inline(item.runs)}".rstrip())
                    lines.extend(render(item.children, depth + 1, quote))
                lines.append("")
            elif kind == "listItem":
                lines.append(f"{quote}{pad}- {inline(block.runs)}".rstrip())
                lines.extend(render(block.children, depth + 1, quote))
            elif kind == "codeBlock":
                language = _code_language(block.language)
                body = block.text.rstrip("\n").splitlines()
                if quote:
                    lines.append(f"{quote}    ```{language}")
                    lines.extend(f"{quote}    {line}" for line in body)
                    lines.append(f"{quote}    ```")
                else:
                    lines.append(f"{pad}```{language}")
                    lines.extend(f"{pad}{line}" for line in body)
                    lines.append(f"{pad}```")
                lines.append("")
            elif kind == "blockquote":
                lines.extend(render(block.children, depth, f"{quote}> "))
            elif kind == "heading":
                level = min(max(block.level or 1, 1), 6)
                lines.extend([f"{quote}{'#' * level} {inline(block.runs)}".rstrip(), ""])
            else:
                lines.extend([f"{quote}{inline(block.runs)}", ""])
        return lines

    return ("\n".join(_collapse_blank_lines(render(parsed.blocks))).rstrip() + "\n").encode("utf-8")


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #


def _html_marks(style: InlineStyle) -> list[str]:
    """Map an inline style onto opening HTML tags, outermost first."""
    tags: list[str] = []
    if style.code:
        tags.append("code")
    if style.bold:
        tags.append("strong")
    if style.italic:
        tags.append("em")
    if style.underline:
        tags.append("u")
    if style.strike:
        tags.append("s")
    if style.highlight:
        tags.append(f'mark style="background-color: {style.highlight}"')
    if style.color:
        tags.append(f'span style="color: {style.color}"')
    return tags


def _html_inline(runs: Iterable[TextRun]) -> str:
    out: list[str] = []
    for run in runs:
        text = html_module.escape(run.text, quote=False)
        if not text:
            continue
        for tag in _html_marks(run.style):
            text = f"<{tag}>{text}</{tag.split(' ', 1)[0]}>"
        out.append(text)
    return "".join(out)


def _html_body(parsed: ParsedDocument) -> str:
    """Render the parsed tree as an HTML fragment."""
    parts: list[str] = []

    def emit(blocks: Sequence[Block]) -> None:
        for block in _iter_leaf_blocks(blocks):
            kind = block.kind
            align = f' style="text-align: {block.align}"' if block.align else ""
            if kind == "horizontalRule":
                parts.append("<hr>")
            elif kind == "heading":
                level = min(max(block.level or 1, 1), 6)
                parts.append(f"<h{level}{align}>{_html_inline(block.runs)}</h{level}>")
            elif kind == "codeBlock":
                klass = f' class="language-{html_module.escape(_code_language(block.language), quote=True)}"' if _code_language(block.language) else ""
                parts.append(f"<pre><code{klass}>{html_module.escape(block.text)}</code></pre>")
            elif kind == "blockquote":
                parts.append(f"<blockquote{align}>")
                emit(block.children)
                parts.append("</blockquote>")
            elif kind in _LIST_KINDS:
                tag = "ul" if kind == "bulletList" else "ol"
                parts.append(f"<{tag}>")
                for item in block.children:
                    parts.append(f"<li>{_html_inline(item.runs)}")
                    emit(item.children)
                    parts.append("</li>")
                parts.append(f"</{tag}>")
            else:
                parts.append(f"<p{align}>{_html_inline(block.runs)}</p>")

    emit(parsed.blocks)
    return "\n".join(parts)


_STANDALONE_CSS: Final = """
  :root {{ color-scheme: light; }}
  body {{
    margin: 0 auto;
    padding: 2rem 1rem;
    max-width: {width}px;
    font-family: Georgia, "Times New Roman", serif;
    font-size: 11pt;
    line-height: 1.5;
    color: #111;
    background: #f4f4f5;
  }}
  article {{
    background: #fff;
    padding: {pad_top}px {pad_right}px {pad_bottom}px {pad_left}px;
    box-shadow: 0 1px 4px rgba(0, 0, 0, .18);
  }}
  h1, h2, h3, h4, h5, h6 {{ page-break-after: avoid; line-height: 1.2; }}
  h1 {{ font-size: 26pt; font-weight: 400; margin: 0 0 6pt; }}
  h2 {{ font-size: 15pt; font-weight: 400; color: #555; margin: 0 0 18pt; }}
  h3 {{ font-size: 20pt; font-weight: 600; margin: 18pt 0 6pt; }}
  h4 {{ font-size: 16pt; font-weight: 600; margin: 14pt 0 4pt; }}
  h5 {{ font-size: 14pt; margin: 12pt 0 4pt; }}
  h6 {{ font-size: 12pt; font-weight: 600; color: #555; margin: 12pt 0 4pt; }}
  p {{ margin: 0 0 6pt; }}
  pre {{
    background: #f5f5f5; border: 1px solid #ddd; border-radius: 4px;
    padding: .75em; overflow-x: auto; white-space: pre-wrap;
    font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
    font-size: 10pt;
  }}
  blockquote {{ margin: 0 0 6pt; padding-left: 1em; border-left: 3px solid #ddd; color: #444; }}
  hr {{ border: 0; border-top: 1px solid #ccc; margin: 12pt 0; }}
  @media print {{
    body {{ background: #fff; padding: 0; max-width: none; }}
    article {{ box-shadow: none; padding: 0; }}
  }}
"""


def write_html(document: Document, parsed: ParsedDocument) -> bytes:
    """A standalone HTML page with print CSS matching the editor's page setup."""
    margins = document.page.margins_px
    css = _STANDALONE_CSS.format(
        width=document.page.pixel_width,
        pad_top=margins["top"],
        pad_right=margins["right"],
        pad_bottom=margins["bottom"],
        pad_left=margins["left"],
    )
    title = html_module.escape(document.title, quote=False)
    markup = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n"
        f"<style>{css}</style>\n"
        "</head>\n<body>\n<article>\n"
        f"{_html_body(parsed)}\n"
        "</article>\n</body>\n</html>\n"
    )
    return markup.encode("utf-8")


def write_zip(document: Document, parsed: ParsedDocument) -> bytes:
    """The standalone HTML page, bundled for sharing."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", write_html(document, parsed))
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# RTF
# --------------------------------------------------------------------------- #


def _rtf_escape(text: str) -> str:
    """Encode text per the RTF spec: ASCII literal, everything else ``\\uN?``.

    Surrogate pairs are required for code points outside the BMP; the low word
    is encoded first, as the spec mandates.
    """
    out: list[str] = []
    for char in text:
        if char in "\\{}":
            out.append("\\" + char)
        elif char == "\n":
            out.append(r"\line ")
        elif char == "\t":
            out.append(r"\tab ")
        else:
            point = ord(char)
            if point < 128:
                out.append(char)
            elif point <= 0xFFFF:
                out.append(f"\\u{_rtf_signed(point)}?")
            else:
                offset = point - 0x10000
                out.append(f"\\u{_rtf_signed(0xD800 + (offset >> 10))}?")
                out.append(f"\\u{_rtf_signed(0xDC00 + (offset & 0x3FF))}?")
    return "".join(out)


def _rtf_signed(code_unit: int) -> int:
    """RTF ``\\u`` takes a *signed* 16-bit value."""
    return code_unit - 0x10000 if code_unit > 0x7FFF else code_unit


def write_rtf(document: Document, parsed: ParsedDocument) -> bytes:
    """Rich Text Format, including a colour table and non-ASCII escaping."""
    colors: list[str] = []
    for block in parsed.iter_blocks():
        for run in block.runs:
            for color in (run.style.color, run.style.highlight):
                if color and color not in colors:
                    colors.append(color)
    # \cfN indexes into the colour table positionally, so the table must be
    # fully known before any index can be emitted.
    color_index = {color: position for position, color in enumerate(colors, start=1)}

    def run_markup(run: TextRun, force_bold: bool = False) -> str:
        style = run.style
        if force_bold:
            style = replace(style, bold=True)
        codes = ""
        if style.bold:
            codes += r"\b"
        if style.italic:
            codes += r"\i"
        if style.underline:
            codes += r"\ul"
        if style.strike:
            codes += r"\strike"
        if style.code:
            codes += r"\f1"
        if style.color:
            codes += rf"\cf{color_index[style.color]}"
        if style.highlight:
            codes += rf"\highlight{color_index[style.highlight]}"
        text = _rtf_escape(run.text)
        if not text:
            return ""
        return f"{{{codes} {text}}}" if codes else text

    def inline(runs: Iterable[TextRun], force_bold: bool = False) -> str:
        return "".join(run_markup(run, force_bold) for run in runs)

    def paragraph(content: str, align: str = "", extra: str = "") -> str:
        code = _ALIGN_TO_RTF.get(align, "")
        return f"\\pard{code}{extra} {content}\\par\n" if content or extra else ""

    def emit(blocks: Sequence[Block]) -> None:
        for block in _iter_leaf_blocks(blocks):
            kind = block.kind
            if kind == "horizontalRule":
                out.append(r"\pard\brdrb\brdrs\brdrw10\brsp20 \par")
            elif kind == "heading":
                level = min(max(block.level or 1, 1), 6)
                size = _HEADING_HALF_POINTS[level]
                out.append(
                    paragraph(
                        inline(block.runs, force_bold=True),
                        block.align or "left",
                        rf"\outlinelevel{max(level - 1, 0)}\fs{size}",
                    )
                )
            elif kind == "codeBlock":
                out.append(paragraph(_rtf_escape(block.text), block.align or "left", r"\f1\fs18"))
            elif kind == "blockquote":
                out.append(r"\pard\li360\fi-360 ")
                emit(block.children)
                out.append(r"\pard ")
            elif kind in _LIST_KINDS:
                for index, item in enumerate(block.children, start=1):
                    marker = r"\bullet\tab" if kind == "bulletList" else r"\numbers\tab"
                    label = f"{index}\\tab" if kind == "orderedList" else ""
                    out.append(rf"\pard\fi-360\li360 {marker}{label}{inline(item.runs)}")
                    emit(item.children)
                    out.append(r"\par")
            else:
                out.append(paragraph(inline(block.runs), block.align or "left"))

    out: list[str] = []
    emit(parsed.blocks)

    page = document.page
    color_table = "".join(
        f"\\red{r}\\green{g}\\blue{b};" for r, g, b in map(_hex_to_rgb, colors)
    )
    header = "".join(
        (
            r"{\rtf1\ansi\ansicpg1252\deff0\deflang1033",
            r"{\fonttbl{\f0\fswiss Calibri;}{\f1\fmodern Consolas;}}",
            r"{\colortbl;",
            color_table,
            "}",
            r"\viewkind4\uc1",
            rf"\paperw{round(page.width * _TWIPS_PER_INCH)}",
            rf"\paperh{round(page.height * _TWIPS_PER_INCH)}",
            rf"\margl{round(page.margin_left * _TWIPS_PER_INCH)}",
            rf"\margr{round(page.margin_right * _TWIPS_PER_INCH)}",
            rf"\margt{round(page.margin_top * _TWIPS_PER_INCH)}",
            rf"\margb{round(page.margin_bottom * _TWIPS_PER_INCH)}",
            "}",
        )
    )
    return (header + "".join(out) + "}").encode("ascii", errors="replace")


# --------------------------------------------------------------------------- #
# DOCX
# --------------------------------------------------------------------------- #

#: CSS highlight colours mapped onto python-docx's enumerated values.
_HIGHLIGHT_ALIASES: Final = {
    "#ffff00": "YELLOW",
    "#00ff00": "BRIGHT_GREEN",
    "#00ffff": "TURQUOISE",
    "#ff00ff": "PINK",
    "#0000ff": "BLUE",
    "#ff0000": "RED",
    "#000080": "DARK_BLUE",
    "#008080": "TEAL",
    "#008000": "GREEN",
    "#800080": "VIOLET",
    "#800000": "DARK_RED",
    "#808000": "DARK_YELLOW",
    "#808080": "GRAY_50",
    "#c0c0c0": "GRAY_25",
}


def _highlight_enum(index: Any, color: str | None) -> Any:
    """Resolve a hex highlight colour to a ``WD_COLOR_INDEX`` member.

    Unmapped colours return ``None`` so the run is simply left unhighlighted
    rather than being given an arbitrary colour.
    """
    if not color:
        return None
    return getattr(index, _HIGHLIGHT_ALIASES.get(color.lower(), ""), None)


def write_docx(document: Document, parsed: ParsedDocument) -> bytes:
    """Office Open XML via ``python-docx``."""
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
    from docx.shared import Inches, Pt, RGBColor

    align_map = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }

    package = docx.Document()
    core = package.core_properties
    core.title = document.title
    core.modified = document.modified_at

    page = document.page
    for section in package.sections:
        section.page_width = Inches(page.width)
        section.page_height = Inches(page.height)
        section.top_margin = Inches(page.margin_top)
        section.right_margin = Inches(page.margin_right)
        section.bottom_margin = Inches(page.margin_bottom)
        section.left_margin = Inches(page.margin_left)

    def add_runs(paragraph: Any, runs: Iterable[TextRun], *, force_bold: bool = False) -> None:
        for run in runs:
            if not run.text:
                continue
            style = run.style
            created = paragraph.add_run(run.text)
            created.bold = True if force_bold else style.bold
            created.italic = style.italic
            created.underline = bool(style.underline)
            created.font.strike = bool(style.strike)
            if style.code:
                created.font.name = "Consolas"
            if style.color:
                created.font.color.rgb = RGBColor(*_hex_to_rgb(style.color))
            highlight = _highlight_enum(WD_COLOR_INDEX, style.highlight)
            if highlight is not None:
                created.font.highlight_color = highlight

    def align(paragraph: Any, block: Block) -> None:
        if block.align in align_map:
            paragraph.alignment = align_map[block.align]

    def emit(blocks: Sequence[Block]) -> None:
        for block in _iter_leaf_blocks(blocks):
            kind = block.kind
            if kind == "horizontalRule":
                package.add_paragraph("_" * 40)
            elif kind == "heading":
                level = min(max(block.level or 1, 1), 6)
                paragraph = package.add_paragraph(style=f"Heading {level}")
                align(paragraph, block)
                add_runs(paragraph, block.runs, force_bold=True)
            elif kind == "codeBlock":
                paragraph = package.add_paragraph()
                paragraph.paragraph_format.left_indent = Pt(18)
                runs = block.runs or (TextRun(block.text, InlineStyle(code=True)),)
                for run in runs:
                    created = paragraph.add_run(run.text)
                    created.font.name = "Consolas"
            elif kind == "blockquote":
                for child in _iter_leaf_blocks(block.children):
                    paragraph = package.add_paragraph()
                    paragraph.paragraph_format.left_indent = Pt(24)
                    align(paragraph, child)
                    add_runs(paragraph, child.runs)
            elif kind in _LIST_KINDS:
                list_style = "List Bullet" if kind == "bulletList" else "List Number"
                for item in block.children:
                    paragraph = package.add_paragraph(style=list_style)
                    add_runs(paragraph, item.runs)
                    emit(item.children)
            else:
                paragraph = package.add_paragraph()
                align(paragraph, block)
                add_runs(paragraph, block.runs)

    emit(parsed.blocks)

    buffer = BytesIO()
    package.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# ODT
# --------------------------------------------------------------------------- #


def write_odt(document: Document, parsed: ParsedDocument) -> bytes:
    """OpenDocument Text via ``odfpy``."""
    from odf.opendocument import OpenDocumentText
    from odf.style import (
        MasterPage,
        PageLayout,
        PageLayoutProperties,
        ParagraphProperties,
        Style,
        TextProperties,
    )
    from odf.text import H, List, ListItem, P, Span

    package = OpenDocumentText()
    package.meta.title = document.title

    page = document.page
    layout = PageLayout(name="pm1")
    layout.addElement(
        PageLayoutProperties(
            pagewidth=f"{page.width}in",
            pageheight=f"{page.height}in",
            printorientation="portrait",
        )
    )
    package.automaticstyles.addElement(layout)
    master = MasterPage(name="Standard", pagelayoutname="pm1")
    package.masterstyles.addElement(master)

    text_styles: dict[tuple[Any, ...], str] = {}
    paragraph_styles: dict[str, str] = {}

    def text_style(run: TextRun) -> str:
        style = run.style
        signature = (
            style.bold,
            style.italic,
            style.underline,
            style.strike,
            style.code,
            style.color,
            style.highlight,
        )
        if signature in text_styles:
            return text_styles[signature]
        name = f"LLexT{len(text_styles)}"
        properties: dict[str, str] = {}
        if style.bold:
            properties["fontweight"] = "bold"
        if style.italic:
            properties["fontstyle"] = "italic"
        if style.underline:
            properties["textunderlinestyle"] = "solid"
            properties["textunderlinewidth"] = "auto"
        if style.strike:
            properties["textlinethroughstyle"] = "solid"
        if style.code:
            properties["fontname"] = "Consolas"
        if style.color:
            properties["color"] = style.color
        if style.highlight:
            properties["backgroundcolor"] = style.highlight
        element = Style(name=name, family="text")
        element.addElement(TextProperties(**properties))
        package.automaticstyles.addElement(element)
        text_styles[signature] = name
        return name

    def paragraph_style(block: Block) -> str:
        align = block.align or ""
        if not align or align not in _ALIGN_TO_ODT:
            return ""
        if align not in paragraph_styles:
            name = f"LLex{align.capitalize()}"
            element = Style(name=name, family="paragraph")
            element.addElement(ParagraphProperties(textalign=_ALIGN_TO_ODT[align]))
            package.automaticstyles.addElement(element)
            paragraph_styles[align] = name
        return paragraph_styles[align]

    def inline(parent: Any, runs: Iterable[TextRun]) -> None:
        for run in runs:
            if run.text:
                parent.addElement(Span(stylename=text_style(run), text=run.text))

    def add_list_item(list_element: Any, item: Block, style_name: str) -> ListItem:
        # ODF requires a list item's content to be paragraphs or headings, so
        # even a plain label is wrapped. Nested blocks go inside the item, which
        # is what preserves sub-list indentation.
        element = ListItem()
        label = P(stylename=style_name)
        inline(label, item.runs)
        element.addElement(label)
        for child in _iter_leaf_blocks(item.children):
            if child.kind in _LIST_KINDS:
                element.addElement(_build_list(child, style_name))
            elif child.kind == "heading":
                heading = H(outlinelevel=min(max(child.level or 1, 1), 6), stylename=style_name)
                inline(heading, child.runs)
                element.addElement(heading)
            else:
                element.addElement(_build_paragraph(child, style_name))
        list_element.addElement(element)
        return element

    def _build_list(block: Block, style_name: str) -> List:
        element = List()
        for item in block.children:
            add_list_item(element, item, style_name)
        return element

    def _build_paragraph(block: Block, style_name: str) -> P:
        paragraph = P(stylename=style_name)
        inline(paragraph, block.runs)
        return paragraph

    def emit(blocks: Sequence[Block]) -> None:
        for block in _iter_leaf_blocks(blocks):
            kind = block.kind
            style_name = paragraph_style(block)
            if kind == "horizontalRule":
                package.text.addElement(P(stylename=style_name, text="---"))
            elif kind == "heading":
                level = min(max(block.level or 1, 1), 6)
                heading = H(outlinelevel=level, stylename=style_name)
                inline(heading, block.runs)
                package.text.addElement(heading)
            elif kind == "codeBlock":
                runs = block.runs or (TextRun(block.text, InlineStyle(code=True)),)
                paragraph = P(stylename=style_name)
                inline(paragraph, runs)
                package.text.addElement(paragraph)
            elif kind == "blockquote":
                for child in _iter_leaf_blocks(block.children):
                    package.text.addElement(_build_paragraph(child, style_name))
            elif kind in _LIST_KINDS:
                package.text.addElement(_build_list(block, style_name))
            else:
                package.text.addElement(_build_paragraph(block, style_name))

    emit(parsed.blocks)

    buffer = BytesIO()
    package.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# EPUB
# --------------------------------------------------------------------------- #


def _stable_uuid(seed: str) -> str:
    """A deterministic RFC 4122 identifier derived from ``seed``.

    EPUB requires a unique identifier and readers key their library on it, so
    it must be *stable* across exports of the same document; a fresh random
    UUID on every save would register each export as a different book.
    """
    digest = bytearray(hashlib.sha256(seed.encode("utf-8")).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x40  # version 4 layout
    digest[8] = (digest[8] & 0x3F) | 0x80  # RFC 4122 variant
    return str(uuid.UUID(bytes=bytes(digest)))


_EPUB_CSS: Final = (
    "@page { margin: 5%; }\n"
    "body { font-family: serif; line-height: 1.5; }\n"
    "h1, h2, h3 { page-break-after: avoid; }\n"
    "pre { white-space: pre-wrap; }\n"
)


def write_epub(document: Document, parsed: ParsedDocument) -> bytes:
    """EPUB 3: a zip whose ``mimetype`` entry is first and stored uncompressed."""
    title = document.title or "Untitled Document"
    escaped = html_module.escape(title, quote=False)
    identifier = _stable_uuid(f"{title}\n{document.created_at.isoformat()}")
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    content = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">\n'
        "<head><title>"
        f"{escaped}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f"<body>\n{_html_body(parsed)}\n</body>\n</html>\n"
    )
    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        "  <rootfiles>\n"
        '    <rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n</container>\n"
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="pub-id" xml:lang="en">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f"    <dc:title>{escaped}</dc:title>\n"
        "    <dc:language>en</dc:language>\n"
        f'    <dc:identifier id="pub-id">{identifier}</dc:identifier>\n'
        f'    <meta property="dcterms:modified">{modified}</meta>\n'
        "  </metadata>\n"
        "  <manifest>\n"
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/>\n'
        '    <item id="style" href="style.css" media-type="text/css"/>\n'
        '    <item id="content" href="content.xhtml" '
        'media-type="application/xhtml+xml"/>\n'
        "  </manifest>\n"
        "  <spine>\n"
        '    <itemref idref="content"/>\n'
        "  </spine>\n</package>\n"
    )
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en">\n'
        "<head><title>Contents</title></head>\n"
        "<body>\n"
        '<nav epub:type="toc" id="toc">\n'
        "  <h1>Contents</h1>\n"
        '  <ol><li><a href="content.xhtml">Start</a></li></ol>\n'
        "</nav>\n</body>\n</html>\n"
    )

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # The OCF spec requires mimetype first, stored, and uncompressed.
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/style.css", _EPUB_CSS)
        archive.writestr("OEBPS/nav.xhtml", nav)
        archive.writestr("OEBPS/content.xhtml", content)
        archive.writestr("OEBPS/content.opf", opf)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Exporter
# --------------------------------------------------------------------------- #

_WRITERS: Final[dict[str, Callable[[Document, ParsedDocument], bytes]]] = {
    ".txt": write_txt,
    ".md": write_md,
    ".html": write_html,
    ".rtf": write_rtf,
    ".docx": write_docx,
    ".odt": write_odt,
    ".epub": write_epub,
    ".zip": write_zip,
}


@dataclass(frozen=True, slots=True)
class ExportResult:
    """What a completed export produced."""

    path: Path
    format: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "file_name": self.path.name,
            "format": self.format,
            "size": self.size,
        }


class Exporter:
    """Renders a :class:`~llex.document.Document` to an interchange format."""

    def __init__(self, document: Document) -> None:
        self.document = document

    @property
    def formats(self) -> dict[str, str]:
        """The formats offered to the user, for building the export menu."""
        return dict(SUPPORTED_FORMATS)

    def render(self, suffix: str) -> bytes:
        """Return the encoded document for ``suffix``, e.g. ``".docx"``.

        Performs no I/O, which keeps every format unit-testable.
        """
        key = _normalise_suffix(suffix)
        writer = _WRITERS.get(key)
        if writer is None:
            supported = ", ".join(sorted(SUPPORTED_FORMATS))
            raise ExportError(f"unsupported export format {suffix!r}; expected one of {supported}")
        parsed = parse_document(self.document.content, title=self.document.title)
        try:
            return bytes(writer(self.document, parsed))
        except ExportError:
            raise
        except Exception as exc:
            raise ExportError(f"could not build {key} export: {exc}") from exc

    def suggested_filename(self, suffix: str) -> str:
        """A safe default filename for a save dialog."""
        key = _normalise_suffix(suffix)
        stem = _INVALID_FILENAME_RE.sub("", self.document.title).strip().rstrip(".") or "document"
        return f"{stem[:120]}{key}"

    def write(self, path: Path | str) -> ExportResult:
        """Render the document and write it to ``path``.

        The suffix is authoritative: asking for ``.pdf`` is an error rather than
        a silent fallback to a format the user did not choose.
        """
        target = Path(path).expanduser()
        key = _normalise_suffix(target.suffix)
        payload = self.render(key)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        except OSError as exc:
            raise ExportError(f"could not write {target}: {exc}") from exc
        return ExportResult(path=target, format=key, size=len(payload))


def export_document(document: Document, path: Path | str) -> ExportResult:
    """Convenience wrapper: export ``document`` to ``path`` in one call."""
    return Exporter(document).write(path)
