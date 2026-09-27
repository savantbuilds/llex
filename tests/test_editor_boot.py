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

  // Run last: this mutates the document, so everything above has to observe
  // the state as loaded.
  result.findCheck = (function () {
    if (!window.llex || !window.llex.find) return { skipped: true };
    var find = window.llex.find;
    var ed = window.llex.editor;
    var out = { hasController: Boolean(find.controller) };

    find.open();
    out.panelOpened = !window.document.getElementById('find-panel').hidden;

    var field = window.document.getElementById('find-query');
    field.value = 'test';
    out.matchCount = find.controller.search({ query: 'test' });
    out.countLabel = window.document.getElementById('find-count').textContent;
    out.hasHighlight = Boolean(ed.view.dom.querySelector('.find-match'));

    find.controller.next();
    out.advanced = find.controller.active > 0;

    // Replace the current match, then the remainder.
    var replaceField = window.document.getElementById('find-replace');
    replaceField.value = 'replaced';
    find.controller.search({ query: 'test' });
    out.replacedOne = find.controller.replaceCurrent('replaced');
    out.remainingAfterOne = find.controller.matchCount;

    find.controller.search({ query: 'test' });
    out.replacedAll = find.controller.replaceAll('replaced');
    out.testLeft = (ed.getText().match(/test/g) || []).length;

    // A half-typed regex is the normal state while typing; it must not throw.
    out.invalidRegexThrew = null;
    try {
      find.controller.search({ query: '[bad', regex: true });
    } catch (e) {
      out.invalidRegexThrew = String(e);
    }

    find.close();
    out.panelClosed = window.document.getElementById('find-panel').hidden;
    return out;
  })();

  // Every probe below is wrapped. A probe that throws must not be able to take
  // the harness down: a thrown probe skips the `autosave.stop()` further down,
  // the autosave timers then keep Node's event loop alive, and the whole run
  // hangs instead of failing. That cost an afternoon to find, so it is prevented
  // rather than remembered.
  function probe(body) {
    try {
      return body();
    } catch (e) {
      return { threw: String((e && e.stack) || e).slice(0, 800) };
    }
  }

  // Toolbar tools, driven through the real buttons.
  result.toolCheck = probe(function () {
    if (!window.llex || !window.llex.editor) return { skipped: true };
    var ed = window.llex.editor;
    var out = {};
    var click = function (id) {
      var button = dom.window.document.getElementById(id);
      if (button) button.click();
      return Boolean(button);
    };

    out.hasIndentIncrease = click('btn-indent-increase');
    out.indentAfterIncrease = ed.getAttributes('paragraph').indent;
    out.hasIndentDecrease = click('btn-indent-decrease');
    out.indentAfterDecrease = ed.getAttributes('paragraph').indent;
    // Clamped rather than allowed to run away, because the stylesheet only
    // describes so many levels.
    for (var up = 0; up < 20; up += 1) ed.commands.increaseIndent();
    out.indentClamped = ed.getAttributes('paragraph').indent;
    for (var down = 0; down < 30; down += 1) ed.commands.decreaseIndent();
    out.indentClampedLow = ed.getAttributes('paragraph').indent;

    out.hasRule = click('btn-horizontal-rule');
    out.ruleRendered = ed.getHTML().indexOf('<hr') !== -1;
    ed.commands.undo();

    // A link applies to selected text; with a collapsed caret it only sets the mark
    // for whatever is typed next, and the HTML would legitimately not change.
    ed.commands.selectAll();
    out.hasLink = click('btn-link');
    var doc = dom.window.document;
    out.linkRowShown = doc.getElementById('link-row').hidden === false;
    var input = doc.getElementById('link-url');
    if (input) {
      // A bare address, as a user would type it.
      input.value = 'example.com/page';
      doc.getElementById('link-apply').click();
    }
    out.linkMade = ed.getHTML().indexOf('https://example.com/page') !== -1;
    out.linkRowHidden = doc.getElementById('link-row').hidden === true;
    ed.commands.undo();

    out.hasClearFormat = click('btn-clear-format');
    out.cleared = ed.getHTML().indexOf('<strong') === -1 && ed.getHTML().indexOf('<b') === -1;

    out.hasPageBreak = click('btn-page-break');
    out.pageBreakSet = ed.getAttributes('paragraph').breakBefore === true;
    ed.commands.undo();

    out.hasImage = click('btn-image');
    out.imagePanelOpened = doc.getElementById('image-panel').hidden === false;
    out.ribbonHasImageNode = ed.schema.nodes.image !== undefined;
    out.ribbonHasLinkMark = ed.schema.marks.link !== undefined;
    return out;
  });

  // A dead button is a `undefined` command rather than an error, so it is worth
  // checking that every command the ribbon can call actually exists.
  result.commandCheck = probe(function () {
    var ed = window.llex.editor;
    if (!ed) return { skipped: true };
    var names = ['increaseIndent', 'decreaseIndent', 'setIndent', 'setImage', 'setImageSize',
      'setImageFloat', 'setPageBreakBefore', 'setLink', 'unsetLink', 'setHorizontalRule'];
    var out = {};
    names.forEach(function (name) { out[name] = typeof ed.commands[name]; });
    return out;
  });

  result.imageCheck = probe(function () {
    var panel = window.llex.imagePanel;
    if (!panel) return { skipped: 'no panel' };
    var out = { hasPanel: true, printableWidth: Math.round(panel.printableWidth) };
    panel.open();
    var opened = panel.isOpen();
    panel.close();
    out.closesAgain = opened && !panel.isOpen();
    out.syncWithoutSelection = (function () {
      panel.sync();
      return true;
    })();
    return out;
  });

  // Focus mode, and the model picker.
  result.focusCheck = probe(function () {
    var doc = dom.window.document;
    var out = {};
    var item = doc.getElementById('menu-focus');
    var exit = doc.getElementById('focus-exit');

    out.startsOff = dom.window.document.body.classList.contains('focus-mode') === false;
    out.exitStartsHidden = exit.hidden === true;

    item.click();
    out.turnsOn = dom.window.document.body.classList.contains('focus-mode') === true;
    out.exitAppears = exit.hidden === false;
    out.itemChecked = item.getAttribute('aria-checked') === 'true';
    out.itemRenamed = /leave/i.test(item.textContent);

    // Escape has to work, and without a modifier key.
    var event = new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true });
    dom.window.document.dispatchEvent(event);
    out.escapeLeaves = dom.window.document.body.classList.contains('focus-mode') === false;

    // And the visible control, for a user who does not think to press Escape.
    item.click();
    exit.click();
    out.buttonLeaves = dom.window.document.body.classList.contains('focus-mode') === false;
    out.exitHiddenAgain = exit.hidden === true;
    return out;
  });

  result.modelCheck = probe(function () {
    var llex = window.llex;
    var doc = dom.window.document;
    var select = doc.getElementById('model-select');
    var endpoint = doc.getElementById('model-endpoint');
    var status = doc.getElementById('model-status');
    var out = {
      hasSelect: Boolean(select),
      hasEndpoint: Boolean(endpoint),
      hasStatus: Boolean(status),
      hasPicker: Boolean(llex.settings && llex.settings.models),
    };
    if (!out.hasPicker) return out;
    out.selectionDefaultsToNoModel = llex.settings.models.selection().model === '';
    return out;
  });

  result.undoCheck = (function () {
    if (!window.llex || !window.llex.editor) return { skipped: true };
    var ed = window.llex.editor;
    var out = {};

    out.hasPaginationKey = ed.state.plugins.some(function (plugin) {
      try { return plugin.key === 'llex-pagination$'; } catch (e) { return false; }
    });

    // jsdom has no layout, so pages are stubbed to overflow. Enough blocks are
    // added for a split to be possible at all: with only two, the paginator
    // correctly grows the page instead of moving anything.
    ed.commands.insertContentAt(
      1,
      Array.from({ length: 6 }, function (_, i) { return '<p>filler ' + i + '</p>'; }).join('')
    );

    Object.defineProperty(window.Element.prototype, 'scrollHeight', {
      configurable: true, get() { return 400; },
    });
    Object.defineProperty(window.Element.prototype, 'clientHeight', {
      configurable: true, get() { return 300; },
    });
    Array.prototype.forEach.call(ed.view.dom.children, function (page) {
      Array.prototype.forEach.call(page.children, function (child, i) {
        child.getBoundingClientRect = function () {
          return { top: i * 100, bottom: i * 100 + 100 };
        };
      });
    });

    // An edit that lands on a page boundary, so pagination has work to do.
    var pagesBefore = ed.state.doc.childCount;
    ed.commands.insertContentAt(1, '<p>typed by the test</p>');
    out.paginationHappened = ed.state.doc.childCount > pagesBefore;

    // One undo must take both the typing and the pagination it caused.
    ed.commands.undo();
    out.undoRemovedText = ed.getHTML().indexOf('typed by the test') === -1;
    return out;
  })();

  // Autosave, without waiting for the real four-second timer: drive the same
  // code path the timer does and check it asks the API and stops claiming the
  // document is dirty.
  result.autosaveCheck = (function () {
    if (!window.llex || !window.llex.autosave) return { skipped: true };
    var autosave = window.llex.autosave;
    var out = {};

    // Replace the transport so the check observes the call without a server.
    var calls = [];
    var originalApi = autosave.api;
    autosave.api = {
      autosave: function (html, title) {
        calls.push({ html: html, title: title });
        return Promise.resolve({ status: 'saved', document: { file_name: 'doc.llex' } });
      },
      conflict: function () { return Promise.resolve({ status: 'clear' }); },
    };

    window.llex.state.dirty = true;
    window.llex.state.fileName = 'doc.llex';
    window.llex.state.title = 'Autosave probe';

    return autosave.tick().then(function (response) {
      out.calledOnce = calls.length === 1;
      out.sentTitle = calls.length ? calls[0].title : null;
      // The find probe above replaced "test" with "replaced", so that is the
      // current content: if autosave sent a stale copy this would miss it.
      out.sentCurrentContent = calls.length ? calls[0].html.indexOf('replaced') !== -1 : false;
      out.status = response ? response.status : null;
      out.dirtyCleared = window.llex.state.dirty === false;

      // A clean document must not be rewritten over and over.
      return autosave.tick().then(function (second) {
        out.skippedWhenClean = second === null;
        out.totalCalls = calls.length;

        // A clean document is not probed for conflicts either.
        window.llex.state.fileName = null;
        return autosave.checkConflict().then(function (probe) {
          out.noProbeWithoutAFile = probe === null;
          autosave.api = originalApi;
          return out;
        });
      });
    });
  })();

  Promise.resolve(result.autosaveCheck)
    .catch(function (e) { return { threw: String(e && e.stack || e) }; })
    .then(function (settled) {
      result.autosaveCheck = settled;
      // The autosave timers keep the event loop alive, exactly as they do for the
      // real window; stop them so the harness can exit.
      if (window.llex && window.llex.autosave) window.llex.autosave.stop();
      process.stdout.write('__RESULT__' + JSON.stringify(result));
    });
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
        content=('<div class="page"><h1>Boot</h1><p>One test here.</p>'
                        '<p>And a second test there.</p></div>'),
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
        # `dir="auto"` on every block: a document may mix directions, and only a
        # block's own content can say which way its text runs.
        assert boot_result.get("html") == (
            '<div class="page"><h1 dir="auto">Boot</h1><p dir="auto">One test here.</p>'
            '<p dir="auto">And a second test there.</p></div>'
        )

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


@pytest.fixture(scope="module")
def find_result(boot_result: dict[str, object]) -> dict[str, object]:
    """The find-and-replace probe's report from the single boot."""
    return dict(boot_result.get("findCheck") or {})


@pytest.fixture(scope="module")
def autosave_result(boot_result: dict[str, object]) -> dict[str, object]:
    """The autosave probe's report from the single boot."""
    return dict(boot_result.get("autosaveCheck") or {})


class TestAutosave:
    """Autosave, crash recovery and conflict detection, driven through the real app.

    Before this the editor had no autosave at all: closing the window lost the
    whole session, and a second window on the same file silently won.
    """

    def test_the_controller_was_created(self, autosave_result: dict[str, object]) -> None:
        assert autosave_result, "no autosave controller was reported"
        assert autosave_result.get("skipped") is not True
        assert "threw" not in autosave_result, autosave_result.get("threw")

    def test_a_dirty_document_is_written(self, autosave_result: dict[str, object]) -> None:
        assert autosave_result.get("calledOnce") is True
        assert autosave_result.get("status") == "saved"

    def test_the_current_content_and_title_are_sent(self, autosave_result: dict[str, object]) -> None:
        """Autosaving a stale copy would defeat the purpose."""
        assert autosave_result.get("sentTitle") == "Autosave probe"
        assert autosave_result.get("sentCurrentContent") is True

    def test_saving_clears_the_dirty_flag(self, autosave_result: dict[str, object]) -> None:
        assert autosave_result.get("dirtyCleared") is True

    def test_a_clean_document_is_not_rewritten(self, autosave_result: dict[str, object]) -> None:
        """Otherwise a quiet document would be written to disk forever."""
        assert autosave_result.get("skippedWhenClean") is True
        assert autosave_result.get("totalCalls") == 1

    def test_a_document_with_no_file_is_not_probed(self, autosave_result: dict[str, object]) -> None:
        assert autosave_result.get("noProbeWithoutAFile") is True


class TestFindAndReplace:
    """Find and replace, driven through the real UI.

    The menu item previously flashed "Find is not implemented yet", so this is
    both the feature and the proof that it is reachable.
    """

    def test_the_panel_was_created(self, find_result: dict[str, object]) -> None:
        assert find_result, f"no find panel was reported: {find_result}"
        assert find_result.get("skipped") is not True, "the find panel was not created"

    def test_the_panel_opens(self, find_result: dict[str, object]) -> None:
        assert find_result.get("panelOpened") is True

    def test_matches_are_found_and_counted(self, find_result: dict[str, object]) -> None:
        assert find_result.get("matchCount") == 2, "the fixture document contains two matches"
        assert "of 2" in str(find_result.get("countLabel"))

    def test_matches_are_highlighted(self, find_result: dict[str, object]) -> None:
        assert find_result.get("hasHighlight") is True

    def test_next_advances(self, find_result: dict[str, object]) -> None:
        assert find_result.get("advanced") is True

    def test_replace_one(self, find_result: dict[str, object]) -> None:
        assert find_result.get("replacedOne") is True
        assert find_result.get("remainingAfterOne") == 1

    def test_replace_all_clears_every_match(self, find_result: dict[str, object]) -> None:
        assert find_result.get("replacedAll") == 1
        assert find_result.get("testLeft") == 0

    def test_an_incomplete_regex_does_not_throw(self, find_result: dict[str, object]) -> None:
        assert find_result.get("invalidRegexThrew") is None

    def test_the_panel_closes(self, find_result: dict[str, object]) -> None:
        assert find_result.get("panelClosed") is True

    def test_the_menu_item_no_longer_reports_it_is_unimplemented(self) -> None:
        """The stub said "not implemented"; nothing should claim that now."""
        script_dir = Path(__file__).resolve().parent.parent / "llex" / "static" / "js"
        for path in script_dir.glob("*.js"):
            assert "not implemented yet" not in path.read_text(encoding="utf-8"), path.name


class TestUndoAndPagination:
    """A reflow must be part of the undo step that caused it.

    The paginator used to run from an animation-frame loop, dispatching the page
    move as its own transaction. That put the move on top of the undo stack, so
    the first Ctrl+Z after typing near a page boundary reverted the pagination
    and left the typing in place. It is now produced by a plugin's
    `appendTransaction`, which groups it with the edit.
    """

    def test_pagination_runs_from_a_plugin(self, boot_result: dict[str, object]) -> None:
        undo = dict(boot_result.get("undoCheck") or {})
        assert undo.get("skipped") is not True, "the editor did not boot"
        assert undo.get("hasPaginationKey") is True

    def test_one_undo_removes_the_text_the_user_typed(self, boot_result: dict[str, object]) -> None:
        undo = dict(boot_result.get("undoCheck") or {})
        assert undo.get("paginationHappened") is True, "the probe forced a reflow"
        assert undo.get("undoRemovedText") is True

    def test_the_boot_is_still_error_free_with_pagination_active(
        self, boot_result: dict[str, object]
    ) -> None:
        assert boot_result.get("errors") == []

    def test_the_paginator_exposes_no_removed_api(self) -> None:
        """`run()` was replaced by `apply()`; a stale call site would throw."""
        source = (Path(__file__).resolve().parent.parent / "llex" / "static" / "js" / "main.js").read_text(
            encoding="utf-8"
        )
        assert "paginator.run()" not in source
        assert "paginator.apply()" in source


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
        # "Boot", "One test here." and "And a second test there." is nine words.
        meta = str(boot_result.get("metaText"))
        assert "9 words" in meta
        assert "42 characters" in meta
        assert "1 pages" in meta

    def test_the_zoom_indicator_shows_a_level(self, boot_result: dict[str, object]) -> None:
        assert boot_result.get("zoomLabel") == "100%"


@pytest.fixture(scope="module")
def tool_result(boot_result: dict[str, object]) -> dict[str, object]:
    """The toolbar probe's report from the single boot."""
    return dict(boot_result.get("toolCheck") or {})


class TestToolbarTools:
    """The tools added to the ribbon, driven through the real buttons."""

    def test_the_probe_ran(self, tool_result: dict[str, object]) -> None:
        assert tool_result, "no toolbar probe was reported"
        assert tool_result.get("skipped") is not True

    @pytest.mark.parametrize(
        "control", ["hasIndentIncrease", "hasIndentDecrease", "hasRule", "hasLink", "hasClearFormat", "hasPageBreak", "hasImage"]
    )
    def test_each_button_exists_and_is_wired(self, tool_result: dict[str, object], control: str) -> None:
        assert tool_result.get(control) is True, control

    def test_indent_goes_up_and_down(self, tool_result: dict[str, object]) -> None:
        assert tool_result.get("indentAfterIncrease") == 1
        assert tool_result.get("indentAfterDecrease") == 0

    def test_a_horizontal_rule_is_inserted(self, tool_result: dict[str, object]) -> None:
        assert tool_result.get("ruleRendered") is True

    def test_a_bare_address_becomes_https(self, tool_result: dict[str, object]) -> None:
        """Typing `example.com` and getting a dead relative link is the usual way a
        link box disappoints people."""
        assert tool_result.get("linkMade") is True
        assert tool_result.get("linkRowHidden") is True

    def test_clear_formatting_removes_the_marks(self, tool_result: dict[str, object]) -> None:
        assert tool_result.get("cleared") is True

    def test_a_forced_page_break_is_recorded(self, tool_result: dict[str, object]) -> None:
        assert tool_result.get("pageBreakSet") is True

    def test_the_indent_is_clamped(self, tool_result: dict[str, object]) -> None:
        # The stylesheet only describes eight levels, so a stylesheet that runs
        # out would silently stop indenting.
        assert tool_result.get("indentClamped") == 8
        assert tool_result.get("indentClampedLow") == 0

    def test_the_image_panel_opens_from_the_ribbon(self, tool_result: dict[str, object]) -> None:
        assert tool_result.get("imagePanelOpened") is True

    def test_the_image_node_is_in_the_schema(self, tool_result: dict[str, object]) -> None:
        """A block the paginator has to measure, so it must be a node and not markup."""
        assert tool_result.get("ribbonHasImageNode") is True

    def test_the_link_mark_is_in_the_schema(self, tool_result: dict[str, object]) -> None:
        assert tool_result.get("ribbonHasLinkMark") is True
@pytest.fixture(scope="module")
def command_result(boot_result: dict[str, object]) -> dict[str, object]:
    """Which of the ribbon's commands actually exist."""
    return dict(boot_result.get("commandCheck") or {})


class TestNoDeadCommands:
    """A dead button is `undefined` rather than an error, so nothing complains.

    This is the check that would have caught the indent, image and page-break
    commands being written but never registered: the buttons were present, the
    markup was right, and clicking did nothing.
    """

    @pytest.mark.parametrize(
        "command",
        [
            "increaseIndent", "decreaseIndent", "setIndent",
            "setImage", "setImageSize", "setImageFloat",
            "setPageBreakBefore", "setLink", "unsetLink", "setHorizontalRule",
        ],
    )
    def test_the_command_exists(self, command_result: dict[str, object], command: str) -> None:
        assert command_result.get("skipped") is not True
        assert command_result.get("threw") is None, command_result.get("threw")
        assert command_result.get(command) == "function", f"{command} is not registered"


@pytest.fixture(scope="module")
def image_result(boot_result: dict[str, object]) -> dict[str, object]:
    """The image panel's report from the single boot."""
    return dict(boot_result.get("imageCheck") or {})


class TestImagePanel:
    def test_the_panel_exists(self, image_result: dict[str, object]) -> None:
        assert image_result.get("hasPanel") is True
        assert image_result.get("threw") is None, image_result.get("threw")

    def test_it_opens_and_closes(self, image_result: dict[str, object]) -> None:
        assert image_result.get("closesAgain") is True

    def test_it_copes_with_no_selection(self, image_result: dict[str, object]) -> None:
        """Syncing with no image selected must not throw; the panel just disables
        its own controls."""
        assert image_result.get("syncWithoutSelection") is True

    def test_the_printable_width_is_positive(self, image_result: dict[str, object]) -> None:
        """Every size clamp is relative to it, so zero would silently break them."""
        assert image_result.get("printableWidth", 0) > 0

@pytest.fixture(scope="module")
def focus_result(boot_result: dict[str, object]) -> dict[str, object]:
    """The focus-mode probe's report from the single boot."""
    return dict(boot_result.get("focusCheck") or {})


class TestFocusModeIsReversible:
    """Focus mode hides the menu bar, and with it the menu item that turned it on.

    Without a way back it is a one-way door whose only exit is restarting the
    app, which is what it was.
    """

    def test_the_probe_ran(self, focus_result: dict[str, object]) -> None:
        assert focus_result, "no focus-mode probe was reported"
        assert "threw" not in focus_result, focus_result.get("threw")

    def test_it_starts_off_with_the_exit_hidden(self, focus_result: dict[str, object]) -> None:
        assert focus_result.get("startsOff") is True
        assert focus_result.get("exitStartsHidden") is True

    def test_the_menu_item_turns_it_on(self, focus_result: dict[str, object]) -> None:
        assert focus_result.get("turnsOn") is True
        assert focus_result.get("itemChecked") is True

    def test_the_exit_appears_when_it_is_on(self, focus_result: dict[str, object]) -> None:
        """The menu item is gone by then, so something has to replace it."""
        assert focus_result.get("exitAppears") is True

    def test_the_item_says_what_it_will_do(self, focus_result: dict[str, object]) -> None:
        assert focus_result.get("itemRenamed") is True

    def test_escape_leaves_it(self, focus_result: dict[str, object]) -> None:
        assert focus_result.get("escapeLeaves") is True

    def test_the_visible_control_leaves_it(self, focus_result: dict[str, object]) -> None:
        """For a user who does not think to press Escape."""
        assert focus_result.get("buttonLeaves") is True
        assert focus_result.get("exitHiddenAgain") is True


@pytest.fixture(scope="module")
def model_result(boot_result: dict[str, object]) -> dict[str, object]:
    """The model picker's report from the single boot."""
    return dict(boot_result.get("modelCheck") or {})


class TestTheModelIsChosenByTheUser:
    """It used to be configurable only by an environment variable."""

    def test_the_probe_ran(self, model_result: dict[str, object]) -> None:
        assert model_result, "no model-picker probe was reported"
        assert "threw" not in model_result, model_result.get("threw")

    def test_the_controls_exist(self, model_result: dict[str, object]) -> None:
        assert model_result.get("hasSelect") is True
        assert model_result.get("hasEndpoint") is True
        assert model_result.get("hasStatus") is True

    def test_the_picker_is_wired(self, model_result: dict[str, object]) -> None:
        assert model_result.get("hasPicker") is True

    def test_it_offers_no_model_as_the_default(self, model_result: dict[str, object]) -> None:
        """Turning the model off has to be possible without editing a file."""
        assert model_result.get("selectionDefaultsToNoModel") is True
