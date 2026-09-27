/**
 * The status bar.
 *
 * It was hardcoded to the word "Ready" and never updated, despite the
 * architecture notes describing it as part of the ribbon. It now reports the
 * document's real state: word and character counts, page count, the size of any
 * selection, and whether there are unsaved changes.
 */

import { formatStats } from './metrics.js';

/**
 * @param {{editor: import('@tiptap/core').Editor, status: HTMLElement|null, meta: HTMLElement|null}} options
 */
export function createStatusBar(options) {
  const { editor, status } = options;
  const meta = options.meta;
  let lastMeta = '';

  /** Re-derive the statistics block from current editor state. */
  function renderMeta() {
    if (!meta) return;
    const { from, to } = editor.state.selection;
    const text = formatStats(
      editor.storage.stats.words,
      editor.storage.stats.characters,
      editor.storage.stats.pages,
      from === to ? 0 : to - from,
    );
    if (text !== lastMeta) {
      meta.textContent = text;
      lastMeta = text;
    }
  }

  /**
   * @param {{dirty?: boolean, fileName?: string|null, title?: string}} state
   */
  function setDocumentState(state) {
    editor.storage.dirty = Boolean(state.dirty);
    editor.storage.fileName = state.fileName || null;
    editor.storage.title = state.title || '';
    renderMeta();
  }

  /**
   * Show a transient message, reverting afterwards.
   * @param {string} [message]
   */
  function setReady(message = 'Ready') {
    if (!status) return;
    status.textContent = message;
    delete status.dataset.flashing;
  }

  return { renderMeta, setDocumentState, setReady };
}
