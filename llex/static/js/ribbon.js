/**
 * The ribbon toolbar: formatting commands and active-state reflection.
 *
 * The toolbar is a projection of editor state, never a second source of truth.
 * Every button dispatches a ProseMirror command and every highlight is derived
 * from `editor.isActive`, so the two cannot disagree.
 */

import { MAX_INDENT_LEVEL } from './extensions.js';

const FONT_SIZES = [8, 10, 11, 12, 14, 18, 24, 36];

/** Shown when the selection carries no explicit character formatting. */
const FONT_FAMILY_DEFAULT = 'Calibri';
const FONT_SIZE_DEFAULT = '11pt';

/**
 * Move a font size to the next or previous step on the scale.
 *
 * Accepts the `pt`-suffixed values the size dropdown uses as well as bare
 * numbers. A size that is not on the scale is first snapped to the nearest step
 * in the requested direction, rather than jumping to an extreme.
 *
 * @param {number|string} current
 * @param {1|-1} direction
 * @returns {number}
 */
export function stepFontSize(current, direction) {
  const points = Number.parseFloat(String(current));
  if (!Number.isFinite(points)) return FONT_SIZES[0];

  const index = FONT_SIZES.indexOf(points);
  if (index !== -1) {
    const next = Math.min(FONT_SIZES.length - 1, Math.max(0, index + direction));
    return FONT_SIZES[next];
  }

  // Off the scale: find the first step in the direction of travel.
  const ordered = direction > 0 ? FONT_SIZES : [...FONT_SIZES].reverse();
  const nearest = ordered.find((size) => (direction > 0 ? size > points : size < points));
  if (nearest !== undefined) return nearest;
  return direction > 0 ? FONT_SIZES[FONT_SIZES.length - 1] : FONT_SIZES[0];
}

/** The style id for a heading level, or `null` for body text. */
export function styleForHeading(editor) {
  for (let level = 1; level <= 6; level += 1) {
    if (editor.isActive('heading', { level })) return `h${level}`;
  }
  return 'p';
}

/**
 * Attach the ribbon.
 * @param {import('@tiptap/core').Editor} editor
 * @param {{onFind?: () => void, onImage?: () => void, onStatus?: (message: string) => void}} [hooks]
 * @returns {{sync: () => void}}
 */
export function createRibbon(editor, hooks = {}) {
  const bind = (id, command) => {
    const button = document.getElementById(id);
    if (button) button.addEventListener('click', command);
    return button;
  };

  const commands = [
    ['btn-undo', () => editor.chain().focus().undo().run()],
    ['btn-redo', () => editor.chain().focus().redo().run()],
    ['btn-bold', () => editor.chain().focus().toggleBold().run()],
    ['btn-italic', () => editor.chain().focus().toggleItalic().run()],
    ['btn-underline', () => editor.chain().focus().toggleUnderline().run()],
    ['btn-strikethrough', () => editor.chain().focus().toggleStrike().run()],
    ['btn-align-left', () => editor.chain().focus().setTextAlign('left').run()],
    ['btn-align-center', () => editor.chain().focus().setTextAlign('center').run()],
    ['btn-align-right', () => editor.chain().focus().setTextAlign('right').run()],
    ['btn-align-justify', () => editor.chain().focus().setTextAlign('justify').run()],
    ['btn-bullet', () => editor.chain().focus().toggleBulletList().run()],
    ['btn-number', () => editor.chain().focus().toggleOrderedList().run()],
    ['btn-indent-increase', () => editor.chain().focus().increaseIndent().run()],
    ['btn-indent-decrease', () => editor.chain().focus().decreaseIndent().run()],
    ['btn-horizontal-rule', () => editor.chain().focus().setHorizontalRule().run()],
    [
      'btn-clear-format',
      () => {
        // Character formatting is marks and block formatting is attributes, so
        // clearing has to do both. `unsetAllMarks` alone leaves a centred,
        // indented, highlighted paragraph behind, which is not what "clear
        // formatting" means in any word processor.
        editor
          .chain()
          .focus()
          .unsetAllMarks()
          .unsetMark('textStyle', { fontFamily: null })
          .unsetMark('textStyle', { fontSize: null })
          .setTextAlign('left')
          .setIndent(0)
          .run();
      },
    ],
    ['btn-page-break', () => editor.chain().focus().setPageBreakBefore(true).run()],
    ['btn-image', () => hooks.onImage && hooks.onImage()],
    ['btn-find', () => hooks.onFind && hooks.onFind()],
  ];

  const buttons = new Map(commands.map(([id, command]) => [id, bind(id, command)]));
  const status = hooks.onStatus || (() => {});

  // -- Link --------------------------------------------------------------- //

  bind('btn-link', () => toggleLink());
  const linkInput = document.getElementById('link-url');
  const linkApply = document.getElementById('link-apply');
  const linkRemove = document.getElementById('link-remove');
  const linkRow = document.getElementById('link-row');

  const setLinkRowVisible = (visible) => {
    if (linkRow) linkRow.hidden = !visible;
  };

  /**
   * Apply or remove a link on the selection.
   *
   * A bare address is assumed to be https, because typing `example.com` and
   * having it silently become a dead relative link is the most common way a
   * word processor's link box disappoints people.
   *
   * @param {string|null} href
   */
  function applyLink(href) {
    if (href === null) {
      editor.chain().focus().unsetLink().run();
      status('Link removed');
      return;
    }
    const trimmed = String(href).trim();
    if (!trimmed) return;
    const url = /^[a-z][a-z0-9+.-]*:/i.test(trimmed) ? trimmed : `https://${trimmed}`;
    editor.chain().focus().setLink({ href: url }).run();
    status(`Linked to ${url}`);
  }

  function toggleLink() {
    if (editor.isActive('link')) {
      applyLink(null);
      return;
    }
    setLinkRowVisible(true);
    if (linkInput) {
      linkInput.value = editor.getAttributes('link').href || '';
      linkInput.focus();
      linkInput.select();
    }
  }

  linkApply?.addEventListener('click', () => {
    applyLink(linkInput?.value || '');
    setLinkRowVisible(false);
  });
  linkRemove?.addEventListener('click', () => {
    applyLink(null);
    setLinkRowVisible(false);
  });
  linkInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      applyLink(linkInput.value);
      setLinkRowVisible(false);
    }
    if (event.key === 'Escape') setLinkRowVisible(false);
  });

  const styleDropdown = document.getElementById('style-dropdown');
  if (styleDropdown) {
    styleDropdown.addEventListener('change', (event) => {
      const value = event.target.value;
      if (value === 'p') {
        editor.chain().focus().setParagraph().run();
        return;
      }
      const level = Number.parseInt(value.slice(1), 10);
      if (Number.isInteger(level)) {
        editor.chain().focus().setHeading({ level }).run();
      }
    });
  }

  // Font family and size are character properties, so they go on the selection
  // as a mark. Rewriting the paragraph node instead -- which is what this did
  // first -- reformatted text the user had not selected, which is not what a
  // word processor's font box does.
  const fontFamily = document.getElementById('font-family');
  const fontSize = document.getElementById('font-size');

  const applyFontFamily = (value) => {
    const chain = editor.chain().focus();
    if (value) chain.setMark('textStyle', { fontFamily: value });
    else chain.unsetMark('textStyle', { fontFamily: null });
    chain.run();
  };

  const applyFontSize = (value) => {
    const chain = editor.chain().focus();
    if (value) chain.setMark('textStyle', { fontSize: value });
    else chain.unsetMark('textStyle', { fontSize: null });
    chain.run();
  };

  if (fontFamily) fontFamily.addEventListener('change', (event) => applyFontFamily(event.target.value));
  if (fontSize) fontSize.addEventListener('change', (event) => applyFontSize(event.target.value));

  // A toolbar can change the document without a selection changing, e.g. via a
  // menu command, so the ribbon also refreshes on every update.
  const markActive = (button, active) => {
    if (!button) return;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  };

  /** Reflect the current selection onto every control. */
  function sync() {
    markActive(buttons.get('btn-undo'), editor.can().undo());
    markActive(buttons.get('btn-redo'), editor.can().redo());
    markActive(buttons.get('btn-bold'), editor.isActive('bold'));
    markActive(buttons.get('btn-italic'), editor.isActive('italic'));
    markActive(buttons.get('btn-underline'), editor.isActive('underline'));
    markActive(buttons.get('btn-strikethrough'), editor.isActive('strike'));
    markActive(buttons.get('btn-align-left'), editor.isActive({ textAlign: 'left' }));
    markActive(buttons.get('btn-align-center'), editor.isActive({ textAlign: 'center' }));
    markActive(buttons.get('btn-align-right'), editor.isActive({ textAlign: 'right' }));
    markActive(buttons.get('btn-align-justify'), editor.isActive({ textAlign: 'justify' }));
    markActive(buttons.get('btn-bullet'), editor.isActive('bulletList'));
    markActive(buttons.get('btn-number'), editor.isActive('orderedList'));
    markActive(buttons.get('btn-link'), editor.isActive('link'));
    markActive(buttons.get('btn-page-break'), Boolean(editor.getAttributes('paragraph').breakBefore));
    // Indent has no "active" state in the usual sense -- it is a level, not a
    // toggle -- but the decrease button is useless at level zero, so it is
    // disabled there rather than silently doing nothing.
    const indent = Number(editor.getAttributes('paragraph').indent ?? 0);
    const decrease = buttons.get('btn-indent-decrease');
    if (decrease) decrease.disabled = indent <= 0;
    const increase = buttons.get('btn-indent-increase');
    if (increase) increase.disabled = indent >= MAX_INDENT_LEVEL;

    const undo = buttons.get('btn-undo');
    if (undo) undo.disabled = !editor.can().undo();
    const redo = buttons.get('btn-redo');
    if (redo) redo.disabled = !editor.can().redo();

    if (styleDropdown) styleDropdown.value = styleForHeading(editor);

    // Show the mark's value when the selection has one, falling back to the
    // document default rather than blanking the control.
    const character = editor.getAttributes('textStyle');
    if (fontFamily) {
      fontFamily.value = character.fontFamily || FONT_FAMILY_DEFAULT;
    }
    if (fontSize) {
      fontSize.value = character.fontSize || FONT_SIZE_DEFAULT;
    }
  }

  return { sync, stepFontSize, applyFontFamily, applyFontSize, applyLink, toggleLink };
}
