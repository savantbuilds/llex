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
 * The decision-making is separated from the DOM work, and the whole reflow for
 * a document is computed in one pass rather than one block per frame. Getting
 * the document positions wrong does not throw, it silently corrupts a
 * document, so the arithmetic is derived from first principles in one place and
 * checked against real ProseMirror documents in the test suite.
 *
 * ## What this model can and cannot do about widows and orphans
 *
 * Word processors offer "widow/orphan control": never leave a single line of a
 * paragraph alone at the bottom of a page, never start a page with the last
 * line of a paragraph. That is a *line*-level rule, and it cannot be expressed
 * here, because this model only ever moves whole blocks between pages -- a
 * paragraph is entirely on one page or entirely on the next, so it can never
 * have a widow or an orphan in the first place. Implementing it properly would
 * mean letting a page break fall *inside* a paragraph, which changes the
 * document's node structure rather than only its layout, and is a much larger
 * change than a pagination tweak.
 *
 * The block-level consequences of the same concern are handled, and are what
 * actually go wrong in practice:
 *
 *  - {@link countKeepWithNextTail} stops a heading being stranded at the bottom
 *    of a page, separated from the text it introduces.
 *  - A page that cannot be split without leaving fewer than
 *    {@link MIN_SPLITTABLE_CHILDREN} blocks behind grows instead, so a page is
 *    never left nearly blank.
 *  - A single block too tall for any page is allowed to overflow visibly rather
 *    than being clipped into invisibility.
 *
 * A page holding nothing but one trailing block is a legitimate result here: it
 * is a genuine page break, not a stranded line.
 */

/**
 * @typedef {object} PageInfo
 * @property {number} index          Position of the page in `doc`.
 * @property {number} pos            Document position of the page node itself.
 * @property {number} nodeSize       Size of the page node in the document.
 * @property {number} childCount     Number of blocks inside the page.
 * @property {number} contentStart   Position of the page's first child.
 * @property {number} contentSize    Total size of the page's children.
 * @property {number} lastChildStart Position of the page's last child, or -1.
 * @property {number} lastChildSize  Size of the last child, or 0.
 * @property {number[]} childSizes   Size of each child, in document order.
 */

/**
 * @typedef {object} ReflowPlan
 * @property {'move'|'grow'} kind
 *   `move` shifts a range of blocks to the next page; `grow` lets an overfull
 *   page expand so its content stays reachable.
 * @property {number} pageIndex Index of the page the plan applies to.
 * @property {number} [from]     Start of the range to move. `move` only.
 * @property {number} [to]       End of the range to move. `move` only.
 * @property {number} [count]    How many trailing blocks move. `move` only.
 * @property {number} [targetIndex] Index the blocks move to. `move` only.
 */

/** Tolerance for the scrollHeight/clientHeight comparison, in CSS pixels. */
export const OVERFLOW_TOLERANCE = 1;

/** Below this many blocks a page cannot usefully be split. */
export const MIN_SPLITTABLE_CHILDREN = 2;

/**
 * Whether a block must be followed by content on the same page.
 *
 * A heading stranded as the last thing on a page is separated from the text it
 * introduces, which is the most common way a paginated document reads as broken.
 *
 * @param {import('@tiptap/pm/model').Node} node
 * @returns {boolean}
 */
export function keepsWithNext(node) {
  const spec = node.type.spec;
  if (!spec || !spec.attrs || !('keepWithNext' in spec.attrs)) return false;
  return node.attrs.keepWithNext !== false;
}

/**
 * How many trailing blocks must leave a page to honour keep-with-next.
 *
 * Separate from {@link countOverflowingTail} because it applies to pages that
 * do not overflow at all: a page can fit its content perfectly and still end
 * with a stranded heading, and an overflow-driven reflow would never look at it.
 *
 * One block is enough to fix it. A run of consecutive headings is resolved by
 * moving the last one, because whatever it lands on -- the moved heading, or the
 * next page's existing first block -- is content that follows it.
 *
 * @param {import('@tiptap/pm/model').Node} doc
 * @param {number} pageIndex
 * @returns {number} Blocks to move, or 0 when the page may end where it does.
 */
export function countKeepWithNextTail(doc, pageIndex) {
  // `Node.child` throws for an out-of-range index rather than returning null, and
  // a caller holding a stale page index must get an answer rather than a crash
  // in the middle of a reflow.
  if (pageIndex < 0 || pageIndex >= doc.childCount) return 0;
  const page = doc.child(pageIndex);
  if (!page) return 0;
  const last = page.lastChild;
  if (!last || !keepsWithNext(last)) return 0;
  // Nothing follows it in the document, so there is nothing to keep it with.
  // Moving it would only manufacture an empty final page.
  if (pageIndex >= doc.childCount - 1) return 0;
  // Moving everything would leave a blank page behind.
  if (page.childCount < MIN_SPLITTABLE_CHILDREN) return 0;
  return 1;
}

/**
 * Whether a block carries a manual page break.
 *
 * Checked against the attribute rather than the spec, so a node type that does
 * not declare `breakBefore` simply reports `false` instead of throwing.
 *
 * @param {import('@tiptap/pm/model').Node} node
 * @returns {boolean}
 */
export function breaksBefore(node) {
  return node.attrs.breakBefore === true;
}

/**
 * The first block in a page that begins after a manual page break.
 *
 * The `data-page-break-before` attribute was rendered and honoured by the
 * stylesheet for print, but nothing ever read it during reflow, so pressing
 * "Page break" set an attribute that changed nothing on screen. This is that
 * reader.
 *
 * @param {import('@tiptap/pm/model').Node} page
 * @returns {number} Index within the page, or -1 when there is none.
 */
export function findForcedBreak(page) {
  let found = -1;
  let index = 0;
  page.forEach((child) => {
    if (found === -1 && breaksBefore(child)) found = index;
    index += 1;
  });
  return found;
}

/**
 * Plan the split that honours a manual page break.
 *
 * Everything from the break to the end of the page moves to a new page. Unlike an
 * overflow split this is never a compromise: the user asked for it, so a page is
 * allowed to end early, and a break on the first block of a page is already
 * satisfied and produces no plan rather than an empty page.
 *
 * A page with nothing to break before is left alone, including the common case
 * of a document whose blocks carry the attribute from a file that set it.
 *
 * @param {import('@tiptap/pm/model').Node} doc
 * @param {number} pageIndex
 * @param {ReadonlySet<number>} pinned
 * @returns {ReflowPlan|null}
 */
export function planForcedBreak(doc, pageIndex, pinned) {
  if (pinned.has(pageIndex)) return null;
  const page = describePages(doc)[pageIndex];
  if (!page) return null;

  const at = findForcedBreak(doc.child(pageIndex));
  // No break, or one already at the very top of the page.
  if (at <= 0) return null;

  const offset = page.childSizes.slice(0, at).reduce((total, size) => total + (size ?? 0), 0);
  return {
    kind: 'move',
    pageIndex,
    from: page.contentStart + offset,
    to: page.lastChildStart + page.lastChildSize,
    count: page.childCount - at,
    targetIndex: pageIndex + 1,
  };
}

/**
 * Plan the move that stops a page ending in a stranded heading.
 *
 * Deliberately not {@link planPage}: that refuses to leave fewer than
 * {@link MIN_SPLITTABLE_CHILDREN} blocks behind, which is right for an overflow
 * split -- a page left with one block grows instead -- but wrong here. Removing
 * a heading from `[body, heading]` leaves `[body]`, an entirely ordinary page,
 * and the alternative is a heading stranded at the bottom of a page that fits.
 *
 * @param {import('@tiptap/pm/model').Node} doc
 * @param {number} pageIndex
 * @param {ReadonlySet<number>} pinned
 * @returns {ReflowPlan|null}
 */
export function planKeepWithNext(doc, pageIndex, pinned) {
  if (pinned.has(pageIndex)) return null;
  const count = countKeepWithNextTail(doc, pageIndex);
  if (count <= 0) return null;
  const info = describePages(doc)[pageIndex];
  return {
    kind: 'move',
    pageIndex,
    from: startOfTrailingRun(info, count),
    to: info.lastChildStart + info.lastChildSize,
    count,
    targetIndex: pageIndex + 1,
  };
}

/**
 * Describe every page in `doc`, with the document positions needed to move
 * blocks between them.
 *
 * ProseMirror node positions are easy to get subtly wrong, so the arithmetic
 * is done once, here, rather than accumulated at each call site. For a node at
 * position `pos`:
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
    const childSizes = [];
    page.forEach((child) => childSizes.push(child.nodeSize));
    pages.push({
      index,
      pos,
      nodeSize: page.nodeSize,
      childCount: page.childCount,
      contentStart: pos + 1,
      contentSize,
      lastChildStart: lastChild ? pos + 1 + contentSize - lastChildSize : -1,
      lastChildSize,
      childSizes,
    });
    // The next sibling begins immediately after this node's closing token.
    pos += page.nodeSize;
  }
  return pages;
}

/**
 * How many trailing blocks must leave a page for the remainder to fit.
 *
 * @param {{height: number}[]} blocks Measured heights, in document order.
 * @param {number} available Printable height in CSS pixels.
 * @param {number} gap Space between blocks that also has to be accounted for.
 * @returns {number} How many trailing blocks must move. 0 means it either
 *   already fits, or cannot be split without orphaning a block -- in which case
 *   the caller grows the page instead.
 */
export function countOverflowingTail(blocks, available, gap = 0) {
  // Accumulate from the front: what matters is whether the blocks that stay
  // fit, so measuring the kept run is the direct question. Accumulating from
  // the end has to reason about the gap on the wrong side of the boundary.
  let height = 0;
  for (let index = 0; index < blocks.length; index += 1) {
    if (index > 0) height += gap;
    height += blocks[index].height;
    if (height <= available) continue;

    const keep = index;
    if (keep < MIN_SPLITTABLE_CHILDREN) {
      // Too few blocks would remain, and a single block that cannot fit is not
      // something moving can fix. The page is grown instead.
      return 0;
    }
    return blocks.length - keep;
  }
  return 0;
}

/**
 * Decide how a single overflowing page should be split.
 *
 * @param {import('@tiptap/pm/model').Node} doc
 * @param {number} pageIndex
 * @param {number} overflowCount How many trailing blocks must move, from
 *   {@link countOverflowingTail}.
 * @param {ReadonlySet<number>} pinned Pages already allowed to grow.
 * @returns {ReflowPlan|null}
 */
export function planPage(doc, pageIndex, overflowCount, pinned) {
  if (overflowCount <= 0) return null;
  if (pinned.has(pageIndex)) return null;

  const page = describePages(doc)[pageIndex];
  if (!page) return null;

  if (page.childCount - overflowCount < MIN_SPLITTABLE_CHILDREN) {
    // Whatever would remain is a single block. Growing is the only option that
    // keeps the text visible; moving it would just repeat the problem.
    return { kind: 'grow', pageIndex };
  }

  const to = page.lastChildStart + page.lastChildSize;
  return {
    kind: 'move',
    pageIndex,
    from: startOfTrailingRun(page, overflowCount),
    to,
    count: overflowCount,
    targetIndex: pageIndex + 1,
  };
}

/**
 * Document position of the block that begins the last `count` blocks of a page.
 *
 * Walks the child sizes from the start rather than backwards from the last
 * child, because only the total content size and the last child's size are
 * otherwise available, and that is not enough to locate the boundary when more
 * than one block moves.
 *
 * @param {PageInfo} page
 * @param {number} count
 * @returns {number}
 */
export function startOfTrailingRun(page, count) {
  const keep = Math.max(0, page.childCount - count);
  let offset = 0;
  for (let index = 0; index < keep; index += 1) {
    offset += page.childSizes[index] ?? 0;
  }
  return page.contentStart + offset;
}

/**
 * Apply a {@link ReflowPlan} to a document, as a single transaction.
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

  const to = source.lastChildStart + source.lastChildSize;
  const from = plan.from === undefined ? source.lastChildStart : plan.from;
  if (from < source.contentStart || to > state.doc.content.size) return null;

  const tr = state.tr;
  const slice = tr.doc.slice(from, to);
  if (slice.content.size === 0) return null;

  if (plan.targetIndex < state.doc.childCount) {
    // The next page already exists: prepend the blocks to it.
    const target = pages[plan.targetIndex];
    tr.insert(target.contentStart, slice.content);
  } else {
    // No page after this one, so start a new one holding the moved blocks.
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

// --------------------------------------------------------------------------
// Measurement
// --------------------------------------------------------------------------

/**
 * The printable height inside a page, in CSS pixels.
 *
 * Read from the page's own box rather than recomputed from the geometry
 * constants, so a change to margins or paper size is picked up automatically.
 *
 * @param {Element} page
 * @returns {number}
 */
export function printableHeight(page) {
  const style = page.ownerDocument.defaultView.getComputedStyle(page);
  const paddingTop = Number.parseFloat(style.paddingTop) || 0;
  const paddingBottom = Number.parseFloat(style.paddingBottom) || 0;
  return page.clientHeight - paddingTop - paddingBottom;
}

/**
 * Measure each direct child of a page, in document order.
 *
 * `offsetTop` is relative to the offset parent, which for a page is the page
 * itself once it is positioned -- and it is, so the values are directly
 * comparable to the printable height.
 *
 * @param {Element} page
 * @returns {{height: number}[]}
 */
export function measureBlocks(page) {
  const base = page.getBoundingClientRect ? page.getBoundingClientRect().top : 0;
  return Array.prototype.map.call(page.children, (child) => {
    const top =
      typeof child.getBoundingClientRect === 'function'
        ? child.getBoundingClientRect().top
        : base + child.offsetTop;
    const bottom =
      typeof child.getBoundingClientRect === 'function'
        ? child.getBoundingClientRect().bottom
        : base + child.offsetTop + child.offsetHeight;
    return { height: Math.max(0, bottom - top) };
  });
}

/**
 * The gap between consecutive blocks on a page, in CSS pixels.
 *
 * Taken from the first gap actually rendered rather than assumed, so a change
 * to the page's `gap` does not have to be mirrored here.
 *
 * @param {Element} page
 * @returns {number}
 */
export function measureGap(page) {
  const children = page.children;
  if (children.length < 2) return 0;
  const first = children[0].getBoundingClientRect
    ? children[0].getBoundingClientRect()
    : { bottom: 0 };
  const second = children[1].getBoundingClientRect
    ? children[1].getBoundingClientRect()
    : { top: 0 };
  return Math.max(0, second.top - first.bottom);
}

/**
 * Measure which pages overflow, and by how much they should shed.
 *
 * Uses each page's own scrollable box rather than the outer element, so
 * padding and margins cannot be mistaken for overflow.
 *
 * @param {Element} container Element whose direct children are the pages.
 * @returns {{overflowed: Set<number>, counts: Map<number, number>}}
 */
export function measureOverflow(container) {
  /** @type {Set<number>} */
  const overflowed = new Set();
  /** @type {Map<number, number>} */
  const counts = new Map();
  const pages = Array.prototype.slice.call(container.children);

  pages.forEach((page, index) => {
    if (page.dataset.overfull === 'true') return;
    if (page.scrollHeight <= page.clientHeight + OVERFLOW_TOLERANCE) return;

    overflowed.add(index);
    const available = printableHeight(page);
    counts.set(index, countOverflowingTail(measureBlocks(page), available, measureGap(page)));
  });

  return { overflowed, counts };
}
