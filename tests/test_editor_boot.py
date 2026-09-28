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
  // jsdom does not implement getClientRects at all, so calling it throws
  // "is not a function" as an uncaught error. Real browsers have it, and both
  // elements and ranges legitimately use it, so the harness supplies the empty
  // result a zero-layout node would give. Left unshimmed this only showed up
  // once a probe kept the process alive long enough to hit it.
  var emptyRects = function () {
    return Object.assign([], { item: function () { return null; } });
  };
  window.Element.prototype.getClientRects = emptyRects;
  if (window.Range) {
    window.Range.prototype.getClientRects = emptyRects;
    // Ranges are zero-sized and unlaid out here, which is exactly what jsdom
    // already reports for elements.
    window.Range.prototype.getBoundingClientRect = function () {
      return { top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0, x: 0, y: 0 };
    };
  }

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

  // Menu wiring and the shortcut table.
  //
  // Both were checkable only by clicking things in a browser. "No dead commands"
  // used to mean "no file contains the words not implemented yet", which a menu
  // item wired to nothing passes.
  result.menuIds = (function () {
    var found = {};
    var doc = dom.window.document;
    [
      'menu-undo', 'menu-redo', 'menu-cut', 'menu-copy', 'menu-paste',
      'menu-select-all', 'menu-zoom-in', 'menu-zoom-out', 'menu-zoom-reset',
      'menu-bold', 'menu-italic', 'menu-underline', 'menu-strikethrough',
      'menu-highlight', 'menu-clear-format', 'menu-bullet', 'menu-number',
      'menu-indent-increase', 'menu-indent-decrease', 'menu-move-up',
      'menu-move-down', 'menu-page-break', 'menu-heading-1', 'menu-heading-2',
      'menu-heading-3', 'menu-body-text', 'menu-link', 'menu-image',
      'menu-horizontal-rule', 'menu-word-count', 'menu-find-next',
    ].forEach(function (id) { found[id] = Boolean(doc.getElementById(id)); });
    return found;
  })();

  // Menu items that exist in the markup but were never wired to a handler.
  //
  // This compares the DOM against the ids the menu module actually attached
  // listeners to, so a new item added to the template and forgotten shows up
  // here. The File and Edit menus were full of those, and "no dead commands" used
  // to mean only that no file contained the words "not implemented yet".
  result.deadMenuItems = (function () {
    var doc = dom.window.document;
    var menus = window.llex.menus || {};
    var wired = menus.wiredIds || [];
    var dead = [];
    doc.querySelectorAll('.dropdown-menu button[role="menuitem"], .dropdown-menu button[role="menuitemcheckbox"]')
      .forEach(function (item) {
        if (item.classList.contains('nested-trigger')) return;
        if (item.dataset.export !== undefined) return;
        // The context menu's Cut/Copy/Paste carry `data-action` and are dispatched
        // by the context-menu module rather than by id.
        if (item.dataset.action !== undefined) return;
        if (wired.indexOf(item.id) !== -1) return;
        dead.push(item.id || item.textContent.trim().slice(0, 24));
      });
    return dead;
  })();

  result.shortcutCheck = (function () {
    if (!window.llex || !window.llex.shortcuts) return { count: 0, unbound: ['not loaded'] };
    return {
      count: window.llex.shortcuts.SHORTCUTS.length,
      unbound: window.llex.shortcuts.unbound(),
    };
  })();

  // Page break, through the real ribbon button and the real paginator.
  //
  // A function, not an immediately-invoked one, so it runs when the chain gets
  // to it rather than at load time -- alongside the probes it would race.
  function pageBreakCheck() {
    var ed = window.llex.editor;
    var doc = dom.window.document;
    var out = {};
    var button = doc.getElementById('btn-page-break');
    if (!button || !window.llex.paginator) return { skipped: true };

    var pageCount = function () {
      return ed.state.doc.childCount;
    };
    var blocksOn = function (index) {
      var found = [];
      var page = ed.state.doc.child(index);
      page.forEach(function (block) { found.push(block.textContent); });
      return found;
    };

    // Reflow is driven through the plugin, which runs inside the click's
    // transaction, so one settle is enough. The bounded loop is a safety net
    // rather than the mechanism: an unbounded poll here would hang the harness
    // instead of failing it, which is the failure mode this file already had.
    var settle = function (ms) {
      return new Promise(function (resolve) { setTimeout(resolve, ms); });
    };
    var reflowUntil = function (wanted, tries) {
      if (pageCount() === wanted || tries <= 0) return Promise.resolve(pageCount() === wanted);
      window.llex.paginator.apply();
      return settle(20).then(function () { return reflowUntil(wanted, tries - 1); });
    };

    ed.commands.setContent(
      '<div class="page"><p>alpha</p><p>beta</p><p>gamma</p></div>'
    );
    // Inside the *second* block. A break on the first block of a page is already
    // satisfied -- there is nothing before it to break away from -- and the
    // paginator deliberately does not create an empty page for it.
    ed.commands.setTextSelection(1 + ed.state.doc.child(0).child(0).nodeSize + 1);
    out.pagesBefore = pageCount();
    out.brokeSecondBlock = ed.state.selection.$from.parent.textContent === 'beta';

    button.click();
    return reflowUntil(2, 8).then(function () {
      out.pagesAfter = pageCount();
      out.splitAfter = blocksOn(0).join(',');
      var all = [];
      for (var i = 0; i < pageCount(); i += 1) {
        blocksOn(i).forEach(function (text) { all.push(text); });
      }
      out.textIntact = all.join(',') === 'alpha,beta,gamma';
      out.breakSurvived = ed.getAttributes('paragraph').breakBefore === true;

      // And the same button takes it back out again.
      button.click();
      return reflowUntil(1, 8);
    }).then(function () {
      out.pagesAfterToggleOff = pageCount();
      ed.commands.setTextSelection(2);
      out.attributeCleared = ed.getAttributes('paragraph').breakBefore === false;
      return out;
    });
  }

  // Concurrent-open conflict, through the real dialog.
  //
  // The bug this guards: a conflict used to flash a message and then restart
  // autosave a second later, so the "detection" was a one-second warning before
  // the last writer won anyway. `acceptDiskVersion` existed, was correct, and was
  // called from nowhere.
  function conflictSelection() {
    var autosave = window.llex.autosave;
    var doc = dom.window.document;
    var ed = window.llex.editor;
    var out = {};
    var modal = doc.getElementById('conflict-modal');
    var detail = doc.getElementById('conflict-detail');
    var keepButton = doc.getElementById('btn-conflict-keep');
    var diskButton = doc.getElementById('btn-conflict-disk');

    out.hasDialog = Boolean(modal && keepButton && diskButton);
    out.startsHidden = Boolean(modal && modal.hidden);
    if (!out.hasDialog) return Promise.resolve(out);

    // Let the dialog's own async handler run before anything is asserted.
    var settle = function () {
      return new Promise(function (resolve) { setTimeout(resolve, 60); });
    };

    var writes = [];
    var originalApi = autosave.api;
    var report = { status: 'conflict', detail: 'doc.llex was changed by another window.' };
    autosave.api = {
      autosave: function (html, title) {
        writes.push({ html: html, title: title });
        return Promise.resolve({ status: 'saved', document: { file_name: 'doc.llex' } });
      },
      conflict: function () { return Promise.resolve(report); },
      acceptDisk: function () {
        return Promise.resolve({
          status: 'reloaded',
          html: '<div class="page"><p>the version from disk</p></div>',
          document: { file_name: 'doc.llex', title: 'From disk' },
        });
      },
    };

    window.llex.state.dirty = true;
    window.llex.state.fileName = 'doc.llex';

    return autosave.checkConflict()
      .then(function (result) {
        out.reported = Boolean(result && result.status === 'conflict');
        out.shown = modal.hidden === false;
        out.detailShown = Boolean(detail && detail.textContent.indexOf('another window') !== -1);
        // The important one: autosave must stay stopped, or it races whatever is
        // writing the file while the user is deciding.
        out.timersStopped = autosave.timer === null && autosave.watchTimer === null;

        keepButton.click();
        return settle();
      })
      .then(function () {
        out.keepWrote = writes.length === 1;
        out.keepClosed = modal.hidden === true;
        out.timersResumed = autosave.timer !== null;

        autosave.stop();
        // Re-assert: `checkConflict` returns early without a file name, and the
        // save above is entitled to leave the shared state however it likes.
        window.llex.state.fileName = 'doc.llex';
        report = { status: 'conflict', detail: 'doc.llex was changed again.' };
        return autosave.checkConflict();
      })
      .then(function (second) {
        out.secondReported = Boolean(second && second.status === 'conflict');
        out.modalShownForSecond = modal.hidden === false;
        out.detailNow = detail ? detail.textContent : null;
      })
      .then(function () {
        diskButton.click();
        return settle();
      })
      .then(function () {
        // Read the document rather than the rendered text: the paginator owns the
        // rendered pages, and the reload lands in the document underneath it.
        var after = ed.state.doc.textBetween(0, ed.state.doc.content.size, '\n');
        out.diskLoaded = after.indexOf('the version from disk') !== -1;
        out.afterText = after.slice(0, 120);
        // Read the document rather than the rendered text: the paginator owns the
        // rendered pages, and the reload lands in the document underneath it.
        var after = ed.state.doc.textBetween(0, ed.state.doc.content.size, '\n');
        out.diskLoaded = after.indexOf('the version from disk') !== -1;
        out.afterText = after.slice(0, 120);
        out.diskClearedDirty = window.llex.state.dirty === false;
        out.diskWroteNothing = writes.length === 1;
      })
      .then(function () {
        autosave.api = originalApi;
        autosave.stop();
        return out;
      });
  }

  // Rewrite Selection. The button is named for what it does, so this checks that
  // it does -- and that it replaces the range the user *selected*, rather than
  // wherever the caret happened to be when a slow model finally answered.
  //
  // It runs after everything else has settled, not alongside it. Several probes
  // undo their own edits, and an undo() landing while this one waits for the
  // model would put the old content back underneath the exact-text assertion.
  function rewriteSelection() {
    var doc = dom.window.document;
    var ed = window.llex.editor;
    var out = {};
    var button = doc.getElementById('btn-rewrite');
    var output = doc.getElementById('llm-output');
    var fullText = function () { return ed.state.doc.textBetween(0, ed.state.doc.content.size, '\n'); };
    // A throw inside a timer callback escapes the try/catch around a synchronous
    // probe, and an unsettled promise here means no result is ever written. So
    // every step settles, and every failure is reported as data.
    var settle = function (ms, body) {
      return new Promise(function (resolve) {
        setTimeout(function () {
          try { body(); } catch (e) { out.threw = String((e && e.stack) || e).slice(0, 600); }
          resolve(out);
        }, ms);
      });
    };

    // The assistant holds the same api object it was built with, so replacing a
    // method on it reaches the assistant; swapping `window.llex.api` would not.
    //
    // Everything the app uses to *load* a document is stubbed out too: the editor
    // was fed the fixture at boot, and an in-flight request landing during the
    // probe would overwrite the content under test.
    var real = {};
    ['document', 'environment', 'setContent', 'autosave', 'save'].forEach(function (name) {
      real[name] = window.llex.api[name];
      window.llex.api[name] = function () { return Promise.resolve({ html: ed.getHTML() }); };
    });
    var release = null;
    window.llex.api.rewrite = function () {
      return new Promise(function (resolve) { release = resolve; });
    };
    var restore = function () {
      Object.keys(real).forEach(function (name) { window.llex.api[name] = real[name]; });
    };

    // The probe owns the document state it asserts on, so it sets it outright.
    ed.commands.setContent('<div class="page"><p>the meeting was moved to friday</p></div>');
    // Read it back rather than assuming: the paginator may have re-flowed it.
    var before = fullText();
    // A word the result does not itself contain, so "'moved' is still there" is a
    // meaningful check rather than a coincidence.
    var needle = 'moved';
    out.needleFound = before.indexOf(needle) !== -1;
    // Located by scanning document positions, not by indexing the text: a text
    // offset is not a document position, because positions count the node
    // boundaries the text projection does not. Bounded, so a failure here cannot
    // hang the harness.
    var from = -1;
    for (var pos = 0; pos < ed.state.doc.content.size && from < 0; pos += 1) {
      if (ed.state.doc.textBetween(pos, pos + needle.length, '') === needle) from = pos;
    }
    out.foundPosition = from >= 0;
    if (from < 0) {
      // Reported rather than guessed at: guessing here is what made an earlier
      // version of this probe pass without testing anything.
      out.skipped = 'the probe word is not in the document';
      restore();
      return Promise.resolve(out);
    }
    ed.commands.setTextSelection({ from: from, to: from + needle.length });
    out.selectedIsNeedle = ed.state.doc.textBetween(
      ed.state.selection.from, ed.state.selection.to, '') === needle;

    button.click();
    out.started = release !== null;

    // The user clicks somewhere else while the model is thinking.
    ed.commands.setTextSelection(1);
    out.selectionMoved = ed.state.selection.from !== from;
    out.caretAfterMove = ed.state.selection.from;

    var expected = 'The meeting was rescheduled for Friday.';
    release({ result: expected });

    return settle(60, function () {
      // Exact: the old text with the selected substring replaced, nothing else.
      out.exactReplacement = fullText() === before.replace(needle, expected);
      out.replacedText = fullText().indexOf(expected) !== -1;
      out.oldTextGone = fullText().indexOf(needle) === -1;
      // The result sits at the captured range, not at the caret it moved to.
      out.landsAtSelection = ed.state.doc.textBetween(from, from + expected.length, ' ') === expected;
      out.notAtCaret = from !== out.caretAfterMove;
      out.outputShown = output.textContent.indexOf('rescheduled') !== -1;

      // With nothing selected it must decline rather than take the document.
      out.beforeDecline = fullText();
      ed.commands.setTextSelection(1);
      button.click();
    })
      .then(function () {
        return settle(60, function () {
          out.declinesWithoutSelection = fullText() === out.beforeDecline;
          out.declineMessage = output.textContent;
        });
      })
      .then(function () { restore(); return out; });
  }

  // A probe that never settles would leave the autosave timers running and the
  // process alive, so the run would time out with nothing to explain it. Give up
  // loudly instead, and stop the timers either way.
  var finished = false;
  var writeResult = function (errors) {
    if (finished) return;
    finished = true;
    if (window.llex && window.llex.autosave) window.llex.autosave.stop();
    if (errors) result.errors = errors;
    process.stdout.write('__RESULT__' + JSON.stringify(result));
  };
  var watchdog = setTimeout(function () { writeResult(['harness: no result after 15s']); }, 15000);
  var describeFailure = function (e) { return { threw: String((e && e.stack) || e).slice(0, 600) }; };

  Promise.resolve(result.autosaveCheck)
    .catch(describeFailure)
    .then(function (settled) {
      result.autosaveCheck = settled;
      // Sequential, not concurrent: the conflict probe replaces the document
      // wholesale, so a page-break probe running alongside it would be asserting
      // about content that no longer exists.
      return conflictSelection();
    })
    .catch(describeFailure)
    .then(function (settled) {
      result.conflictCheck = settled;
      return pageBreakCheck();
    })
    .catch(describeFailure)
    .then(function (settled) {
      result.pageBreakCheck = settled;
      return rewriteSelection();
    })
    .catch(describeFailure)
    .then(function (settled) {
      result.rewriteCheck = settled;
      clearTimeout(watchdog);
      writeResult(null);
    });
  })().catch((e) => {
  if (window.llex && window.llex.autosave) window.llex.autosave.stop();
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


class TestNoDeadMenuItems:
    """Every menu entry has to do something.

    The File and Edit menus were full of items that did nothing at all, and the
    formatting toolbar advertised ``Ctrl+Enter`` for a page break that changed
    nothing visible. Both look fine in a screenshot, so they are checked here
    against the real DOM and the real shortcut table.
    """

    @pytest.mark.parametrize(
        "item_id",
        [
            "menu-undo", "menu-redo", "menu-cut", "menu-copy", "menu-paste",
            "menu-select-all", "menu-zoom-in", "menu-zoom-out", "menu-zoom-reset",
            "menu-bold", "menu-italic", "menu-underline", "menu-strikethrough",
            "menu-highlight", "menu-clear-format", "menu-bullet", "menu-number",
            "menu-indent-increase", "menu-indent-decrease", "menu-move-up",
            "menu-move-down", "menu-page-break", "menu-heading-1", "menu-heading-2",
            "menu-heading-3", "menu-body-text", "menu-link", "menu-image",
            "menu-horizontal-rule", "menu-word-count", "menu-find-next",
        ],
    )
    def test_the_item_exists(self, boot_result: dict[str, object], item_id: str) -> None:
        assert boot_result.get("menuIds", {}).get(item_id) is True, f"{item_id} is missing"

    def test_every_menu_item_is_wired_to_an_action(
        self, boot_result: dict[str, object]
    ) -> None:
        dead = boot_result.get("deadMenuItems")
        assert dead == [], f"menu items with no handler: {dead}"

    def test_the_shortcut_table_is_loaded(self, boot_result: dict[str, object]) -> None:
        check = boot_result.get("shortcutCheck") or {}
        assert check.get("count", 0) > 30, "the shortcut table did not load"
        assert check.get("unbound") == [], f"shortcuts with no action: {check.get('unbound')}"


class TestPageBreakActuallyBreaksThePage:
    """The page-break button was a no-op on screen.

    The attribute was set, saved, exported and honoured by the stylesheet when
    printing -- but nothing read it during reflow, which is what draws the pages
    the user is looking at. The button's tooltip promised a page break and the
    page did not change.
    """

    def test_the_reflow_moves_the_block_onto_a_new_page(
        self, boot_result: dict[str, object]
    ) -> None:
        check = boot_result.get("pageBreakCheck") or {}
        assert "threw" not in check, check.get("threw")
        assert check.get("brokeSecondBlock") is True, "the cursor was not in the second block"
        assert check.get("pagesBefore") == 1, "the fixture was not a single page"
        assert check.get("pagesAfter") == 2, "the page break did not add a page"
        # Only the first block stays behind; the break lands before the second.
        assert check.get("splitAfter") == "alpha", check.get("splitAfter")
        assert check.get("textIntact") is True
        assert check.get("breakSurvived") is True, "the attribute was consumed"

    def test_pressing_it_again_removes_the_break(
        self, boot_result: dict[str, object]
    ) -> None:
        check = boot_result.get("pageBreakCheck") or {}
        assert check.get("pagesAfterToggleOff") == 1, "the break could not be removed"
        assert check.get("attributeCleared") is True


class TestConcurrentOpenIsResolvedByTheUser:
    """A conflict has to be settled, not just announced.

    The detection existed and worked; what it did not do was let anyone answer.
    It flashed a message and restarted autosave a second later, so the window
    that happened to save last won — which is the thing it was built to prevent.
    ``acceptDiskVersion`` was correct, and called from nowhere.
    """

    @staticmethod
    def probe(boot_result: dict[str, object]) -> dict[str, object]:
        data = boot_result.get("conflictCheck")
        assert isinstance(data, dict), "the conflict probe reported nothing"
        assert "threw" not in data, data["threw"]
        return data

    def test_there_is_a_dialog_to_answer_with(self, boot_result: dict[str, object]) -> None:
        probe = self.probe(boot_result)
        assert probe["hasDialog"] is True
        assert probe["startsHidden"] is True, "the dialog was open before anything happened"

    def test_a_conflict_is_shown_rather_than_only_flashed(
        self, boot_result: dict[str, object]
    ) -> None:
        probe = self.probe(boot_result)
        assert probe["reported"] is True
        assert probe["shown"] is True
        assert probe["detailShown"] is True

    def test_nothing_is_written_while_the_user_decides(
        self, boot_result: dict[str, object]
    ) -> None:
        # The one-second restart this replaced: autosave coming back on its own
        # during the decision is what made the detection pointless.
        probe = self.probe(boot_result)
        assert probe["timersStopped"] is True

    def test_keeping_this_window_overwrites_the_file_and_carries_on(
        self, boot_result: dict[str, object]
    ) -> None:
        probe = self.probe(boot_result)
        assert probe["keepWrote"] is True
        assert probe["keepClosed"] is True
        assert probe["timersResumed"] is True

    def test_taking_the_version_on_disk_replaces_the_document(
        self, boot_result: dict[str, object]
    ) -> None:
        probe = self.probe(boot_result)
        assert probe["secondReported"] is True
        assert probe["modalShownForSecond"] is True
        assert probe["diskLoaded"] is True
        assert probe["diskClearedDirty"] is True
        assert probe["diskWroteNothing"] is True, "reloading wrote over the file it read"


class TestRewriteReplacesTheSelection:
    """The button says it rewrites the selection, so it must.

    The assistant waits on a local model, which can take seconds. Anything that
    reads the selection *after* that wait is reading the wrong one: the user has
    moved on. The probe below reproduces exactly that — it clicks Rewrite, moves
    the caret, and only then lets the model answer.
    """

    @staticmethod
    def probe(boot_result: dict[str, object]) -> dict[str, object]:
        data = boot_result.get("rewriteCheck")
        assert isinstance(data, dict), "the rewrite probe reported nothing"
        assert "threw" not in data, data["threw"]
        assert "skipped" not in data, data["skipped"]
        return data

    def test_the_model_is_asked_for_the_selected_words(self, boot_result: dict[str, object]) -> None:
        probe = self.probe(boot_result)
        assert probe["needleFound"] and probe["foundPosition"]
        assert probe["selectedIsNeedle"] is True
        assert probe["started"] is True, "clicking the button did not reach the model"

    def test_it_replaces_exactly_the_selected_text(self, boot_result: dict[str, object]) -> None:
        probe = self.probe(boot_result)
        assert probe["exactReplacement"] is True
        assert probe["replacedText"] is True
        assert probe["oldTextGone"] is True

    def test_the_result_lands_where_the_user_pointed(self, boot_result: dict[str, object]) -> None:
        probe = self.probe(boot_result)
        assert probe["selectionMoved"] is True, "the probe failed to move the caret"
        assert probe["notAtCaret"] is True
        assert probe["landsAtSelection"] is True, "the rewrite went to the caret, not the selection"

    def test_the_result_is_also_shown(self, boot_result: dict[str, object]) -> None:
        probe = self.probe(boot_result)
        assert probe["outputShown"] is True

    def test_it_declines_when_nothing_is_selected(self, boot_result: dict[str, object]) -> None:
        probe = self.probe(boot_result)
        assert probe["declinesWithoutSelection"] is True
        assert "select" in str(probe["declineMessage"]).lower()


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
