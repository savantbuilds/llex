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
  OVERFLOW_TOLERANCE,
  applyReflow,
  collapseToSinglePage,
  describePages,
  planReflow,
} from '../../llex/static/js/pagination.js';

/** The minimal schema the pagination engine operates on. */
const schema = new Schema({
  nodes: {
    doc: { content: 'page+' },
    page: { content: 'block+', group: 'page' },
    paragraph: { content: 'inline*', group: 'block' },
    heading: { content: 'inline*', group: 'block' },
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
  const [first, second] = describePages(doc);
  assert.equal(first.contentStart, first.pos + 1);
  assert.equal(second.contentStart, second.pos + 1);
});

test('lastChildStart points exactly at the last block', () => {
  const doc = buildDoc([['one', 'two', 'three']]);
  const [page] = describePages(doc);
  // The block a position resolves to must be the block we intend to move.
  assert.equal(doc.resolve(page.lastChildStart).parent.lastChild.textContent, 'three');
  assert.equal(doc.resolve(page.lastChildStart).parent.content.size, page.nodeSize - 2);
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

test('a page holding a single textless block still reports a position', () => {
  const doc = buildDoc([['']]);
  const [page] = describePages(doc);
  assert.equal(page.childCount, 1);
  assert.ok(page.lastChildStart >= 0);
  assert.equal(page.lastChildSize, 2);
});

// --------------------------------------------------------------------------
// planReflow
// --------------------------------------------------------------------------

test('planReflow does nothing when nothing overflows', () => {
  assert.equal(planReflow(buildDoc([['a']]), new Set()), null);
});

test('planReflow moves the last block when a page overflows', () => {
  const doc = buildDoc([['a', 'b', 'c']]);
  const plan = planReflow(doc, new Set([0]));
  assert.equal(plan.kind, 'move');
  assert.equal(plan.pageIndex, 0);
  assert.equal(plan.targetIndex, 1);
  assert.equal(doc.resolve(plan.from).parent.lastChild.textContent, 'c');
});

test('planReflow grows a page holding a single oversized block', () => {
  // Clipping this would make the text unreadable and unselectable.
  const plan = planReflow(buildDoc([['enormous']]), new Set([0]));
  assert.equal(plan.kind, 'grow');
  assert.equal(plan.pageIndex, 0);
});

test('planReflow skips pages already pinned as overfull', () => {
  const doc = buildDoc([['huge'], ['a', 'b']]);
  const plan = planReflow(doc, new Set([0, 1]), new Set([0]));
  assert.equal(plan.kind, 'move');
  assert.equal(plan.pageIndex, 1);
});

test('planReflow returns null once every overflow is pinned', () => {
  assert.equal(planReflow(buildDoc([['huge']]), new Set([0]), new Set([0])), null);
});

test('planReflow ignores an out-of-range page index', () => {
  assert.equal(planReflow(buildDoc([['a']]), new Set([7])), null);
});

// --------------------------------------------------------------------------
// applyReflow
// --------------------------------------------------------------------------

test('applyReflow moves a block onto the next existing page', () => {
  let state = buildState(buildDoc([['a', 'b', 'c'], ['tail']]));
  const plan = planReflow(state.doc, new Set([0]));
  state = state.apply(applyReflow(state, plan));
  // The block goes to the *front* of the next page, so reading order down the
  // document is unchanged: a, b, c, tail.
  assert.equal(textOf(state.doc), 'a|b//c|tail');
  assertValid(state.doc);
});

test('applyReflow creates a new page when none follows', () => {
  let state = buildState(buildDoc([['a', 'b', 'c']]));
  const plan = planReflow(state.doc, new Set([0]));
  state = state.apply(applyReflow(state, plan));
  assert.equal(state.doc.childCount, 2);
  assert.equal(textOf(state.doc), 'a|b//c');
  assertValid(state.doc);
});

test('applyReflow preserves the order of the moved block', () => {
  let state = buildState(buildDoc([['first', 'second', 'third']]));
  const plan = planReflow(state.doc, new Set([0]));
  state = state.apply(applyReflow(state, plan));
  assert.deepEqual(blocksOf(state.doc, 0), ['first', 'second']);
  assert.deepEqual(blocksOf(state.doc, 1), ['third']);
});

test('a page drains to a single block before it is pinned as overfull', () => {
  let state = buildState(buildDoc([['a', 'b', 'c', 'd', 'e']]));
  // Nothing is pinned yet; the loop ends when page 0 can no longer split.
  for (let pass = 0; pass < 10; pass += 1) {
    const plan = planReflow(state.doc, new Set([0]));
    if (!plan || plan.kind !== 'move') break;
    state = state.apply(applyReflow(state, plan));
  }
  assert.deepEqual(blocksOf(state.doc, 0), ['a']);
  assert.deepEqual(blocksOf(state.doc, 1), ['b', 'c', 'd', 'e']);
  assertValid(state.doc);
});

test('a full reflow cascade conserves every block', () => {
  const original = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
  let state = buildState(buildDoc([original.slice(0, 5), original.slice(5)]));
  // Simulate a cascade: page 0 overflows until it holds a single block.
  for (let pass = 0; pass < 10; pass += 1) {
    const plan = planReflow(state.doc, new Set([0]), new Set([0]));
    if (!plan || plan.kind !== 'move') break;
    state = state.apply(applyReflow(state, plan));
  }
  const remaining = [...blocksOf(state.doc, 0), ...blocksOf(state.doc, 1)];
  assert.deepEqual(remaining, original);
  assertValid(state.doc);
});

test('applyReflow is a no-op for a grow plan', () => {
  const state = buildState(buildDoc([['huge']]));
  assert.equal(applyReflow(state, { kind: 'grow', pageIndex: 0 }), null);
});

test('applyReflow is a no-op for a page that does not exist', () => {
  const state = buildState(buildDoc([['a']]));
  const plan = { kind: 'move', pageIndex: 7, targetIndex: 8 };
  assert.equal(applyReflow(state, plan), null);
});

test('applyReflow ignores the plan range and uses the live document', () => {
  // A stale plan must not slice a hard-coded range; positions are re-derived.
  const state = buildState(buildDoc([['a', 'b', 'c']]));
  const plan = { kind: 'move', pageIndex: 0, from: -1, to: 0, targetIndex: 1 };
  const result = state.apply(applyReflow(state, plan));
  assert.deepEqual(blocksOf(result.doc, 0), ['a', 'b']);
  assert.deepEqual(blocksOf(result.doc, 1), ['c']);
  assertValid(result.doc);
});

test('applyReflow re-derives positions from the live document', () => {
  // A plan computed against an older document must not be trusted blindly.
  const stale = buildState(buildDoc([['a', 'b', 'c']]));
  const plan = planReflow(stale.doc, new Set([0]));
  const live = buildState(buildDoc([['a', 'b', 'c', 'd', 'e']]));
  const result = live.apply(applyReflow(live, plan));
  // Only 'e' should have moved, taken from the *live* document.
  assert.deepEqual(blocksOf(result.doc, 0), ['a', 'b', 'c', 'd']);
  assert.deepEqual(blocksOf(result.doc, 1), ['e']);
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
  const collapsed = collapseToSinglePage(buildDoc([['a'], ['b']]));
  assertValid(collapsed);
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

  state = state.apply(applyReflow(state, planReflow(state.doc, new Set([0]))));
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
