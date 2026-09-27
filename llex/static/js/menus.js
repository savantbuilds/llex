/**
 * Application menus, keyboard shortcuts, zoom and focus mode.
 *
 * The File, Edit and View menus previously had items with no handlers at all:
 * "New" and every "Download" entry did nothing when clicked.
 */

import { hasModifier, flash, byId } from './dom.js';

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

  function toggleFocusMode() {
    if (!editorRoot) return;
    const active = document.body.classList.toggle('focus-mode');
    byId('menu-focus')?.setAttribute('aria-checked', active ? 'true' : 'false');
    flash(status, active ? 'Focus mode on' : 'Focus mode off');
    if (actions.onZoom) actions.onZoom(zoom);
  }

  // -- Menu items --------------------------------------------------------- //

  const wiring = [
    ['menu-undo', () => editor.chain().focus().undo().run()],
    ['menu-redo', () => editor.chain().focus().redo().run()],
    ['menu-cut', () => document.execCommand('cut')],
    ['menu-copy', () => document.execCommand('copy')],
    ['menu-paste', async () => {
      try {
        const text = await navigator.clipboard.readText();
        editor.chain().focus().insertContent(text).run();
      } catch {
        flash(status, 'Clipboard access denied; use Ctrl+V.');
      }
    }],
    ['menu-select-all', () => editor.chain().focus().selectAll().run()],
    ['menu-find', () => flash(status, 'Find is not implemented yet.')],
    ['menu-zoom-in', () => stepZoom(1)],
    ['menu-zoom-out', () => stepZoom(-1)],
    ['menu-zoom-reset', () => applyZoom(100)],
    ['menu-focus', toggleFocusMode],
    ['menu-print', () => window.print()],
  ];

  for (const [id, action] of wiring) {
    const element = byId(id);
    if (element) element.addEventListener('click', action);
  }

  byId('menu-toggle-outline')?.addEventListener('click', () => actions.toggleOutline?.());
  byId('menu-toggle-assistant')?.addEventListener('click', () => actions.toggleAssistant?.());

  // -- Keyboard shortcuts ------------------------------------------------- //

  document.addEventListener('keydown', (event) => {
    if (!hasModifier(event)) return;
    const key = event.key.toLowerCase();

    const shortcut = {
      s: event.shiftKey ? actions.saveAs : actions.save,
      o: actions.open,
      n: event.shiftKey ? actions.newDocument : undefined,
      p: () => window.print(),
      '=': () => stepZoom(1),
      '+': () => stepZoom(1),
      '-': () => stepZoom(-1),
      '0': () => applyZoom(100),
    }[key];

    if (!shortcut) return;
    event.preventDefault();
    Promise.resolve(shortcut()).catch((error) => {
      flash(status, error && error.message ? error.message : String(error));
    });
  });

  return { applyZoom, toggleFocusMode, get zoom() { return zoom; } };
}
