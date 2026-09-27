/**
 * TipTap extensions that give the document its page model.
 *
 * The editor's content is a sequence of `page` nodes rather than a flat block
 * list. That is what lets page breaks be part of the document -- saved, undone
 * and exported -- instead of being a purely visual effect.
 */

import { Node, Mark, mergeAttributes } from '@tiptap/core';
import { TextStyleKit } from '@tiptap/extension-text-style';

/**
 * An inline prompt attached to a highlighted span.
 *
 * The `instruction` is the user's note ("expand this", "cite a source"); the
 * surrounding text is what gets replaced when the batch is executed.
 */
export const Scaffold = Mark.create({
  name: 'scaffold',

  // A scaffold should not survive a split, or one prompt would be duplicated
  // across two fragments and executed twice.
  keepOnSplit: false,

  addAttributes() {
    return {
      id: { default: null },
      instruction: { default: '' },
    };
  },

  parseHTML() {
    return [{ tag: 'span[data-scaffold]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'span',
      mergeAttributes(HTMLAttributes, {
        'data-scaffold': '',
        'data-scaffold-id': HTMLAttributes.id,
        'data-scaffold-instruction': HTMLAttributes.instruction,
      }),
      0,
    ];
  },
});

/**
 * Character formatting, as a mark on the selection rather than a property of
 * the block.
 *
 * Word and LibreOffice both distinguish block properties (alignment, indents)
 * from character properties (font, size, colour). `TextStyleKit` provides the
 * single `textStyle` mark carrying fontFamily, fontSize, color, backgroundColor
 * and lineHeight.
 *
 * Registering this matters more than it looks: without it, `setMark('textStyle',
 * ...)` reaches for a mark that does not exist in the schema and throws, and
 * applying a font has to be faked by rewriting the enclosing paragraph, which
 * reformats text the user did not select.
 */
export const CharacterStyle = TextStyleKit;

/** Heading levels 1-6. */
export const Heading = Node.create({
  name: 'heading',
  group: 'block',
  content: 'inline*',
  defining: true,
  addOptions() {
    return { levels: [1, 2, 3, 4, 5, 6] };
  },
  addAttributes() {
    return {
      level: {
        default: 1,
        parseHTML: (element) => Number(element.tagName.slice(1)) || 1,
        renderHTML: (attributes) => ({ level: attributes.level }),
      },
      // Typographic constraint: a heading must not be the last thing on a page.
      // The paginator reads this to keep a heading with the text it introduces.
      // Only an explicit opt-out is serialised, so the common case adds no
      // attribute noise to the document.
      keepWithNext: {
        default: true,
        parseHTML: (element) => element.getAttribute('data-keep-with-next') !== 'false',
        renderHTML: (attributes) =>
          attributes.keepWithNext ? {} : { 'data-keep-with-next': 'false' },
      },
    };
  },
  parseHTML() {
    return this.options.levels.map((level) => ({ tag: `h${level}`, priority: 51 - level }));
  },
  renderHTML({ node, HTMLAttributes }) {
    const level = Math.min(Math.max(node.attrs.level || 1, 1), 6);
    // `level` is carried by the tag name; emitting it as an attribute as well
    // would put `level="1"` in the HTML for no reader to use.
    const { level: _level, ...rest } = HTMLAttributes;
    return [`h${level}`, mergeAttributes(this.options.HTMLAttributes, rest), 0];
  },
});

/** A single physical page. Always has at least one block. */
export const Page = Node.create({
  name: 'page',
  group: 'page',
  content: 'block+',
  defining: true,

  parseHTML() {
    return [{ tag: 'div.page' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { class: 'page' }), 0];
  },
});

/**
 * Allocate an id for a new scaffold.
 *
 * Ids only have to be unique within one document, and only for as long as the
 * scaffold exists. A monotonic counter beats `Date.now()`, which collides when
 * two scaffolds are added in the same millisecond.
 */
let scaffoldCounter = 0;

export function nextScaffoldId() {
  scaffoldCounter += 1;
  return `s${scaffoldCounter.toString(36)}`;
}
