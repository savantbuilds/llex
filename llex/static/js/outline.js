/**
 * Document outline built from real headings.
 *
 * Headings are structural metadata, not just large text, so the outline is
 * derived from the ProseMirror node tree rather than by scraping the DOM. That
 * gives exact positions to navigate to, and it cannot drift from the document.
 *
 * The previous version only listed levels 3-6, on the grounds that h1/h2 are
 * "title" and "subtitle" in the stylesheet. That silently hid a document's top
 * two heading levels from its own table of contents.
 */

const INDENT_PER_LEVEL = 14;

/** @param {import('@tiptap/core').Editor} editor @returns {HTMLElement|null} */
export function createOutline(editor) {
  const container = document.getElementById('document-outline');
  if (!container) return null;

  let timer = 0;

  /** Rebuild the list from the current document state. */
  function render() {
    const items = [];
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === 'heading') {
        items.push({ level: node.attrs.level || 1, text: node.textContent.trim(), pos });
      }
    });

    container.replaceChildren();

    if (items.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'outline-empty';
      empty.textContent = 'Apply a heading style to build an outline.';
      container.append(empty);
      return;
    }

    const list = document.createElement('ul');
    list.className = 'outline-list';

    for (const item of items) {
      const entry = document.createElement('li');
      entry.className = `outline-item outline-level-${Math.min(item.level, 6)}`;

      const link = document.createElement('button');
      link.type = 'button';
      link.className = 'outline-link';
      link.style.paddingInlineStart = `${(Math.min(item.level, 6) - 1) * INDENT_PER_LEVEL}px`;
      link.textContent = item.text || 'Untitled heading';
      link.title = item.text || 'Untitled heading';
      link.addEventListener('click', () => {
        editor.chain().focus().setTextSelection(item.pos + 1).scrollIntoView().run();
      });

      entry.append(link);
      list.append(entry);
    }

    container.append(list);
  }

  /** Coalesce rebuilds: a paste can fire dozens of updates. */
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(render, 150);
  }

  schedule();
  return { render, schedule };
}
