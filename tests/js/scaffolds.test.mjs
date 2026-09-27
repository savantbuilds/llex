/**
 * Unit tests for the scaffold pipeline's position arithmetic.
 *
 * Batch replacement is the classic place to corrupt a document: each edit
 * changes the length of the document, so applying them in document order
 * replaces the wrong text. The ordering is therefore tested directly against
 * real ProseMirror documents.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { Schema } from '@tiptap/pm/model';
import { EditorState } from '@tiptap/pm/state';

import {
  applyScaffoldEdits,
  collectScaffolds,
  orderScaffoldEdits,
} from '../../llex/static/js/scaffolds.js';

const schema = new Schema({
  nodes: {
    doc: { content: 'block+' },
    paragraph: { content: 'inline*', group: 'block' },
    text: { group: 'inline' },
  },
  marks: {
    scaffold: {
      attrs: { id: { default: null }, instruction: { default: '' } },
      inclusive: false,
    },
  },
});

const mark = (id, instruction) => schema.marks.scaffold.create({ id, instruction });

/**
 * @param {{text: string, mark?: import('@tiptap/pm/model').Mark}[]} paragraphs
 */
function buildDoc(paragraphs) {
  return schema.nodes.doc.create(
    null,
    paragraphs.map((entry) =>
      schema.nodes.paragraph.create(
        null,
        entry.text ? schema.text(entry.text, entry.mark ? [entry.mark] : []) : null,
      ),
    ),
  );
}

const buildState = (doc) => EditorState.create({ schema, doc });

const texts = (doc) => {
  const found = [];
  doc.forEach((block) => found.push(block.textContent));
  return found;
};

// --------------------------------------------------------------------------
// collectScaffolds
// --------------------------------------------------------------------------

test('collectScaffolds finds nothing in plain text', () => {
  assert.deepEqual(collectScaffolds(buildDoc([{ text: 'nothing marked' }])), []);
});

test('collectScaffolds reports text, instruction and range', () => {
  const doc = buildDoc([{ text: 'first' }, { text: 'flagged', mark: mark('s1', 'expand') }]);
  const found = collectScaffolds(doc);
  assert.equal(found.length, 1);
  assert.equal(found[0].id, 's1');
  assert.equal(found[0].instruction, 'expand');
  assert.equal(found[0].text, 'flagged');
  assert.ok(found[0].end > found[0].start);
});

test('collectScaffolds merges adjacent runs sharing an id', () => {
  // One prompt spanning two nodes must be executed once, not twice.
  const shared = mark('s1', 'expand');
  const doc = buildDoc([
    { text: 'before' },
    { text: 'part one ', mark: shared },
    { text: 'part two', mark: shared },
  ]);
  const found = collectScaffolds(doc);
  assert.equal(found.length, 1);
  assert.equal(found[0].text, 'part one part two');
});

test('collectScaffolds keeps distinct ids separate', () => {
  const doc = buildDoc([
    { text: 'a', mark: mark('s1', 'x') },
    { text: 'b', mark: mark('s2', 'y') },
  ]);
  const found = collectScaffolds(doc);
  assert.deepEqual(found.map((item) => item.id), ['s1', 's2']);
});

test('collectScaffolds drops whitespace-only scaffolds', () => {
  const doc = buildDoc([{ text: '   ', mark: mark('s1', 'x') }]);
  assert.deepEqual(collectScaffolds(doc), []);
});

test('collectScaffolds ignores a mark with no id', () => {
  const doc = buildDoc([{ text: 'text', mark: mark(null, 'x') }]);
  assert.deepEqual(collectScaffolds(doc), []);
});

test('collectScaffolds reports ranges that resolve to the right text', () => {
  const doc = buildDoc([
    { text: 'keep' },
    { text: 'replace me', mark: mark('s1', 'x') },
  ]);
  const [found] = collectScaffolds(doc);
  assert.equal(doc.textBetween(found.start, found.end), 'replace me');
});

test('collectScaffolds tolerates a missing instruction', () => {
  const doc = buildDoc([{ text: 'x', mark: mark('s1', '') }]);
  assert.equal(collectScaffolds(doc)[0].instruction, '');
});

// --------------------------------------------------------------------------
// orderScaffoldEdits
// --------------------------------------------------------------------------

const target = (id, start, end) => ({ id, instruction: 'x', text: 't', start, end });

test('orderScaffoldEdits sorts by descending start position', () => {
  const targets = [target('a', 0, 5), target('b', 10, 15), target('c', 20, 25)];
  const edits = orderScaffoldEdits(
    [
      { id: 'a', text: 'A' },
      { id: 'c', text: 'C' },
      { id: 'b', text: 'B' },
    ],
    targets,
  );
  assert.deepEqual(edits.map((edit) => edit.target.id), ['c', 'b', 'a']);
});

test('orderScaffoldEdits ignores results with no matching target', () => {
  const edits = orderScaffoldEdits(
    [
      { id: 'ghost', text: 'G' },
      { id: 'a', text: 'A' },
    ],
    [target('a', 0, 5)],
  );
  assert.deepEqual(edits.map((edit) => edit.target.id), ['a']);
});

test('orderScaffoldEdits drops results with no replacement text', () => {
  const edits = orderScaffoldEdits(
    [
      { id: 'a', text: '' },
      { id: 'b', text: 'B' },
    ],
    [target('a', 0, 5), target('b', 10, 15)],
  );
  assert.deepEqual(edits.map((edit) => edit.target.id), ['b']);
});

test('orderScaffoldEdits drops non-string replacements', () => {
  const edits = orderScaffoldEdits(
    [
      { id: 'a', text: null },
      { id: 'b', text: 'B' },
    ],
    [target('a', 0, 5), target('b', 10, 15)],
  );
  assert.deepEqual(edits.map((edit) => edit.target.id), ['b']);
});

test('orderScaffoldEdits handles an empty batch', () => {
  assert.deepEqual(orderScaffoldEdits([], []), []);
});

// --------------------------------------------------------------------------
// applyScaffoldEdits
// --------------------------------------------------------------------------

test('applyScaffoldEdits replaces text and removes the mark', () => {
  let state = buildState(
    buildDoc([{ text: 'before' }, { text: 'old text', mark: mark('s1', 'x') }]),
  );
  const [found] = collectScaffolds(state.doc);
  state = state.apply(applyScaffoldEdits(state.tr, [{ target: found, text: 'new text' }]));

  assert.deepEqual(texts(state.doc), ['before', 'new text']);
  // The prompt must be gone, or it would be offered for execution again.
  assert.deepEqual(collectScaffolds(state.doc), []);
});

test('applyScaffoldEdits applies a whole batch correctly', () => {
  // The regression this guards: edits applied in document order would replace
  // the wrong text, because each one shifts the positions of the rest.
  let state = buildState(
    buildDoc([
      { text: 'aaa', mark: mark('s1', 'x') },
      { text: 'bbbbbb', mark: mark('s2', 'x') },
      { text: 'c', mark: mark('s3', 'x') },
    ]),
  );
  const targets = collectScaffolds(state.doc);
  const edits = orderScaffoldEdits(
    [
      { id: 's1', text: 'A' },
      { id: 's2', text: 'B-longer' },
      { id: 's3', text: 'C' },
    ],
    targets,
  );
  state = state.apply(applyScaffoldEdits(state.tr, edits));

  assert.deepEqual(texts(state.doc), ['A', 'B-longer', 'C']);
});

test('applyScaffoldEdits handles a replacement longer than the original', () => {
  let state = buildState(buildDoc([{ text: 'x', mark: mark('s1', 'go') }]));
  const [found] = collectScaffolds(state.doc);
  const replacement = 'a much longer generated passage than the original fragment';
  state = state.apply(applyScaffoldEdits(state.tr, [{ target: found, text: replacement }]));
  assert.deepEqual(texts(state.doc), [replacement]);
});

test('applyScaffoldEdits leaves a valid document', () => {
  let state = buildState(buildDoc([{ text: 'aaa', mark: mark('s1', 'x') }]));
  const [found] = collectScaffolds(state.doc);
  state = state.apply(applyScaffoldEdits(state.tr, [{ target: found, text: 'A' }]));
  assert.doesNotThrow(() => state.doc.check());
});

test('applyScaffoldEdits is a no-op for an empty batch', () => {
  const state = buildState(buildDoc([{ text: 'a' }]));
  const empty = applyScaffoldEdits(state.tr, []);
  assert.doesNotThrow(() => state.apply(empty));
});
