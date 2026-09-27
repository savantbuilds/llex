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

import {
  MIN_SPLITTABLE_CHILDREN,
  OVERFLOW_TOLERANCE,
  applyReflow,
  collapseToSinglePage,
  countOverflowingTail,
  describePages,
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
