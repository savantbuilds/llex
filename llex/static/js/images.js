/**
 * Image insertion, sizing and text wrapping.
 *
 * ## The problem that shapes everything here
 *
 * LLex decides where pages break by measuring rendered layout: a page whose
 * content is taller than its printable area sheds blocks onto the next one. An
 * image therefore has to have a *known height from the first paint*, or the page
 * it sits on measures as fitting when it is not, the break lands in the wrong
 * place, and every break below it is wrong too. Once the bytes arrive the page
 * grows and the layout is wrong until the next edit happens to fix it.
 *
 * So an inserted image is read before it is placed. The browser decodes it, the
 * natural dimensions and aspect ratio are captured, and the node is created with
 * explicit `width` and `height`. The space is then reserved on the first paint
 * and `aspect-ratio` holds it even while the file is still in flight. A document
 * that has been saved and reloaded has those numbers in its HTML, so it reopens
 * already correct.
 *
 * ## Units
 *
 * Sizes are stored in **CSS pixels** and rendered as such, because that is the
 * unit the paginator measures in and the unit the stylesheet already uses for
 * page geometry. Storing inches would mean a conversion at every measurement,
 * and a rounding error there shows up as a page that is a pixel too tall.
 *
 * ## Wrapping
 *
 * `float: none` is a block on its own line, which is what a "picture" usually
 * is in a document. `left` and `right` float it and let the following text flow
 * beside it. `inline` sits on the text baseline, the way an inline picture does.
 * Every one of these changes the height of the page it is on, so every command
 * here asks the paginator to re-measure rather than assuming the edit did it.
 */

import { MAX_IMAGE_WIDTH_INCHES } from './image-limits.js';

/** Aspect ratios outside this range are almost certainly a decode failure. */
const MIN_ASPECT = 0.02;
const MAX_ASPECT = 50;

/** The only schemes an image may use. */
const SAFE_SCHEME = /^data:image\/(png|jpe?g|gif|webp|bmp|svg\+xml);base64,/i;
const REMOTE_SCHEME = /^https?:\/\//i;

/**
 * Whether a URL is one this editor will put in a document.
 *
 * `javascript:` is the reason this exists. An image source is a URL the editor
 * will later render, and a document is a file the user opens, so a crafted `.llex`
 * carrying `javascript:` in an `img src` is a script injection into the editor
 * itself. Only data URIs for real image types and plain http(s) are accepted.
 *
 * @param {string} src
 * @returns {boolean}
 */
export function isSafeImageSource(src) {
  if (typeof src !== 'string') return false;
  const value = src.trim();
  if (!value) return false;
  return SAFE_SCHEME.test(value) || REMOTE_SCHEME.test(value);
}

/**
 * Clamp a requested width to what the page can actually show.
 *
 * An image wider than the printable area cannot be read, and cannot be selected
 * either once it is clipped by the page's `overflow: hidden` — the same
 * invisible-data problem an overfull page has.
 *
 * @param {number} width Desired width in CSS pixels.
 * @param {number} pageWidth Printable width in CSS pixels.
 * @returns {number}
 */
export function clampImageWidth(width, pageWidth) {
  const limit = Math.max(1, Math.min(pageWidth, pageWidth * MAX_IMAGE_WIDTH_INCHES));
  if (!Number.isFinite(width) || width <= 0) return limit;
  return Math.min(width, limit);
}

/**
 * The size an image should be inserted at.
 *
 * Scales to fit the printable width if it overflows, and otherwise inserts at
 * its natural size. Upscaling is never done: a 120-pixel image blown up to fill
 * a page is a blurry page, and a word processor does not do it either.
 *
 * @param {{width: number, height: number}} natural Decoded pixel dimensions.
 * @param {number} pageWidth Printable width in CSS pixels.
 * @returns {{width: number, height: number}}
 */
export function fitImage(natural, pageWidth) {
  const width = natural.width;
  const height = natural.height;
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    // Undecodable: fall back to a placeholder box rather than inserting nothing,
    // so the user sees where the image was meant to go.
    return { width: Math.round(Math.min(320, pageWidth)), height: 180 };
  }
  const fitted = clampImageWidth(width, pageWidth);
  return {
    width: Math.round(fitted),
    height: Math.round((height / width) * fitted),
  };
}

/**
 * Resize an image, preserving its aspect ratio.
 *
 * @param {{width: number, height: number}} current Current size in pixels.
 * @param {number} width Desired new width in pixels.
 * @param {number} [pageWidth] Printable width, used to clamp.
 * @returns {{width: number, height: number}} The size actually applied.
 */
export function resizeImage(current, width, pageWidth) {
  if (!Number.isFinite(current.width) || current.width <= 0) return current;
  const ratio = current.height / current.width;
  const fitted = clampImageWidth(width, pageWidth ?? current.width);
  return { width: Math.round(fitted), height: Math.max(1, Math.round(fitted * ratio)) };
}

/**
 * Read a `File` into a data URI and its pixel dimensions.
 *
 * The dimensions come from decoding rather than from the file header, so a file
 * that lies about its size -- or a format the browser decodes to something else
 * -- is measured as what it actually is.
 *
 * @param {File} file
 * @returns {Promise<{src: string, width: number, height: number, type: string}>}
 */
export function readImageFile(file) {
  return new Promise((resolve, reject) => {
    if (!file || !/^image\//i.test(file.type || '')) {
      reject(new Error(`${file && file.name ? file.name : 'that file'} is not an image`));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`could not read ${file.name}`));
    reader.onload = () => {
      const src = String(reader.result || '');
      decodeImage(src)
        .then(({ width, height }) => resolve({ src, width, height, type: file.type }))
        // A file the browser cannot decode is still worth placing, as a box of
        // the right size, rather than refusing and leaving the user guessing.
        .catch(() => resolve({ src, width: 0, height: 0, type: file.type }));
    };
    reader.readAsDataURL(file);
  });
}

/**
 * Measure a source's pixel dimensions by decoding it.
 *
 * @param {string} src
 * @returns {Promise<{width: number, height: number}>}
 */
export function decodeImage(src) {
  return new Promise((resolve, reject) => {
    if (!isSafeImageSource(src)) {
      reject(new Error('that image address is not allowed'));
      return;
    }
    const image = new Image();
    image.onload = () => {
      const ratio = image.naturalWidth / Math.max(1, image.naturalHeight);
      if (!Number.isFinite(ratio) || ratio < MIN_ASPECT || ratio > MAX_ASPECT) {
        reject(new Error('that image has an implausible shape'));
        return;
      }
      resolve({ width: image.naturalWidth, height: image.naturalHeight });
    };
    image.onerror = () => reject(new Error('that image could not be decoded'));
    image.src = src;
  });
}

/**
 * Read the size and wrapping of the image the selection is inside.
 *
 * @param {import('@tiptap/core').Editor} editor
 * @returns {{src: string, alt: string, width: number|null, height: number|null, float: string, display: string}|null}
 */
export function readImageAttributes(editor) {
  const attributes = editor.getAttributes('image');
  if (!attributes || !attributes.src) return null;
  return {
    src: attributes.src,
    alt: attributes.alt || '',
    width: attributes.width ?? null,
    height: attributes.height ?? null,
    float: attributes.float || 'none',
    display: attributes.display || 'block',
  };
}

/** Wrapping modes offered in the UI, in the order they are offered. */
export const WRAP_MODES = [
  { value: 'none', label: 'In line with text', hint: 'A block of its own' },
  { value: 'left', label: 'Wrap left', hint: 'Text flows down the right' },
  { value: 'right', label: 'Wrap right', hint: 'Text flows down the left' },
  { value: 'inline', label: 'With text', hint: 'Sits on the text baseline' },
];

/** Widths offered in the UI, as fractions of the printable width. */
export const SIZE_PRESETS = [
  { label: 'Quarter', fraction: 0.25 },
  { label: 'Half', fraction: 0.5 },
  { label: 'Three quarters', fraction: 0.75 },
  { label: 'Full', fraction: 1 },
];

/**
 * A readable description of an image, for the alternative text box.
 *
 * @param {{alt: string, width: number|null, height: number|null, float: string}} image
 * @returns {string}
 */
export function describeImage(image) {
  if (image.alt) return image.alt;
  const size = image.width && image.height ? `${image.width} by ${image.height} pixels, ` : '';
  const placement = image.float && image.float !== 'none' ? `wrapped ${image.float}` : 'inline';
  return `Image, ${size}${placement}`;
}
