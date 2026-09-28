/**
 * Application menus, keyboard shortcuts, zoom and focus mode.
 *
 * The File, Edit and View menus previously had items with no handlers at all:
 * "New" and every "Download" entry did nothing when clicked.
 */

import { flash, byId, IS_APPLE } from './dom.js';
import { shortcutFor } from './shortcuts.js';

/** Zoom levels offered by View > Zoom. */
export const ZOOM_LEVELS = [50, 75, 90, 100, 110, 125, 150, 200];

/**
 * @param {{editor: import('@tiptap/core').Editor, status: HTMLElement|null, actions: object, editorRoot: HTMLElement|null}} options
 */
export function createMenus(options) {
  const { editor, status, actions } = options;
  const editorRoot = options.editorRoot;

  let zoom = 100;

  /**
   * Zoom by scaling the editor viewport, and nothing else.
   *
   * The page boxes are measured in CSS pixels, so the scale has to apply to the
   * whole document viewport -- hence a transform. The root font size is
   * deliberately left alone: it is sized in `rem` throughout the chrome, so
   * changing it as well would scale the toolbar and panels a second time.
   */
  function applyZoom(percent) {
    zoom = Math.min(Math.max(percent, 25), 400);
    if (editorRoot) {
      editorRoot.style.setProperty('--zoom', String(zoom / 100));
    }
    const label = byId('zoom-level');
    if (label) label.textContent = `${zoom}%`;
    if (actions.onZoom) actions.onZoom(zoom);
    flash(status, `Zoom ${zoom}%`);
  }

  const stepZoom = (direction) => {
    const index = ZOOM_LEVELS.findIndex((level) => level >= zoom);
    const base = index === -1 ? ZOOM_LEVELS.length - 1 : index;
    const next = Math.min(ZOOM_LEVELS.length - 1, Math.max(0, base + direction));
    applyZoom(ZOOM_LEVELS[next]);
  };

  /**
   * Leave focus mode if it is on, and report whether it did.
   *
   * Focus mode hides the menu bar and the ribbon, which means it also hides the
   * menu item that turned it on. Without this, entering it is a one-way door
   * whose only exit is restarting the app, so it is called from the Escape key
   * and from the button focus mode shows in its place.
   *
   * @returns {boolean} True when focus mode was on and is now off.
   */
  function exitFocusMode() {
    if (!editorRoot || !document.body.classList.contains('focus-mode')) return false;
    toggleFocusMode();
    return true;
  }

  function toggleFocusMode() {
    if (!editorRoot) return;
    const active = document.body.classList.toggle('focus-mode');
    const item = byId('menu-focus');
    if (item) {
      item.setAttribute('aria-checked', active ? 'true' : 'false');
      // The label has to say what the item will *do*, which changes with state.
      const label = item.querySelector('.menu-label') || item;
      if (label.textContent) label.textContent = active ? 'Leave Focus Mode' : 'Focus Mode';
    }
    // Focus mode covers the chrome, so it brings its own way out.
    //
    // `setAttribute('hidden', 'false')` would *not* show it: the attribute's
    // presence is what hides an element, whatever its value. It has to be
    // removed.
    const exit = byId('focus-exit');
    if (exit) {
      if (active) exit.removeAttribute('hidden');
      else exit.setAttribute('hidden', '');
    }
    flash(status, active ? 'Focus mode on — press Esc to leave' : 'Focus mode off');
    if (actions.onZoom) actions.onZoom(zoom);
  }

  byId('menu-toggle-outline')?.addEventListener('click', () => actions.toggleOutline?.());
  byId('menu-toggle-assistant')?.addEventListener('click', () => actions.toggleAssistant?.());

  // -- Keyboard shortcuts ------------------------------------------------- //

  /**
   * Actions the shortcut table and the menu can name, whatever else is passed
   * in. Declared before the menu wiring above uses them, and every entry is a
   * function: a binding that resolves to nothing is reported by the tests rather
   * than swallowing a keystroke at runtime.
   */
  const builtins = {
    clearFormatting: () => {
      // Character formatting is marks and block formatting is attributes, so
      // clearing has to do both. `unsetAllMarks` alone leaves a centred,
      // indented paragraph behind, which is not what "clear formatting" means.
      editor
        .chain()
        .focus()
        .unsetAllMarks()
        .unsetMark('textStyle', { fontFamily: null })
        .unsetMark('textStyle', { fontSize: null })
        .setTextAlign('left')
        .setIndent(0)
        .run();
    },
    undo: () => editor.chain().focus().undo().run(),
    redo: () => editor.chain().focus().redo().run(),
    cut: () => document.execCommand('cut'),
    copy: () => document.execCommand('copy'),
    paste: async () => {
      try {
        const text = await navigator.clipboard.readText();
        editor.chain().focus().insertContent(text).run();
      } catch {
        flash(status, 'Clipboard access denied; use Ctrl+V.');
      }
    },
    pasteAsPlainText: async () => {
      try {
        const text = await navigator.clipboard.readText();
        editor
          .chain()
          .focus()
          .insertContent(text.split(/\n{2,}/).map((line) => ({ type: 'paragraph', content: [{ type: 'text', text: line }] })))
          .run();
      } catch {
        flash(status, 'Clipboard access denied; use Ctrl+Shift+V.');
      }
    },
    selectAll: () => editor.chain().focus().selectAll().run(),
    bold: () => editor.chain().focus().toggleBold().run(),
    italic: () => editor.chain().focus().toggleItalic().run(),
    underline: () => editor.chain().focus().toggleUnderline().run(),
    strikethrough: () => editor.chain().focus().toggleStrike().run(),
    highlight: () => actions.highlight,
    alignLeft: () => editor.chain().focus().setTextAlign('left').run(),
    alignCenter: () => editor.chain().focus().setTextAlign('center').run(),
    alignRight: () => editor.chain().focus().setTextAlign('right').run(),
    alignJustify: () => editor.chain().focus().setTextAlign('justify').run(),
    heading1: () => editor.chain().focus().toggleHeading({ level: 1 }).run(),
    heading2: () => editor.chain().focus().toggleHeading({ level: 2 }).run(),
    heading3: () => editor.chain().focus().toggleHeading({ level: 3 }).run(),
    heading4: () => editor.chain().focus().toggleHeading({ level: 4 }).run(),
    heading5: () => editor.chain().focus().toggleHeading({ level: 5 }).run(),
    heading6: () => editor.chain().focus().toggleHeading({ level: 6 }).run(),
    bodyText: () => editor.chain().focus().setParagraph().run(),
    bulletList: () => editor.chain().focus().toggleBulletList().run(),
    orderedList: () => editor.chain().focus().toggleOrderedList().run(),
    // Adding a break is a normal edit: the plugin's appendTransaction picks it
    // up. *Removing* one is not. The split is already in the document by then,
    // and no overflow is involved, so nothing would move the blocks back -- the
    // page would stay split with the break gone, which is worse than the
    // original bug because the toolbar no longer reflects the document. So the
    // whole page set is recomputed.
    pageBreak: () => {
      const on = Boolean(editor.getAttributes('paragraph').breakBefore);
      const result = editor.chain().focus().setPageBreakBefore(!on).run();
      if (on && options.onRelayout) options.onRelayout();
      return result;
    },
    indent: () => editor.chain().focus().increaseIndent().run(),
    outdent: () => editor.chain().focus().decreaseIndent().run(),
    horizontalRule: () => editor.chain().focus().setHorizontalRule().run(),
    moveBlockUp: () => actions.moveBlock && actions.moveBlock(-1),
    moveBlockDown: () => actions.moveBlock && actions.moveBlock(1),
    link: () => actions.link && actions.link(),
    image: () => actions.image && actions.image(),
    zoomIn: () => stepZoom(1),
    zoomOut: () => stepZoom(-1),
    zoomReset: () => applyZoom(100),
    print: () => window.print(),
    exitFocusMode: () => exitFocusMode(),
  };

  // -- Menu items --------------------------------------------------------- //

  /**
   * Menu entries that are the same action as a shortcut or a ribbon button.
   *
   * Named by action id rather than by a closure, so a menu item cannot drift
   * from the key printed beside it: running a different command from the
   * shortcut next to it is a bug the user finds by comparing the two.
   */
  const menuActions = {
    'menu-undo': 'undo',
    'menu-redo': 'redo',
    'menu-cut': 'cut',
    'menu-copy': 'copy',
    'menu-paste': 'paste',
    // The image panel's own menu, which has no ids of its own.
    'image-cut': 'cut',
    'image-copy': 'copy',
    'image-paste': 'paste',
    // Paste without formatting. Word and Writer both put this behind a menu item
    // rather than a bare shortcut; the shortcut exists whether or not the item
    // does, so the item is added rather than assumed.
    'menu-paste-plain': 'pasteAsPlainText',
    'menu-print': 'print',
    'menu-paste-plain': 'pasteAsPlainText',
    'menu-select-all': 'selectAll',
    'menu-zoom-in': 'zoomIn',
    'menu-zoom-out': 'zoomOut',
    'menu-zoom-reset': 'zoomReset',
    'menu-bold': 'bold',
    'menu-italic': 'italic',
    'menu-underline': 'underline',
    'menu-strikethrough': 'strikethrough',
    'menu-highlight': 'highlight',
    'menu-clear-format': 'clearFormatting',
    'menu-bullet': 'bulletList',
    'menu-number': 'orderedList',
    'menu-indent-increase': 'indent',
    'menu-indent-decrease': 'outdent',
    'menu-move-up': 'moveBlockUp',
    'menu-move-down': 'moveBlockDown',
    'menu-page-break': 'pageBreak',
    'menu-heading-1': 'heading1',
    'menu-heading-2': 'heading2',
    'menu-heading-3': 'heading3',
    'menu-body-text': 'bodyText',
    'menu-link': 'link',
    'menu-image': 'image',
    'menu-horizontal-rule': 'horizontalRule',
    'menu-word-count': 'wordCount',
  };

  /**
   * Resolve an action id to something callable and run it.
   *
   * A binding that resolves to nothing returns `false` rather than running, so
   * the caller can fall through instead of swallowing the key.
   *
   * @param {string} name
   * @returns {boolean} Whether the action ran.
   */
  const runAction = (name) => {
    const action = actions[name] ?? builtins[name];
    if (typeof action !== 'function') return false;
    const result = action();
    Promise.resolve(result).catch((error) => {
      flash(status, error && error.message ? error.message : String(error));
    });
    return true;
  };

  for (const [id, run] of Object.entries(menuActions)) {
    const element = byId(id);
    if (element) element.addEventListener('click', () => runAction(run));
  }

  for (const [id, action] of [
    ['menu-find', () => actions.find && actions.find()],
    ['menu-replace', () => actions.replace && actions.replace()],
    ['menu-find-next', () => actions.findNext && actions.findNext()],
    ['menu-focus', toggleFocusMode],
    ['focus-exit', exitFocusMode],
  ]) {
    const element = byId(id);
    if (element) element.addEventListener('click', action);
  }

  document.addEventListener('keydown', (event) => {
    // Escape leaves focus mode, whatever else has focus. Checked first, because
    // the exit control is the only thing visible while focus mode is on and
    // Escape is the key people try first.
    if (event.key === 'Escape' && exitFocusMode()) {
      event.preventDefault();
      return;
    }

    const run = shortcutFor(event, { apple: IS_APPLE });
    if (!run) return;
    // A shortcut that is bound but has no action would otherwise swallow the
    // key and do nothing at all, which is worse than letting the browser have
    // it.
    if (!runAction(run)) return;
    if (event.target !== editorRoot) event.preventDefault();
  });

  return {
    applyZoom,
    toggleFocusMode,
    exitFocusMode,
    get zoom() { return zoom; },
    /**
     * Whether an action id in the shortcut table resolves to something.
     *
     * Published so the wiring can be checked rather than assumed: an entry that
     * names an action nobody implements is a key that silently does nothing,
     * which is the failure this whole table exists to prevent.
     *
     * @param {string} name
     * @returns {boolean}
     */
    hasAction: (name) => typeof (actions[name] ?? builtins[name]) === 'function',
    /**
     * Every element id this module attached a handler to.
     *
     * Published so the boot harness can compare the menu markup against the
     * wiring: an item in the template with no handler looks fine in a screenshot
     * and does nothing when clicked.
     */
    wiredIds: [
      ...Object.keys(menuActions),
      ...['menu-find', 'menu-replace', 'menu-find-next', 'menu-focus', 'focus-exit'],
      ...(actions.wiredElsewhere || []),
    ],
    wiredElsewhere: actions.wiredElsewhere || [],
  };
}
