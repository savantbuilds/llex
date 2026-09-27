/**
 * TipTap extensions that give the document its page model.
 *
 * The editor's content is a sequence of `page` nodes rather than a flat block
 * list. That is what lets page breaks be part of the document -- saved, undone
 * and exported -- instead of being a purely visual effect.
 */

import { Extension, Node, Mark, mergeAttributes } from '@tiptap/core';
import Paragraph from '@tiptap/extension-paragraph';
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

/**
 * Direction handling for block nodes.
 *
 * `dir="auto"` makes each block take its direction from its own text, which is
 * what a bilingual document needs: an Arabic heading inside an English document
 * has to lay out right-to-left on its own, and a document-level `dir` could not
 * express that. The browser derives the direction from the first strong
 * character, so an empty or purely numeric block stays with the surrounding text
 * rather than guessing.
 *
 * Exported because paragraphs need it too, and they come from StarterKit.
 */
export const DIRECTION = { dir: 'auto' };

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
    return [
      `h${level}`,
      mergeAttributes(this.options.HTMLAttributes, rest, DIRECTION),
      0,
    ];
  },
});

/**
 * Block indentation, as an attribute on the paragraph.
 *
 * TipTap's own indent extension is not a dependency, and a paragraph attribute
 * is the better fit here anyway: the indent has to survive a save/load round
 * trip as a number the backend can read, and it has to be measured by the
 * paginator as part of the block's own width rather than applied as a wrapper.
 *
 * Stored as a level rather than a length so the same document looks the same at
 * any zoom and on any paper size, and so the HTML stays readable.
 */
export const MAX_INDENT_LEVEL = 8;

/** Inches of indent per level, matching Word's default tab geometry. */
export const INDENT_INCHES = 0.5;

export const IndentableParagraph = Paragraph.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      indent: {
        default: 0,
        parseHTML: (element) => {
          const raw = element.getAttribute('data-indent');
          const level = Number.parseInt(raw ?? '', 10);
          return Number.isFinite(level) ? clampIndent(level) : 0;
        },
        // Only a non-zero indent is written, so the common case adds no
        // attribute noise and a file diff is not littered with `data-indent="0"`.
        renderHTML: (attributes) =>
          attributes.indent ? { 'data-indent': String(attributes.indent) } : {},
      },
      breakBefore: {
        default: false,
        parseHTML: (element) => element.getAttribute('data-page-break-before') === 'true',
        renderHTML: (attributes) =>
          attributes.breakBefore ? { 'data-page-break-before': 'true' } : {},
      },
    };
  },
});

/** Keep an indent level inside the range the stylesheet has rules for. */
export function clampIndent(level) {
  const value = Number.parseInt(String(level), 10);
  if (!Number.isFinite(value)) return 0;
  return Math.min(MAX_INDENT_LEVEL, Math.max(0, value));
}

/** An image, with the geometry a paginating editor needs before it loads.
 *
 * The width and height are stored explicitly rather than left to the browser.
 * That is not a convenience: the paginator decides where pages break by measuring
 * rendered height, so an image whose size is unknown until it decodes reports
 * zero height, the page it sits on measures as fitting, and the break lands in
 * the wrong place. Once the image arrives the page grows and every break below
 * it is wrong. Storing the size lets the space be reserved on the first paint,
 * and `aspect-ratio` holds it even before the bytes arrive.
 *
 * `float` is the text wrapping. `none` is a block of its own; `left` and `right`
 * let the following text flow beside it, which is what a Word processor does for
 * an inline picture.
 */
export const Image = Node.create({
  name: 'image',
  group: 'block',
  atom: true,
  draggable: true,
  selectable: true,
  defining: true,

  addAttributes() {
    return {
      src: { default: '', parseHTML: (el) => el.getAttribute('src') || '', renderHTML: (a) => ({ src: a.src }) },
      alt: { default: '', parseHTML: (el) => el.getAttribute('alt') || '', renderHTML: (a) => ({ alt: a.alt }) },
      title: { default: null, parseHTML: (el) => el.getAttribute('title'), renderHTML: (a) => (a.title ? { title: a.title } : {}) },
      width: { default: null, parseHTML: (el) => Number.parseInt(el.getAttribute('width') || '', 10) || null, renderHTML: (a) => (a.width ? { width: String(a.width) } : {}) },
      height: { default: null, parseHTML: (el) => Number.parseInt(el.getAttribute('height') || '', 10) || null, renderHTML: (a) => (a.height ? { height: String(a.height) } : {}) },
      /** 'none' | 'left' | 'right' -- which side the text wraps to. */
      float: {
        default: 'none',
        parseHTML: (el) => {
          const value = el.getAttribute('data-float');
          return value === 'left' || value === 'right' ? value : 'none';
        },
        renderHTML: (a) => (a.float && a.float !== 'none' ? { 'data-float': a.float } : {}),
      },
      /** 'none' | 'block' -- whether the image sits on the text baseline. */
      display: {
        default: 'block',
        parseHTML: (el) => (el.getAttribute('data-display') === 'inline' ? 'inline' : 'block'),
        renderHTML: (a) => (a.display === 'inline' ? { 'data-display': 'inline' } : {}),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'img[src]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['img', mergeAttributes(this.options.HTMLAttributes, HTMLAttributes)];
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

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

/**
 * Block indentation, as a chainable command.
 *
 * Level-based rather than length-based so the same document indents the same way
 * at any zoom and on any paper size, and so the stored HTML stays a small
 * integer. Clamped rather than allowed to run away, because a stylesheet can only
 * describe so many levels.
 */
export const Indent = Extension.create({
  name: 'indentCommands',

  addCommands() {
    // TipTap calls a command as `command(...args)(props)`, so each entry has to
    // be a function that *returns* the props function. Returning the props
    // function directly looks equivalent and is not: TipTap calls it with no
    // arguments, the destructuring gets `undefined`, and the throw happens deep
    // inside a dispatch rather than failing cleanly.
    //
    // The current value comes from `editor.getAttributes`, not from
    // `commands.getAttributes`: the first is an Editor method and the second is
    // not a registered command, so the latter is `undefined`.
    const step = (delta) => () => ({ editor, commands }) => {
      const current = editor.getAttributes('paragraph').indent ?? 0;
      return commands.updateAttributes('paragraph', { indent: clampIndent(current + delta) });
    };
    return {
      increaseIndent: step(1),
      decreaseIndent: step(-1),
      setIndent:
        (level) =>
        ({ commands }) =>
          commands.updateAttributes('paragraph', { indent: clampIndent(level) }),
    };
  },
});

/**
 * Image insertion and geometry.
 *
 * `setImage` is a command rather than a direct `insertContent` so the panel and a
 * paste handler can share it, and so the size attributes are always set the same
 * way. A width and height are required: without them the block measures as empty
 * until the file decodes, and the paginator places the page break wrongly.
 */
export const ImageCommands = Extension.create({
  name: 'imageCommands',

  addCommands() {
    return {
      setImage:
        (options) =>
        ({ commands }) => {
          const { src, width, height, ...rest } = options || {};
          if (!src) return false;
          return commands.insertContent({
            type: 'image',
            attrs: { src, width: width || null, height: height || null, ...rest },
          });
        },
      setImageSize:
        (attributes) =>
        ({ commands }) =>
          commands.updateAttributes('image', attributes),
      setImageFloat:
        (value) =>
        ({ commands }) =>
          commands.updateAttributes('image', { float: value }),
    };
  },
});

/** A forced page break before this block. */
export const PageBreak = Extension.create({
  name: 'pageBreakCommand',

  addCommands() {
    return {
      setPageBreakBefore:
        (value = true) =>
        ({ commands }) =>
          commands.updateAttributes('paragraph', { breakBefore: Boolean(value) }),
    };
  },
});