"""End-to-end check that the editor front end actually boots.

The other suites verify the contract between the template, the script and the
CSS, and the unit tests verify individual modules. None of them can catch the
most important failure of all: the bundle failing to start at all, which is
what happens when a change breaks the editor's boot sequence.

This boots the real server, serves the real template, evaluates the real bundle
in a headless DOM, and asserts that a real ProseMirror editor is constructed
against the real page markup. It is the test that would have caught the
`editor.storage.stats` bug immediately, since the app died during boot and
nothing else noticed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from llex import main as launcher
from llex.api import AppServices, build_app
from llex.document import Document

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "llex" / "static" / "editor.bundle.js"
NODE_MODULES = ROOT / "node_modules"

pytestmark = pytest.mark.e2e

#: Evaluated inside the headless DOM. Kept as a string so the harness has no
#: build step of its own.
_HARNESS = r"""
const fs = require('fs');
const { JSDOM, VirtualConsole } = require('jsdom');

const html = fs.readFileSync(process.argv[2], 'utf8');
const bundlePath = process.argv[3];
const baseUrl = process.argv[4];

const errors = [];
const warnings = [];
const calls = [];

const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', (e) => errors.push('jsdomError: ' + (e.stack || e.message)));
virtualConsole.on('error', (...a) => errors.push('console.error: ' + a.map(String).join(' ')));
virtualConsole.on('warn', (...a) => warnings.push(a.map(String).join(' ')));

const dom = new JSDOM(html, {
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  url: baseUrl + '/',
  virtualConsole,
});
const { window } = dom;

window.matchMedia = window.matchMedia
  || (() => ({ matches: false, addListener() {}, removeListener() {} }));
if (!window.requestAnimationFrame) {
  window.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
  window.cancelAnimationFrame = (id) => clearTimeout(id);
}
window.Response = Response;
window.Headers = Headers;
window.Request = Request;
window.AbortController = AbortController;

(async () => {
  const nodeFetch = globalThis.fetch;
  window.fetch = async (url, init) => {
    const target = String(url).startsWith('http') ? String(url) : baseUrl + String(url);
    calls.push({
      url: String(url),
      method: (init && init.method) || 'GET',
      token: Boolean(init && init.headers && init.headers['X-LLex-Token']),
    });
    return nodeFetch(target, init);
  };
  globalThis.fetch = window.fetch;

  // jsdom has no layout engine, so scrollHeight and clientHeight are both zero.
  // Pinning them to the page height lets the paginator settle instead of
  // looping; the overflow logic itself is unit-tested with real measurements.
  Object.defineProperty(window.Element.prototype, 'scrollHeight', {
    configurable: true, get() { return 1056; },
  });
  Object.defineProperty(window.Element.prototype, 'clientHeight', {
    configurable: true, get() { return 1056; },
  });

  try {
    window.eval(fs.readFileSync(bundlePath, 'utf8'));
  } catch (e) {
    errors.push('eval: ' + (e && e.stack || e));
  }

  await new Promise((r) => setTimeout(r, 900));

  const text = (id) => {
    const el = window.document.getElementById(id);
    return el ? el.textContent : null;
  };

  const result = {
    errors,
    warnings,
    calls,
    hasApp: Boolean(window.llex),
    hasEditor: Boolean(window.llex && window.llex.editor),
    editorClass: (window.document.querySelector('#editor .tiptap') || {}).className || null,
    pageCount: window.document.querySelectorAll('#editor .page').length,
    tokenInjected: (() => {
      const meta = window.document.querySelector('meta[name="llex-api-token"]');
      if (!meta) return false;
      const value = meta.getAttribute('content') || '';
      return value.length > 0 && value !== '__LLEX_API_TOKEN__';
    })(),
    statusText: text('status-bar-text'),
    metaText: text('status-meta'),
    zoomLabel: text('zoom-level'),
    exportItems: window.document.querySelectorAll('#export-menu .dropdown-item').length,
    paginatorInfo: window.llex && window.llex.paginator ? { containerClass: window.llex.paginator.container ? window.llex.paginator.container.className : null, childCount: window.llex.paginator.container ? window.llex.paginator.container.children.length : -1, hasNumberPages: typeof window.llex.paginator.numberPages } : null,
    pageNumbers: Array.from(window.document.querySelectorAll('#editor .page')).map(
      (page) => page.getAttribute('data-page-number') || ''
    ),
    rootFontSize: window.document.documentElement.style.fontSize || '',
    footerChildren: (window.document.getElementById('status-bar') || {}).children
      ? window.document.getElementById('status-bar').children.length
      : 0,
  };

  if (window.llex && window.llex.editor) {
    try {
      const ed = window.llex.editor;
      result.html = ed.getHTML();
      result.schemaNodes = Object.keys(ed.schema.nodes).sort();
      result.markNodes = Object.keys(ed.schema.marks).sort();
      result.stateKeys = Object.keys(window.llex.state || {}).sort();
      result.canUndo = ed.can().undo();
    } catch (e) {
      errors.push('introspection: ' + e.message);
    }
  }

  process.stdout.write('__RESULT__' + JSON.stringify(result));
})().catch((e) => {
  process.stdout.write('__RESULT__' + JSON.stringify({ errors: ['harness: ' + (e.stack || e)] }));
});
"""


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("e2e") / "harness.cjs"
    path.write_text(_HARNESS, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def boot_result(harness: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """Boot the real server and the real bundle once, for the whole module."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    if not BUNDLE.is_file():
        pytest.skip("editor bundle is not built; run `npm ci && npm run build`")
    if not (NODE_MODULES / "jsdom").is_dir():
        pytest.skip("jsdom is not installed; run `npm ci`")

    directory = tmp_path_factory.mktemp("boot")
    document = Document(
        title="E2E",
        content='<div class="page"><h1>Boot</h1><p>Hello.</p></div>',
    )
    server = launcher._ServerThread(
        build_app(AppServices(document=document)),
        launcher.HOST,
        launcher.reserve_port(launcher.HOST, 0),
        debug=False,
    )
    port = server.server.config.port
    server.start()
    if not launcher._wait_until_serving(launcher.HOST, port, 15.0):  # type: ignore[arg-type]
        server.shutdown(timeout=5)
        pytest.fail("the local server never started")

    base = f"http://{launcher.HOST}:{port}"
    try:
        import urllib.request

        with urllib.request.urlopen(f"{base}/", timeout=15) as response:
            html = response.read().decode("utf-8")
        page = directory / "index.html"
        page.write_text(html, encoding="utf-8")

        # The harness lives in a temp directory, so Node must be told where the
        # repository's node_modules is before `require('jsdom')` will resolve.
        import os

        env = {**os.environ, "NODE_PATH": str(NODE_MODULES)}
        completed = subprocess.run(
            ["node", str(harness), str(page), str(BUNDLE), base],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(ROOT),
            env=env,
        )
    finally:
        server.shutdown(timeout=5)

    marker = completed.stdout.find("__RESULT__")
    if marker < 0:
        pytest.fail(
            f"the harness produced no result\n"
            f"stdout: {completed.stdout[-2000:]}\nstderr: {completed.stderr[-2000:]}"
        )
    return json.loads(completed.stdout[marker + len("__RESULT__") :])


class TestEditorBoots:
    def test_no_errors_during_boot(self, boot_result: dict[str, object]) -> None:
        assert boot_result.get("errors") == []

    def test_no_extension_warnings(self, boot_result: dict[str, object]) -> None:
        """A duplicate extension is a silent correctness risk, not a nit."""
        assert boot_result.get("warnings") == []

    def test_the_application_completed_boot(self, boot_result: dict[str, object]) -> None:
        assert boot_result.get("hasApp") is True
        assert boot_result.get("hasEditor") is True

    def test_prosemirror_mounted_on_the_editor_element(self, boot_result: dict[str, object]) -> None:
        classes = str(boot_result.get("editorClass"))
        assert "ProseMirror" in classes
        assert "tiptap" in classes
        # TipTap supplies the class itself; the app must not add a second copy.
        assert classes.split().count("tiptap") == 1

    def test_a_page_was_rendered(self, boot_result: dict[str, object]) -> None:
        assert boot_result.get("pageCount") == 1

    def test_the_document_round_trips(self, boot_result: dict[str, object]) -> None:
        assert boot_result.get("html") == '<div class="page"><h1>Boot</h1><p>Hello.</p></div>'

    def test_the_schema_includes_the_page_node(self, boot_result: dict[str, object]) -> None:
        nodes = list(boot_result.get("schemaNodes") or [])
        for required in ("doc", "page", "paragraph", "heading", "bulletList", "orderedList"):
            assert required in nodes, f"{required} missing from the schema"

    def test_the_scaffold_mark_is_registered(self, boot_result: dict[str, object]) -> None:
        assert "scaffold" in list(boot_result.get("markNodes") or [])

    def test_the_character_style_mark_is_registered(self, boot_result: dict[str, object]) -> None:
        """Regression: `textStyle` was used but never registered.

        StarterKit v3 does not bundle `@tiptap/extension-text-style`, so
        `schema.marks.textStyle` was `undefined` and clicking Highlight or Text
        colour threw a TypeError. Asserting the *presence* of a handler was not
        enough; what matters is that the mark the handler reaches for exists.
        """
        assert "textStyle" in list(boot_result.get("markNodes") or [])

    def test_heading_is_registered(self, boot_result: dict[str, object]) -> None:
        assert "heading" in list(boot_result.get("schemaNodes") or [])

    def test_page_numbers_come_from_a_css_counter(self) -> None:
        """Page numbering must not be stamped onto the page nodes.

        It was, and ProseMirror removed the attribute on the next transaction
        because it is not in the node spec, so the numbers silently vanished.
        A CSS counter cannot desync and cannot be stripped.
        """
        css = (Path(__file__).resolve().parent.parent / "llex" / "static" / "styles.css").read_text(
            encoding="utf-8"
        )
        assert "counter-reset: llex-page" in css
        assert "counter-increment: llex-page" in css
        assert "content: counter(llex-page)" in css
        assert "attr(data-page-number)" not in css, (
            "page numbering must not depend on a DOM attribute"
        )

    def test_the_script_does_not_stamp_page_numbers(self) -> None:
        script_dir = Path(__file__).resolve().parent.parent / "llex" / "static" / "js"
        source = "\n".join(path.read_text(encoding="utf-8") for path in script_dir.glob("*.js"))
        assert "data-page-number" not in source
        assert "pageNumber" not in source

    def test_the_root_font_size_is_not_scaled(self, boot_result: dict[str, object]) -> None:
        """Regression: zoom scaled the root font size *and* the viewport.

        The chrome is sized in `rem`, so it was being scaled twice.
        """
        assert boot_result.get("rootFontSize") == ""

    def test_application_state_exists(self, boot_result: dict[str, object]) -> None:
        keys = list(boot_result.get("stateKeys") or [])
        for required in ("words", "characters", "pages", "dirty", "fileName", "title"):
            assert required in keys, f"{required} missing from the app state"


class TestTokenHandshake:
    def test_the_token_was_injected_into_the_page(self, boot_result: dict[str, object]) -> None:
        """The placeholder must be replaced before the page is served."""
        assert boot_result.get("tokenInjected") is True

    def test_every_api_call_carried_the_token(self, boot_result: dict[str, object]) -> None:
        calls = list(boot_result.get("calls") or [])
        assert calls, "the editor made no API calls"
        assert all(call["token"] for call in calls), "a request went out unauthenticated"

    def test_the_editor_loaded_its_environment_and_document(
        self, boot_result: dict[str, object]
    ) -> None:
        urls = [str(call["url"]) for call in boot_result.get("calls") or []]
        assert any("/api/environment" in url for url in urls)
        assert any("/api/document" in url for url in urls)

    def test_no_token_leaked_into_a_url(self, boot_result: dict[str, object]) -> None:
        """The token belongs in a header, never in a query string."""
        for call in boot_result.get("calls") or []:
            assert "token=" not in str(call["url"])


class TestChrome:
    def test_the_export_menu_was_built_from_the_backend(self, boot_result: dict[str, object]) -> None:
        """The menu must mirror what the backend can actually write."""
        assert boot_result.get("exportItems") == 8

    def test_the_status_bar_kept_its_regions(self, boot_result: dict[str, object]) -> None:
        """`setReady` used to write to the footer and delete the other regions."""
        assert boot_result.get("footerChildren", 0) >= 3

    def test_the_status_message_is_present(self, boot_result: dict[str, object]) -> None:
        assert boot_result.get("statusText") == "Ready"

    def test_statistics_were_computed(self, boot_result: dict[str, object]) -> None:
        # "Boot" and "Hello." are two words and ten characters.
        meta = str(boot_result.get("metaText"))
        assert "2 words" in meta
        assert "10 characters" in meta
        assert "1 pages" in meta

    def test_the_zoom_indicator_shows_a_level(self, boot_result: dict[str, object]) -> None:
        assert boot_result.get("zoomLabel") == "100%"

