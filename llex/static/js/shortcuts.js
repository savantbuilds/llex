/**
 * Keyboard shortcuts a word processor is expected to have.
 *
 * Kept as data rather than as a `switch`, so the table can be asserted on
 * directly. "The shortcut exists" is otherwise only checkable by pressing it in
 * a browser, and the shortcuts that regress are always the ones nobody types in
 * a test.
 *
 * ## The conventions, and why
 *
 * The keys below are the ones people already have in their fingers from Word,
 * Google Docs, LibreOffice and Writer. Where those products disagree, the more
 * widely used binding is bound *in addition*, because shipping only one means
 * the other set of users press a key that does nothing:
 *
 * - **Redo is `Ctrl+Y` *and* `Ctrl+Shift+Z`.** Windows users expect the first,
 *   everyone else the second.
 * - **Line moves are `Ctrl+Alt+Up`/`Down` *and* `Alt+Up`/`Down`.** Word uses the
 *   former, Google Docs the latter, and both are muscle memory by now.
 * - **Lists get three bindings each.** `Ctrl+Shift+8`/`7` are the intuitive
 *   digits, `Ctrl+Shift+L`/`N` are Word's, and `Ctrl+.` is the one people
 *   remember from Writer. Any single choice excludes somebody.
 * - **`Ctrl+Enter` inserts a page break.** `Ctrl+Shift+Enter` is a column break
 *   in Word, and multi-column layout is not implemented, so it is deliberately
 *   absent rather than silently doing the wrong thing.
 * - **`Ctrl+G` is "find next", not "go to".** No go-to dialog exists, and taking
 *   the key for a feature that is not there would be worse than using it for
 *   something real.
 * - **`Ctrl+Shift+G` is the word count**, as in Word.
 *
 * `Mod` stands in for `Ctrl` on Windows and `Cmd` on macOS, so one table entry
 * serves both platforms. The alternative is a second hand-maintained copy of
 * the same shortcuts, which drifts from the first.
 */

/**
 * @typedef {object} Shortcut
 * @property {string} keys  Canonical key combination, lower case, `mod` for the
 *   platform's primary modifier.
 * @property {string} label What the user is told to press.
 * @property {string} run   Action id, resolved by the owner.
 * @property {string} [group] Menu group, for the entries that appear in a menu.
 */

/** @type {Shortcut[]} */
export const SHORTCUTS = [
  // -- File ---------------------------------------------------------------
  { keys: 'mod+n', label: 'Ctrl+N', run: 'new', group: 'File' },
  { keys: 'mod+o', label: 'Ctrl+O', run: 'open', group: 'File' },
  { keys: 'mod+s', label: 'Ctrl+S', run: 'save', group: 'File' },
  { keys: 'mod+shift+s', label: 'Ctrl+Shift+S', run: 'saveAs', group: 'File' },
  { keys: 'mod+p', label: 'Ctrl+P', run: 'print', group: 'File' },

  // -- Edit ---------------------------------------------------------------
  { keys: 'mod+z', label: 'Ctrl+Z', run: 'undo', group: 'Edit' },
  { keys: 'mod+shift+z', label: 'Ctrl+Shift+Z', run: 'redo', group: 'Edit' },
  { keys: 'mod+y', label: 'Ctrl+Y', run: 'redo', group: 'Edit' },
  { keys: 'mod+x', label: 'Ctrl+X', run: 'cut', group: 'Edit' },
  { keys: 'mod+c', label: 'Ctrl+C', run: 'copy', group: 'Edit' },
  { keys: 'mod+v', label: 'Ctrl+V', run: 'paste', group: 'Edit' },
  { keys: 'mod+shift+v', label: 'Ctrl+Shift+V', run: 'pasteAsPlainText', group: 'Edit' },
  { keys: 'mod+a', label: 'Ctrl+A', run: 'selectAll', group: 'Edit' },
  { keys: 'mod+f', label: 'Ctrl+F', run: 'find', group: 'Edit' },
  { keys: 'mod+h', label: 'Ctrl+H', run: 'replace', group: 'Edit' },
  { keys: 'mod+g', label: 'Ctrl+G', run: 'findNext', group: 'Edit' },

  // -- Character formatting ------------------------------------------------
  { keys: 'mod+b', label: 'Ctrl+B', run: 'bold' },
  { keys: 'mod+i', label: 'Ctrl+I', run: 'italic' },
  { keys: 'mod+u', label: 'Ctrl+U', run: 'underline' },
  { keys: 'mod+shift+x', label: 'Ctrl+Shift+X', run: 'strikethrough' },
  { keys: 'mod+shift+h', label: 'Ctrl+Shift+H', run: 'highlight' },

  // -- Paragraph formatting ------------------------------------------------
  { keys: 'mod+l', label: 'Ctrl+L', run: 'alignLeft' },
  { keys: 'mod+e', label: 'Ctrl+E', run: 'alignCenter' },
  { keys: 'mod+r', label: 'Ctrl+R', run: 'alignRight' },
  { keys: 'mod+j', label: 'Ctrl+J', run: 'alignJustify' },

  // -- Structure -----------------------------------------------------------
  { keys: 'mod+alt+1', label: 'Ctrl+Alt+1', run: 'heading1' },
  { keys: 'mod+alt+2', label: 'Ctrl+Alt+2', run: 'heading2' },
  { keys: 'mod+alt+3', label: 'Ctrl+Alt+3', run: 'heading3' },
  { keys: 'mod+alt+4', label: 'Ctrl+Alt+4', run: 'heading4' },
  { keys: 'mod+alt+5', label: 'Ctrl+Alt+5', run: 'heading5' },
  { keys: 'mod+alt+6', label: 'Ctrl+Alt+6', run: 'heading6' },
  { keys: 'mod+alt+0', label: 'Ctrl+Alt+0', run: 'bodyText' },
  { keys: 'mod+shift+8', label: 'Ctrl+Shift+8', run: 'bulletList' },
  { keys: 'mod+shift+l', label: 'Ctrl+Shift+L', run: 'bulletList' },
  { keys: 'mod+.', label: 'Ctrl+.', run: 'bulletList' },
  { keys: 'mod+shift+7', label: 'Ctrl+Shift+7', run: 'orderedList' },
  { keys: 'mod+shift+n', label: 'Ctrl+Shift+N', run: 'orderedList' },
  { keys: 'mod+enter', label: 'Ctrl+Enter', run: 'pageBreak' },
  { keys: 'tab', label: 'Tab', run: 'indent' },
  { keys: 'shift+tab', label: 'Shift+Tab', run: 'outdent' },

  // -- Movement ------------------------------------------------------------
  { keys: 'mod+alt+arrowup', label: 'Ctrl+Alt+Up', run: 'moveBlockUp' },
  { keys: 'mod+alt+arrowdown', label: 'Ctrl+Alt+Down', run: 'moveBlockDown' },
  { keys: 'alt+arrowup', label: 'Alt+Up', run: 'moveBlockUp' },
  { keys: 'alt+arrowdown', label: 'Alt+Down', run: 'moveBlockDown' },

  // -- Insert --------------------------------------------------------------
  { keys: 'mod+k', label: 'Ctrl+K', run: 'link' },
  { keys: 'mod+alt+r', label: 'Ctrl+Alt+R', run: 'horizontalRule' },
  { keys: 'mod+alt+i', label: 'Ctrl+Alt+I', run: 'image' },

  // -- View ----------------------------------------------------------------
  { keys: 'mod+=', label: 'Ctrl+=', run: 'zoomIn', group: 'View' },
  { keys: 'mod++', label: 'Ctrl++', run: 'zoomIn', group: 'View' },
  { keys: 'mod+-', label: 'Ctrl+-', run: 'zoomOut', group: 'View' },
  { keys: 'mod+0', label: 'Ctrl+0', run: 'zoomReset', group: 'View' },
  { keys: 'mod+shift+g', label: 'Ctrl+Shift+G', run: 'wordCount', group: 'View' },
  { keys: 'escape', label: 'Esc', run: 'exitFocusMode' },
];

/**
 * Shortcuts that still apply while a text field has focus.
 *
 * `Ctrl+H` in the find field is the case that matters: replacing should not
 * require clicking the document first. Everything else is excluded, because
 * `Ctrl+A` in a text field means "all of this field" and hijacking it to select
 * the document is one of the oldest browser-editor bugs.
 */
export const ALWAYS_APPLIES = new Set(['find', 'replace', 'save', 'print']);

/**
 * Canonical form of a keyboard event: sorted modifiers plus the key.
 *
 * `ArrowUp` rather than `Up`, and lower case, because the table is written in
 * the form a spec sheet uses rather than the form one browser reports.
 *
 * @param {{key: string, ctrlKey?: boolean, metaKey?: boolean, altKey?: boolean, shiftKey?: boolean}} event
 * @param {{apple?: boolean}} [options]
 * @returns {string}
 */
export function eventKey(event, options = {}) {
  const apple = options.apple ?? false;
  const parts = [];
  if (apple ? event.metaKey : event.ctrlKey) parts.push('mod');
  if (event.altKey) parts.push('alt');
  if (event.shiftKey) parts.push('shift');
  // Reported even when it is not the primary modifier, so a stray second
  // modifier does not collapse onto a shorter binding.
  if (apple ? event.ctrlKey : event.metaKey) parts.push('meta');
  parts.push(String(event.key || '').toLowerCase());
  return parts.join('+');
}

/**
 * Look up the action for an event.
 *
 * @param {{key: string, ctrlKey?: boolean, metaKey?: boolean, altKey?: boolean, shiftKey?: boolean}} event
 * @param {{apple?: boolean}} [options]
 * @returns {string|null} The action id, or null when the key is not a shortcut.
 */
export function shortcutFor(event, options = {}) {
  const key = eventKey(event, options);
  const match = SHORTCUTS.find((entry) => entry.keys === key);
  return match ? match.run : null;
}

/**
 * Whether the event came from a form control that handles its own keyboard input.
 *
 * Deliberately *not* keyed on `isContentEditable`. The editor is a contenteditable
 * div, so treating that as "in a field" would switch off every shortcut in the
 * document -- and it is the reason so many editors get `Ctrl+A` wrong.
 *
 * @param {EventTarget|null} target
 * @returns {boolean}
 */
export function isInEditable(target) {
  const element = /** @type {Element|null} */ (target);
  if (!element || element.nodeType !== 1) return false;
  if (element.isContentEditable) return false;
  return ['input', 'textarea', 'select'].includes(
    String(element.tagName || '').toLowerCase(),
  );
}

/**
 * Whether a shortcut should be handled given where focus is.
 *
 * @param {string} run
 * @param {EventTarget|null} target
 * @returns {boolean}
 */
export function appliesHere(run, target) {
  if (!isInEditable(target)) return true;
  return ALWAYS_APPLIES.has(run);
}
