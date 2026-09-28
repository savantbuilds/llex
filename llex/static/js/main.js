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

import { Editor, mergeAttributes } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import Link from '@tiptap/extension-link';
import TextAlign from '@tiptap/extension-text-align';
import Document from '@tiptap/extension-document';
import {
  CharacterStyle,
  DIRECTION,
  Heading,
  Image,
  ImageCommands,
  Indent,
  IndentableParagraph,
  PageBreak,
  Page,
  Scaffold,
} from './extensions.js';
import { Paginator, VIRTUAL_CLASS, supportsContainment } from './paginator.js';
import { stripPageWrappers } from './pagination.js';
import { createRibbon } from './ribbon.js';
import { createOutline } from './outline.js';
import { createContextMenu } from './contextmenu.js';
import { createAssistant } from './assistant.js';
import { runScaffolds, collectScaffolds } from './scaffolds.js';
import { createFind } from './findbar.js';
import { findPlugin } from './find.js';
import { createImagePanel } from './imagepanel.js';
import { isSafeImageSource } from './images.js';
import { createAutosave } from './autosave.js';
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

  /**
   * The 1-based page the cursor is on, or 0 when it cannot be determined.
   *
   * Derived from the DOM rather than from the document, because only the
   * rendered layout knows which page a position ended up on.
   *
   * @returns {number}
   */
  function currentPage() {
    if (!paginator) return 0;
    const { node, offset } = editor.state.selection.$head;
    if (!node.isInline) {
      // A block position: the page is whichever one contains it.
      const dom = editor.view.domAtPos(editor.state.selection.from);
      let element = dom.node;
      while (element && !(element.classList && element.classList.contains('page'))) {
        element = element.parentElement;
      }
      if (!element) return 0;
      return Array.prototype.indexOf.call(paginator.container.children, element) + 1;
    }
    const dom = editor.view.domAtPos(editor.state.selection.from - offset);
    let element = dom.node;
    while (element && !(element.classList && element.classList.contains('page'))) {
      element = element.parentElement;
    }
    if (!element) return 0;
    return Array.prototype.indexOf.call(paginator.container.children, element) + 1;
  }

  const scheduleStats = debounce(() => {
    const text = editor.getText();
    state.words = countWords(text);
    state.characters = countCharacters(text);
    state.pages = editor.view.dom.querySelectorAll('.page').length || 1;
    statusBar.renderMeta(currentPage());
  }, STATS_DEBOUNCE_MS);

  let markDirtyNow = () => {};

  const editor = new Editor({
    element: surface,
    extensions: [
      // The page wrapper replaces the stock document node.
      Document.extend({ content: 'page+' }),
      Page,
      Scaffold,
      CharacterStyle,
      // Our own heading, so `keepWithNext` can be carried; StarterKit's is
      // configured off below.
      Heading,
      // StarterKit v3 already bundles Underline and Heading.
      StarterKit.configure({
      document: false,
      heading: false,
      paragraph: false,
      // StarterKit ships a link of its own; this one is configured differently,
      // and registering both leaves two nodes with the same name.
      link: false,
    }),
    // Registered separately rather than through `StarterKit.configure`, which
    // silently keeps its own copy: the custom `renderHTML` below is then never
    // called and the attribute is simply absent. Disabling StarterKit's
    // paragraph and supplying this one is the pattern already used for `heading`
    // and `document` above.
    IndentableParagraph.extend({
      // `dir="auto"` per block, for the same reason the headings have it: a
      // document may contain text in both directions, and only the block's own
      // content can say which way it runs.
      renderHTML({ HTMLAttributes }) {
        return [
          'p',
          mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, DIRECTION),
          0,
        ];
      },
    }),
    Link.configure({
      // Clicking a link in an editor should put the caret there, not follow it.
      // The reader opens links from the exported document instead.
      openOnClick: false,
      autolink: true,
      linkOnPaste: true,
      HTMLAttributes: { rel: 'noopener noreferrer', class: 'doc-link' },
    }),
    Image,
    // The commands the new toolbar buttons call. Without these registered the
    // buttons exist and do nothing, and nothing complains: a missing command is
    // `undefined` rather than an error until something calls it.
    ImageCommands,
    Indent,
    PageBreak,
    Placeholder.configure({ placeholder: 'Start writing…' }),
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
      // An attribute change -- an indent, an alignment, a wrap -- is a state
      // change too, and it fires `onUpdate` rather than `onSelectionUpdate`, so
      // syncing only on the latter would leave the toolbar showing the previous
      // state until the user happened to move the caret.
      ribbon.sync();
    },
    onSelectionUpdate: () => {
      ribbon.sync();
      statusBar.renderMeta(currentPage());
    },
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
  // `imagePanel` and `paginator` are assigned below; the hooks are closures, so
  // they read whatever is current when a click happens rather than at wiring
  // time. A button cannot be pressed before boot finishes, so this is safe and
  // it avoids threading a not-yet-built object through the ribbon.
  let imagePanel = null;
  const ribbon = createRibbon(editor, {
    onFind: () => find && find.open(),
    onImage: () => imagePanel && imagePanel.open(),
    onStatus: (message) => flash(status, message, 2500),
  });
  const outline = createOutline(editor);

  paginator = new Paginator({
    editor,
    onChange: () => {
      scheduleStats.cancel?.();
      statusBar.renderMeta();
    },
  });

  // The assistant is created after the settings dialog, and the model picker
  // needs to tell it when the model changes. A holder rather than a direct
  // reference: the callback only ever runs on a click, by which time the
  // assistant exists, but reading a `const` before it is initialised throws.
  const assistantRef = { current: null };

  const settings = createSettings({
    api,
    onModelChanged: (info) => {
      const assistant = assistantRef.current;
      if (!assistant) return;
      const model = info && info.current ? info.current.model : '';
      assistant.describe({ backend: info ? info.backend : '', online: Boolean(model) });
    },
    onPageChanged: ({ width, height, margins }) => {
      const style = document.documentElement.style;
      style.setProperty('--page-width', `${width * PIXELS_PER_INCH}px`);
      style.setProperty('--page-height', `${height * PIXELS_PER_INCH}px`);
      style.setProperty('--margin-top', `${margins.top * PIXELS_PER_INCH}px`);
      style.setProperty('--margin-right', `${margins.right * PIXELS_PER_INCH}px`);
      style.setProperty('--margin-bottom', `${margins.bottom * PIXELS_PER_INCH}px`);
      style.setProperty('--margin-left', `${margins.left * PIXELS_PER_INCH}px`);
      // Page geometry changed, so the stored breaks are no longer trustworthy.
      paginator.schedule();
    },
  });

  const assistant = createAssistant(editor, { api, status, panel: byId('sidebar') });
  assistantRef.current = assistant;

  // The highlight plugin is registered here and reads its state from the
  // controller lazily, so the controller can be created after the editor.
  let find = null;
  editor.registerPlugin(
    findPlugin((state) => (find && find.controller.isOpen
      ? { matches: find.controller.matches, active: find.controller.active }
      : null)),
  );

  find = createFind(editor, { status });

  imagePanel = createImagePanel({ editor, paginator, status });
  for (const preset of document.querySelectorAll('#image-size-presets .image-preset')) {
    preset.addEventListener('click', () => {
      const fraction = Number.parseFloat(preset.dataset.fraction || '1');
      if (Number.isFinite(fraction)) imagePanel.applyPreset(fraction);
    });
  }

  // Pasting an image from the clipboard should insert it the same way choosing
  // a file does, including measuring it first -- a pasted image with no
  // dimensions is the same pagination hazard as a dropped one.
  const pasteImages = (event) => {
    const items = Array.from(event.clipboardData?.items || []);
    const item = items.find((entry) => /^image\//i.test(entry.type || ''));
    if (!item) return false;
    const file = item.getAsFile();
    if (!file) return false;
    event.preventDefault();
    imagePanel.insertFile(file);
    return true;
  };
  editor.view.dom.addEventListener('paste', pasteImages);

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

  // -- Autosave and conflict detection ------------------------------------- //
  //
  // A conflict is a question, not a notification. Which version of the file wins
  // is the user's decision, so the dialog has two real answers and dismissing it
  // is not one of them: closing it without answering leaves the document alone
  // and autosave stopped, rather than letting a later save quietly overwrite the
  // other window's work.

  // Let the browser skip rendering pages that are nowhere near the viewport.
  // Opt-in on capability, so a browser without `content-visibility` simply lays
  // every page out as it always did. See the module docstring in paginator.js:
  // this is containment, not virtualisation, and the model needs a real DOM.
  if (supportsContainment()) {
    byId('main-content').classList.add(VIRTUAL_CLASS);
  }

  const conflictModal = byId('conflict-modal');
  const conflictDetail = byId('conflict-detail');
  const conflictKeep = byId('btn-conflict-keep');
  const conflictDisk = byId('btn-conflict-disk');

  /**
   * Show the conflict question.
   *
   * Declared before the autosave that calls it, and not inline as its
   * `onConflict`, so the wiring below reads in order: dialog, then controller,
   * then handlers.
   *
   * @param {object} conflict The API report.
   */
  function askConflict(conflict) {
    if (conflictDetail) {
      conflictDetail.textContent = conflict && conflict.detail
        ? conflict.detail
        : 'This file was changed by something else.';
    }
    if (conflictModal) conflictModal.hidden = false;
    (conflictDisk || conflictKeep || conflictModal)?.focus();
  }

  /**
   * Apply an answer to a reported conflict.
   *
   * A `null` response means the action failed and left the document alone, so
   * the document is still in conflict and autosave is still stopped: the dialog
   * stays up and the user can try again, which is the only honest outcome when
   * the request did not work.
   *
   * @param {() => Promise<object|null>} action
   * @param {string} message
   */
  async function settleConflict(action, message) {
    if (conflictKeep) conflictKeep.disabled = true;
    if (conflictDisk) conflictDisk.disabled = true;
    try {
      const response = await action();
      if (response === null) throw new Error('the request did not complete');
      if (conflictModal) conflictModal.hidden = true;
      flash(status, message);
    } catch (error) {
      const detail = error && error.message ? error.message : String(error);
      flash(status, `Could not resolve the conflict: ${detail}`, 8000);
    } finally {
      if (conflictKeep) conflictKeep.disabled = false;
      if (conflictDisk) conflictDisk.disabled = false;
    }
  }

  const autosave = createAutosave({
    editor,
    api,
    state,
    status,
    onConflict: askConflict,
  });
  autosave.start();

  // Attached once, after `autosave` exists, and not per report: a second conflict
  // on the same document reuses the same dialog, and a handler added each time
  // would mean every past conflict firing a second write.
  if (conflictKeep) {
    conflictKeep.addEventListener('click', () => {
      settleConflict(() => autosave.keepMine(), 'Kept this window’s version.');
    });
  }
  if (conflictDisk) {
    conflictDisk.addEventListener('click', () => {
      settleConflict(() => autosave.acceptDiskVersion(), 'Loaded the file from disk.');
    });
  }

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
      executeButton.textContent = 'Running…';
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
        window.setTimeout(() => paginator.apply(), 150);
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
    resizeTimer = window.setTimeout(() => paginator.apply(), 200);
  });

  // -- Go ----------------------------------------------------------------- //

  assistant.describe(environment.assistant);
  statusBar.setDocumentState(environment.document);
  loadPayload(documentPayload);
  statusBar.setReady('Ready');

  // Page height depends on font metrics, so a fallback font would produce a
  // wrong first layout. Re-measure once the real fonts are in.
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => paginator.apply()).catch(() => paginator.apply());
  }
  window.setTimeout(() => paginator.apply(), 400);

  // Exposed deliberately, for debugging from the webview console.
  window.llex = { editor, paginator, api, menus, settings, files, assistant, contextMenu, find, imagePanel, autosave, state };
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
