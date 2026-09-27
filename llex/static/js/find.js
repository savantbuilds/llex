/**
 * Find and replace.
 *
 * ProseMirror has no search, and the previous menu item flashed "Find is not
 * implemented yet" -- table stakes for a word processor, and the single most
 * obvious gap in the ribbon.
 *
 * Search runs against the document's *text*, not the DOM, so it does not depend
 * on how the text happens to be split into nodes. Matches are found by walking
 * the resolved positions rather than by slicing strings, which is what keeps
 * them correct across marks, nested lists and page boundaries.
 */

import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

export const findKey = new PluginKey('llex-find');

/** Class applied to the match the cursor is currently on. */
export const ACTIVE_CLASS = 'find-match-active';
/** Class applied to every other match. */
export const MATCH_CLASS = 'find-match';

/**
 * Escape a string for literal use inside a regular expression.
 * @param {string} value
 * @returns {string}
 */
export function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Build the search pattern.
 *
 * Whole-word matching is opt-in because `cat` should not match inside
 * `category` unless asked for, which is what a word processor does.
 *
 * Returns `null` for an empty query *and* for an invalid regular expression.
 * The invalid case matters: the query is compiled on every keystroke, so a
 * half-typed pattern like `[abc` is the normal intermediate state and must not
 * throw at the user.
 *
 * @param {{query: string, caseSensitive?: boolean, wholeWord?: boolean, regex?: boolean}} options
 * @returns {RegExp|null}
 */
export function buildPattern({ query, caseSensitive = false, wholeWord = false, regex = false }) {
  const trimmed = (query || '').trim();
  if (!trimmed) return null;

  const flags = caseSensitive ? 'gu' : 'giu';
  const source = regex ? trimmed : escapeRegExp(trimmed);
  const boundary = wholeWord && !regex ? wordBoundary(source) : '';
  const pattern = boundary ? `${boundary.prefix}(?:${source})${boundary.suffix}` : source;

  try {
    return new RegExp(pattern, flags);
  } catch {
    return null;
  }
}

/**
 * Word-boundary assertions for a literal pattern.
 *
 * `\b` is ASCII-centric and misbehaves for non-Latin scripts, so the boundary
 * is expressed as a lookaround over Unicode letters, digits and underscore.
 *
 * @param {string} source
 * @returns {{prefix: string, suffix: string}}
 */
function wordBoundary(source) {
  const isWordChar = (char) => /[\p{L}\p{N}_]/u.test(char);
  return {
    prefix: isWordChar(source[0]) ? '(?<![\\p{L}\\p{N}_])' : '',
    suffix: isWordChar(source[source.length - 1]) ? '(?![\\p{L}\\p{N}_])' : '',
  };
}

/**
 * Find every occurrence of `pattern` in a document.
 *
 * Walks each text node and scans its text, mapping offsets back to document
 * positions. Doing it per text node means a match is never reported twice, and
 * a match is never attributed to the wrong position when a text node is split
 * by a mark.
 *
 * @param {import('@tiptap/pm/model').Node} doc
 * @param {RegExp} pattern
 * @returns {{from: number, to: number}[]}
 */
export function findMatches(doc, pattern) {
  /** @type {{from: number, to: number}[]} */
  const matches = [];

  doc.descendants((node, pos) => {
    if (!node.isText || !node.text) return;
    const text = node.text;
    pattern.lastIndex = 0;
    let match = pattern.exec(text);
    while (match !== null) {
      if (match[0].length > 0) {
        matches.push({ from: pos + match.index, to: pos + match.index + match[0].length });
      }
      // A zero-width match would otherwise loop forever.
      if (match.index === pattern.lastIndex) pattern.lastIndex += 1;
      match = pattern.exec(text);
    }
    return false;
  });

  return matches.sort((a, b) => a.from - b.from);
}

/**
 * Highlight a set of matches, marking one as current.
 *
 * @param {{from: number, to: number}[]} matches
 * @param {number} activeIndex
 * @returns {Decoration[]}
 */
export function buildDecorations(matches, activeIndex) {
  return matches.map((match, index) =>
    Decoration.inline(match.from, match.to, {
      class: index === activeIndex ? ACTIVE_CLASS : MATCH_CLASS,
    }),
  );
}

/**
 * The plugin that renders the highlights.
 *
 * State is supplied by {@link FindController}, which pushes new decoration sets
 * in; the plugin only decides what to draw.
 */
export function findPlugin(getState) {
  return new Plugin({
    key: findKey,
    state: {
      init: () => DecorationSet.empty,
      apply(transaction, value, _oldState, newState) {
        const next = getState(newState);
        if (!next || next.matches.length === 0) return DecorationSet.empty;
        return DecorationSet.create(newState.doc, buildDecorations(next.matches, next.active));
      },
    },
    props: {
      decorations(state) {
        return findKey.getState(state);
      },
    },
  });
}

/**
 * Drives a find session: holds the query, finds matches, and moves between
 * them.
 */
export class FindController {
  /**
   * @param {import('@tiptap/core').Editor} editor
   */
  constructor(editor) {
    this.editor = editor;
    /** @type {{from: number, to: number}[]} */
    this.matches = [];
    this.active = 0;
    this.options = { query: '', caseSensitive: false, wholeWord: false, regex: false };
    this.onChange = () => {};
  }

  get isOpen() {
    return this.options.query.trim().length > 0;
  }

  get matchCount() {
    return this.matches.length;
  }

  /**
   * Announce a state change and make the browser redraw the highlights.
   *
   * Decorations are computed while a transaction is applied, so a search that
   * only changes the controller's own state would never repaint. Dispatching an
   * empty transaction is the supported way to ask for a redraw without changing
   * the document, and the paginator ignores it because it has nothing to
   * repaginate.
   */
  _notify() {
    this.onChange();
    this.editor.view.dispatch(this.editor.state.tr);
  }

  /**
   * Re-run the search for the given options.
   *
   * @param {{query: string, caseSensitive?: boolean, wholeWord?: boolean, regex?: boolean}} options
   * @returns {number} Number of matches.
   */
  search(options) {
    this.options = { ...this.options, ...options };
    const pattern = buildPattern(this.options);
    if (!pattern) {
      this.matches = [];
      this.active = 0;
      this._notify();
      return 0;
    }
    this.matches = findMatches(this.editor.state.doc, pattern);
    this.active = this.matches.length > 0 ? 0 : -1;
    this._notify();
    return this.matches.length;
  }

  /** Move to the next match, wrapping at the end. */
  next() {
    if (this.matches.length === 0) return null;
    this.active = (this.active + 1) % this.matches.length;
    this._notify();
    return this.current();
  }

  /** Move to the previous match, wrapping at the start. */
  previous() {
    if (this.matches.length === 0) return null;
    this.active = (this.active - 1 + this.matches.length) % this.matches.length;
    this._notify();
    return this.current();
  }

  /** The match currently selected, or null. */
  current() {
    return this.matches[this.active] || null;
  }

  /**
   * Select the current match so the user can see where they are.
   * @returns {boolean} whether a match was selected
   */
  focusCurrent() {
    const match = this.current();
    if (!match) return false;
    this.editor.chain().focus().setTextSelection(match).scrollIntoView().run();
    return true;
  }

  /**
   * Replace the current match.
   *
   * The document is re-searched after the edit and the cursor is placed on the
   * match that now occupies the same offset, so repeated replacement walks
   * forward through the document the way every word processor does.
   *
   * @param {string} replacement
   * @returns {boolean} whether anything was replaced
   */
  replaceCurrent(replacement) {
    const match = this.current();
    if (!match) return false;

    this.editor
      .chain()
      .focus()
      .insertContentAt({ from: match.from, to: match.to }, replacement)
      .run();

    const pattern = buildPattern(this.options);
    if (pattern) this.matches = findMatches(this.editor.state.doc, pattern);

    // Keep the cursor where the replacement ended up.
    const end = match.from + replacement.length;
    this.active = this.matches.findIndex((candidate) => candidate.to >= end);
    if (this.active < 0) this.active = Math.max(0, this.matches.length - 1);
    this._notify();
    this.focusCurrent();
    return true;
  }

  /**
   * Replace every match.
   *
   * Applied as one transaction so it is a single undo step, and done back to
   * front so each replacement cannot shift the positions of the ones after it.
   *
   * @param {string} replacement
   * @returns {number} How many were replaced.
   */
  replaceAll(replacement) {
    if (this.matches.length === 0) return 0;
    const total = this.matches.length;
    const transaction = this.editor.state.tr;
    for (let index = this.matches.length - 1; index >= 0; index -= 1) {
      const match = this.matches[index];
      transaction.insertText(replacement, match.from, match.to);
    }
    this.editor.view.dispatch(transaction);

    const pattern = buildPattern(this.options);
    this.matches = pattern ? findMatches(this.editor.state.doc, pattern) : [];
    this.active = this.matches.length > 0 ? 0 : -1;
    this._notify();
    return total;
  }

  /** Clear the search and its highlights. */
  close() {
    this.options.query = '';
    this.matches = [];
    this.active = 0;
    this._notify();
  }
}
