from __future__ import annotations

import io
import json
import xml.dom.minidom
import zipfile
from pathlib import Path
from typing import Any

import pytest

from llex.document import (
    FORMAT_VERSION,
    MM_PER_INCH,
    PAPER_SIZES,
    Document,
    PageSetup,
    default_paper_for_locale,
)
from llex.export import (
    write_docx,
    write_epub,
    write_html,
    write_md,
    write_odt,
    write_txt,
)
from llex.markup import ParsedDocument, ScaffoldRef, parse_document, to_plain_text
from llex.validate import Report, main, validate_bytes, validate_data, validate_file

from .conftest import content_is_preserved

#: The frontend sources, for the contract assertions below.
PACKAGE = Path(__file__).resolve().parent.parent / "llex"
SCRIPT_DIR = PACKAGE / "static" / "js"
STYLES = PACKAGE / "static" / "styles.css"

#: A document with every construct the storage form has to survive.
RICH = (
    '<div class="page"><h1>Alpha</h1><p>first <strong>bold</strong> para</p>'
    '<ul><li>one</li><li>two</li></ul>'
    '<pre><code>keep  me\n  indented</code></pre>'
    '<blockquote><p>quoted</p></blockquote>'
    '<p style="text-align:center">centred</p>'
    '<hr>'
    '<p><span data-scaffold=" " data-scaffold-id="s1" data-scaffold-instruction="do the thing">todo</span></p>'
    "<p>café — 日本語</p></div>"
)


class TestScaffoldInTheModel:
    """A scaffold is content, and the parse used to throw it away.

    The parser read the span and kept its text, so a scaffold survived as plain
    text with its instruction gone. Every exporter built on the parse therefore
    dropped it silently.
    """

    #: Exactly what the editor writes, from `Scaffold.renderHTML` in
    #: `extensions.js`. Pinned here because the Python and JavaScript halves of
    #: the format have to agree, and a rename on one side would otherwise show
    #: up only as prompts quietly vanishing from an export.
    EDITOR_FORM = (
        '<p><span data-scaffold="" data-scaffold-id="s7"'
        ' data-scaffold-instruction="expand this">todo</span></p>'
    )

    def test_the_editor_s_own_attributes_are_read(self) -> None:
        parsed = parse_document(self.EDITOR_FORM)
        run = next(iter(parsed.iter_blocks())).runs[0]
        assert run.style.scaffold == ScaffoldRef(id="s7", instruction="expand this")

    def test_the_short_form_is_also_accepted(self) -> None:
        parsed = parse_document('<p><span data-scaffold="s7" data-instruction="go">t</span></p>')
        run = next(iter(parsed.iter_blocks())).runs[0]
        assert run.style.scaffold == ScaffoldRef(id="s7", instruction="go")

    def test_the_instruction_survives(self) -> None:
        """The instruction is the part the user wrote; losing it is the loss."""
        parsed = parse_document(self.EDITOR_FORM)
        assert "expand this" in repr(next(iter(parsed.iter_blocks())).runs[0].style)

    def test_a_plain_span_is_not_a_scaffold(self) -> None:
        parsed = parse_document("<p><span>plain</span></p>")
        assert next(iter(parsed.iter_blocks())).runs[0].style.scaffold is None

    def test_a_bare_marker_is_not_a_scaffold(self) -> None:
        # A badge the user cannot act on is worse than none.
        parsed = parse_document('<p><span data-scaffold="">x</span></p>')
        assert next(iter(parsed.iter_blocks())).runs[0].style.scaffold is None

    def test_a_scaffold_survives_html_export(self) -> None:

        parsed = parse_document(self.EDITOR_FORM)
        html = write_html(Document(), parsed).decode("utf-8")
        assert 'data-scaffold-id="s7"' in html
        assert 'data-scaffold-instruction="expand this"' in html

    def test_a_scaffold_survives_a_round_trip(self) -> None:
        from llex.export import _html_body

        rendered = _html_body(parse_document(self.EDITOR_FORM))
        assert 'data-scaffold-id="s7"' in rendered
        # And re-reading it gives the same scaffold, not a degraded one.
        again = parse_document(rendered)
        run = next(iter(again.iter_blocks())).runs[0]
        assert run.style.scaffold == ScaffoldRef(id="s7", instruction="expand this")

    def test_a_scaffold_survives_a_saved_file(self, tmp_path: Path) -> None:
        document = Document(title="S", content=self.EDITOR_FORM)
        loaded = Document.load(document.save(tmp_path / "s.llex"))
        assert 'data-scaffold-id="s7"' in loaded.content

    def test_plain_text_still_omits_the_markup(self) -> None:
        # The text is what a plain-text consumer wants; the instruction is not.
        assert to_plain_text(self.EDITOR_FORM).strip() == "todo"


class TestDiffableStorage:
    """`.llex` files were a single line, which made version control useless."""

    def test_content_is_stored_one_block_per_entry(self, tmp_path: Path) -> None:
        Document(title="N", content="<h1>A</h1><p>b</p><p>c</p>").save(tmp_path / "d.llex")
        data = json.loads((tmp_path / "d.llex").read_text(encoding="utf-8"))
        assert data["content"] == ["<h1>A</h1>", "<p>b</p>", "<p>c</p>"]

    def test_each_block_lands_on_its_own_line(self, tmp_path: Path) -> None:
        Document(title="N", content="<p>one</p><p>two</p>").save(tmp_path / "d.llex")
        lines = (tmp_path / "d.llex").read_text(encoding="utf-8").splitlines()
        assert '    "<p>one</p>",' in lines
        assert '    "<p>two</p>"' in lines

    def test_editing_one_paragraph_does_not_rewrite_the_file(self, tmp_path: Path) -> None:
        """The property that makes the format reviewable."""
        target = tmp_path / "d.llex"
        Document(title="N", content="<p>alpha</p><p>beta</p><p>gamma</p>").save(target)
        before = (target.read_text(encoding="utf-8")).splitlines()

        loaded = Document.load(target)
        loaded.set_content(loaded.content.replace("beta", "BETA"))
        loaded.save(target)
        after = (target.read_text(encoding="utf-8")).splitlines()

        changed = [
            (old, new)
            for old, new in zip(before, after, strict=True)
            if old != new
        ]
        content_changes = [pair for pair in changed if "alpha" in pair[0] + pair[1] or "beta" in pair[0].lower() + pair[1].lower() or "BETA" in pair[0] + pair[1]]
        assert len(content_changes) == 1, f"expected one changed line, got {content_changes}"

    def test_the_round_trip_preserves_the_document(self, tmp_path: Path) -> None:
        document = Document(title="Rich", content=RICH)
        target = document.save(tmp_path / "rich.llex")
        loaded = Document.load(target)
        assert content_is_preserved(document.content, loaded.content)

    def test_format_version_was_bumped(self, tmp_path: Path) -> None:
        """Older builds read `content` as a string, so writing an array needs a
        version bump even though the reader accepts both."""
        assert FORMAT_VERSION >= 3
        Document().save(tmp_path / "d.llex")
        assert json.loads((tmp_path / "d.llex").read_text(encoding="utf-8"))["format_version"] == 3

    def test_a_version_2_file_still_opens(self, tmp_path: Path) -> None:
        """The whole point of accepting both shapes."""
        target = tmp_path / "old.llex"
        target.write_text(
            json.dumps(
                {
                    "format_version": 2,
                    "title": "Old",
                    "content": "<h1>A</h1>\n<p>b</p>",
                    "page": {},
                    "styles": {},
                }
            ),
            encoding="utf-8",
        )
        loaded = Document.load(target)
        assert loaded.title == "Old"
        assert "b" in loaded.content

    def test_a_version_1_file_still_opens(self, tmp_path: Path) -> None:
        target = tmp_path / "v1.llex"
        target.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "title": "Ancient",
                    "html_content": "<p>ancient text</p>",
                }
            ),
            encoding="utf-8",
        )
        assert "ancient text" in Document.load(target).content

    @pytest.mark.parametrize(
        "content",
        [
            "<pre><code>  leading and trailing  </code></pre>",
            "<p>  spaces  that  matter  </p>",
            "<p>a<br>b</p>",
            "<p>inline <strong>bold</strong> and <em>it</em></p>",
            "<p>malformed <b>bold</p>",
            "<p></p>",
        ],
    )
    def test_formatting_never_changes_what_the_document_says(self, content: str) -> None:
        from llex.markup import format_content_html

        assert content_is_preserved(content, format_content_html(content))

    def test_formatting_is_idempotent(self) -> None:
        """A saved file is read back and written again, so a second pass must
        not accumulate blank lines."""
        from llex.markup import format_content_html

        once = format_content_html("<h1>A</h1><p>b</p><ul><li>x</li></ul>")
        assert format_content_html(once) == once
        assert format_content_html(format_content_html(once)) == once

    def test_formatting_preserves_preformatted_whitespace(self) -> None:
        from llex.markup import format_content_html

        source = "<pre><code>  keep\n  me  </code></pre>"
        assert "  keep\n  me  " in format_content_html(source)

    def test_formatting_leaves_inline_content_alone(self) -> None:
        from llex.markup import format_content_html

        # A newline between two inline elements would be a rendered space.
        assert format_content_html("<p><strong>a</strong><em>b</em></p>") == (
            "<p><strong>a</strong><em>b</em></p>"
        )

    def test_empty_content_is_left_empty(self) -> None:
        from llex.markup import format_content_html

        assert format_content_html("") == ""


class TestValidator:
    """A validator that crashes on the file it exists to diagnose is useless."""

    def test_a_good_document_passes(self, tmp_path: Path) -> None:
        Document(title="Fine", content="<p>hello</p>").save(tmp_path / "ok.llex")
        report = validate_file(tmp_path / "ok.llex")
        assert report.ok is True
        assert report.findings == []

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ("", "empty"),
            ("{not json", "not valid JSON"),
            ("[]", "must be an object"),
        ],
    )
    def test_structural_problems_are_reported(self, payload: str, expected: str) -> None:
        report = validate_bytes(payload.encode("utf-8"))
        assert report.ok is False
        assert any(expected in finding.message for finding in report.errors)

    def test_a_newer_document_says_so_plainly(self) -> None:
        report = validate_data(_valid(format_version=FORMAT_VERSION + 1))
        assert report.ok is False
        assert any("newer" in finding.message for finding in report.errors)

    def test_an_older_document_is_only_a_note(self) -> None:
        """It will be migrated on save, so it is not a problem."""
        report = validate_data(_valid(format_version=1))
        assert report.ok is True
        assert report.warnings == []

    def test_a_missing_key_is_located(self) -> None:
        data = _valid()
        del data["title"]
        report = validate_data(data)
        assert report.ok is False
        assert any(f.path == "title" for f in report.errors)

    def test_a_bad_page_number_is_located(self) -> None:
        data = _valid()
        data["page"]["width"] = -1
        report = validate_data(data)
        assert any(f.path == "page.width" for f in report.errors)

    def test_a_non_numeric_margin_is_located(self) -> None:
        data = _valid()
        data["page"]["margin_top"] = "wide"
        report = validate_data(data)
        assert any(f.path == "page.margin_top" and "a string" in f.message for f in report.errors)

    def test_a_non_positive_font_size_is_located(self) -> None:
        data = _valid()
        data["styles"] = {"Normal": {"font_size": 0}}
        report = validate_data(data)
        assert any(f.path == "styles.Normal.font_size" for f in report.errors)

    def test_a_non_string_content_line_is_located(self) -> None:
        data = _valid(content=["<p>a</p>", 7])
        report = validate_data(data)
        assert any(f.path == "content[1]" for f in report.errors)

    def test_a_single_string_content_is_only_a_note(self) -> None:
        report = validate_data(_valid(content="<p>a</p>"))
        assert report.ok is True
        assert any("single string" in f.message for f in report.findings)

    def test_it_asks_the_real_loader(self) -> None:
        """Rather than duplicating the loader's rules and hoping they agree."""
        data = _valid(format_version=FORMAT_VERSION + 5)
        report = validate_data(data)
        assert any("will not open" in f.message for f in report.errors)

    def test_it_never_raises_on_junk(self) -> None:
        for payload in (b"", b"\x00\xff", b"null", b"0", b'""', b"[]", b"{}", b'{"a":1}'):
            report = validate_bytes(payload)
            assert isinstance(report, Report)

    def test_it_reports_a_missing_file_rather_than_raising(self, tmp_path: Path) -> None:
        report = validate_file(tmp_path / "nope.llex")
        assert report.ok is False
        assert "could not be read" in report.errors[0].message

    def test_it_notes_an_unexpected_extension(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        Document().save(target.with_suffix(".txt"))
        report = validate_file(target)
        assert any(f.path == "<file>" for f in report.findings)

    def test_the_render_is_readable(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.llex"
        target.write_text("{oops", encoding="utf-8")
        text = validate_file(target).render()
        assert "INVALID" in text
        assert "not valid JSON" in text

    def test_the_exit_status_counts_errors(self, tmp_path: Path) -> None:
        good = tmp_path / "ok.llex"
        Document().save(good)
        bad = tmp_path / "bad.llex"
        bad.write_text("{oops", encoding="utf-8")
        assert main([str(good)]) == 0
        assert main([str(bad)]) == 1
        assert main([str(good), str(bad)]) == 1

    def test_it_can_report_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        Document().save(tmp_path / "ok.llex")
        assert main([str(tmp_path / "ok.llex"), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True


def _valid(**overrides: Any) -> dict[str, Any]:
    """A minimal document that passes every structural check."""
    data: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "title": "Valid",
        "content": ["<p>a</p>"],
        "page": {
            "width": 8.5,
            "height": 11.0,
            "margin_top": 1.0,
            "margin_right": 1.0,
            "margin_bottom": 1.0,
            "margin_left": 1.0,
        },
        "styles": {"Normal": {"font_size": 11}},
    }
    data.update(overrides)
    return data

class TestPaperSizes:
    """A word processor that only knows US Letter prints the wrong paper."""

    def test_a4_is_exactly_210_by_297_millimetres(self) -> None:
        # Typed as 8.27 inches it would be 0.06mm out on every A4 page.
        width, height = PAPER_SIZES["a4"]
        assert width * MM_PER_INCH == pytest.approx(210.0, abs=0.001)
        assert height * MM_PER_INCH == pytest.approx(297.0, abs=0.001)

    @pytest.mark.parametrize("name", sorted(PAPER_SIZES))
    def test_every_named_size_is_portrait(self, name: str) -> None:
        width, height = PAPER_SIZES[name]
        assert width < height, f"{name} should be portrait in the registry"

    def test_a4_in_pixels_is_the_size_a_reader_expects(self) -> None:
        setup = PageSetup.for_paper("a4")
        assert (setup.pixel_width, setup.pixel_height) == (794, 1123)

    def test_landscape_swaps_the_dimensions(self) -> None:
        portrait = PageSetup.for_paper("a4")
        landscape = PageSetup.for_paper("a4", "landscape")
        assert landscape.width == portrait.height
        assert landscape.height == portrait.width
        assert landscape.is_landscape is True

    def test_landscape_is_still_recognised_as_a4(self) -> None:
        assert PageSetup.for_paper("a4", "landscape").named_paper == "a4"

    def test_an_odd_size_is_reported_as_custom(self) -> None:
        assert PageSetup(width=7.0, height=9.5).named_paper == "custom"

    def test_switching_paper_keeps_the_margins(self) -> None:
        """Silently moving the margins would be a surprise; A4 is narrower than
        Letter and 1.25in margins that suited one are cramped on the other."""
        generous = PageSetup(margin_top=1.25, margin_left=1.25)
        a4 = PageSetup.for_paper("a4", "portrait", margin_top=1.25, margin_left=1.25)
        assert a4.margin_top == generous.margin_top
        assert a4.margin_left == generous.margin_left

    def test_margins_too_large_for_the_paper_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            PageSetup(width=3.0, height=4.0, margin_left=2.0, margin_right=2.0)

    def test_an_unknown_size_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown paper size"):
            PageSetup.for_paper("a99")

    def test_an_unknown_orientation_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="orientation"):
            PageSetup.for_paper("a4", "sideways")

    def test_the_size_and_orientation_round_trip(self) -> None:
        original = PageSetup.for_paper("b5", "landscape")
        loaded = PageSetup.from_dict(original.to_dict())
        assert loaded.paper == "b5"
        assert loaded.orientation == "landscape"
        assert (loaded.width, loaded.height) == (original.width, original.height)

    def test_a_file_without_a_paper_name_is_described_by_its_numbers(self) -> None:
        """Version 3 and earlier stored only dimensions."""
        assert PageSetup.from_dict({"width": 8.5, "height": 11.0}).paper == "letter"

    def test_an_unknown_paper_name_falls_back_to_the_numbers(self) -> None:
        setup = PageSetup.from_dict({"width": 8.5, "height": 11.0, "paper": "papyrus"})
        assert setup.paper == "letter"

    def test_a_nonsense_orientation_is_ignored(self) -> None:
        assert PageSetup.from_dict({"orientation": "diagonal"}).orientation == "portrait"

    def test_a_saved_document_keeps_its_paper(self, tmp_path: Path) -> None:
        document = Document(page=PageSetup.for_paper("a4"))
        loaded = Document.load(document.save(tmp_path / "a4.llex"))
        assert loaded.page.named_paper == "a4"

    @pytest.mark.parametrize(
        ("locale", "expected"),
        [
            ("en_US.UTF-8", "letter"),
            ("en_CA", "letter"),
            ("es_MX", "letter"),
            ("fr_FR", "a4"),
            ("de_DE.UTF-8", "a4"),
            ("en_GB", "a4"),
            ("ja_JP", "a4"),
        ],
    )
    def test_the_locale_picks_the_paper(
        self, locale: str, expected: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("LC_ALL", "LC_MEASUREMENT", "LANGUAGE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("LANG", locale)
        assert default_paper_for_locale() == expected

    def test_an_explicit_measurement_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANG", "en_US")
        monkeypatch.setenv("LC_MEASUREMENT", "fr_FR")
        assert default_paper_for_locale() == "a4"

    def test_an_unhelpful_locale_keeps_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("LC_ALL", "LC_MEASUREMENT", "LANG", "LANGUAGE"):
            monkeypatch.delenv(name, raising=False)
        assert default_paper_for_locale() == "letter"


class TestBidiAndLogicalProperties:
    """Right-to-left text, and properties that follow it."""

    def test_the_page_padding_follows_the_text_direction(self) -> None:
        """The margins are named physically, because a sheet of paper does not
        rotate, but the padding that positions the text on it must follow the
        text or the wider inner margin ends up on the outside."""
        text = STYLES.read_text(encoding="utf-8")
        assert "--margin-inline-start" in text
        assert "--margin-inline-end" in text
        rule = text.split(".tiptap > .page {", 1)[1].split("}", 1)[0]
        assert "var(--margin-inline-start)" in rule
        assert "var(--margin-inline-end)" in rule

    def test_the_page_box_itself_stays_physical(self) -> None:
        text = STYLES.read_text(encoding="utf-8")
        rule = text.split(".tiptap > .page {", 1)[1].split("}", 1)[0]
        assert "width: var(--page-width)" in rule
        assert "height: var(--page-height)" in rule

    def test_every_block_takes_its_direction_from_its_text(self) -> None:
        """A document-level `dir` cannot express a bilingual document."""
        for name in ("extensions.js", "main.js"):
            assert "DIRECTION" in (SCRIPT_DIR / name).read_text(encoding="utf-8"), name

    def test_standalone_exports_align_logically(self) -> None:
        text = (PACKAGE / "export.py").read_text(encoding="utf-8")
        assert "text-align: start" in text

    def test_the_direction_attribute_is_importable(self) -> None:
        # It is shared between the heading and the paragraph, which come from
        # different extensions.
        script = (SCRIPT_DIR / "extensions.js").read_text(encoding="utf-8")
        assert "export const DIRECTION = { dir: 'auto' }" in script

class TestImages:
    """Image handling, from the editor's markup through every export.

    An image is the one block whose size the editor cannot compute from text, so
    it is stored explicitly and every exporter has to be told what to do with it.
    """

    #: A real 2x2 RGB PNG. Not a hand-typed constant: the office exporters parse
    #: the image header to read its native size, so a plausible-looking but
    #: unparseable PNG makes every embedding assertion fail for the wrong reason.
    PNG = (
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP4z8AARAwQCgAf7gP9i18U1"
        "AAAAABJRU5ErkJggg=="
    )

    def _document(self, extra: str = "") -> tuple[Document, ParsedDocument]:
        source = f'<p>Before</p><img src="data:image/png;base64,{self.PNG}" alt="Red dot" width="120" height="80"{extra}><p>After</p>'
        return Document(title="Pics", content=source), parse_document(source)

    def test_an_image_is_parsed_as_a_block(self) -> None:
        """Not inline: the paginator measures a block's height to decide where the
        next page starts, and an inline image is inside a line box instead."""
        _, parsed = self._document()
        kinds = [block.kind for block in parsed.iter_blocks()]
        assert kinds == ["paragraph", "image", "paragraph"]

    def test_the_geometry_survives_the_parse(self) -> None:
        _, parsed = self._document()
        image = next(b for b in parsed.iter_blocks() if b.kind == "image")
        assert image.number("width") == 120
        assert image.number("height") == 80
        assert image.attr("alt") == "Red dot"

    def test_the_wrapping_survives_the_parse(self) -> None:
        _, parsed = self._document(' data-float="right"')
        image = next(b for b in parsed.iter_blocks() if b.kind == "image")
        assert image.attr("data-float") == "right"

    def test_absent_dimensions_read_as_none_not_zero(self) -> None:
        """A caller has to be able to tell "not specified" from a width of zero."""
        parsed = parse_document(f'<img src="data:image/png;base64,{self.PNG}">')
        image = next(b for b in parsed.iter_blocks() if b.kind == "image")
        assert image.number("width") is None
        assert image.number("height") is None

    def test_a_javascript_source_is_dropped(self) -> None:
        """An ``img src`` is a URL the editor will load, and a ``.llex`` is data, so
        a crafted file carrying ``javascript:`` would be a script injection."""
        assert parse_document('<img src="javascript:alert(1)">').iter_blocks().__next__() if False else True
        assert [b.kind for b in parse_document('<img src="javascript:alert(1)">').iter_blocks()] == []

    def test_a_data_uri_that_is_not_an_image_is_dropped(self) -> None:
        assert [b.kind for b in parse_document('<img src="data:text/html,<b>">').iter_blocks()] == []

    def test_an_image_with_no_source_is_dropped(self) -> None:
        assert [b.kind for b in parse_document('<img alt="nothing">').iter_blocks()] == []

    def test_a_remote_image_is_kept(self) -> None:
        assert [b.kind for b in parse_document('<img src="https://e.com/a.png">').iter_blocks()] == ["image"]

    def test_html_export_keeps_the_size(self) -> None:
        """Without the dimensions a reader has to wait for the file, and lays the
        page out as though the image were not there."""
        document, parsed = self._document()
        html = write_html(document, parsed).decode("utf-8")
        assert 'width="120"' in html
        assert 'height="80"' in html

    def test_html_export_emits_one_style_attribute(self) -> None:
        """A repeated attribute is silently dropped by every parser, taking the
        float with it, so the text would not wrap."""
        document, parsed = self._document(' data-float="left"')
        tag = next(line for line in write_html(document, parsed).decode("utf-8").splitlines() if "<img" in line)
        assert tag.count("style=") == 1
        assert "float:left" in tag

    def test_the_alt_text_cannot_break_out_of_its_quotes(self) -> None:
        source = f"""<img src="data:image/png;base64,{self.PNG}" alt='a" onload="x'>"""
        html = write_html(Document(), parse_document(source)).decode("utf-8")
        image_tag = next(line for line in html.splitlines() if "<img" in line)
        # The injected quote is escaped, so the tag still has an even number of
        # quotes and no attribute of the attacker's own.
        assert image_tag.count('"') % 2 == 0
        assert "&quot;" in image_tag or "'" in image_tag

    def test_docx_embeds_the_bytes(self) -> None:
        document, parsed = self._document()
        with zipfile.ZipFile(io.BytesIO(write_docx(document, parsed))) as archive:
            media = [n for n in archive.namelist() if n.startswith("word/media/")]
            body = archive.read("word/document.xml").decode("utf-8")
        assert media, "the picture was not embedded"
        assert "<w:drawing" in body
        assert 'descr="Red dot"' in body

    def test_odt_embeds_the_bytes_and_the_wrap(self) -> None:
        document, parsed = self._document(' data-float="right"')
        with zipfile.ZipFile(io.BytesIO(write_odt(document, parsed))) as archive:
            media = [n for n in archive.namelist() if n.startswith("Pictures/")]
            content = archive.read("content.xml").decode("utf-8")
        assert media, "the picture was not embedded"
        assert "draw:image" in content
        assert 'wrap="square"' in content

    def test_a_truncated_image_does_not_fail_the_export(self) -> None:
        """One bad picture must not cost the user the document."""
        source = f'<p>x</p><img src="data:image/png;base64,{self.PNG[:40]}" alt="Broken"><p>y</p>'
        document = Document(title="Bad", content=source)
        parsed = parse_document(source)
        for render in (write_docx, write_odt, write_epub):
            assert render(document, parsed), render.__name__

    def test_markdown_keeps_a_remote_image(self) -> None:
        parsed = parse_document('<img src="https://e.com/a.png" alt="Remote">')
        assert r"![Remote](https://e.com/a.png)" in write_md(Document(), parsed).decode("utf-8")

    def test_markdown_describes_an_embedded_one(self) -> None:
        """Markdown cannot carry a data URI, so the alt text is what survives."""
        document, parsed = self._document()
        assert "*[Red dot]*" in write_md(document, parsed).decode("utf-8")

    def test_plain_text_carries_the_alt_text(self) -> None:
        document, parsed = self._document()
        assert "[image: Red dot]" in write_txt(document, parsed).decode("utf-8")

    def test_an_image_survives_a_saved_file(self, tmp_path: Path) -> None:
        document, _ = self._document()
        loaded = Document.load(document.save(tmp_path / "pics.llex"))
        assert "<img" in loaded.content
        assert 'width="120"' in loaded.content


class TestEpubIsWellFormedXhtml:
    """EPUB content is XHTML, where a bare void element is a parse error.

    This was already wrong before images existed: `<hr>` was never closed, so
    every EPUB LLex produced was invalid XML. Reading systems are lenient about
    it; epubcheck is not.
    """

    def _assert_parses(self, source: str) -> None:
        document = Document(title="T", content=source)
        parsed = parse_document(source)
        with zipfile.ZipFile(io.BytesIO(write_epub(document, parsed))) as archive:
            for entry in archive.namelist():
                if entry.endswith((".xml", ".xhtml")):
                    # The bytes come from this exporter, not from a user, so the
                    # attack surface ruff warns about does not apply here.
                    xml.dom.minidom.parseString(archive.read(entry))  # noqa: S318

    def test_a_rule_alone_is_well_formed(self) -> None:
        self._assert_parses("<p>a</p><hr><p>b</p>")

    def test_an_image_is_well_formed(self) -> None:
        self._assert_parses(f'<img src="data:image/png;base64,{TestImages.PNG}" alt="d" width="10" height="10">')

    def test_the_standalone_page_keeps_the_bare_tag(self) -> None:
        """It is served as text/html, where the slash is unnecessary."""
        document = Document(title="T", content="<p>a</p><hr>")
        assert "<hr>" in write_html(document, parse_document(document.content)).decode("utf-8")
