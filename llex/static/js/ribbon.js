/**
 * The ribbon toolbar: formatting commands and active-state reflection.
 *
 * The toolbar is a projection of editor state, never a second source of truth.
 * Every button dispatches a ProseMirror command and every highlight is derived
 * from `editor.isActive`, so the two cannot disagree.
 */

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
 * @returns {{sync: () => void}}
 */
export function createRibbon(editor) {
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
  ];

  const buttons = new Map(commands.map(([id, command]) => [id, bind(id, command)]));

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

  return { sync, stepFontSize, applyFontFamily, applyFontSize };
}
