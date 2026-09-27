/**
 * Drives pagination by measuring rendered layout and applying reflow plans.
 *
 * See `pagination.js` for why the decisions are separated from the DOM work.
 * This module owns only the parts that genuinely need a browser: measuring
 * pages, scheduling work, and applying transactions.
 */

import {
  applyReflow,
  collapseToSinglePage,
  measureOverflow,
  planReflow,
} from './pagination.js';

/** Ceiling on reflow passes per burst, so a pathological document cannot spin. */
const MAX_PASSES = 500;

export class Paginator {
  /**
   * @param {{editor: import('@tiptap/core').Editor, onChange?: () => void}} options
   */
  constructor({ editor, onChange }) {
    this.editor = editor;
    this.onChange = onChange || (() => {});
    /** Page indices allowed to grow, so they are not re-flagged every pass. */
    this.pinned = new Set();
    this.frame = 0;
    this.running = false;
    this._tick = this._tick.bind(this);
  }

  /** The element that directly contains the page nodes. */
  get container() {
    return this.editor.view.dom;
  }

  /**
   * Schedule a repagination pass on the next animation frame.
   *
   * Coalesced deliberately: typing fires an update per keystroke, and
   * measuring layout for each one would make typing janky.
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
    this.run();
  }

  /**
   * Run reflow passes until the layout settles.
   *
   * @returns {{passes: number, moved: number, settled: boolean}}
   */
  run() {
    const { editor } = this;
    if (this.running) return { passes: 0, moved: 0, settled: true };
    this.running = true;
    this.pinned.clear();

    let passes = 0;
    let moved = 0;
    let settled = false;

    try {
      for (; passes < MAX_PASSES; passes += 1) {
        // Reading scrollHeight forces layout, so the DOM is read once per pass
        // and never interleaved with writes inside the loop body.
        const overflowed = measureOverflow(this.container);
        if (overflowed.size === 0) {
          settled = true;
          break;
        }

        const plan = planReflow(editor.state.doc, overflowed, this.pinned);
        if (!plan) {
          // Everything still overflowing is already pinned as overfull.
          settled = true;
          break;
        }

        if (plan.kind === 'grow') {
          this._markOverfull(plan.pageIndex);
          continue;
        }

        const changed = applyReflow(editor.state, plan);
        if (!changed) {
          // The plan no longer applies; stop rather than spin.
          settled = true;
          break;
        }
        editor.view.dispatch(changed);
        moved += 1;
      }
    } finally {
      this.running = false;
    }

    if (moved >= MAX_PASSES) {
      // Refusing to spin silently would leave the document mis-paginated with
      // no indication of why, which is the worst of the available outcomes.
      console.warn(
        `llex: pagination gave up after ${MAX_PASSES} passes; the layout may be wrong`,
      );
    }

    if (moved > 0) this.onChange();
    return { passes, moved, settled };
  }

  /**
   * Let a page grow past its fixed height so its content stays visible.
   *
   * Without this the page is `overflow: hidden` and the text is neither
   * readable nor selectable -- invisible data loss.
   */
  _markOverfull(pageIndex) {
    this.pinned.add(pageIndex);
    const page = this.container.children[pageIndex];
    if (page) page.dataset.overfull = 'true';
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
    const collapsed = collapseToSinglePage(editor.state.doc);
    if (!collapsed) return false;

    editor.view.dispatch(editor.state.tr.replaceWith(0, editor.state.doc.content.size, collapsed));
    this.pinned.clear();
    this.container.querySelectorAll('[data-overfull]').forEach((page) => {
      delete page.dataset.overfull;
    });
    this.run();
    return true;
  }
}
