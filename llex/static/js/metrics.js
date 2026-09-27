/**
 * Document metrics shared by the status bar and the pagination engine.
 *
 * Deliberately free of DOM and editor dependencies so the numbers can be
 * unit-tested directly, and so there is exactly one implementation of each.
 */

/**
 * Count words the way the backend does.
 *
 * The rule must match `llex/markup.py` exactly, or the status bar and the
 * counts stored in the `.llex` metadata would disagree: a run of letters,
 * digits or underscores, optionally joined by an internal apostrophe so `don't`
 * counts once.
 *
 * The character class is spelled with Unicode property escapes rather than
 * `\w`, because JavaScript's `\w` is ASCII-only even with the `u` flag. Using
 * `\w` here made a wholly non-Latin document report a word count of zero,
 * while the Python side -- whose `\w` is Unicode-aware -- counted it correctly.
 *
 * @param {string} text
 * @returns {number}
 */
export function countWords(text) {
  if (!text) return 0;
  const matches = String(text).match(/[\p{L}\p{N}_]+(?:['\u2019][\p{L}\p{N}_]+)*/gu);
  return matches ? matches.length : 0;
}

/**
 * Count characters, excluding the paragraph separators ProseMirror inserts
 * when flattening a document to text.
 *
 * @param {string} text
 * @returns {number}
 */
export function countCharacters(text) {
  return text ? text.replace(/\n/g, '').length : 0;
}

/**
 * Format a number for display with locale grouping.
 * @param {number} value
 * @returns {string}
 */
export function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

/**
 * Summarise a selection for the status bar.
 *
 * @param {number} words
 * @param {number} characters
 * @param {number} pages
 * @param {number} [selected]
 * @param {number} [page] 1-based index of the page the cursor is on.
 * @returns {string}
 */
export function formatStats(words, characters, pages, selected = 0, page = 0) {
  const base =
    `${formatNumber(words)} words | ${formatNumber(characters)} characters` +
    ` | ${formatNumber(pages)} pages`;
  const where = page > 0 ? ` | page ${formatNumber(page)} of ${formatNumber(pages)}` : '';
  const selection = selected > 0 ? ` | ${formatNumber(selected)} selected` : '';
  return `${base}${where}${selection}`;
}
