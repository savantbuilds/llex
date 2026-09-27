/**
 * Unit tests for the pure helpers that the editor modules share.
 *
 * These are the parts that must agree exactly with the Python backend. If the
 * two drift, a document's status bar and its stored metadata will disagree, and
 * nothing else will catch it.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  countCharacters,
  countWords,
  formatNumber,
  formatStats,
} from '../../llex/static/js/metrics.js';

// --------------------------------------------------------------------------
// countWords -- must match llex/markup.py::count_words
// --------------------------------------------------------------------------

test('countWords handles the basics', () => {
  assert.equal(countWords(''), 0);
  assert.equal(countWords('one two three'), 3);
  assert.equal(countWords('   '), 0);
  assert.equal(countWords('  spaced   out  '), 2);
});

test("countWords keeps an internal apostrophe as one word", () => {
  assert.equal(countWords("don't stop"), 2);
  assert.equal(countWords('It\u2019s fine'), 2);
});

test('countWords treats underscores as word characters', () => {
  // Matches how mainstream word processors count `under_score`.
  assert.equal(countWords('under_score counts as one'), 4);
});

test('countWords ignores punctuation between words', () => {
  assert.equal(countWords('punctuation, everywhere!  really?'), 3);
});

test('countWords counts digits as words', () => {
  assert.equal(countWords('123 456'), 2);
});

test('countWords handles non-Latin scripts', () => {
  assert.equal(countWords('中文 文字'), 2);
});

// --------------------------------------------------------------------------
// countCharacters
// --------------------------------------------------------------------------

test('countCharacters excludes paragraph separators', () => {
  assert.equal(countCharacters('abc'), 3);
  assert.equal(countCharacters('a\nb\nc'), 3);
  assert.equal(countCharacters(''), 0);
});

// --------------------------------------------------------------------------
// Formatting
// --------------------------------------------------------------------------

test('formatNumber groups thousands', () => {
  assert.equal(formatNumber(0), '0');
  assert.equal(formatNumber(1234).replace(/,/g, ''), '1234');
  assert.equal(formatNumber(undefined), '0');
});

test('formatStats always reports the three core counts', () => {
  const text = formatStats(10, 55, 2);
  assert.match(text, /10 words/);
  assert.match(text, /55 characters/);
  assert.match(text, /2 pages/);
});

test('formatStats omits the selection when there is none', () => {
  assert.doesNotMatch(formatStats(1, 2, 1), /selected/);
  assert.match(formatStats(1, 2, 1, 9), /9 selected/);
});
