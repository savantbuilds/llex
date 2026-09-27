/**
 * Drives pagination by measuring rendered layout and applying reflow plans.
 *
 * See `pagination.js` for why the decisions are separated from the DOM work.
 * This module owns only the parts that genuinely need a browser: measuring
 * pages, and applying the reflow transaction.
 *
 * ## Why a plugin, not an update handler
 *
 * The reflow is produced by a ProseMirror plugin's `appendTransaction` rather
 * than by an `onUpdate` handler or an animation-frame loop. That is what makes
 * undo behave. A reflow triggered by the user pressing Enter near a page
 * boundary is *appended to* the transaction that inserted the break, so one
 * undo removes both. Dispatched separately -- as the previous frame loop did --
 * the page move landed on top of the undo stack and the first Ctrl+Z reverted
 * the pagination instead of the typing.
 *
 * ProseMirror re-invokes `appendTransaction` for each appended transaction, so
 * a cascade across many pages continues within the same dispatch and is grouped
 * into the same history event. That is why only one measurement is taken per
 * invocation: simulating further rounds here would measure a DOM that has not
 * re-rendered, and would produce plans based on stale layout.
 */

import { Plugin, PluginKey } from '@tiptap/pm/state';

import { applyReflow, collapseToSinglePage, measureOverflow, planPage } from './pagination.js';

/** Ceiling on reflow rounds, so a pathological document cannot spin. */
export const MAX_ROUNDS = 200;

export const paginationKey = new PluginKey('llex-pagination');

/**
 * Compute the transaction that reflows every currently-overflowing page.
 *
 * Pure with respect to the editor: it reads the DOM and returns a transaction,
 * but dispatches nothing, so it is straightforward to reason about and to
 * assert on.
 *
 * @param {import('@tiptap/pm/state').EditorState} state
 * @param {Element} container Element whose direct children are the pages.
 * @param {Set<number>} pinned Pages already allowed to grow.
 * @returns {{transaction: import('@tiptap/pm/transform').Transaction, moved: number, grown: number[]} | null}
 */
export function reflowTransaction(state, container, pinned) {
  const { overflowed, counts } = measureOverflow(container);
  if (overflowed.size === 0) return null;

  let transaction = null;
  let moved = 0;
  const grown = [];

  for (const pageIndex of overflowed) {
    const plan = planPage(state.doc, pageIndex, counts.get(pageIndex) || 0, pinned);
    if (!plan) continue;
    if (plan.kind === 'grow') {
      grown.push(pageIndex);
      continue;
    }
    const step = applyReflow(state, plan);
    if (!step) continue;
    transaction = transaction ? transaction.step(step) : step;
    moved += 1;
  }

  if (grown.length > 0) return { transaction, moved, grown };
  if (!transaction) return null;
  return { transaction, moved, grown };
}

export class Paginator {
  /**
   * @param {{editor: import('@tiptap/core').Editor, onChange?: () => void}} options
   */
  constructor({ editor, onChange }) {
    this.editor = editor;
    this.onChange = onChange || (() => {});
    /** Page indices allowed to grow, so they are not re-flagged every round. */
    this.pinned = new Set();
    this.frame = 0;
    this.rounds = 0;
    this.suspended = false;
    this._tick = this._tick.bind(this);
    this._registerPlugin();
  }

  /** The element that directly contains the page nodes. */
  get container() {
    return this.editor.view.dom;
  }

  _registerPlugin() {
    this.plugin = new Plugin({
      key: paginationKey,
      appendTransaction: (transactions, _oldState, newState) => {
        if (this.suspended) return null;
        if (!transactions.some((tr) => tr.docChanged)) return null;
        return this.reflow(newState);
      },
      view: () => ({
        // Re-measure for layout changes the document does not record, such as
        // a window resize or a font finishing load.
        update: (view) => {
          if (this.pendingRelayout) this.relayout();
          else this.schedule();
          return true;
        },
      }),
    });
    this.editor.registerPlugin(this.plugin);
  }

  /** Suppress reflow while the paginator is restructuring the document itself. */
  suspend(value) {
    this.suspended = Boolean(value);
  }

  /**
   * One reflow round over the current layout.
   *
   * @param {import('@tiptap/pm/state').EditorState} [state]
   * @returns {import('@tiptap/pm/transform').Transaction|null}
   */
  reflow(state = this.editor.state) {
    this.rounds += 1;
    if (this.rounds > MAX_ROUNDS) {
      // Refusing to spin silently would leave the document mis-paginated with
      // no indication of why, which is the worst of the available outcomes.
      console.warn(
        `llex: pagination gave up after ${MAX_ROUNDS} rounds; the layout may be wrong`,
      );
      this.rounds = 0;
      return null;
    }

    const result = reflowTransaction(state, this.container, this.pinned);
    if (!result) {
      this.rounds = 0;
      return null;
    }

    for (const pageIndex of result.grown) this.markOverfull(pageIndex);
    if (result.moved > 0) this.onChange();
    return result.transaction;
  }

  /**
   * Schedule a repagination on the next animation frame.
   *
   * Coalesced deliberately: typing fires an update per keystroke, and measuring
   * layout for each one would make typing janky. Edits are already reflowed
   * inline by the plugin; this is only for changes the document does not
   * record.
   */
  schedule() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(this._tick);
  }

  /** Cancel any pending pass. */
  cancel() {
    if (this.frame) {
      cancelAnimationFrame(this.frame);
      this.frame = 0;
    }
  }

  _tick() {
    this.frame = 0;
    this.apply();
  }

  /** Run a reflow and dispatch it, for layout-driven rather than edit-driven work. */
  apply() {
    this.rounds = 0;
    const transaction = this.reflow();
    if (transaction) this.editor.view.dispatch(transaction);
  }

  /**
   * Let a page grow past its fixed height so its content stays visible.
   *
   * Without this the page is `overflow: hidden` and the text is neither
   * readable nor selectable -- invisible data loss.
   */
  markOverfull(pageIndex) {
    this.pinned.add(pageIndex);
    const page = this.container.children[pageIndex];
    if (page && page.dataset.overfull !== 'true') page.dataset.overfull = 'true';
  }

  /**
   * Discard stored page breaks and re-flow the content from scratch.
   *
   * Required after loading: breaks in a saved file were computed elsewhere,
   * under different fonts and a different zoom, so they cannot be trusted to
   * still fit.
   */
  relayout() {
    const { editor } = this;
    this.pendingRelayout = false;
    const collapsed = collapseToSinglePage(editor.state.doc);
    if (collapsed) {
      // Suspending stops the plugin reflowing the collapsed intermediate state,
      // which is one enormous page by definition.
      this.suspend(true);
      try {
        editor.view.dispatch(editor.state.tr.replaceWith(0, editor.state.doc.content.size, collapsed));
      } finally {
        this.suspend(false);
      }
    }
    this.pinned.clear();
    Array.prototype.forEach.call(this.container.querySelectorAll('[data-overfull]'), (page) => {
      delete page.dataset.overfull;
    });
    this.apply();
  }
}
