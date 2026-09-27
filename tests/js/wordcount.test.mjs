import test from 'node:test';
import assert from 'node:assert/strict';
import { countWords } from '../../llex/static/js/metrics.js';

// The Python side asserts the same cases in tests/test_markup.py. These
// expectations are duplicated deliberately: if the two implementations drift,
// a document's status bar and its stored metadata will disagree, and a change
// to either regex would otherwise pass unnoticed in the other language.
const SHARED_CASES = [
  ['', 0],
  ['one two three', 3],
  ["don't stop", 2],
  ['It\u2019s fine', 2],
  ['under_score counts as one', 4],
  ['punctuation, everywhere!  really?', 3],
  ['123 456', 2],
  ['  spaced   out  ', 2],
  ['\u4e2d\u6587 \u6587\u5b57', 2],
  ['caf\u00e9 na\u00efve', 2],
];

for (const [text, expected] of SHARED_CASES) {
  test(`countWords agrees with Python for ${JSON.stringify(text)}`, () => {
    assert.equal(countWords(text), expected);
  });
}

test('a wholly non-Latin document is not counted as zero words', () => {
  // The bug this guards: JavaScript's \w is ASCII-only, so a CJK document
  // reported 0 words while the Python side counted 2.
  assert.ok(countWords('\u4e2d\u6587\u6587\u5b57\u6bb5\u843d') > 0);
});
