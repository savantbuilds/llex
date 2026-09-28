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

import {
  applyReflow,
  collapseToSinglePage,
  measureOverflow,
  planForcedBreak,
  planKeepWithNext,
  planPage,
} from './pagination.js';

/** Ceiling on reflow rounds, so a pathological document cannot spin. */
export const MAX_ROUNDS = 200;

/** Class that enables CSS containment of off-screen pages. */
export const VIRTUAL_CLASS = 'llex-virtual';

/** Class that suspends it while the layout is being measured. */
export const MEASURING_CLASS = 'llex-measuring';

export const paginationKey = new PluginKey('llex-pagination');

/**
 * Whether the browser can skip rendering off-screen pages.
 *
 * `content-visibility` is how a long document is made cheap to scroll without
 * touching ProseMirror's model, because the model needs a real DOM for every
 * block. Where it is unsupported the page set is simply laid out as before, so
 * this is an optimisation and never a requirement.
 *
 * @returns {boolean}
 */
export function supportsContainment() {
  if (typeof CSS === 'undefined' || typeof CSS.supports !== 'function') return false;
  return CSS.supports('content-visibility', 'auto');
}

/**
 * Run `measure` with page containment suspended.
 *
 * The paginator decides what overflows by comparing each page's `scrollHeight`
 * with its `clientHeight`. A page the browser is skipping layout for reports
 * only its `contain-intrinsic-size`, so every page would measure as fitting and
 * pagination would silently stop working on exactly the long documents this
 * optimisation exists for.
 *
 * @template T
 * @param {Element} container
 * @param {() => T} measure
 * @returns {T}
 */
export function measuring(container, measure) {
  if (!supportsContainment()) return measure();
  const wasVirtual = container.classList.contains(VIRTUAL_CLASS);
  if (wasVirtual) container.classList.add(MEASURING_CLASS);
  try {
    return measure();
  } finally {
    if (wasVirtual) container.classList.remove(MEASURING_CLASS);
  }
}

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
  // Containment is suspended for the measurement: a skipped page reports only
  // its intrinsic size and would measure as fitting.
  const { overflowed, counts } = measuring(container, () => measureOverflow(container));

  const grown = [];
  let moved = 0;

  // Each pass plans against the document as the previous passes left it, and
  // applies to a real state.
  //
  // The two obvious alternatives are both wrong. Composing the passes' own
  // transactions -- `transaction.step(step)` -- throws, because `Transaction.step`
  // takes a `Step` and `applyReflow` returns a `Transaction`; it has no `apply`
  // method. And planning every pass against the original `state.doc` lets two
  // passes plan over the same blocks, so a page that both overflows and carries a
  // manual break moves its trailing blocks twice.
  let current = state;
  const tr = state.tr;
  const apply = (plan) => {
    const step = applyReflow(current, plan);
    if (!step) return false;
    // Applied to the accumulating transaction as individual steps, so it stays
    // anchored to `state.doc` -- which is what `appendTransaction` is given.
    for (const one of step.steps) tr.step(one);
    current = current.apply(step);
    moved += 1;
    return true;
  };

  for (const pageIndex of overflowed) {
    const plan = planPage(current.doc, pageIndex, counts.get(pageIndex) || 0, pinned);
    if (!plan) continue;
    if (plan.kind === 'grow') {
      grown.push(pageIndex);
      continue;
    }
    apply(plan);
  }

  // A manual page break is honoured next, and before keep-with-next because both
  // can move blocks across a boundary the user chose. It also cannot be detected
  // from the layout: a page that already ends where the break is looks identical
  // to one the break has not been applied to.
  for (let pageIndex = 0; pageIndex < current.doc.childCount; pageIndex += 1) {
    const plan = planForcedBreak(current.doc, pageIndex, pinned);
    if (plan) apply(plan);
  }

  // Keep-with-next is a third, independent pass, because it applies to pages that
  // do not overflow: a page can fit its content exactly and still leave a heading
  // stranded at the bottom, and the overflow pass above would never look at such
  // a page. The last page is excluded because nothing follows it.
  for (let pageIndex = 0; pageIndex < current.doc.childCount - 1; pageIndex += 1) {
    const plan = planKeepWithNext(current.doc, pageIndex, pinned);
    if (plan) apply(plan);
  }

  if (moved === 0 && grown.length === 0) return null;
  return { transaction: tr, moved, grown };
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
    /** Page count `pinned` was collected against, to detect renumbering. */
    this.pageCount = 0;
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

    // `pinned` holds page *indices*, and inserting or removing a page renumbers
    // every page after it. A stale index then pins a page that is merely a
    // bystander, and `planPage` and `planForcedBreak` both skip pinned pages --
    // so pagination quietly stops for the rest of the session. Nothing else
    // clears it except `relayout`, which is not something a user does.
    if (this.pageCount !== state.doc.childCount) {
      this.pinned.clear();
      this.pageCount = state.doc.childCount;
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
