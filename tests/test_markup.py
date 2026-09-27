"""Tests for the HTML structural parser."""

from __future__ import annotations

import pytest

from llex.markup import Block, count_words, normalize_color, parse_document, to_plain_text


def kinds(html: str) -> list[str]:
    return [block.kind for block in parse_document(html).blocks]


def texts(html: str) -> list[str]:
    return [block.text for block in parse_document(html).blocks]


class TestToleratesMalformedInput:
    @pytest.mark.parametrize(
        "html",
        [
            "",
            "   ",
            "<p>unclosed",
            "<p>a<p>b</p>",
            "<ul><li>a<li>b</ul>",
            "</p></div></ul>stray closers",
            "<div><span>bare</div>",
            "<p>text</p><p>",
            "<b><i>mismatched</b></i>",
        ],
    )
    def test_never_raises(self, html: str) -> None:
        assert isinstance(parse_document(html).blocks, tuple)

    def test_unclosed_paragraph_is_kept(self) -> None:
        assert texts("<p>unclosed") == ["unclosed"]

    def test_implicit_paragraph_close(self) -> None:
        assert texts("<p>a<p>b</p>") == ["a", "b"]

    def test_implicit_list_item_close(self) -> None:
        document = parse_document("<ul><li>a<li>b</ul>")
        assert document.blocks[0].kind == "bulletList"
        assert [item.text for item in document.blocks[0].children] == ["a", "b"]

    def test_nested_list_is_not_flattened(self) -> None:
        document = parse_document("<ul><li>a<ul><li>b</li></ul><li>c</ul>")
        outer = document.blocks[0]
        # ``.text`` is recursive, so the nested label is reachable from it too;
        # the item's *own* content is what must stay separate.
        assert ["".join(r.text for r in item.runs) for item in outer.children] == ["a", "c"]
        assert outer.children[0].children[0].kind == "bulletList"
        assert outer.children[0].children[0].children[0].text == "b"
        assert to_plain_text("<ul><li>a<ul><li>b</li></ul><li>c</ul>") == "- a\n  - b\n- c"

    def test_stray_closing_tags_are_ignored(self) -> None:
        assert texts("</div>real text") == ["real text"]


class TestStructure:
    def test_page_wrapper_is_preserved(self) -> None:
        document = parse_document('<div class="page"><p>a</p></div>')
        assert document.blocks[0].kind == "page"
        assert document.blocks[0].children[0].kind == "paragraph"

    def test_multiple_pages(self) -> None:
        document = parse_document(
            '<div class="page"><p>one</p></div><div class="page"><p>two</p></div>'
        )
        assert len(document.blocks) == 2
        assert [b.text for b in document.blocks] == ["one", "two"]

    def test_heading_levels(self) -> None:
        document = parse_document("<h1>a</h1><h3>b</h3><h6>c</h6>")
        assert [(b.kind, b.level) for b in document.blocks] == [
            ("heading", 1),
            ("heading", 3),
            ("heading", 6),
        ]

    def test_outline(self) -> None:
        document = parse_document("<h1>T</h1><p>x</p><h3>S</h3>")
        assert document.outline() == ((1, "T"), (3, "S"))

    def test_horizontal_rule(self) -> None:
        assert "horizontalRule" in kinds("<p>a</p><hr><p>b</p>")

    def test_ordered_vs_bullet(self) -> None:
        assert kinds("<ol><li>a</li></ol>") == ["orderedList"]
        assert kinds("<ul><li>a</li></ul>") == ["bulletList"]

    def test_blockquote(self) -> None:
        document = parse_document("<blockquote><p>quoted</p></blockquote>")
        assert document.blocks[0].kind == "blockquote"
        assert document.blocks[0].text == "quoted"

    def test_alignment_from_style_and_align_attribute(self) -> None:
        assert parse_document('<p style="text-align: center">a</p>').blocks[0].align == "center"
        assert parse_document('<p align="right">a</p>').blocks[0].align == "right"
        assert parse_document("<p style='text-align: sideways'>a</p>").blocks[0].align is None

    def test_code_block_language_from_child_code(self) -> None:
        document = parse_document('<pre><code class="language-python">x</code></pre>')
        assert document.blocks[0].kind == "codeBlock"
        assert document.blocks[0].language == "language-python"

    def test_code_block_language_from_pre(self) -> None:
        document = parse_document('<pre class="language-rust">x</pre>')
        assert document.blocks[0].language == "language-rust"


class TestInlineMarks:
    def test_semantic_tags(self) -> None:
        document = parse_document(
            "<p>a<strong>b</strong><em>c</em><u>d</u><s>e</s><code>f</code></p>"
        )
        runs = document.blocks[0].runs
        assert [(r.text, r.style.bold, r.style.italic, r.style.underline, r.style.strike, r.style.code) for r in runs] == [
            ("a", False, False, False, False, False),
            ("b", True, False, False, False, False),
            ("c", False, True, False, False, False),
            ("d", False, False, True, False, False),
            ("e", False, False, False, True, False),
            ("f", False, False, False, False, True),
        ]

    def test_style_attribute_marks(self) -> None:
        document = parse_document(
            '<p><span style="font-weight: 700; font-style: italic; '
            'text-decoration: underline line-through; color: #f00">x</span></p>'
        )
        style = document.blocks[0].runs[0].style
        assert (style.bold, style.italic, style.underline, style.strike) == (True, True, True, True)
        assert style.color == "#ff0000"

    def test_outer_text_is_not_retroactively_styled(self) -> None:
        document = parse_document("<p>plain<strong>bold</strong></p>")
        runs = document.blocks[0].runs
        assert runs[0].text == "plain" and not runs[0].style.bold
        assert runs[1].text == "bold" and runs[1].style.bold

    def test_nested_marks_combine(self) -> None:
        document = parse_document("<p><strong><em>both</em></strong></p>")
        style = document.blocks[0].runs[0].style
        assert style.bold and style.italic

    def test_highlight(self) -> None:
        document = parse_document("<p><mark>hi</mark></p>")
        assert document.blocks[0].runs[0].style.highlight == "#ffff00"

    def test_br_produces_newline(self) -> None:
        assert texts("<p>a<br>b</p>") == ["a\nb"]

    def test_adjacent_same_style_runs_are_coalesced(self) -> None:
        document = parse_document("<p><strong>a</strong><strong>b</strong></p>")
        assert len(document.blocks[0].runs) == 1

    def test_color_normalisation(self) -> None:
        assert normalize_color("#abc") == "#aabbcc"
        assert normalize_color("rgb(255, 0, 0)") == "#ff0000"
        assert normalize_color("RED") == "#ff0000"
        assert normalize_color("not-a-color") is None
        assert normalize_color("inherit") is None
        assert normalize_color(None) is None


class TestSafety:
    def test_script_content_is_dropped(self) -> None:
        assert texts('<script>alert("x")</script><p>safe</p>') == ["safe"]

    def test_style_content_is_dropped(self) -> None:
        assert ".page" not in to_plain_text("<style>.page{color:red}</style><p>safe</p>")

    def test_nested_script_is_dropped(self) -> None:
        assert texts("<script>var a='<p>x</p>'</script><p>ok</p>") == ["ok"]


class TestWhitespaceHandling:
    def test_stray_whitespace_between_blocks_is_dropped(self) -> None:
        document = parse_document('<div class="page">\n  <h1>T</h1>\n</div>')
        assert [b.kind for b in document.blocks[0].children] == ["heading"]

    def test_explicit_empty_paragraph_is_preserved(self) -> None:
        document = parse_document("<p></p><p>a</p>")
        assert len(document.blocks) == 2


class TestPlainText:
    @pytest.mark.parametrize(
        ("html", "expected"),
        [
            ("<p>a</p><p>b</p>", "a\nb"),
            ("<h1>T</h1><p>b</p>", "T\nb"),
            ("<ul><li>a</li><li>b</li></ul>", "- a\n- b"),
            ("<ol><li>a</li><li>b</li></ol>", "1. a\n2. b"),
            ("<ul><li>a<ul><li>b</li></ul></ul>", "- a\n  - b"),
            ("<p><strong>bold</strong></p>", "bold"),
            ("<p>a<br>b</p>", "a\nb"),
            ("<blockquote><p>q</p></blockquote>", "q"),
            ('<div class="page"><p>x</p></div>', "x"),
            ("<script>bad</script><p>ok</p>", "ok"),
        ],
    )
    def test_extraction(self, html: str, expected: str) -> None:
        assert to_plain_text(html) == expected

    def test_empty_document(self) -> None:
        assert to_plain_text("") == ""


class TestWordCount:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("", 0),
            ("one two three", 3),
            ("don't stop", 2),
            ("It\u2019s fine", 2),
            ("punctuation, everywhere!  really?", 3),
            ("  spaced   out  ", 2),
            ("under_score counts as one", 4),
            ("123 456", 2),
            # The frontend must agree with these numbers exactly; see
            # tests/js/wordcount.test.mjs for the mirrored expectations.
            ("\u4e2d\u6587 \u6587\u5b57", 2),
            ("caf\u00e9 na\u00efve", 2),
        ],
    )
    def test_counts(self, text: str, expected: int) -> None:
        assert count_words(text) == expected


class TestBlockInvariants:
    def test_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown block kind"):
            Block(kind="nonsense")

    def test_text_recurses_into_children(self) -> None:
        block = Block(kind="listItem", children=(Block(kind="paragraph", runs=()),))
        assert block.text == ""

    def test_iter_blocks_is_depth_first(self) -> None:
        document = parse_document("<h1>a</h1><ul><li>b</li></ul>")
        assert [b.kind for b in document.iter_blocks()] == [
            "heading",
            "bulletList",
            "listItem",
        ]
