/**
 * Unit tests for the pagination engine.
 *
 * The document-position arithmetic in `describePages` is the part most worth
 * testing: getting it wrong does not throw, it silently slices the wrong range
 * and corrupts a document. Real ProseMirror documents and states are built here
 * so the arithmetic is checked against the actual schema, and the reflow is
 * verified by applying a real transaction rather than by inspecting a mock.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { Schema } from '@tiptap/pm/model';
import { EditorState } from '@tiptap/pm/state';

import { reflowTransaction } from '../../llex/static/js/paginator.js';

import {
  MIN_SPLITTABLE_CHILDREN,
  OVERFLOW_TOLERANCE,
  applyReflow,
  collapseToSinglePage,
  countKeepWithNextTail,
  countOverflowingTail,
  describePages,
  keepsWithNext,
  planKeepWithNext,
  planPage,
  startOfTrailingRun,
} from '../../llex/static/js/pagination.js';

/** The minimal schema the pagination engine operates on. */
const schema = new Schema({
  nodes: {
    doc: { content: 'page+' },
    page: { content: 'block+', group: 'page' },
    paragraph: { content: 'inline*', group: 'block' },
    heading: {
      content: 'inline*',
      group: 'block',
      attrs: { level: { default: 1 }, keepWithNext: { default: true } },
    },
    text: { group: 'inline' },
  },
  marks: {
    scaffold: {
      attrs: { id: { default: null }, instruction: { default: '' } },
      inclusive: false,
    },
  },
});

/**
 * Build a document with one page per entry.
 * @param {string[][]} pages
 */
function buildDoc(pages) {
  const children = pages.map((blocks) =>
    schema.nodes.page.create(
      null,
      blocks.map((text) =>
        schema.nodes.paragraph.create(null, text ? schema.text(text) : null),
      ),
    ),
  );
  return schema.nodes.doc.create(null, children);
}

/** @param {import('@tiptap/pm/model').Node} doc */
function buildState(doc) {
  return EditorState.create({ schema, doc });
}

/** `Node.check()` throws on a structurally invalid document. */
function assertValid(doc, message) {
  assert.doesNotThrow(() => doc.check(), message);
}

/**
 * Build a document from block descriptors, so a test can use headings.
 *
 * @param {Array<Array<{text: string, type?: string, attrs?: object}|string>>} pages
 */
function buildTypedDoc(pages) {
  const children = pages.map((blocks) =>
    schema.nodes.page.create(
      null,
      blocks.map((block) => {
        const spec = typeof block === 'string' ? { text: block } : block;
        const type = schema.nodes[spec.type || 'paragraph'];
        return type.create(spec.attrs || null, spec.text ? schema.text(spec.text) : null);
      }),
    ),
  );
  return schema.nodes.doc.create(null, children);
}

/** Page-separated plain text, so document shape is easy to assert on. */
function textOf(doc) {
  const parts = [];
  doc.forEach((page) => {
    const blocks = [];
    page.forEach((block) => blocks.push(block.textContent));
    parts.push(blocks.join('|'));
  });
  return parts.join('//');
}

/** The block text at each child index of page `pageIndex`. */
function blocksOf(doc, pageIndex) {
  const found = [];
  doc.child(pageIndex).forEach((block) => found.push(block.textContent));
  return found;
}

// --------------------------------------------------------------------------
// describePages
// --------------------------------------------------------------------------

test('describePages reports one entry per page', () => {
  const pages = describePages(buildDoc([['a'], ['b'], ['c']]));
  assert.equal(pages.length, 3);
  assert.deepEqual(pages.map((page) => page.index), [0, 1, 2]);
});

test('page positions accumulate from previous node sizes', () => {
  const doc = buildDoc([['aa'], ['b']]);
  const [first, second] = describePages(doc);
  assert.equal(first.pos, 0);
  assert.equal(second.pos, first.nodeSize);
  // The decisive check: each position lands exactly on the node it describes.
  assert.equal(doc.resolve(first.pos + 1).parent, doc.child(0));
  assert.equal(doc.resolve(second.pos + 1).parent, doc.child(1));
});

test('contentStart is one past the page position', () => {
  const doc = buildDoc([['a'], ['b']]);
  for (const page of describePages(doc)) {
    assert.equal(page.contentStart, page.pos + 1);
  }
});

test('lastChildStart points exactly at the last block', () => {
  const doc = buildDoc([['one', 'two', 'three']]);
  const [page] = describePages(doc);
  // The block a position resolves to must be the block we intend to move.
  assert.equal(doc.resolve(page.lastChildStart).parent.lastChild.textContent, 'three');
  assert.equal(doc.resolve(page.lastChildStart).parent.content.size, page.contentSize);
});

test('the slice at lastChildStart covers exactly the last block', () => {
  const doc = buildDoc([['one', 'two', 'three']]);
  const [page] = describePages(doc);
  const slice = doc.slice(page.lastChildStart, page.lastChildStart + page.lastChildSize);
  assert.equal(slice.content.childCount, 1);
  assert.equal(slice.content.firstChild.textContent, 'three');
});

test('lastChildStart is correct on a later page as well', () => {
  const doc = buildDoc([['a'], ['x', 'y'], ['z']]);
  const second = describePages(doc)[1];
  assert.equal(doc.resolve(second.lastChildStart).parent.lastChild.textContent, 'y');
});

test('every page of a longer document reports a resolvable last child', () => {
  // Guards the accumulation of `pos` across many pages.
  const doc = buildDoc([['a'], ['b'], ['c'], ['d'], ['e']]);
  for (const page of describePages(doc)) {
    const resolved = doc.resolve(page.lastChildStart);
    assert.ok(resolved.parent, `page ${page.index} has no resolvable parent`);
    assert.equal(resolved.parent.lastChild.nodeSize, page.lastChildSize);
  }
});

test('childSizes sum to the content size', () => {
  const doc = buildDoc([['a', 'bb', 'ccc']]);
  const [page] = describePages(doc);
  const total = page.childSizes.reduce((sum, size) => sum + size, 0);
  assert.equal(total, page.contentSize);
  assert.equal(page.childSizes.length, page.childCount);
});

// --------------------------------------------------------------------------
// startOfTrailingRun
// --------------------------------------------------------------------------

test('startOfTrailingRun locates the last block', () => {
  const doc = buildDoc([['a', 'b', 'c', 'd']]);
  const [page] = describePages(doc);
  assert.equal(startOfTrailingRun(page, 1), page.lastChildStart);
});

test('startOfTrailingRun locates the boundary when several blocks move', () => {
  const doc = buildDoc([['a', 'b', 'c', 'd', 'e']]);
  const [page] = describePages(doc);
  const from = startOfTrailingRun(page, 2);
  // The block that begins the run must be the third from the end.
  const blocks = blocksOf(doc, 0);
  assert.equal(blocks.length, 5);
  assert.equal(doc.resolve(from).parent.child(2).textContent, 'c');
});

test('startOfTrailingRun of the whole page is the content start', () => {
  const doc = buildDoc([['a', 'b', 'c']]);
  const [page] = describePages(doc);
  assert.equal(startOfTrailingRun(page, page.childCount), page.contentStart);
});

// --------------------------------------------------------------------------
// countOverflowingTail
// --------------------------------------------------------------------------

test('countOverflowingTail reports nothing when it already fits', () => {
  const blocks = [{ height: 10 }, { height: 10 }, { height: 10 }];
  assert.equal(countOverflowingTail(blocks, 1000, 0), 0);
});

test('countOverflowingTail moves only what must move', () => {
  // 100 printable; 10+10+900 => the 900 block alone overflows.
  const blocks = [{ height: 10 }, { height: 10 }, { height: 900 }];
  assert.equal(countOverflowingTail(blocks, 100, 0), 1);
});

test('countOverflowingTail accounts for the gap between blocks', () => {
  const blocks = [{ height: 40 }, { height: 40 }, { height: 40 }];
  // 80 printable, no gap: two blocks need exactly 80, so one moves.
  assert.equal(countOverflowingTail(blocks, 80, 0), 1);
  // With a 10px gap the same two blocks need 90, so only one fits and the
  // page must be grown rather than split.
  assert.equal(countOverflowingTail(blocks, 80, 10), 0);
  // Give it room for two and the split becomes possible again.
  assert.equal(countOverflowingTail(blocks, 95, 10), 1);
});

test('countOverflowingTail refuses to split a two-block page', () => {
  // A page with a single remaining block would clip it, so it is grown instead.
  assert.equal(countOverflowingTail([{ height: 500 }, { height: 500 }], 100, 0), 0);
});

test('countOverflowingTail handles an empty page', () => {
  assert.equal(countOverflowingTail([], 100, 0), 0);
});

test('MIN_SPLITTABLE_CHILDREN is two', () => {
  assert.equal(MIN_SPLITTABLE_CHILDREN, 2);
});

// --------------------------------------------------------------------------
// planPage
// --------------------------------------------------------------------------

test('planPage does nothing when nothing must move', () => {
  assert.equal(planPage(buildDoc([['a', 'b']]), 0, 0, new Set()), null);
});

test('planPage moves the measured number of trailing blocks', () => {
  const doc = buildDoc([['a', 'b', 'c']]);
  const plan = planPage(doc, 0, 1, new Set());
  assert.equal(plan.kind, 'move');
  assert.equal(plan.pageIndex, 0);
  assert.equal(plan.count, 1);
  assert.equal(plan.targetIndex, 1);
  assert.equal(doc.resolve(plan.from).parent.child(2).textContent, 'c');
});

test('planPage can move several blocks at once', () => {
  const doc = buildDoc([['a', 'b', 'c', 'd']]);
  const plan = planPage(doc, 0, 2, new Set());
  assert.equal(plan.kind, 'move');
  assert.equal(plan.count, 2);
  assert.equal(doc.resolve(plan.from).parent.child(1).textContent, 'b');
});

test('planPage grows rather than leaving a single block', () => {
  const doc = buildDoc([['a', 'b']]);
  // Moving one would leave a single block, which could not fit anyway.
  const plan = planPage(doc, 0, 1, new Set());
  assert.equal(plan.kind, 'grow');
});

test('planPage skips a pinned page', () => {
  assert.equal(planPage(buildDoc([['a', 'b', 'c']]), 0, 1, new Set([0])), null);
});

test('planPage ignores an out-of-range page index', () => {
  assert.equal(planPage(buildDoc([['a', 'b']]), 7, 1, new Set()), null);
});

// --------------------------------------------------------------------------
// applyReflow
// --------------------------------------------------------------------------

test('applyReflow moves a block onto the next existing page', () => {
  let state = buildState(buildDoc([['a', 'b', 'c'], ['tail']]));
  const plan = planPage(state.doc, 0, 1, new Set());
  state = state.apply(applyReflow(state, plan));
  // The block goes to the *front* of the next page, so reading order down the
  // document is unchanged: a, b, c, tail.
  assert.equal(textOf(state.doc), 'a|b//c|tail');
  assertValid(state.doc);
});

test('applyReflow moves several blocks in one transaction', () => {
  let state = buildState(buildDoc([['a', 'b', 'c', 'd']]));
  const plan = planPage(state.doc, 0, 2, new Set());
  state = state.apply(applyReflow(state, plan));
  // Two of four blocks move, so the source keeps two.
  assert.equal(textOf(state.doc), 'a|b//c|d');
  assertValid(state.doc);
});

test('applyReflow creates a new page when none follows', () => {
  let state = buildState(buildDoc([['a', 'b', 'c']]));
  const plan = planPage(state.doc, 0, 1, new Set());
  state = state.apply(applyReflow(state, plan));
  assert.equal(state.doc.childCount, 2);
  assert.equal(textOf(state.doc), 'a|b//c');
  assertValid(state.doc);
});

test('a page drains to the minimum splittable block count', () => {
  let state = buildState(buildDoc([['a', 'b', 'c', 'd', 'e']]));
  for (let pass = 0; pass < 10; pass += 1) {
    const plan = planPage(state.doc, 0, 1, new Set());
    if (!plan || plan.kind !== 'move') break;
    state = state.apply(applyReflow(state, plan));
  }
  // It stops with two blocks, because moving another would leave one, which
  // could not fit anyway and would have to be grown instead.
  assert.deepEqual(blocksOf(state.doc, 0), ['a', 'b']);
  assert.deepEqual(blocksOf(state.doc, 1), ['c', 'd', 'e']);
  assertValid(state.doc);
});

test('a full reflow cascade conserves every block', () => {
  const original = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
  let state = buildState(buildDoc([original.slice(0, 5), original.slice(5)]));
  for (let pass = 0; pass < 10; pass += 1) {
    const plan = planPage(state.doc, 0, 1, new Set());
    if (!plan || plan.kind !== 'move') break;
    state = state.apply(applyReflow(state, plan));
  }
  const remaining = [...blocksOf(state.doc, 0), ...blocksOf(state.doc, 1)];
  assert.deepEqual(remaining, original);
  assertValid(state.doc);
});

test('applyReflow is a no-op for a grow plan', () => {
  const state = buildState(buildDoc([['a', 'b']]));
  assert.equal(applyReflow(state, { kind: 'grow', pageIndex: 0 }), null);
});

test('applyReflow is a no-op for a page that does not exist', () => {
  const state = buildState(buildDoc([['a']]));
  assert.equal(applyReflow(state, { kind: 'move', pageIndex: 7, targetIndex: 8 }), null);
});

test('applyReflow refuses a plan with an out-of-range position', () => {
  // A stale plan must not be trusted to slice a hard-coded range.
  const state = buildState(buildDoc([['a', 'b', 'c']]));
  const plan = { kind: 'move', pageIndex: 0, from: -1, to: 0, targetIndex: 1 };
  assert.equal(applyReflow(state, plan), null);
});

test('applyReflow derives positions when the plan omits them', () => {
  const state = buildState(buildDoc([['a', 'b', 'c']]));
  const result = state.apply(applyReflow(state, { kind: 'move', pageIndex: 0, targetIndex: 1 }));
  assert.deepEqual(blocksOf(result.doc, 0), ['a', 'b']);
  assert.deepEqual(blocksOf(result.doc, 1), ['c']);
  assertValid(result.doc);
});

// --------------------------------------------------------------------------
// collapseToSinglePage
// --------------------------------------------------------------------------

test('collapseToSinglePage merges every page in order', () => {
  const collapsed = collapseToSinglePage(buildDoc([['a', 'b'], ['c']]));
  assert.equal(collapsed.childCount, 1);
  assert.equal(textOf(collapsed), 'a|b|c');
});

test('collapseToSinglePage handles a single page', () => {
  assert.equal(textOf(collapseToSinglePage(buildDoc([['a', 'b']]))), 'a|b');
});

test('collapseToSinglePage keeps every block', () => {
  const doc = buildDoc([['a'], ['b'], ['c'], ['d']]);
  const collapsed = collapseToSinglePage(doc);
  assert.equal(collapsed.child(0).childCount, 4);
  assert.equal(collapsed.child(0).textContent, 'abcd');
});

test('collapseToSinglePage output is a valid document', () => {
  assertValid(collapseToSinglePage(buildDoc([['a'], ['b']])));
});

test('collapseToSinglePage always leaves at least one block', () => {
  const collapsed = collapseToSinglePage(buildDoc([['a']]));
  assert.equal(collapsed.child(0).childCount, 1);
});

test('a collapsed document can be re-expanded by the paginator', () => {
  // The full load path: collapse every saved page, then flow blocks back out.
  let state = buildState(collapseToSinglePage(buildDoc([['a', 'b', 'c'], ['d']])));
  assert.equal(state.doc.childCount, 1);
  assert.deepEqual(blocksOf(state.doc, 0), ['a', 'b', 'c', 'd']);

  state = state.apply(applyReflow(state, planPage(state.doc, 0, 1, new Set())));
  assert.equal(state.doc.childCount, 2);
  assert.equal(textOf(state.doc), 'a|b|c//d');
  assertValid(state.doc);
});

// --------------------------------------------------------------------------
// Constants
// --------------------------------------------------------------------------

test('OVERFLOW_TOLERANCE is a small positive number', () => {
  assert.equal(typeof OVERFLOW_TOLERANCE, 'number');
  assert.ok(OVERFLOW_TOLERANCE > 0 && OVERFLOW_TOLERANCE <= 2);
});

// --------------------------------------------------------------------------
// Keep with next
//
// A heading stranded as the last thing on a page is the most common way a
// paginated document reads as broken, and it is a defect the overflow pass
// cannot see: the page may fit its content perfectly and still strand one.
// --------------------------------------------------------------------------

test('keepsWithNext is true for a heading and false for a paragraph', () => {
  const doc = buildTypedDoc([[{ type: 'heading', text: 'H' }, { text: 'body' }]]);
  assert.equal(keepsWithNext(doc.child(0).child(0)), true);
  assert.equal(keepsWithNext(doc.child(0).child(1)), false);
});

test('keepsWithNext honours an explicit opt-out', () => {
  const doc = buildTypedDoc([
    [{ type: 'heading', text: 'H', attrs: { level: 1, keepWithNext: false } }, { text: 'body' }],
  ]);
  assert.equal(keepsWithNext(doc.child(0).child(0)), false);
});

test('a page ending in a heading must give it up', () => {
  const doc = buildTypedDoc([
    [{ text: 'body' }, { type: 'heading', text: 'Section' }],
    [{ text: 'more' }],
  ]);
  assert.equal(countKeepWithNextTail(doc, 0), 1);
});

test('a page ending in a paragraph is left alone', () => {
  const doc = buildTypedDoc([[{ text: 'body' }, { text: 'tail' }], [{ text: 'more' }]]);
  assert.equal(countKeepWithNextTail(doc, 0), 0);
});

test('a heading in the middle of a page is left alone', () => {
  const doc = buildTypedDoc([
    [{ type: 'heading', text: 'Section' }, { text: 'body' }],
    [{ text: 'more' }],
  ]);
  assert.equal(countKeepWithNextTail(doc, 0), 0);
});

test('the last page may end in a heading', () => {
  // Nothing follows it, so there is nothing to keep it with.
  const doc = buildTypedDoc([[{ text: 'body' }], [{ type: 'heading', text: 'End' }]]);
  assert.equal(countKeepWithNextTail(doc, 1), 0);
});

test('a page holding only the heading is not emptied', () => {
  const doc = buildTypedDoc([[{ type: 'heading', text: 'Only' }], [{ text: 'more' }]]);
  assert.equal(countKeepWithNextTail(doc, 0), 0);
});

test('an out-of-range page reports nothing to move', () => {
  const doc = buildTypedDoc([[{ text: 'body' }]]);
  assert.equal(countKeepWithNextTail(doc, 7), 0);
});

test('the move relocates the heading without corrupting the document', () => {
  const doc = buildTypedDoc([
    [{ text: 'body' }, { type: 'heading', text: 'Section' }],
    [{ text: 'more' }],
  ]);
  const plan = planKeepWithNext(doc, 0, new Set());
  assert.ok(plan);
  assert.equal(plan.kind, 'move');
  assert.equal(plan.count, 1);

  const state = buildState(doc);
  const result = applyReflow(state, plan);
  assert.ok(result);
  const after = state.apply(result);
  assert.equal(blocksOf(after.doc, 0).join('|'), 'body');
  assert.equal(blocksOf(after.doc, 1).join('|'), 'Section|more');
  assertValid(after.doc);
});

test('a run of consecutive headings is resolved by moving only the last', () => {
  // Whatever the moved heading lands on is content that follows it, so one
  // block is enough and moving the whole run would leave a gap.
  const doc = buildTypedDoc([
    [
      { text: 'body' },
      { type: 'heading', text: 'First' },
      { type: 'heading', text: 'Second' },
    ],
    [{ text: 'more' }],
  ]);
  assert.equal(countKeepWithNextTail(doc, 0), 1);
});

test('a page pinned to grow is not asked to move its heading', () => {
  const doc = buildTypedDoc([
    [{ text: 'body' }, { type: 'heading', text: 'Section' }],
    [{ text: 'more' }],
  ]);
  assert.equal(planKeepWithNext(doc, 0, new Set([0])), null);
});
test('a page holding only the heading is not emptied', () => {
  const doc = buildTypedDoc([[{ type: 'heading', text: 'Only' }], [{ text: 'more' }]]);
  assert.equal(countKeepWithNextTail(doc, 0), 0);
  assert.equal(planKeepWithNext(doc, 0, new Set()), null);
});

test('moving the heading leaves a readable page, not a stranded one', () => {
  // The decisive case for the dedicated plan: [body, heading] is two blocks, so
  // the overflow rule's "do not leave fewer than two" would refuse to split it
  // and grow the page instead. Leaving [body] behind is the normal outcome.
  const doc = buildTypedDoc([
    [{ text: 'body' }, { type: 'heading', text: 'Section' }],
    [{ text: 'more' }],
  ]);
  const state = buildState(doc);
  const result = applyReflow(state, planKeepWithNext(doc, 0, new Set()));
  const after = state.apply(result);
  assert.equal(blocksOf(after.doc, 0).length, 1);
  assert.equal(blocksOf(after.doc, 0)[0], 'body');
  assert.deepEqual(blocksOf(after.doc, 1), ['Section', 'more']);
  assertValid(after.doc);
});

test('no page in a reflowed document ends in a stranded heading', () => {
  // The property that matters, checked on a document with several pages.
  let state = buildState(
    buildTypedDoc([
      [{ text: 'a' }, { type: 'heading', text: 'H1' }],
      [{ text: 'b' }, { type: 'heading', text: 'H2' }],
      [{ text: 'c' }],
    ]),
  );

  for (let round = 0; round < 6; round += 1) {
    // Every page each round, not just the first: a fix on one page shifts the
    // content of the next, so a single-page pass would report a stale result.
    let applied = false;
    for (let index = 0; index < state.doc.childCount - 1; index += 1) {
      const plan = planKeepWithNext(state.doc, index, new Set());
      if (!plan) continue;
      const step = applyReflow(state, plan);
      if (!step) continue;
      state = state.apply(step);
      applied = true;
      break;
    }
    if (!applied) break;
  }

  for (let index = 0; index < state.doc.childCount; index += 1) {
    const page = state.doc.child(index);
    const last = page.lastChild;
    if (index < state.doc.childCount - 1 && last) {
      assert.equal(
        keepsWithNext(last),
        false,
        `page ${index} still ends in a heading: ${page.textContent}`,
      );
    }
  }
  assertValid(state.doc);
});
// --------------------------------------------------------------------------
// The two passes together
//
// Keep-with-next has to run even when nothing overflows, which is the whole
// reason it is a separate pass: the previous implementation returned early when
// no page overflowed, so a page that fit perfectly while stranding a heading was
// never examined.
// --------------------------------------------------------------------------

/**
 * A container whose pages report whatever `overflows` says they should.
 *
 * @param {number} pageCount
 * @param {{overflows?: boolean, printable?: number, blockHeight?: number}} [options]
 */
function fakeContainer(pageCount, options = {}) {
  const printable = options.printable ?? 300;
  const blockHeight = options.blockHeight ?? 100;
  // `printableHeight` reads padding through the page's own window, so the stub
  // needs one; zero padding keeps the arithmetic obvious.
  const ownerDocument = {
    defaultView: { getComputedStyle: () => ({ paddingTop: '0px', paddingBottom: '0px' }) },
  };
  const children = [];
  for (let index = 0; index < pageCount; index += 1) {
    children.push({
      dataset: {},
      ownerDocument,
      scrollHeight: options.overflows ? printable + 10 : printable,
      clientHeight: printable,
      children: [],
      getBoundingClientRect: () => ({ top: 0, bottom: blockHeight }),
      querySelectorAll: () => [],
    });
  }
  return { children };
}

test('a stranded heading is fixed when nothing overflows at all', () => {
  const doc = buildTypedDoc([
    [{ text: 'a' }, { type: 'heading', text: 'Section' }],
    [{ text: 'b' }],
  ]);
  const state = buildState(doc);
  const container = fakeContainer(2, { overflows: false });

  const result = reflowTransaction(state, container, new Set());
  assert.ok(result, 'keep-with-next must not require an overflow to act');
  assert.equal(result.moved, 1);

  const after = state.apply(result.transaction);
  assert.deepEqual(blocksOf(after.doc, 0), ['a']);
  assert.deepEqual(blocksOf(after.doc, 1), ['Section', 'b']);
  assertValid(after.doc);
});

test('a document with nothing to fix produces no transaction', () => {
  const state = buildState(buildTypedDoc([[{ text: 'a' }], [{ text: 'b' }]]));
  assert.equal(reflowTransaction(state, fakeContainer(2, { overflows: false }), new Set()), null);
});

test('an overflowing document with a trailing heading needs only one fix', () => {
  const doc = buildTypedDoc([
    [{ text: 'a' }, { text: 'b' }, { type: 'heading', text: 'Section' }],
    [{ text: 'c' }],
  ]);
  const state = buildState(doc);
  const result = reflowTransaction(state, fakeContainer(2, { overflows: true }), new Set());
  assert.ok(result);
  const after = state.apply(result.transaction);
  assertValid(after.doc);
  const lastOfFirst = after.doc.child(0).lastChild;
  assert.equal(keepsWithNext(lastOfFirst), false, 'the heading should have moved');
});