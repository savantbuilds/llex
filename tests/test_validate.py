from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from llex.document import FORMAT_VERSION, Document
from llex.markup import ScaffoldRef, parse_document, to_plain_text
from llex.validate import Report, main, validate_bytes, validate_data, validate_file

from .conftest import content_is_preserved

#: A document with every construct the storage form has to survive.
RICH = (
    '<div class="page"><h1>Alpha</h1><p>first <strong>bold</strong> para</p>'
    '<ul><li>one</li><li>two</li></ul>'
    '<pre><code>keep  me\n  indented</code></pre>'
    '<blockquote><p>quoted</p></blockquote>'
    '<p style="text-align:center">centred</p>'
    '<hr>'
    '<p><span data-scaffold="s1" data-instruction="do the thing">todo</span></p>'
    "<p>café — 日本語</p></div>"
)


class TestScaffoldInTheModel:
    """A scaffold is content, and the parse used to throw it away.

    The parser read the span and kept its text, so a scaffold survived as plain
    text with its instruction gone. Every exporter built on the parse therefore
    dropped it silently.
    """

    def test_a_scaffold_is_read_off_a_span(self) -> None:
        parsed = parse_document('<p><span data-scaffold="s1" data-instruction="go">todo</span></p>')
        run = next(iter(parsed.iter_blocks())).runs[0]
        assert run.style.scaffold == ScaffoldRef(id="s1", instruction="go")

    def test_the_instruction_survives(self) -> None:
        """The instruction is the part the user wrote; losing it is the loss."""
        parsed = parse_document('<p><span data-scaffold="s1" data-instruction="rewrite this">t</span></p>')
        assert "rewrite this" in repr(next(iter(parsed.iter_blocks())).runs[0].style)

    def test_a_plain_span_is_not_a_scaffold(self) -> None:
        parsed = parse_document("<p><span>plain</span></p>")
        assert next(iter(parsed.iter_blocks())).runs[0].style.scaffold is None

    def test_an_empty_id_is_not_a_scaffold(self) -> None:
        # A badge the user cannot act on is worse than none.
        parsed = parse_document('<p><span data-scaffold="">x</span></p>')
        assert next(iter(parsed.iter_blocks())).runs[0].style.scaffold is None

    def test_a_scaffold_survives_html_export(self) -> None:
        from llex.export import write_html

        parsed = parse_document('<p><span data-scaffold="s1" data-instruction="go">todo</span></p>')
        html = write_html(Document(), parsed).decode("utf-8")
        assert 'data-scaffold="s1"' in html
        assert 'data-instruction="go"' in html

    def test_a_scaffold_survives_a_round_trip(self) -> None:
        from llex.export import _html_body

        source = '<p><span data-scaffold="s1" data-instruction="go">todo</span></p>'
        assert 'data-scaffold="s1"' in _html_body(parse_document(source))

    def test_plain_text_still_omits_the_markup(self) -> None:
        # The text is what a plain-text consumer wants; the instruction is not.
        assert to_plain_text('<p><span data-scaffold="s1" data-instruction="go">todo</span></p>').strip() == "todo"


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
