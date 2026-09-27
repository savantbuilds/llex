/**
 * Application bootstrap.
 *
 * Creates the editor, wires every control, and keeps the document, the
 * pagination engine, the status bar and the backend in agreement.
 *
 * The previous `editor.js` was one 690-line file containing two independent
 * `DOMContentLoaded` handlers. The second could not see the `editor` const
 * declared in the first, so "Execute Scaffolds" threw a `ReferenceError` the
 * moment it was clicked, and roughly a dozen menu items had no handler at all.
 * Each concern now lives in its own module with explicit dependencies.
 */

import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import TextAlign from '@tiptap/extension-text-align';
import Document from '@tiptap/extension-document';

import { PagedDocument, Page, Scaffold } from './extensions.js';
import { Paginator } from './paginator.js';
import { stripPageWrappers } from './pagination.js';
import { createRibbon } from './ribbon.js';
import { createOutline } from './outline.js';
import { createContextMenu } from './contextmenu.js';
import { createAssistant } from './assistant.js';
import { collectScaffolds, runScaffolds } from './scaffolds.js';
import { createFileOperations } from './fileops.js';
import { createSettings } from './settings.js';
import { createMenus } from './menus.js';
import { createStatusBar } from './statusbar.js';
import { api } from './api.js';
import { byId, debounce, flash, require } from './dom.js';
import { countCharacters, countWords } from './metrics.js';

/** Pasted content larger than this is worth confirming first. */
const LARGE_PASTE_CHARS = 8000;

/** Word counts are recomputed this often while typing. */
const STATS_DEBOUNCE_MS = 200;

/** Undo the "unsaved" marker if the user stops typing for this long. */
const DIRTY_DEBOUNCE_MS = 400;

/** CSS pixels per inch, matching the browser's definition of 1in. */
const PIXELS_PER_INCH = 96;

/** A blank page, so the schema is always satisfied. */
const EMPTY_DOCUMENT = '<div class="page"><p></p></div>';

/**
 * Application state shared between modules.
 *
 * Owned here rather than on `editor.storage`, because TipTap namespaces
 * extension storage by extension name -- `editor.storage.documentStats`, not
 * `editor.storage.stats` -- which makes it the wrong home for a bag of
 * application state that several unrelated modules need to read.
 *
 * @returns {{words: number, characters: number, pages: number, dirty: boolean, fileName: string|null, title: string}}
 */
function createAppState() {
  return { words: 0, characters: 0, pages: 1, dirty: false, fileName: null, title: '' };
}

function escapeHtml(text) {
  return String(text).replace(
    /[&<>"]/g,
    (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[char],
  );
}

async function boot() {
  const state = createAppState();
  const surface = require('editor')[0];
  if (!surface) return;

  // -- Backend handshake -------------------------------------------------- //

  let environment;
  let documentPayload;
  try {
    [environment, documentPayload] = await Promise.all([api.environment(), api.document()]);
  } catch (error) {
    surface.innerHTML =
      '<p class="fatal">LLex could not reach its local service. ' +
      `${escapeHtml(error && error.message ? error.message : error)}</p>`;
    return;
  }

  // -- Editor ------------------------------------------------------------- //
  //
  // `paginator` is created immediately after the editor but is referenced from
  // the editor's own update hooks, so the binding is declared first and read
  // defensively.

  /** @type {Paginator|undefined} */
  let paginator;

  const scheduleStats = debounce(() => {
    const text = editor.getText();
    state.words = countWords(text);
    state.characters = countCharacters(text);
    state.pages = editor.view.dom.querySelectorAll('.page').length || 1;
    statusBar.renderMeta();
  }, STATS_DEBOUNCE_MS);

  let markDirtyNow = () => {};

  const editor = new Editor({
    element: surface,
    extensions: [
      PagedDocument(Document),
      Page,
      Scaffold,
      // StarterKit v3 already bundles Underline, so registering it again would
      // install a duplicate extension under the same name.
      StarterKit.configure({ document: false }),
      TextAlign.configure({ types: ['heading', 'paragraph'] }),
    ],
    content: EMPTY_DOCUMENT,
    editorProps: {
      attributes: {
        spellcheck: 'true',
        role: 'textbox',
        'aria-multiline': 'true',
        'aria-label': 'Document body',
      },
      // Pasting another document would nest its pages inside ours; the
      // pagination engine re-creates breaks from measured layout instead.
      transformPastedHTML: stripPageWrappers,
      handlePaste: (_view, _event, slice) => {
        if (slice.content.size <= LARGE_PASTE_CHARS) return false;
        const proceed = window.confirm(
          `You are pasting about ${slice.content.size.toLocaleString()} characters, ` +
            'which will cascade across several pages. Continue?',
        );
        return !proceed; // true means "handled", i.e. do not paste
      },
    },
    onUpdate: () => {
      paginator?.schedule();
      scheduleStats();
      markDirtyNow();
    },
    onSelectionUpdate: () => ribbon.sync(),
  });

  // -- Components --------------------------------------------------------- //

  const status = byId('status-bar');
  const statusBar = createStatusBar({
    editor,
    state,
    status,
    message: byId('status-bar-text'),
    meta: byId('status-meta'),
  });
  const ribbon = createRibbon(editor);
  const outline = createOutline(editor);

  paginator = new Paginator({
    editor,
    onChange: () => {
      scheduleStats.cancel?.();
      statusBar.renderMeta();
    },
  });

  const settings = createSettings({
    onMarginsChanged: (margins) => {
      const style = document.documentElement.style;
      style.setProperty('--margin-top', `${margins.top * PIXELS_PER_INCH}px`);
      style.setProperty('--margin-right', `${margins.right * PIXELS_PER_INCH}px`);
      style.setProperty('--margin-bottom', `${margins.bottom * PIXELS_PER_INCH}px`);
      style.setProperty('--margin-left', `${margins.left * PIXELS_PER_INCH}px`);
      // Page geometry changed, so the stored breaks are no longer trustworthy.
      paginator.schedule();
    },
  });

  const assistant = createAssistant(editor, { api, status, panel: byId('sidebar') });

  const contextMenu = createContextMenu(editor, {
    status,
    onScaffoldChange: () => flash(status, 'Scaffold added. Run the batch when ready.'),
  });

  // -- Document loading --------------------------------------------------- //

  /**
   * Replace the editor's content with a document from the backend.
   * @param {{html?: string, document: object}} payload
   */
  function loadPayload(payload) {
    if (!payload) return;
    if (typeof payload.html === 'string' && payload.html) {
      editor.commands.setContent(payload.html, false);
      // Stored page breaks were computed under different fonts and zoom, so the
      // content is re-flowed rather than trusted to still fit.
      paginator.relayout();
    }
    if (payload.document) statusBar.setDocumentState(payload.document);
    ribbon.sync();
    outline.render();
    scheduleStats();
  }

  const files = createFileOperations(editor, {
    api,
    state,
    status,
    onLoaded: loadPayload,
    onSaved: (payload) => statusBar.setDocumentState(payload.document),
  });
  files.buildExportMenu(environment.formats || []);

  // -- Dirty tracking ----------------------------------------------------- //

  markDirtyNow = debounce(() => {
    state.dirty = true;
    statusBar.renderMeta();
  }, DIRTY_DEBOUNCE_MS);

  // pywebview closes the process on window close, so `beforeunload` is the only
  // chance to warn about unsaved work.
  window.addEventListener('beforeunload', (event) => {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = '';
  });

  // -- Scaffolds ---------------------------------------------------------- //

  const executeButton = byId('btn-execute-scaffolds');
  if (executeButton) {
    executeButton.addEventListener('click', async () => {
      const pending = collectScaffolds(editor);
      if (pending.length === 0) {
        flash(
          status,
          'No scaffolds yet. Highlight some text, right-click and choose Scaffold.',
        );
        return;
      }
      executeButton.disabled = true;
      executeButton.textContent = 'RunningÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦';
      try {
        const { total, applied, failures } = await runScaffolds(editor, {
          run: (scaffolds) => api.runScaffolds(scaffolds),
        });
        flash(
          status,
          failures.length > 0
            ? `${applied} of ${total} replaced; ${failures.length} failed`
            : `Replaced ${applied} scaffold${applied === 1 ? '' : 's'}`,
          failures.length > 0 ? 5000 : 2500,
        );
      } catch (error) {
        flash(status, error && error.message ? error.message : String(error), 5000);
      } finally {
        executeButton.disabled = false;
        executeButton.textContent = 'Execute Scaffolds';
      }
    });
  }

  // -- Panels, menus, zoom ------------------------------------------------ //

  function togglePanel(id, buttonId) {
    const panel = byId(id);
    const button = byId(buttonId);
    if (!panel) return;
    const open = panel.classList.toggle('open');
    if (button) {
      button.classList.toggle('active', open);
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    // A panel change alters the available width, which changes the wrapping.
    paginator.schedule();
  }

  const menus = createMenus({
    editor,
    status,
    editorRoot: byId('main-content'),
    actions: {
      save: files.save,
      saveAs: files.saveAs,
      open: files.open,
      newDocument: files.newDocument,
      toggleOutline: () => togglePanel('left-sidebar', 'btn-toggle-outline'),
      toggleAssistant: () => togglePanel('sidebar', 'btn-toggle-sidebar'),
      onZoom: () => {
        paginator.schedule();
        window.setTimeout(() => paginator.run(), 150);
      },
    },
  });

  byId('btn-toggle-outline')?.addEventListener('click', () =>
    togglePanel('left-sidebar', 'btn-toggle-outline'),
  );
  byId('btn-toggle-sidebar')?.addEventListener('click', () =>
    togglePanel('sidebar', 'btn-toggle-sidebar'),
  );

  // A narrower column wraps differently, so a resize changes page heights.
  let resizeTimer = 0;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => paginator.run(), 200);
  });

  // -- Go ----------------------------------------------------------------- //

  assistant.describe(environment.assistant);
  statusBar.setDocumentState(environment.document);
  loadPayload(documentPayload);
  statusBar.setReady('Ready');

  // Page height depends on font metrics, so a fallback font would produce a
  // wrong first layout. Re-measure once the real fonts are in.
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => paginator.run()).catch(() => paginator.run());
  }
  window.setTimeout(() => paginator.run(), 400);

  // Exposed deliberately, for debugging from the webview console.
  window.llex = { editor, paginator, api, menus, settings, files, assistant, contextMenu, state };
}

function start() {
  boot().catch((error) => {
    console.error('llex: failed to start', error);
    const surface = byId('editor');
    if (surface) {
      surface.innerHTML = `<p class="fatal">LLex failed to start: ${escapeHtml(
        error && error.message ? error.message : error,
      )}</p>`;
    }
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', start, { once: true });
} else {
  start();
}
