/**
 * The find-and-replace panel.
 *
 * A bar rather than a modal: a word processor's find must be dismissible with a
 * single keystroke and must not get in the way of the text, and Ctrl+F should
 * put the caret in the box with the current selection already selected so
 * searching for a selected phrase is one keystroke.
 */

import { FindController } from './find.js';
import { byId, flash } from './dom.js';

const SHORTCUT_HINT = /Mac|iPhone|iPad/.test(
  (typeof navigator !== 'undefined' && navigator.platform) || '',
)
  ? '⌘F'
  : 'Ctrl+F';

/**
 * @param {import('@tiptap/core').Editor} editor
 * @param {{status?: HTMLElement|null}} options
 */
export function createFind(editor, options = {}) {
  const panel = byId('find-panel');
  const queryField = byId('find-query');
  const replaceField = byId('find-replace');
  const countLabel = byId('find-count');
  const options_ = {
    caseSensitive: byId('find-case'),
    wholeWord: byId('find-whole-word'),
    regex: byId('find-regex'),
  };
  const status = options.status || null;

  if (!panel || !queryField) return null;

  const controller = new FindController(editor);

  function renderCount() {
    if (!countLabel) return;
    if (!queryField.value.trim()) {
      countLabel.textContent = '';
      return;
    }
    const total = controller.matchCount;
    if (total === 0) {
      countLabel.textContent = 'No matches';
      countLabel.dataset.state = 'none';
      return;
    }
    const position = controller.active >= 0 ? controller.active + 1 : 0;
    countLabel.textContent = `${position} of ${total}`;
    countLabel.dataset.state = 'found';
  }

  function rerun({ keepFocus = true } = {}) {
    controller.search({
      query: queryField.value,
      caseSensitive: options_.caseSensitive ? options_.caseSensitive.checked : false,
      wholeWord: options_.wholeWord ? options_.wholeWord.checked : false,
      regex: options_.regex ? options_.regex.checked : false,
    });
    renderCount();
    if (controller.matchCount > 0) controller.focusCurrent();
    if (keepFocus) queryField.focus();
  }

  controller.onChange = renderCount;

  function open() {
    panel.hidden = false;
    panel.style.display = 'flex';
    // Pre-fill with the selection, which is what makes Ctrl+F useful: the
    // phrase you just selected is almost always what you want to find.
    const { from, to } = editor.state.selection;
    if (from !== to) {
      const selected = editor.state.doc.textBetween(from, to, ' ');
      if (selected && !selected.includes('\n')) queryField.value = selected;
    }
    queryField.focus();
    queryField.select();
    rerun();
  }

  function close() {
    panel.hidden = true;
    panel.style.display = 'none';
    controller.close();
    editor.commands.focus();
  }

  function go(direction) {
    if (controller.matchCount === 0) {
      flash(status, 'No matches');
      return;
    }
    const match = direction > 0 ? controller.next() : controller.previous();
    renderCount();
    if (match) {
      editor.chain().focus().setTextSelection(match).scrollIntoView().run();
    }
  }

  // -- wiring ------------------------------------------------------------- //

  byId('menu-find')?.addEventListener('click', open);
  byId('menu-replace')?.addEventListener('click', open);
  byId('find-next')?.addEventListener('click', () => go(1));
  byId('find-previous')?.addEventListener('click', () => go(-1));
  byId('find-close')?.addEventListener('click', close);
  byId('find-replace-one')?.addEventListener('click', () => {
    if (controller.replaceCurrent(replaceField.value)) renderCount();
    else flash(status, 'No matches to replace');
  });
  byId('find-replace-all')?.addEventListener('click', () => {
    const count = controller.replaceAll(replaceField.value);
    renderCount();
    flash(status, count === 1 ? 'Replaced 1 match' : `Replaced ${count} matches`);
  });

  // Live search as the user types, but not on every keystroke of a fast typist.
  let timer = 0;
  queryField.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => rerun({ keepFocus: false }), 150);
  });

  for (const key of ['caseSensitive', 'wholeWord', 'regex']) {
    options_[key]?.addEventListener('change', () => rerun());
  }

  replaceField.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      byId('find-replace-one')?.click();
    }
  });

  queryField.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      const useShift = event.shiftKey;
      if (useShift) go(-1);
      else if (replaceField.value) byId('find-replace-one')?.click();
      else go(1);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  });

  // Ctrl/Cmd+F anywhere, and Escape to dismiss from anywhere.
  document.addEventListener('keydown', (event) => {
    const modifier = /Mac|iPhone|iPad/.test(navigator.platform || '') ? event.metaKey : event.ctrlKey;
    if (modifier && event.key.toLowerCase() === 'f') {
      event.preventDefault();
      open();
      return;
    }
    if (modifier && event.key.toLowerCase() === 'h') {
      event.preventDefault();
      open();
      byId('find-replace')?.focus();
      return;
    }
    if (event.key === 'Escape' && !panel.hidden) {
      event.preventDefault();
      close();
    }
  });

  byId('find-shortcut')?.replaceChildren(document.createTextNode(SHORTCUT_HINT));

  return { open, close, rerun, controller };
}
