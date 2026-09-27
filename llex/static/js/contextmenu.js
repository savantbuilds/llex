/**
 * The right-click menu: a formatting mini-toolbar plus the standard edit menu.
 *
 * The two were previously shown at the same time at overlapping coordinates,
 * which meant the standard menu could cover the mini-toolbar and the click
 * handlers fought over the same `click` event.
 */

import { stepFontSize } from './ribbon.js';
import { Scaffold, nextScaffoldId } from './extensions.js';
import { flash } from './dom.js';

const MENU_MARGIN = 8;

/**
 * Keep a menu inside the viewport.
 * @param {HTMLElement} menu
 * @param {number} x
 * @param {number} y
 */
function place(menu, x, y) {
  menu.style.left = '0px';
  menu.style.top = '0px';
  const rect = menu.getBoundingClientRect();
  const maxLeft = window.innerWidth - rect.width - MENU_MARGIN;
  const maxTop = window.innerHeight - rect.height - MENU_MARGIN;
  menu.style.left = `${Math.max(MENU_MARGIN, Math.min(x, maxLeft))}px`;
  menu.style.top = `${Math.max(MENU_MARGIN, Math.min(y, maxTop))}px`;
}

/**
 * @param {import('@tiptap/core').Editor} editor
 * @param {{onScaffoldChange: () => void, status?: HTMLElement|null}} options
 */
export function createContextMenu(editor, options = {}) {
  const mini = document.getElementById('context-menu');
  const standard = document.getElementById('context-menu-standard');
  const status = options.status || null;

  if (!mini || !standard) return { close };

  function close() {
    mini.style.display = 'none';
    standard.style.display = 'none';
  }

  function isOpen() {
    return mini.style.display !== 'none' || standard.style.display !== 'none';
  }

  // -- Mini toolbar actions ---------------------------------------------- //

  const actions = {
    bold: () => editor.chain().focus().toggleBold().run(),
    italic: () => editor.chain().focus().toggleItalic().run(),
    underline: () => editor.chain().focus().toggleUnderline().run(),
    'bullet-list': () => editor.chain().focus().toggleBulletList().run(),
    'ordered-list': () => editor.chain().focus().toggleOrderedList().run(),
    'clear-format': () => editor.chain().focus().unsetAllMarks().clearNodes().run(),
    'increase-font': () => changeFontSize(1),
    'decrease-font': () => changeFontSize(-1),
    highlight: () => toggleHighlight(),
    'font-color': () => pickColor(),
    styles: () => cycleHeading(),
    'add-scaffold': () => addScaffold(),
  };

  function changeFontSize(direction) {
    const current = editor.getAttributes('paragraph').fontSize || '12pt';
    const points = Number.parseFloat(current) || 12;
    editor
      .chain()
      .focus()
      .setNode('paragraph', { fontSize: `${stepFontSize(points, direction)}pt` })
      .run();
  }

  function toggleHighlight() {
    const current = editor.getAttributes('textStyle').backgroundColor;
    editor
      .chain()
      .focus()
      .setMark('textStyle', { backgroundColor: current ? null : '#fff176' })
      .run();
  }

  function pickColor() {
    // A native colour input, so there is one colour source rather than a
    // hardcoded palette the user cannot extend.
    const picker = document.createElement('input');
    picker.type = 'color';
    picker.value = editor.getAttributes('textStyle').color || '#000000';
    picker.addEventListener('input', () => {
      editor.chain().focus().setMark('textStyle', { color: picker.value }).run();
    });
    picker.click();
  }

  function cycleHeading() {
    // Cycle body -> h3 -> h4 -> h5 -> h6 -> body, matching the outline levels
    // the document structure actually uses for sections.
    const order = [null, 3, 4, 5, 6];
    let index = 0;
    for (let i = 1; i <= 6; i += 1) {
      if (editor.isActive('heading', { level: i })) {
        index = i;
        break;
      }
    }
    const next = order[(order.indexOf(index === 0 ? null : index) + 1) % order.length];
    if (next) editor.chain().focus().setHeading({ level: next }).run();
    else editor.chain().focus().setParagraph().run();
  }

  function addScaffold() {
    if (editor.state.selection.empty) {
      flash(status, 'Select some text before adding a scaffold.');
      close();
      return;
    }
    const instruction = window.prompt('What should LLex do with this text?');
    if (!instruction || !instruction.trim()) {
      close();
      return;
    }
    const { from, to } = editor.state.selection;
    const mark = editor.schema.marks.scaffold.create({
      id: nextScaffoldId(),
      instruction: instruction.trim(),
    });
    editor.view.dispatch(editor.state.tr.addMark(from, to, mark));
    if (options.onScaffoldChange) options.onScaffoldChange();
    close();
  }

  mini.addEventListener('click', (event) => {
    if (event.target instanceof HTMLSelectElement) return;
    const control = event.target.closest('[data-action]');
    if (!control) return;
    event.preventDefault();
    event.stopPropagation();
    const action = actions[control.dataset.action];
    if (action) {
      action();
      if (control.dataset.action !== 'add-scaffold') close();
    }
  });

  mini.addEventListener('change', (event) => {
    const select = event.target;
    if (!(select instanceof HTMLSelectElement)) return;
    if (select.dataset.action === 'font-family') {
      editor.chain().focus().setNode('paragraph', { fontFamily: select.value }).run();
    } else if (select.dataset.action === 'font-size') {
      editor.chain().focus().setNode('paragraph', { fontSize: select.value }).run();
    }
  });

  // -- Standard edit menu ------------------------------------------------- //

  standard.addEventListener('click', async (event) => {
    const item = event.target.closest('[data-action]');
    if (!item) return;
    const action = item.dataset.action;

    if (action === 'cut' || action === 'copy') {
      // execCommand is deprecated but remains the only way to put the real
      // selection on the system clipboard from a custom menu.
      document.execCommand(action);
    } else if (action === 'paste') {
      try {
        const text = await navigator.clipboard.readText();
        editor.chain().focus().insertContent(text).run();
      } catch {
        flash(status, 'Clipboard access was denied; use Ctrl+V instead.');
      }
    }
    close();
  });

  // -- Show and hide ------------------------------------------------------- //

  editor.view.dom.addEventListener('contextmenu', (event) => {
    if (!event.target.closest('.page')) return;
    event.preventDefault();

    const hasSelection = !editor.state.selection.empty;
    close();

    if (hasSelection) {
      mini.style.display = 'flex';
      place(mini, event.pageX, event.pageY);
      const rect = mini.getBoundingClientRect();
      standard.style.display = 'flex';
      place(standard, event.pageX, event.pageY + rect.height + 4);
      syncMiniState();
    } else {
      standard.style.display = 'flex';
      place(standard, event.pageX, event.pageY);
    }
  });

  function syncMiniState() {
    const marks = editor.state.selection.$from.marks().map((mark) => mark.type.name);
    mini.querySelectorAll('.mt-btn[data-action]').forEach((button) => {
      const action = button.dataset.action;
      const active = marks.includes(action) || editor.isActive({ textAlign: action });
      button.classList.toggle('active', active);
    });
    const attributes = editor.getAttributes('textStyle');
    const family = mini.querySelector('[data-action="font-family"]');
    if (family && attributes.fontFamily) family.value = attributes.fontFamily;
  }

  editor.view.dom.addEventListener('keydown', close);
  window.addEventListener('resize', close);
  document.addEventListener('click', (event) => {
    if (isOpen() && !event.target.closest('#context-menu, #context-menu-standard')) close();
  });
  editor.on('selectionUpdate', () => {
    if (isOpen()) syncMiniState();
  });

  return { close, isOpen, addScaffold, actions };
}
