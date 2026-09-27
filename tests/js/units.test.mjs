/**
 * Unit tests for the settings, API-error and font-size helpers.
 *
 * Each is a small pure function that would otherwise only be exercised by
 * clicking around in a webview, which is exactly the code most likely to rot.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { clampMargin } from '../../llex/static/js/settings.js';
import { ApiError } from '../../llex/static/js/api.js';
import { stepFontSize, styleForHeading } from '../../llex/static/js/ribbon.js';
import { ZOOM_LEVELS } from '../../llex/static/js/menus.js';
import { OVERFLOW_TOLERANCE } from '../../llex/static/js/pagination.js';

// --------------------------------------------------------------------------
// clampMargin
// --------------------------------------------------------------------------

test('clampMargin keeps sensible values unchanged', () => {
  assert.equal(clampMargin(1), 1);
  assert.equal(clampMargin(2.5), 2.5);
});

test('clampMargin rejects values that would leave no printable area', () => {
  assert.equal(clampMargin(0), 0.25);
  assert.equal(clampMargin(-3), 0.25);
  assert.equal(clampMargin(99), 4);
});

test('clampMargin falls back to 1 for unparseable input', () => {
  assert.equal(clampMargin('abc'), 1);
  assert.equal(clampMargin(''), 1);
  assert.equal(clampMargin(NaN), 1);
  assert.equal(clampMargin(null), 1);
});

test('clampMargin respects the page width it is given', () => {
  // A narrow page cannot carry a 4-inch margin on each side.
  assert.ok(clampMargin(4, 5) < 4);
});

// --------------------------------------------------------------------------
// ApiError classification
// --------------------------------------------------------------------------

test('ApiError carries its status and detail', () => {
  const error = new ApiError('boom', 500, 'detail text');
  assert.equal(error.message, 'boom');
  assert.equal(error.status, 500);
  assert.equal(error.detail, 'detail text');
  assert.equal(error.name, 'ApiError');
});

test('ApiError recognises an offline backend', () => {
  assert.equal(new ApiError('no model', 503).isOffline, true);
  assert.equal(new ApiError('bad request', 422).isOffline, false);
});

test('ApiError recognises a validation failure', () => {
  assert.equal(new ApiError('bad', 422).isValidation, true);
  assert.equal(new ApiError('bad', 400).isValidation, true);
  assert.equal(new ApiError('other', 500).isValidation, false);
});

test('a network failure is distinguishable from an HTTP error', () => {
  // Status 0 means the request never reached the server at all.
  const error = new ApiError('unreachable', 0);
  assert.equal(error.status, 0);
  assert.equal(error.isOffline, false);
  assert.equal(error.isValidation, false);
});

// --------------------------------------------------------------------------
// stepFontSize
// --------------------------------------------------------------------------

test('stepFontSize advances and retreats along the scale', () => {
  assert.equal(stepFontSize(11, 1), 12);
  assert.equal(stepFontSize(12, 1), 14);
  assert.equal(stepFontSize(14, -1), 12);
});

test('stepFontSize clamps at both ends', () => {
  assert.equal(stepFontSize(8, -1), 8);
  assert.equal(stepFontSize(36, 1), 36);
});

test('stepFontSize snaps an off-scale size to the nearest step', () => {
  assert.equal(stepFontSize(13, 1), 14);
  assert.equal(stepFontSize(13, -1), 12);
  assert.equal(stepFontSize(999, 1), 36);
  assert.equal(stepFontSize(1, -1), 8);
});

test('stepFontSize accepts string sizes from the dropdown', () => {
  assert.equal(stepFontSize('11pt', 1), 12);
  assert.equal(stepFontSize('12pt', -1), 11);
});

test('stepFontSize handles nonsense input without throwing', () => {
  assert.equal(typeof stepFontSize('abc', 1), 'number');
  assert.equal(typeof stepFontSize(undefined, -1), 'number');
});

// --------------------------------------------------------------------------
// styleForHeading
// --------------------------------------------------------------------------

test('styleForHeading reports body text when nothing is a heading', () => {
  const editor = { isActive: () => false };
  assert.equal(styleForHeading(editor), 'p');
});

test('styleForHeading reports the active heading level', () => {
  const editor = { isActive: (type, attrs) => attrs && attrs.level === 3 };
  assert.equal(styleForHeading(editor), 'h3');
});

test('styleForHeading prefers the outermost active level', () => {
  const editor = { isActive: (type, attrs) => attrs && attrs.level <= 2 };
  assert.equal(styleForHeading(editor), 'h1');
});

// --------------------------------------------------------------------------
// Constants
// --------------------------------------------------------------------------

test('ZOOM_LEVELS is sorted and includes 100%', () => {
  assert.ok(Array.isArray(ZOOM_LEVELS));
  assert.ok(ZOOM_LEVELS.includes(100));
  for (let i = 1; i < ZOOM_LEVELS.length; i += 1) {
    assert.ok(ZOOM_LEVELS[i] > ZOOM_LEVELS[i - 1], 'zoom levels must ascend');
  }
});

test('zoom levels stay within a usable range', () => {
  assert.ok(Math.min(...ZOOM_LEVELS) >= 50);
  assert.ok(Math.max(...ZOOM_LEVELS) <= 200);
});

test('OVERFLOW_TOLERANCE is exported for the CSS contract', () => {
  assert.equal(typeof OVERFLOW_TOLERANCE, 'number');
});
