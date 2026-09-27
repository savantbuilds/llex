/**
 * Limits that must agree between the editor and the paginator.
 *
 * Kept in their own module because both sides need them and neither should import
 * the other: `images.js` needs the maximum width to clamp an insert against, and
 * `pagination.js` needs the printable width to measure against. A circular import
 * between those two would be a bundler warning at best and an undefined value at
 * worst.
 */

/** Printable width as a fraction of the page width, from the page geometry. */
export const PRINTABLE_WIDTH_FRACTION = 1;

/**
 * How far a single image may exceed the printable width, as a multiple.
 *
 * Never below 1: an image wider than the text column is unreadable and, because
 * the page clips its overflow, partly unreachable.
 */
export const MAX_IMAGE_WIDTH_INCHES = 1;

/** Printable width in CSS pixels, given a page width and its margins. */
export function printableWidth(pageWidthPx, margins) {
  const horizontal = (margins?.left ?? 0) + (margins?.right ?? 0);
  return Math.max(1, pageWidthPx - horizontal);
}
