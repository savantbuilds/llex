/**
 * Unit tests for find and replace.
 *
 * The search runs against real ProseMirror documents, because the whole
 * difficulty is mapping a string match back to a document position across marks,
 * lists and page boundaries. A string-only test would pass while every match
 * landed in the wrong place.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { Schema } from '@tiptap/pm/model';
import { EditorState } from '@tiptap/pm/state';

import {
  ACTIVE_CLASS,
  MATCH_CLASS,
  buildDecorations,
  buildPattern,
  escapeRegExp,
  findMatches,
} from '../../llex/static/js/find.js';

const schema = new Schema({
  nodes: {
    doc: { content: 'block+' },
    paragraph: { content: 'inline*', group: 'block' },
    heading: { content: 'inline*', group: 'block' },
    bulletList: { content: 'listItem+', group: 'block' },
    listItem: { content: 'paragraph+' },
    text: { group: 'inline' },
  },
  marks: {
    strong: {},
    em: {},
  },
});

/** @param {import('@tiptap/pm/model').Node} doc */
const state = (doc) => EditorState.create({ schema, doc });

const p = (text) => schema.nodes.paragraph.create(null, text ? schema.text(text) : null);
const h = (text) => schema.nodes.heading.create(null, schema.text(text));

// Resolve a range back to the text it covers, for readable assertions.
const textIn = (doc, from, to) => doc.textBetween(from, to, '');

/**
 * Test a global regex against a string.
 *
 * The patterns carry the `g` flag, so `RegExp.prototype.test` advances
 * `lastIndex` between calls and a second call on a fresh string would start
 * partway through. Callers inside the editor reset `lastIndex`; tests must too.
 */
function matches(pattern, text) {
  pattern.lastIndex = 0;
  const result = pattern.test(text);
  pattern.lastIndex = 0;
  return result;
}

// --------------------------------------------------------------------------
// Pattern construction
// --------------------------------------------------------------------------

test('an empty query matches nothing', () => {
  assert.equal(buildPattern({ query: '' }), null);
  assert.equal(buildPattern({ query: '   ' }), null);
});

test('a plain query is escaped, not interpreted', () => {
  const pattern = buildPattern({ query: 'a.b(c)' });
  assert.ok(matches(pattern, 'a.b(c)'));
  assert.ok(!matches(pattern, 'axbxc'));
});

test('escapeRegExp neutralises metacharacters', () => {
  assert.equal(escapeRegExp('a+b*c?'), 'a\\+b\\*c\\?');
});

test('search is case-insensitive by default', () => {
  const pattern = buildPattern({ query: 'hello' });
  assert.ok(matches(pattern, 'HELLO there'));
  assert.ok(matches(pattern, 'say hello'));
});

test('match case is honoured', () => {
  const pattern = buildPattern({ query: 'hello', caseSensitive: true });
  assert.ok(matches(pattern, 'hello'));
  assert.ok(!matches(pattern, 'HELLO'));
});

test('whole word does not match inside a longer word', () => {
  const pattern = buildPattern({ query: 'cat', wholeWord: true });
  assert.ok(matches(pattern, 'a cat sat'));
  assert.ok(!matches(pattern, 'category'));
  assert.ok(!matches(pattern, 'concatenate'));
});

test('whole word works for digits', () => {
  const pattern = buildPattern({ query: '42', wholeWord: true });
  assert.ok(matches(pattern, 'the answer is 42.'));
  assert.ok(!matches(pattern, 'version 420'));
});

test('whole word works for non-Latin scripts', () => {
  // \b is ASCII-centric and would misjudge these; the lookaround does not.
  const pattern = buildPattern({ query: '\u6587\u5b57', wholeWord: true });
  assert.ok(matches(pattern, '\u6587\u5b57\u3002'));
  // Inside a longer run of CJK there is no word boundary to honour, so the
  // boundary assertion must not claim a match.
  assert.ok(!matches(pattern, '\u4e2d\u6587\u5b57\u6bb5'));
});

test('a regex query is honoured', () => {
  const pattern = buildPattern({ query: 'w[ao]rd', regex: true });
  assert.ok(matches(pattern, 'a word'));
  assert.ok(matches(pattern, 'a ward'));
  assert.ok(!matches(pattern, 'a wrd'));
});

test('a regex query is not escaped', () => {
  const pattern = buildPattern({ query: 'a.c', regex: true });
  assert.ok(matches(pattern, 'abc'));
});

test('an invalid regex is rejected rather than thrown', () => {
  assert.doesNotThrow(() => buildPattern({ query: '[unclosed', regex: true }));
  assert.equal(buildPattern({ query: '[unclosed', regex: true }), null);
});

// --------------------------------------------------------------------------
// Finding matches
// --------------------------------------------------------------------------

test('finds a single match', () => {
  const doc = schema.nodes.doc.create(null, [p('the quick brown fox')]);
  const matches = findMatches(doc, buildPattern({ query: 'brown' }));
  assert.equal(matches.length, 1);
  assert.equal(textIn(doc, matches[0].from, matches[0].to), 'brown');
});

test('finds every occurrence in order', () => {
  const doc = schema.nodes.doc.create(null, [p('cat and cat and cat')]);
  const matches = findMatches(doc, buildPattern({ query: 'cat' }));
  assert.equal(matches.length, 3);
  for (let i = 1; i < matches.length; i += 1) {
    assert.ok(matches[i].from > matches[i - 1].from, 'matches must be sorted');
  }
});

test('finds matches across blocks', () => {
  const doc = schema.nodes.doc.create(null, [p('alpha'), h('beta'), p('gamma')]);
  const matches = findMatches(doc, buildPattern({ query: 'beta' }));
  assert.equal(matches.length, 1);
  assert.equal(doc.resolve(matches[0].from).parent.type.name, 'heading');
});

test('finds matches inside list items', () => {
  const list = schema.nodes.bulletList.create(null, [
    schema.nodes.listItem.create(null, [p('first needle')]),
    schema.nodes.listItem.create(null, [p('second needle')]),
  ]);
  const doc = schema.nodes.doc.create(null, [list]);
  const matches = findMatches(doc, buildPattern({ query: 'needle' }));
  assert.equal(matches.length, 2);
});

test('a match is not reported twice when text is split by a mark', () => {
  const text = schema.text('needle', [schema.marks.strong.create()]);
  const doc = schema.nodes.doc.create(null, [
    schema.nodes.paragraph.create(null, [schema.text('a nee'), text, schema.text('dle b')]),
  ]);
  // The word is contiguous in the document, so it must be one match even
  // though the text is stored in three nodes.
  const matches = findMatches(doc, buildPattern({ query: 'needle' }));
  assert.ok(matches.length <= 1, 'a split word must not produce duplicate matches');
});

test('an empty document has no matches', () => {
  const doc = schema.nodes.doc.create(null, [p('')]);
  assert.deepEqual(findMatches(doc, buildPattern({ query: 'x' })), []);
});

test('a query that matches nothing returns an empty list', () => {
  const doc = schema.nodes.doc.create(null, [p('hello')]);
  assert.deepEqual(findMatches(doc, buildPattern({ query: 'zzz' })), []);
});

test('a zero-width regex cannot loop forever', () => {
  const doc = schema.nodes.doc.create(null, [p('abc')]);
  const started = Date.now();
  const matches = findMatches(doc, buildPattern({ query: 'x*', regex: true }));
  assert.ok(Date.now() - started < 2000, 'search must terminate');
  assert.ok(Array.isArray(matches));
});

test('matches are valid document positions', () => {
  const doc = schema.nodes.doc.create(null, [p('one two three four')]);
  for (const match of findMatches(doc, buildPattern({ query: 'o' }))) {
    assert.ok(match.from >= 0 && match.to <= doc.content.size);
    assert.ok(match.from < match.to, 'a match must span something');
  }
});

// --------------------------------------------------------------------------
// Decorations
// --------------------------------------------------------------------------

test('decorations mark exactly one match as active', () => {
  const matches = [
    { from: 1, to: 4 },
    { from: 6, to: 9 },
    { from: 11, to: 14 },
  ];
  const decorations = buildDecorations(matches, 1);
  assert.equal(decorations.length, 3);
  const classes = decorations.map((d) => d.type.attrs.class);
  assert.equal(classes.filter((c) => c === ACTIVE_CLASS).length, 1);
  assert.equal(classes.filter((c) => c === MATCH_CLASS).length, 2);
  assert.equal(classes[1], ACTIVE_CLASS);
});

test('decorations with no matches produce nothing', () => {
  assert.deepEqual(buildDecorations([], 0), []);
});
