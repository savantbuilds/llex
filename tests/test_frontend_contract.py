"""Static consistency checks between the template, the script and the CSS.

The editor is wired by element id across three files, and a typo in any of them
fails silently: the control simply does nothing. These tests catch that without
a browser, which is worth far more than their size suggests.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from llex.document import PAPER_MM, PAPER_SIZES

PACKAGE = Path(__file__).resolve().parent.parent / "llex"
TEMPLATE = PACKAGE / "templates" / "index.html"
SCRIPT_DIR = PACKAGE / "static" / "js"
STYLES = PACKAGE / "static" / "styles.css"

#: Class names shared between paginator.js and styles.css. Read from the source
#: rather than repeated, so a rename cannot leave the contract test agreeing with
#: itself and disagreeing with the application.
_PAGINATOR = (SCRIPT_DIR / "paginator.js").read_text(encoding="utf-8")


def _constant(name: str) -> str:
    match = re.search(rf"export const {name} = '([^']+)'", _PAGINATOR)
    assert match, f"{name} is not exported from paginator.js"
    return match.group(1)


VIRTUAL_CLASS = _constant("VIRTUAL_CLASS")
MEASURING_CLASS = _constant("MEASURING_CLASS")
MINI_TOOLBAR = PACKAGE / "static" / "mini-toolbar.css"

#: ``getElementById('x')``, ``byId('x')`` and ``require('x', ...)``
_ID_CALL = re.compile(
    r"""(?:getElementById|byId|require)\(\s*(['"])([A-Za-z][\w-]*)\1""",
)
#: ``document.querySelector('#x')``
_ID_QUERY = re.compile(r"""querySelector\(\s*['"]#([A-Za-z][\w-]*)['"]""")
#: ``id="x"`` in the template
_ID_ATTR = re.compile(r"""\bid\s*=\s*(['"])([\w-]+)\1""")
#: ``#id`` in a stylesheet. Hex colour literals such as ``#f0f0f0`` match the
#: same shape, so they are filtered out separately.
_CSS_ID = re.compile(r"(?<![\w-])#([A-Za-z][\w-]*)")
_HEX_COLOUR = re.compile(r"^[0-9a-fA-F]{3,8}$")

#: Ids the script looks up that are not controls but part of the CSS contract
#: or of the token handshake, so their absence is not a wiring bug.
_ALLOWED_MISSING = {
    "editor",  # the TipTap mount point, required but not a control
}

#: Menu items that must have a handler registered in menus.js. A menu entry
#: with no handler is a dead control, which is the defect this suite exists for.
_MENU_ITEMS = [
    "menu-new",
    "menu-open",
    "menu-save",
    "menu-save-as",
    "menu-print",
    "menu-undo",
    "menu-redo",
    "menu-cut",
    "menu-copy",
    "menu-paste",
    "menu-select-all",
    "menu-find",
    "menu-replace",
    "menu-zoom-in",
    "menu-zoom-out",
    "menu-zoom-reset",
    "menu-toggle-outline",
    "menu-toggle-assistant",
    "menu-focus",
    "menu-settings",
]

_RIBBON_BUTTONS = [
    "btn-undo",
    "btn-redo",
    "btn-bold",
    "btn-italic",
    "btn-underline",
    "btn-strikethrough",
    "btn-align-left",
    "btn-align-center",
    "btn-align-right",
    "btn-align-justify",
    "btn-bullet",
    "btn-number",
    "btn-toggle-outline",
    "btn-toggle-sidebar",
    "style-dropdown",
    "font-family",
    "font-size",
]

_ASSISTANT_CONTROLS = [
    "btn-summarize",
    "btn-rewrite",
    "btn-outline",
    "btn-ask",
    "btn-execute-scaffolds",
    "llm-prompt",
    "llm-tone",
    "llm-output",
    "llm-status",
]

_SETTINGS_CONTROLS = [
    "settings-modal",
    "btn-settings-save",
    "btn-settings-cancel",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "page-paper",
    "page-orientation",
    "page-size-note",
    "theme-bg",
    "theme-paper",
    "theme-text",
    "theme-ribbon",
    "theme-border",
]

# The find bar was the one control set with no contract test, which is how it
# shipped with a panel the script never actually built.
_FIND_CONTROLS = [
    "find-panel",
    "find-query",
    "find-replace",
    "find-previous",
    "find-next",
    "find-replace-one",
    "find-replace-all",
    "find-count",
    "find-close",
    "find-case",
    "find-whole-word",
    "find-regex",
]


@pytest.fixture(scope="module")
def template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def template_ids(template: str) -> set[str]:
    return {match.group(2) for match in _ID_ATTR.finditer(template)}


@pytest.fixture(scope="module")
def script() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SCRIPT_DIR.glob("*.js"))
    )


class TestTemplateIntegrity:
    def test_is_valid_html_structure(self, template: str) -> None:
        """Every element must be closed and the document must have one body."""
        assert template.lstrip().startswith("<!DOCTYPE html>")
        assert len(re.findall(r"<html[\s>]", template)) == 1
        assert template.count("</html>") == 1
        assert len(re.findall(r"<body[\s>]", template)) == 1
        assert template.count("</body>") == 1
        assert len(re.findall(r"<head[\s>]", template)) == 1
        assert template.count("</head>") == 1

    def test_no_markup_after_the_closing_html_tag(self, template: str) -> None:
        """The settings dialog used to live after </html> and never rendered."""
        assert "</html>" in template
        assert not template.split("</html>", 1)[1].strip()

    def test_ids_are_unique(self, template: str) -> None:
        ids = [match.group(2) for match in _ID_ATTR.finditer(template)]
        duplicates = {name for name in ids if ids.count(name) > 1}
        assert not duplicates, f"duplicate element ids: {sorted(duplicates)}"

    def test_declares_the_api_token_placeholder(self, template: str) -> None:
        assert '__LLEX_API_TOKEN__' in template
        assert 'name="llex-api-token"' in template

    def test_loads_the_built_bundle(self, template: str) -> None:
        assert '/static/editor.bundle.js' in template

    def test_loads_both_stylesheets(self, template: str) -> None:
        assert '/static/styles.css' in template
        assert '/static/mini-toolbar.css' in template

    def test_every_control_has_an_accessible_name(self, template: str) -> None:
        """A control with neither text nor aria-label is unusable by AT."""
        for match in re.finditer(r"<button\b[^>]*>(.*?)</button>", template, re.DOTALL):
            tag = match.group(0)
            body = match.group(1).strip()
            named = (
                body
                or 'aria-label="' in tag
                or 'aria-labelledby="' in tag
                or "<span" in body
            )
            assert named, f"button without an accessible name: {tag[:80]!r}"

    def test_toggle_buttons_declare_their_state(self, template: str) -> None:
        for name in ("btn-bold", "btn-italic", "btn-underline", "btn-strikethrough"):
            tag = re.search(rf'<button[^>]*id="{name}"[^>]*>', template)
            assert tag is not None, f"{name} is missing"
            assert 'aria-pressed=' in tag.group(0), f"{name} needs aria-pressed"

    def test_panel_buttons_point_at_their_panels(self, template: str) -> None:
        for button, panel in (
            ("btn-toggle-outline", "left-sidebar"),
            ("btn-toggle-sidebar", "sidebar"),
        ):
            tag = re.search(rf'<button[^>]*id="{button}"[^>]*>', template)
            assert tag is not None
            assert f'aria-controls="{panel}"' in tag.group(0)


class TestScriptTargetsExist:
    def test_every_looked_up_id_exists_in_the_template(self, script: str, template_ids: set[str]) -> None:
        referenced: set[str] = set()
        for pattern in (_ID_CALL, _ID_QUERY):
            referenced.update(match.group(2) for match in pattern.finditer(script))

        missing = {
            name
            for name in referenced
            if name not in template_ids and name not in _ALLOWED_MISSING
        }
        assert not missing, f"script references ids absent from the template: {sorted(missing)}"

    def test_every_setting_control_is_read_and_saved(self) -> None:
        """A control can be in the template, listed in the defaults, and still do
        nothing. The left margin was exactly that: present in the template and
        in the defaults, absent from the list of fields the dialog reads and
        writes, so the one margin that could not be changed was the left one."""
        text = (SCRIPT_DIR / "settings.js").read_text(encoding="utf-8")
        block = text.split("const FIELDS = [", 1)[1].split("];", 1)[0]
        entries = re.findall(r"\['([a-z0-9-]+)',\s*'([A-Za-z]+)',\s*'(\w+)'\]", block)
        assert entries, "no setting fields were found in settings.js"
        ids = {element_id for element_id, _key, _kind in entries}
        keys = {key for _element_id, key, _kind in entries}

        defaults_block = text.split("const DEFAULTS = {", 1)[1].split("};", 1)[0]
        defaults = set(re.findall(r"^\s*([A-Za-z]+):", defaults_block, re.MULTILINE))
        # Paper and orientation are read from their own selects, not FIELDS.
        selects = {"paper", "orientation"}
        assert defaults - selects <= keys, f"settings with no control: {sorted(defaults - selects - keys)}"

        # And every control the dialog reads must exist in the template.
        template = PACKAGE.joinpath("templates/index.html").read_text(encoding="utf-8")
        template_ids = set(re.findall(r'id="([^"]+)"', template))
        assert ids <= template_ids, f"controls not in the template: {sorted(ids - template_ids)}"

    def test_the_page_padding_uses_the_logical_margins(self) -> None:
        text = STYLES.read_text(encoding="utf-8")
        rule = text.split(".tiptap > .page {", 1)[1].split("}", 1)[0]
        assert "padding: var(--margin-block-start) var(--margin-inline-end)" in " ".join(rule.split())

    def test_the_python_paper_sizes_match_the_javascript_ones(self) -> None:
        """The two halves of the page setup must not drift apart."""
        import re as _re

        source = (SCRIPT_DIR / "settings.js").read_text(encoding="utf-8")
        block = source.split("export const PAPER_MM = {", 1)[1].split("};", 1)[0]
        javascript = {
            name: (float(a), float(b))
            for name, a, b in _re.findall(r"(\w+):\s*\[([\d.]+),\s*([\d.]+)\]", block)
        }
        assert javascript, "no paper sizes were found in settings.js"
        assert set(javascript) == set(PAPER_SIZES), (
            f"paper sizes differ: {set(javascript) ^ set(PAPER_SIZES)}"
        )
        for name, millimetres in javascript.items():
            assert PAPER_MM[name] == millimetres, f"{name} differs between the two"

    def test_every_select_can_be_read(self) -> None:
        """A `<select>` in dark chrome must be told both its background and its
        text colour.

        The global `color: inherit` gives it the surrounding light text, but the
        control and its open list are painted by the operating system in its own
        light chrome, so the selected item ends up light on light and cannot be
        read. Two controls shipped that way -- the paper and orientation choices
        -- and a third, the context toolbar's font controls, had no rule at all.
        """
        text = STYLES.read_text(encoding="utf-8")
        # Anchored to a line start, so it finds the element selector and not the
        # tail of `.modal-body select {`.
        base = re.search(r"^select \{([^}]*)\}", text, re.MULTILINE)
        assert base, "there is no bare `select` rule"
        assert "background:" in base.group(1)
        assert "color:" in base.group(1)

        # And the open list is drawn separately by the OS, so it needs its own.
        options = re.search(r"^select option \{([^}]*)\}", text, re.MULTILINE)
        assert options, "there is no bare `select option` rule"
        assert "background:" in options.group(1)
        assert "color:" in options.group(1)

    @pytest.mark.parametrize("element_id", ["page-paper", "page-orientation", "mt-font-family", "mt-font-size", "style-dropdown", "font-family", "font-size", "llm-tone"])
    def test_no_select_is_left_unstyled(self, element_id: str, template: str) -> None:
        """Every select either has a class the stylesheet knows, or is a
        descendant of a selector the stylesheet knows."""
        match = re.search(rf'<select\b[^>]*id="{element_id}"[^>]*>', template)
        assert match, f"{element_id} is not a select in the template"
        tag = match.group(0)
        classes = set(re.search(r'class="([^"]+)"', tag).group(1).split()) if 'class="' in tag else set()

        text = STYLES.read_text(encoding="utf-8")
        styled = bool(classes & {name for name in classes if f".{name}" in text})
        # A select with no class at all has to be covered by an element selector.
        assert styled or ".modal-body select" in text, f"{element_id} has no applicable style"

    def test_the_context_toolbar_is_positioned(self) -> None:
        """`place()` sets `left` and `top` from the pointer position; both are
        ignored unless the element is positioned, so the toolbar used to appear
        in the document flow instead of under the cursor."""
        text = STYLES.read_text(encoding="utf-8")
        rule = text.split(".mini-toolbar", 1)[1].split("}", 1)[0]
        assert "position: fixed" in rule
        assert "z-index" in rule

    def test_export_menu_container_exists(self, template_ids: set[str]) -> None:
        """The download menu is populated at runtime, so it needs a container."""
        assert "export-menu" in template_ids

    def test_scaffold_mark_is_readable_from_the_dom(self, template: str) -> None:
        assert "data-scaffold" in (SCRIPT_DIR / "extensions.js").read_text(encoding="utf-8")


class TestNoDeadControls:
    @pytest.mark.parametrize("element_id", _MENU_ITEMS)
    def test_menu_items_are_wired(self, element_id: str, template_ids: set[str], script: str) -> None:
        assert element_id in template_ids, f"{element_id} is missing from the template"
        assert element_id in script, f"{element_id} has no handler"

    @pytest.mark.parametrize("element_id", _RIBBON_BUTTONS)
    def test_ribbon_controls_are_wired(self, element_id: str, template_ids: set[str], script: str) -> None:
        assert element_id in template_ids, f"{element_id} is missing from the template"
        assert element_id in script, f"{element_id} has no handler"

    @pytest.mark.parametrize("element_id", _FIND_CONTROLS)
    def test_find_controls_are_wired(self, element_id: str, template_ids: set[str], script: str) -> None:
        """The find bar shipped as a stub; without a contract test nothing would
        have noticed that its controls were never built."""
        assert element_id in template_ids, f"{element_id} is missing from the template"
        assert element_id in script, f"{element_id} has no handler"

    @pytest.mark.parametrize("element_id", _ASSISTANT_CONTROLS)
    def test_assistant_controls_are_wired(
        self, element_id: str, template_ids: set[str], script: str
    ) -> None:
        assert element_id in template_ids, f"{element_id} is missing from the template"
        assert element_id in script, f"{element_id} has no handler"

    @pytest.mark.parametrize("element_id", _SETTINGS_CONTROLS)
    def test_settings_controls_are_wired(
        self, element_id: str, template_ids: set[str], script: str
    ) -> None:
        assert element_id in template_ids, f"{element_id} is missing from the template"
        assert element_id in script, f"{element_id} has no handler"

    def test_context_menu_actions_all_have_implementations(self, script: str) -> None:
        """A context-menu action with no handler silently does nothing.

        Handlers appear in three shapes: an object key (``'bullet-list':`` or
        the bare identifier ``highlight:``), a comparison in a click handler
        (``action === 'cut'``), and a value comparison in a change handler
        (``select.dataset.action === 'font-family'``).
        """
        template = TEMPLATE.read_text(encoding="utf-8")
        declared = set(re.findall(r"""data-action="([\w-]+)""", template))
        assert declared, "no context menu actions found in the template"

        patterns = [
            r"""(?<![\w-])['"]?{name}['"]?\s*:""",
            r"""===\s*['"]{name}['"]""",
            r"""==\s*['"]{name}['"]""",
        ]
        missing = {
            name
            for name in declared
            if not any(
                re.search(pattern.format(name=re.escape(name)), script) for pattern in patterns
            )
        }
        assert not missing, f"context menu actions with no handler: {sorted(missing)}"


class TestStylesheetIntegrity:
    @pytest.mark.parametrize("path", [STYLES, MINI_TOOLBAR])
    def test_braces_are_balanced(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        # Strip comments before counting, so a brace in prose cannot confuse it.
        stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        assert stripped.count("{") == stripped.count("}"), f"unbalanced braces in {path.name}"

    def test_no_stray_closing_braces(self) -> None:
        for path in (STYLES, MINI_TOOLBAR):
            text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
            depth = 0
            for char in text:
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    assert depth >= 0, f"{path.name} closes a brace that was never opened"
            assert depth == 0, f"{path.name} leaves {depth} brace(s) unclosed"

    def test_page_geometry_constants_agree_with_the_backend(self) -> None:
        """816x1056 is 8.5x11in at 96dpi, and the engine measures against it."""
        text = STYLES.read_text(encoding="utf-8")
        assert "--page-width: 816px" in text
        assert "--page-height: 1056px" in text
        script = (SCRIPT_DIR / "pagination.js").read_text(encoding="utf-8")
        assert "OVERFLOW_TOLERANCE" in script

    def test_overfull_pages_are_not_clipped(self) -> None:
        """A grown page must show its content, or text becomes unreachable."""
        text = STYLES.read_text(encoding="utf-8")
        assert '[data-overfull="true"]' in text
        rule = text.split('[data-overfull="true"]', 1)[1]
        assert "overflow: visible" in rule.split("}", 1)[0]
        assert "height: auto" in rule.split("}", 1)[0]

    def test_default_page_geometry_is_hidden(self) -> None:
        """Overflow must only be hidden for pages the engine has measured."""
        text = STYLES.read_text(encoding="utf-8")
        base = text.split(".tiptap > .page {", 1)[1].split("}", 1)[0]
        assert "overflow: hidden" in base

    def test_off_screen_containment_is_gated_behind_a_class(self) -> None:
        """`content-visibility` must never apply unconditionally.

        The paginator detects overflow by measuring each page, and a page the
        browser is skipping layout for reports only its intrinsic size. If the
        rule applied unconditionally, pagination would silently stop breaking
        pages on exactly the long documents the rule exists to speed up.
        """
        text = STYLES.read_text(encoding="utf-8")
        assert f".{VIRTUAL_CLASS} .tiptap > .page" in text
        for rule in text.split(f".{VIRTUAL_CLASS} .tiptap > .page", 1)[1].split("}", 1)[0].splitlines():
            assert "content-visibility" not in rule or rule.strip().startswith(
                ("content-visibility", "/*", "*", ".", "@")
            )
        # The suspending rule must exist and force the property back on.
        assert f".{MEASURING_CLASS} .tiptap > .page" in text
        suspended = text.split(f".{MEASURING_CLASS} .tiptap > .page", 1)[1].split("}", 1)[0]
        assert "content-visibility: visible" in suspended

    def test_a_placeholder_size_keeps_page_positions_stable(self) -> None:
        """Without `contain-intrinsic-size` a skipped page collapses and the
        document jumps as the user scrolls."""
        text = STYLES.read_text(encoding="utf-8")
        rule = text.split(f".{VIRTUAL_CLASS} .tiptap > .page", 1)[1].split("}", 1)[0]
        assert "contain-intrinsic-size" in rule

    def test_printing_is_never_virtualised(self) -> None:
        """A skipped page would print blank."""
        text = STYLES.read_text(encoding="utf-8")
        printed = text.split("@media print", 1)[-1]
        assert f".{VIRTUAL_CLASS} .tiptap > .page" in printed
        assert "content-visibility: visible" in printed

    def test_the_javascript_adds_the_class_only_when_supported(self) -> None:
        """The rule above is gated on a class; adding it unconditionally would
        opt out of the feature detection entirely."""
        script = (SCRIPT_DIR / "main.js").read_text(encoding="utf-8")
        # The constant, not the literal: the literal's single home is
        # paginator.js, and the rule above is checked against the same constant.
        assert "classList.add(VIRTUAL_CLASS)" in script
        assert "supportsContainment()" in script
        assert "import { Paginator, VIRTUAL_CLASS, supportsContainment }" in script

    def test_every_measurement_suspends_containment(self) -> None:
        """The single most important invariant here: layout may not be skipped
        while the paginator is deciding what overflows."""
        assert "measuring(container, () => measureOverflow(container))" in _PAGINATOR

    @pytest.mark.parametrize("selector", ["#left-sidebar", "#sidebar", "#status-bar", "#ribbon", "#menu-bar"])
    def test_structural_selectors_exist(self, selector: str) -> None:
        assert selector in STYLES.read_text(encoding="utf-8"), f"{selector} is unstyled"

    def test_the_outline_region_is_styled(self) -> None:
        # Styled by class rather than id, so the scroll container is bounded.
        text = STYLES.read_text(encoding="utf-8")
        assert ".outline {" in text
        assert "overflow-y: auto" in text.split(".outline {", 1)[1].split("}", 1)[0]

    def test_every_styled_id_exists_in_the_template(self, template_ids: set[str]) -> None:
        unstyled: set[str] = set()
        for path in (STYLES, MINI_TOOLBAR):
            for match in _CSS_ID.finditer(path.read_text(encoding="utf-8")):
                name = match.group(1)
                if name in template_ids or _HEX_COLOUR.match(name):
                    continue
                unstyled.add(name)
        assert not unstyled, f"CSS targets ids absent from the template: {sorted(unstyled)}"
