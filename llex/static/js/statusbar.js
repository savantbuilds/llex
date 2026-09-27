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
 * @param {{editor: import('@tiptap/core').Editor, state: object, status: HTMLElement|null, meta: HTMLElement|null}} options
 */
export function createStatusBar(options) {
  const { editor, state } = options;
  const meta = options.meta;
  // The status bar is a <footer> holding three regions. Messages go to the
  // message span, never to the footer itself: writing `textContent` on the
  // footer would delete the statistics and the zoom indicator.
  const message = options.message || document.getElementById('status-bar-text');
  let lastMeta = '';

  /** Re-derive the statistics block from current editor state. */
  function renderMeta() {
    if (!meta) return;
    const { from, to } = editor.state.selection;
    const text = formatStats(state.words, state.characters, state.pages, from === to ? 0 : to - from);
    if (text !== lastMeta) {
      meta.textContent = text;
      lastMeta = text;
    }
  }

  /**
   * Record where the document came from and whether it has unsaved changes.
   * @param {{dirty?: boolean, fileName?: string|null, title?: string}} document
   */
  function setDocumentState(document) {
    state.dirty = Boolean(document.dirty);
    state.fileName = document.fileName || null;
    state.title = document.title || '';
    renderMeta();
  }

  /**
   * Show a message, reverting afterwards.
   * @param {string} [text]
   */
  function setReady(text = 'Ready') {
    if (!message) return;
    message.textContent = text;
    delete message.dataset.flashing;
  }

  return { renderMeta, setDocumentState, setReady };
}
