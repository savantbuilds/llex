"""Tests for the document model and the ``.llex`` container format."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from llex.document import (
    DEFAULT_CONTENT,
    FORMAT_VERSION,
    MAX_BACKUPS,
    Document,
    DocumentError,
    DocumentFormatError,
    DocumentNotFoundError,
    PageSetup,
    StyleDefinition,
)

from .conftest import content_is_preserved


class TestConstruction:
    def test_defaults(self) -> None:
        document = Document()
        assert document.title == "Untitled Document"
        assert document.content == DEFAULT_CONTENT
        assert document.path is None
        assert document.is_saved is False
        assert document.is_dirty is False

    def test_default_styles_are_present(self) -> None:
        assert set(Document().styles) == {"Normal", "Heading"}

    def test_timestamps_are_timezone_aware(self) -> None:
        document = Document()
        assert document.created_at.tzinfo is timezone.utc
        assert document.modified_at.tzinfo is timezone.utc

    def test_html_content_alias_round_trips(self) -> None:
        document = Document()
        document.html_content = "<p>legacy</p>"
        assert document.content == "<p>legacy</p>"
        assert document.html_content == "<p>legacy</p>"

    def test_empty_title_is_normalised(self) -> None:
        assert Document(title="").title == "Untitled Document"

    def test_rename_trims_and_defaults(self) -> None:
        document = Document()
        document.rename("  Report  ")
        assert document.title == "Report"
        document.rename("   ")
        assert document.title == "Untitled Document"


class TestDerivedValues:
    def test_text_word_and_character_counts(self) -> None:
        document = Document(content="<div class='page'><p>Hello brave world</p></div>")
        assert document.text == "Hello brave world"
        assert document.word_count == 3
        assert document.character_count == 17

    def test_page_count_reads_the_pagination_markers(self) -> None:
        document = Document(
            content='<div class="page"><p>a</p></div><div class="page"><p>b</p></div>'
        )
        assert document.page_count == 2

    def test_page_count_of_a_pageless_document_is_one(self) -> None:
        assert Document(content="<p>unpaginated</p>").page_count == 1

    def test_dirty_tracking(self) -> None:
        document = Document()
        assert not document.is_dirty
        document.set_content("<p>edited</p>")
        assert document.is_dirty
        document.mark_clean()
        assert not document.is_dirty
        document.rename("New")
        assert document.is_dirty


class TestPageSetup:
    def test_letter_defaults_in_pixels(self) -> None:
        page = PageSetup()
        assert (page.pixel_width, page.pixel_height) == (816, 1056)
        assert page.margins_px == {"top": 96, "right": 96, "bottom": 96, "left": 96}

    def test_custom_margins_convert(self) -> None:
        page = PageSetup(margin_left=1.5)
        assert page.margins_px["left"] == 144

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"width": 0},
            {"margin_top": -1},
            {"margin_left": 5, "margin_right": 5},
            {"margin_top": 6, "margin_bottom": 6},
            {"width": "wide"},  # type: ignore[arg-type]
        ],
    )
    def test_invalid_geometry_is_rejected(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            PageSetup(**kwargs)  # type: ignore[arg-type]

    def test_from_dict_falls_back_per_field(self) -> None:
        page = PageSetup.from_dict({"margin_top": "not a number", "width": 0, "margin_left": 2})
        assert page.margin_top == 1.0  # default
        assert page.width == 8.5  # default
        assert page.margin_left == 2.0  # honoured

    def test_from_dict_ignores_non_mappings(self) -> None:
        assert PageSetup.from_dict(None) == PageSetup()
        assert PageSetup.from_dict(["nope"]) == PageSetup()  # type: ignore[arg-type]

    def test_from_dict_recovers_from_impossible_margins(self) -> None:
        assert PageSetup.from_dict({"margin_left": 9, "margin_right": 9}) == PageSetup()


class TestStyleDefinition:
    def test_unknown_fields_are_dropped(self) -> None:
        style = StyleDefinition.from_dict(
            {"name": "N", "font_size": 14, "from_the_future": True}, name="N"
        )
        assert style.font_size == 14
        assert not hasattr(style, "from_the_future")

    def test_name_is_taken_from_the_mapping_key(self) -> None:
        assert StyleDefinition.from_dict({}, name="Quote").name == "Quote"

    def test_non_mapping_is_rejected(self) -> None:
        with pytest.raises(DocumentFormatError):
            StyleDefinition.from_dict("nope", name="x")  # type: ignore[arg-type]


class TestSerialisation:
    def test_round_trip(self, tmp_path: Path) -> None:
        original = Document(
            title="Round Trip",
            content='<div class="page"><h1>H</h1><p>Body</p></div>',
            page=PageSetup(margin_left=1.25),
        )
        target = original.save(tmp_path / "doc.llex")
        loaded = Document.load(target)
        assert loaded.title == original.title
        # The stored form is laid out one block per line, so the loaded HTML is
        # not byte-identical to the editor's. What has to survive is the text and
        # the structure, which is what `content_is_preserved` checks.
        assert content_is_preserved(original.content, loaded.content)
        assert loaded.page == original.page
        assert loaded.styles == original.styles
        assert loaded.created_at == original.created_at
        assert loaded.path == target

    def test_saved_file_declares_its_version(self, tmp_path: Path) -> None:
        target = Document().save(tmp_path / "doc.llex")
        assert json.loads(target.read_text(encoding="utf-8"))["format_version"] == FORMAT_VERSION

    def test_non_ascii_is_preserved(self, tmp_path: Path) -> None:
        document = Document(content="<p>café 中文 \U0001f600</p>")
        target = document.save(tmp_path / "u.llex")
        assert Document.load(target).content == document.content
        assert "café" in target.read_text(encoding="utf-8")

    def test_save_coerces_the_suffix(self, tmp_path: Path) -> None:
        assert Document().save(tmp_path / "thing.json").suffix == ".llex"

    def test_save_creates_missing_directories(self, tmp_path: Path) -> None:
        assert Document().save(tmp_path / "a" / "b" / "c.llex").is_file()

    def test_save_without_a_path_is_an_error(self) -> None:
        with pytest.raises(DocumentError, match="no path given"):
            Document().save()

    def test_save_clears_the_dirty_flag(self, tmp_path: Path) -> None:
        document = Document()
        document.set_content("<p>x</p>")
        document.save(tmp_path / "doc.llex")
        assert not document.is_dirty

    def test_save_leaves_no_temporary_files(self, tmp_path: Path) -> None:
        Document().save(tmp_path / "doc.llex")
        assert [p.name for p in tmp_path.iterdir()] == ["doc.llex"]

    def test_save_reports_an_unwritable_destination(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        with pytest.raises(DocumentError, match="could not save"):
            Document().save(blocker / "nested.llex")


class TestLoading:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(DocumentNotFoundError):
            Document.load(tmp_path / "absent.llex")

    def test_corrupt_json(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.llex"
        broken.write_text("{ not json", encoding="utf-8")
        with pytest.raises(DocumentFormatError, match="not a valid LLex document"):
            Document.load(broken)

    def test_non_object_root(self, tmp_path: Path) -> None:
        listed = tmp_path / "list.llex"
        listed.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(DocumentFormatError, match="must be a JSON object"):
            Document.load(listed)

    def test_non_utf8_file(self, tmp_path: Path) -> None:
        binary = tmp_path / "binary.llex"
        binary.write_bytes(b"\xff\xfe\x00\x01")
        with pytest.raises(DocumentFormatError, match="not a UTF-8"):
            Document.load(binary)

    def test_future_version_is_refused(self, tmp_path: Path) -> None:
        future = tmp_path / "future.llex"
        future.write_text(json.dumps({"format_version": FORMAT_VERSION + 1}), encoding="utf-8")
        with pytest.raises(DocumentFormatError, match="newer LLex"):
            Document.load(future)

    def test_invalid_version_is_refused(self, tmp_path: Path) -> None:
        odd = tmp_path / "odd.llex"
        odd.write_text(json.dumps({"format_version": "two"}), encoding="utf-8")
        with pytest.raises(DocumentFormatError, match="unsupported format_version"):
            Document.load(odd)

    def test_legacy_v1_file_still_opens(self, tmp_path: Path) -> None:
        """Files written by 0.1 used naive timestamps and ``html_content``."""
        legacy = {
            "title": "Legacy",
            "html_content": '<div class="page"><p>old</p></div>',
            "created_at": "2026-03-30T12:00:00",
            "modified_at": "2026-03-30T12:00:00",
            "paragraphs": [""],
            "styles": {"Normal": {"name": "Normal", "font_family": "Segoe UI", "font_size": 12}},
            "page_size": (8.5, 11.0),
            "margins": (0.75, 0.75, 0.75, 0.75),
        }
        path = tmp_path / "legacy.llex"
        path.write_text(json.dumps(legacy), encoding="utf-8")
        document = Document.load(path)
        assert document.title == "Legacy"
        assert document.content == legacy["html_content"]
        assert document.created_at.tzinfo is timezone.utc
        assert document.styles["Normal"].font_family == "Segoe UI"

    def test_empty_file_yields_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.llex"
        path.write_text("{}", encoding="utf-8")
        document = Document.load(path)
        assert document.content == DEFAULT_CONTENT
        assert document.title == "Untitled Document"

    def test_a_single_bad_style_does_not_break_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "partial.llex"
        path.write_text(
            json.dumps(
                {
                    "content": "<p>x</p>",
                    "styles": {"Good": {"name": "Good"}, "Bad": "not an object"},
                }
            ),
            encoding="utf-8",
        )
        document = Document.load(path)
        assert "Good" in document.styles
        assert "Bad" not in document.styles

    def test_bad_margins_degrade_to_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "margins.llex"
        path.write_text(
            json.dumps({"content": "<p>x</p>", "page": {"margin_left": "wide", "width": -4}}),
            encoding="utf-8",
        )
        assert Document.load(path).page == PageSetup()

    def test_loaded_document_is_clean(self, tmp_path: Path) -> None:
        document = Document(content="<p>x</p>")
        path = document.save(tmp_path / "doc.llex")
        document.set_content("<p>y</p>")
        assert Document.load(path).is_dirty is False

    def test_unparseable_timestamps_fall_back(self, tmp_path: Path) -> None:
        path = tmp_path / "ts.llex"
        path.write_text(json.dumps({"content": "<p>x</p>", "created_at": "yesterday"}), encoding="utf-8")
        assert isinstance(Document.load(path).created_at, datetime)


class TestSummary:
    def test_reports_state(self, tmp_path: Path) -> None:
        document = Document(title="Doc", content='<div class="page"><p>two words</p></div>')
        summary = document.summary()
        assert summary["title"] == "Doc"
        assert summary["path"] is None
        assert summary["file_name"] is None
        assert summary["word_count"] == 2
        assert summary["page_count"] == 1
        assert summary["dirty"] is False

    def test_includes_the_path_after_saving(self, tmp_path: Path) -> None:
        document = Document()
        document.save(tmp_path / "doc.llex")
        assert document.summary()["file_name"] == "doc.llex"

class TestBackups:
    """Rotating backups, so a save cannot be the only copy of the work."""

    def test_the_first_save_leaves_no_backup(self, tmp_path: Path) -> None:
        """There is no previous version to preserve, and inventing an empty one
        would be worse than nothing: it would look like a recoverable document."""
        target = tmp_path / "doc.llex"
        Document().save(target)
        assert list(tmp_path.glob("*.bak")) == []

    def test_a_second_save_rotates_the_first(self, tmp_path: Path) -> None:
        target = tmp_path / "doc.llex"
        first = Document()
        first.save(target)
        original = first.content
        first.set_content("<div class='page'><p>second</p></div>")
        first.save(target)

        backup = tmp_path / "doc.1.llex.bak"
        assert backup.is_file()
        assert content_is_preserved(Document.load(backup).content, original)

    def test_backups_are_numbered_newest_first(self, tmp_path: Path) -> None:
        target = tmp_path / "doc.llex"
        versions = []
        for version in range(3):
            document = Document.load(target) if target.is_file() else Document()
            document.set_content(f"<div class='page'><p>version {version}</p></div>")
            document.save(target)
            versions.append(document.content)

        newest = Document.load(tmp_path / "doc.1.llex.bak").content
        previous = Document.load(tmp_path / "doc.2.llex.bak").content
        assert content_is_preserved(newest, versions[1])
        assert content_is_preserved(previous, versions[0])

    def test_the_rotation_is_bounded(self, tmp_path: Path) -> None:
        """Unbounded backups fill a disk without ever helping."""
        target = tmp_path / "doc.llex"
        for version in range(MAX_BACKUPS + 4):
            document = Document.load(target) if target.is_file() else Document()
            document.set_content(f"<div class='page'><p>version {version}</p></div>")
            document.save(target)

        assert len(list(tmp_path.glob("*.bak"))) == MAX_BACKUPS

    def test_backups_can_be_switched_off(self, tmp_path: Path) -> None:
        target = tmp_path / "doc.llex"
        document = Document()
        document.save(target)
        document.save(target, backup=False)
        assert list(tmp_path.glob("*.bak")) == []

    def test_resaving_unchanged_content_makes_no_backup(self, tmp_path: Path) -> None:
        """Autosave runs on a timer, so most writes are no-ops. Rotating for each
        one would bury the few versions that actually differ."""
        target = tmp_path / "doc.llex"
        document = Document()
        document.save(target)
        for _ in range(6):
            document.save(target)
        assert list(tmp_path.glob("*.bak")) == []

    def test_resaving_unchanged_content_does_not_bury_real_versions(self, tmp_path: Path) -> None:
        target = tmp_path / "doc.llex"
        document = Document()
        document.save(target)
        for version in range(3):
            document.set_content(f"<div class='page'><p>version {version}</p></div>")
            document.save(target)
            for _ in range(3):  # autosave of an unchanged document
                document.save(target)

        # Three genuinely different versions, and not the dozen a naive
        # rotate-on-every-write would have left.
        backups = sorted(path.name for path in tmp_path.glob("*.bak"))
        assert backups == ["doc.1.llex.bak", "doc.2.llex.bak", "doc.3.llex.bak"]
        newest = Document.load(tmp_path / "doc.1.llex.bak").content
        assert content_is_preserved(newest, "<div class='page'><p>version 1</p></div>")

    def test_a_saved_file_is_still_readable(self, tmp_path: Path) -> None:
        """The backup must not be the only valid file after a rotation."""
        target = tmp_path / "doc.llex"
        for version in range(4):
            document = Document.load(target) if target.is_file() else Document()
            document.set_content(f"<div class='page'><p>version {version}</p></div>")
            document.save(target)

        assert "version 3" in Document.load(target).content

    def test_no_temporary_files_are_left_behind(self, tmp_path: Path) -> None:
        """A failed write must not litter the directory the user will look in."""
        target = tmp_path / "doc.llex"
        document = Document()
        document.save(target)
        document.set_content("<div class='page'><p>second</p></div>")
        document.save(target)
        assert list(tmp_path.glob(".*.tmp")) == []


class TestConcurrentModification:
    """Noticing that the file changed underneath us.

    Two windows on one file used to mean last-writer-wins with no warning, so the
    losing window's work was discarded silently.
    """

    def test_no_conflict_when_nothing_else_touched_the_file(self, tmp_path: Path) -> None:
        document = Document()
        document.save(tmp_path / "doc.llex")
        assert document.check_conflict() is None

    def test_no_conflict_after_our_own_save(self, tmp_path: Path) -> None:
        """Autosave rewrites the file constantly, and a false warning on every
        save would train the user to ignore it."""
        target = tmp_path / "doc.llex"
        document = Document()
        document.save(target)
        for _ in range(3):
            document.set_content(f"<div class='page'><p>{document.title}</p></div>")
            document.save(target)
            assert document.check_conflict() is None

    def test_a_foreign_change_is_reported(self, tmp_path: Path) -> None:
        target = tmp_path / "doc.llex"
        document = Document()
        document.save(target)

        target.write_text('{"format": 2, "title": "theirs", "content": "x"}', encoding="utf-8")
        found = document.check_conflict()
        assert found is not None
        assert found.kind == "modified"
        assert "doc.llex" in found.detail

    def test_identical_content_is_not_a_conflict(self, tmp_path: Path) -> None:
        """A rewrite of the same bytes is not a conflict, even though the
        timestamp moved."""
        target = tmp_path / "doc.llex"
        document = Document()
        document.save(target)
        payload = target.read_text(encoding="utf-8")
        target.write_text(payload, encoding="utf-8")
        assert document.check_conflict() is None

    def test_deletion_is_reported_separately(self, tmp_path: Path) -> None:
        target = tmp_path / "doc.llex"
        document = Document()
        document.save(target)
        target.unlink()
        found = document.check_conflict()
        assert found is not None
        assert found.kind == "deleted"

    def test_an_unsaved_document_never_conflicts(self) -> None:
        assert Document().check_conflict() is None

    def test_the_baseline_moves_with_our_writes(self, tmp_path: Path) -> None:
        target = tmp_path / "doc.llex"
        document = Document()
        document.save(target)
        # A foreign change, then our own save adopting it as the baseline.
        target.write_text('{"format": 2, "title": "theirs", "content": "x"}', encoding="utf-8")
        assert document.check_conflict() is not None
        document.note_disk_state()
        assert document.check_conflict() is None
