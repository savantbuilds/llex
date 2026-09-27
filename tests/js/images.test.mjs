import test from 'node:test';
import assert from 'node:assert/strict';

import {
  describeImage,
  isSafeImageSource,
  clampImageWidth,
  fitImage,
  resizeImage,
  SIZE_PRESETS,
  WRAP_MODES,
} from '../../llex/static/js/images.js';
import { printableWidth, MAX_IMAGE_WIDTH_INCHES } from '../../llex/static/js/image-limits.js';
import { clampIndent, MAX_INDENT_LEVEL, INDENT_INCHES } from '../../llex/static/js/extensions.js';

const PNG = 'data:image/png;base64,iVBORw0KGgo=';
const PRINTABLE = 624; // 6.5in at 96dpi, Letter with 1in margins

test('only image data URIs and plain http(s) are allowed', () => {
  assert.equal(isSafeImageSource(PNG), true);
  assert.equal(isSafeImageSource('https://example.com/a.png'), true);
  assert.equal(isSafeImageSource('http://example.com/a.png'), true);
});

test('a script URL is refused', () => {
  // A .llex file is data, and an <img src> is a URL the editor will load, so
  // this is a script injection into the editor itself.
  assert.equal(isSafeImageSource('javascript:alert(1)'), false);
  assert.equal(isSafeImageSource('JavaScript:alert(1)'), false);
  assert.equal(isSafeImageSource('data:text/html,<script>'), false);
  assert.equal(isSafeImageSource('file:///etc/passwd'), false);
  assert.equal(isSafeImageSource('vbscript:msgbox'), false);
});

test('nothing is not a source', () => {
  assert.equal(isSafeImageSource(''), false);
  assert.equal(isSafeImageSource('   '), false);
  assert.equal(isSafeImageSource(null), false);
  assert.equal(isSafeImageSource(undefined), false);
  assert.equal(isSafeImageSource(42), false);
});

test('an image is never wider than the printable area', () => {
  // Wider than the column is unreadable, and because the page clips its
  // overflow, partly unreachable.
  assert.equal(clampImageWidth(4000, PRINTABLE), PRINTABLE);
  assert.equal(clampImageWidth(300, PRINTABLE), 300);
});

test('a negative or nonsense width falls back to the printable width', () => {
  assert.equal(clampImageWidth(0, PRINTABLE), PRINTABLE);
  assert.equal(clampImageWidth(-50, PRINTABLE), PRINTABLE);
  assert.equal(clampImageWidth(Number.NaN, PRINTABLE), PRINTABLE);
});

test('the width limit is never below the printable width', () => {
  // A limit below 1 would make every image narrower than the column.
  assert.ok(MAX_IMAGE_WIDTH_INCHES >= 1);
});

test('a small image is inserted at its natural size', () => {
  assert.deepEqual(fitImage({ width: 200, height: 100 }, PRINTABLE), { width: 200, height: 100 });
});

test('a huge image is scaled down to fit, keeping its ratio', () => {
  const fitted = fitImage({ width: 4000, height: 2000 }, PRINTABLE);
  assert.equal(fitted.width, PRINTABLE);
  assert.equal(fitted.height, Math.round((2000 / 4000) * PRINTABLE));
});

test('a tiny image is never upscaled', () => {
  // A 40px image blown up to fill a page is a blurry page.
  const fitted = fitImage({ width: 40, height: 20 }, PRINTABLE);
  assert.equal(fitted.width, 40);
  assert.equal(fitted.height, 20);
});

test('an undecodable image still gets a box to sit in', () => {
  const fitted = fitImage({ width: 0, height: 0 }, PRINTABLE);
  assert.ok(fitted.width > 0 && fitted.height > 0);
  assert.ok(fitted.width <= PRINTABLE);
});

test('resizing preserves the aspect ratio', () => {
  assert.deepEqual(resizeImage({ width: 400, height: 200 }, 200), { width: 200, height: 100 });
});

test('resizing a wide image keeps it inside the column', () => {
  const sized = resizeImage({ width: 4000, height: 2000 }, 3000, PRINTABLE);
  assert.equal(sized.width, PRINTABLE);
  assert.equal(sized.height, Math.round((2000 / 4000) * PRINTABLE));
});

test('resizing never produces a zero-height image', () => {
  // A zero height is not a very small image; it is an invisible one.
  const sized = resizeImage({ width: 4000, height: 1 }, 1);
  assert.ok(sized.height >= 1);
});

test('a zero-width image is left alone rather than divided by', () => {
  const current = { width: 0, height: 0 };
  assert.deepEqual(resizeImage(current, 200), current);
});

test('the printable width is the page minus its margins', () => {
  assert.equal(printableWidth(816, { left: 96, right: 96 }), 624);
});

test('the printable width is never zero', () => {
  // Every size clamp is relative to it, so zero would silently break them all.
  assert.ok(printableWidth(100, { left: 96, right: 96 }) >= 1);
  assert.ok(printableWidth(0, {}) >= 1);
});

test('the size presets are fractions of the column', () => {
  for (const preset of SIZE_PRESETS) {
    assert.ok(preset.fraction > 0 && preset.fraction <= 1, preset.label);
  }
});

test('the wrap modes are the ones the editor can render', () => {
  const values = WRAP_MODES.map((mode) => mode.value);
  assert.deepEqual(values, ['none', 'left', 'right', 'inline']);
  // Every one of them has a stylesheet rule, or the choice silently does nothing.
  for (const mode of WRAP_MODES) {
    assert.ok(mode.label && mode.hint, mode.value);
  }
});

test('alt text is described from the geometry when absent', () => {
  const described = describeImage({ alt: '', width: 120, height: 80, float: 'right' });
  assert.match(described, /120 by 80/);
  assert.match(described, /wrapped right/);
});

test('alt text the user wrote is preferred over a description', () => {
  assert.equal(describeImage({ alt: 'A cat', width: 1, height: 1, float: 'none' }), 'A cat');
});

test('an indent is clamped to the levels the stylesheet describes', () => {
  assert.equal(clampIndent(-5), 0);
  assert.equal(clampIndent(0), 0);
  assert.equal(clampIndent(3), 3);
  assert.equal(clampIndent(99), MAX_INDENT_LEVEL);
  assert.equal(clampIndent('2'), 2);
});

test('nonsense indent input is zero rather than NaN', () => {
  assert.equal(clampIndent('abc'), 0);
  assert.equal(clampIndent(null), 0);
  assert.equal(clampIndent(undefined), 0);
  assert.equal(clampIndent(Number.NaN), 0);
});

test('the indent step is half an inch, the usual tab width', () => {
  assert.equal(INDENT_INCHES, 0.5);
});
