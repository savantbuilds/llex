/**
 * The pagination engine.
 *
 * LLex renders a document as a stack of fixed 8.5x11 inch pages. ProseMirror
 * owns the document as a flat sequence of blocks wrapped in `page` nodes, and
 * this module's job is to move blocks from one page to the next whenever a page
 * overflows its printable height.
 *
 * Two things make this hard, and both are handled explicitly:
 *
 * 1. **Overflow can only be detected by measuring rendered layout.** Page height
 *    depends on font metrics, zoom and window width, none of which JavaScript
 *    can compute. So the DOM is measured, and the reflow is driven by what it
 *    finds. Repagination therefore has to run again after a load, a zoom
 *    change, or a font change -- not only after typing.
 *
 * 2. **A block that cannot fit on a page by itself must not be clipped.** The
 *    original engine simply gave up when a page held a single overflowing
 *    block. Because the page is `overflow: hidden`, that content became
 *    invisible *and unreachable* -- the user could neither read it nor select
 *    it. Here such a page is marked as overfull and allowed to grow instead,
 *    so the content stays visible and the next block continues on a fresh page.
 *
 * The decision-making is separated from the DOM work: {@link planReflow} is a
 * pure function of the document and the measured overflow, which is what makes
 * the positioning arithmetic testable without a browser. Getting those offsets
 * wrong silently corrupts a document, so it is worth testing directly.
 */

/**
 * @typedef {object} PageInfo
 * @property {number} index          Position of the page in `doc`.
 * @property {number} pos            Document position of the page node itself.
 * @property {number} nodeSize       Size of the page node in the document.
 * @property {number} childCount     Number of blocks inside the page.
 * @property {number} contentStart   Position of the page's first child.
 * @property {number} lastChildStart Position of the page's last child, or -1.
 * @property {number} lastChildSize  Size of the last child, or 0.
 */

/**
 * @typedef {object} ReflowPlan
 * @property {'move'|'grow'} kind
 *   `move` shifts a block to the next page; `grow` lets an overfull single-block
 *   page expand so its content stays reachable.
 * @property {number} pageIndex Index of the page the plan applies to.
 * @property {number} [from]     Start of the range to move. `move` only.
 * @property {number} [to]       End of the range to move. `move` only.
 * @property {number} [targetIndex] Index the block moves to. `move` only.
 */

/** Tolerance for the scrollHeight/clientHeight comparison, in CSS pixels. */
export const OVERFLOW_TOLERANCE = 1;

/** Below this many blocks a page cannot usefully be split. */
const MIN_SPLITTABLE_CHILDREN = 2;

/**
 * Describe every page in `doc`, with the document positions needed to move
 * blocks between them.
 *
 * ProseMirror node positions are easy to get subtly wrong, so the arithmetic
 * is done once, here, from first principles rather than accumulated at each
 * call site. For a node at position `pos`:
 *
 *   - its content starts at `pos + 1`, skipping the opening token;
 *   - its content is `nodeSize - 2` long, the difference being the opening and
 *     closing tokens;
 *   - so its last child starts at `pos + 1 + (nodeSize - 2) - lastChildSize`.
 *
 * Getting this off by one does not throw: it slices one character into the
 * block and corrupts the document, which is why it is unit-tested against real
 * ProseMirror documents.
 *
 * @param {import('@tiptap/pm/model').Node} doc
 * @returns {PageInfo[]}
 */
export function describePages(doc) {
  const pages = [];
  let pos = 0;
  for (let index = 0; index < doc.childCount; index += 1) {
    const page = doc.child(index);
    const lastChild = page.lastChild;
    const lastChildSize = lastChild ? lastChild.nodeSize : 0;
    const contentSize = page.nodeSize - 2;
    pages.push({
      index,
      pos,
      nodeSize: page.nodeSize,
      childCount: page.childCount,
      contentStart: pos + 1,
      lastChildStart: lastChild ? pos + 1 + contentSize - lastChildSize : -1,
      lastChildSize,
    });
    // The next sibling begins immediately after this node's closing token.
    pos += page.nodeSize;
  }
  return pages;
}

/**
 * Decide the single next reflow action, or `null` when the layout is settled.
 *
 * Only one block moves per call. Moving everything at once produced a
 * structurally invalid intermediate document on pages holding many blocks, and
 * measuring one move per frame is what produces the progressive "ripple" the
 * editor is known for.
 *
 * @param {import('@tiptap/pm/model').Node} doc
 * @param {ReadonlySet<number>} overflowed Page indices whose content is taller
 *   than the printable area, from measurement.
 * @param {ReadonlySet<number>} pinned Page indices already allowed to grow, so
 *   an overfull page is not re-marked on every subsequent pass.
 * @returns {ReflowPlan|null}
 */
export function planReflow(doc, overflowed, pinned = new Set()) {
  if (overflowed.size === 0) return null;

  for (const pageIndex of overflowed) {
    if (pinned.has(pageIndex)) continue;
    const page = describePages(doc)[pageIndex];
    if (!page) continue;

    if (page.childCount < MIN_SPLITTABLE_CHILDREN) {
      // A single block that cannot fit a page. Growing is the only option that
      // keeps the text visible; moving it would just repeat the problem.
      return { kind: 'grow', pageIndex };
    }
    return {
      kind: 'move',
      pageIndex,
      from: page.lastChildStart,
      to: page.lastChildStart + page.lastChildSize,
      targetIndex: pageIndex + 1,
    };
  }
  return null;
}

/**
 * Build the transaction that applies a {@link ReflowPlan}.
 *
 * Returns `null` when the plan no longer applies -- a stale page index, a page
 * with nothing to move -- so the caller can stop rather than spin.
 *
 * Positions are re-derived from `state` rather than trusted from the plan,
 * because a plan computed before an intervening edit would slice the wrong
 * range.
 *
 * @param {import('@tiptap/pm/state').EditorState} state
 * @param {ReflowPlan} plan
 * @returns {import('@tiptap/pm/transform').Transaction|null}
 */
export function applyReflow(state, plan) {
  if (plan.kind === 'grow') return null;

  const pages = describePages(state.doc);
  const source = pages[plan.pageIndex];
  if (!source || source.lastChildStart < 0) return null;

  const from = source.lastChildStart;
  const to = from + source.lastChildSize;
  if (from < 0 || to > state.doc.content.size) return null;

  const tr = state.tr;
  const slice = tr.doc.slice(from, to);
  if (slice.content.size === 0) return null;

  if (plan.targetIndex < state.doc.childCount) {
    // The next page already exists: prepend the block to it.
    const target = pages[plan.targetIndex];
    tr.insert(target.contentStart, slice.content);
  } else {
    // No page after this one, so start a new one holding the moved block.
    const pageType = state.schema.nodes.page;
    if (!pageType) return null;
    tr.insert(source.pos + source.nodeSize, pageType.create(null, slice.content));
  }

  // Delete last, so the earlier insert cannot shift the range being removed.
  tr.delete(from, to);
  return tr;
}

/**
 * Flatten every page's children into a single page, in document order.
 *
 * This is what a freshly loaded document goes through. Page breaks in a saved
 * file were computed at a different zoom, with different fonts, possibly on a
 * different machine, so they cannot be trusted; the content is re-flowed from
 * scratch rather than trusted to still fit.
 *
 * @param {import('@tiptap/pm/model').Node} doc
 * @returns {import('@tiptap/pm/model').Node|null} A single-page document, or
 *   `null` if the input has no page node.
 */
export function collapseToSinglePage(doc) {
  const pageType = doc.type.schema.nodes.page;
  if (!pageType) return null;

  const blocks = [];
  for (let index = 0; index < doc.childCount; index += 1) {
    const child = doc.child(index);
    if (child.type.name === 'page') {
      child.forEach((block) => blocks.push(block));
    } else {
      blocks.push(child);
    }
  }
  if (blocks.length === 0) return null;
  return doc.type.create(null, pageType.create(null, blocks));
}

/**
 * Strip page wrappers from pasted HTML.
 *
 * Pasting a whole document from another editor would otherwise nest the
 * source's pages inside the current page, producing a mess of nested, clipped
 * boxes. The pagination engine re-creates breaks itself from measured layout.
 *
 * @param {string} html
 * @returns {string}
 */
export function stripPageWrappers(html) {
  if (!html) return '';
  const parser = new DOMParser();
  const doc = parser.parseFromString(`<body>${html}</body>`, 'text/html');
  const wrappers = doc.body.querySelectorAll('.page');
  wrappers.forEach((wrapper) => {
    while (wrapper.firstChild) {
      wrapper.parentNode.insertBefore(wrapper.firstChild, wrapper);
    }
    wrapper.remove();
  });
  return doc.body.innerHTML;
}

/**
 * Measure which pages overflow their printable area.
 *
 * Uses the page's scrollable content box rather than the outer element, so
 * padding and margins cannot be mistaken for overflow.
 *
 * @param {Element} container Element whose direct children are the pages.
 * @returns {Set<number>} Indices of pages that overflow.
 */
export function measureOverflow(container) {
  const overflowed = new Set();
  const pages = Array.from(container.children);
  pages.forEach((page, index) => {
    if (page.dataset.overfull === 'true') return;
    if (page.scrollHeight > page.clientHeight + OVERFLOW_TOLERANCE) {
      overflowed.add(index);
    }
  });
  return overflowed;
}
