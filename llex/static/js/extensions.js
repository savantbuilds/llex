/**
 * TipTap extensions that give the document its page model.
 *
 * The editor's content is a sequence of `page` nodes rather than a flat block
 * list. That is what lets page breaks be part of the document -- saved, undone
 * and exported -- instead of being a purely visual effect.
 */

import { Node, Mark, mergeAttributes } from '@tiptap/core';

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

/** The document root: an unbroken sequence of pages. */
export const PagedDocument = (Document) =>
  Document.extend({
    content: 'page+',
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
