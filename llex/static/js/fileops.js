/**
 * File operations: new, open, save, save-as and export.
 *
 * Every operation reports its real outcome. The old save handler set its label
 * to "Saved!" without inspecting the response, so a rejected write was
 * indistinguishable from a successful one.
 */

import { busyLabel, flash } from './dom.js';

/**
 * @param {import('@tiptap/core').Editor} editor
 * @param {{api: object, state: object, status: HTMLElement|null, onLoaded: (payload: object) => void, onSaved: (payload: object) => void}} options
 */
export function createFileOperations(editor, options) {
  const { api, state, status, onLoaded, onSaved } = options;

  /**
   * Confirm before discarding unsaved work.
   * @returns {boolean} true when it is safe to proceed
   */
  function confirmDiscard() {
    if (!state.dirty) return true;
    return window.confirm('This document has unsaved changes. Discard them and continue?');
  }

  function handle(label, task) {
    const element = document.getElementById(label);
    return async () => {
      busyLabel(element, true, 'Working…');
      try {
        return await task();
      } finally {
        busyLabel(element, false);
      }
    };
  }

  const newDocument = handle('menu-new', async () => {
    if (!confirmDiscard()) return null;
    const payload = await api.newDocument();
    onLoaded(payload);
    flash(status, 'New document');
    return payload;
  });

  const open = handle('menu-open', async () => {
    if (!confirmDiscard()) return null;
    const payload = await api.open();
    if (payload.status === 'cancelled') {
      flash(status, 'Open cancelled');
      return null;
    }
    onLoaded(payload);
    flash(status, `Opened ${payload.document.file_name || payload.document.title}`);
    return payload;
  });

  const save = handle('menu-save', async () => {
    const payload = await api.save(editor.getHTML(), state.title);
    if (payload.status === 'cancelled') {
      flash(status, 'Save cancelled');
      return null;
    }
    onSaved(payload);
    flash(status, `Saved ${payload.document.file_name || payload.document.title}`);
    return payload;
  });

  const saveAs = handle('menu-save-as', async () => {
    const payload = await api.saveAs(editor.getHTML(), state.title);
    if (payload.status === 'cancelled') {
      flash(status, 'Save cancelled');
      return null;
    }
    onSaved(payload);
    flash(status, `Saved ${payload.document.file_name || payload.document.title}`);
    return payload;
  });

  /**
   * @param {string} format e.g. `docx`
   */
  const exportAs = (format) =>
    handle(`export-${format}`, async () => {
      const payload = await api.exportDocument(editor.getHTML(), format);
      if (payload.status === 'cancelled') {
        flash(status, 'Export cancelled');
        return null;
      }
      flash(status, `Exported ${payload.file_name} (${Math.round(payload.size / 1024)} kB)`);
      return payload;
    })();

  /**
   * Build the File > Download menu from the formats the backend advertises.
   *
   * Generating it from `/api/environment` means the menu cannot offer a format
   * the backend cannot write.
   * @param {{suffix: string, label: string}[]} formats
   */
  function buildExportMenu(formats) {
    const container = document.getElementById('export-menu');
    if (!container) return;
    container.replaceChildren();

    formats.forEach((entry) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'dropdown-item';
      item.id = `export-${entry.suffix.replace('.', '')}`;
      item.dataset.export = entry.suffix;
      item.textContent = `${entry.label} (${entry.suffix})`;
      item.addEventListener('click', exportAs.bind(null, entry.suffix));
      container.append(item);
    });
  }

  function wire(id, action) {
    const element = document.getElementById(id);
    if (element) element.addEventListener('click', action);
  }

  wire('menu-new', newDocument);
  wire('menu-open', open);
  wire('menu-save', save);
  wire('menu-save-as', saveAs);

  return { newDocument, open, save, saveAs, exportAs, buildExportMenu, confirmDiscard };
}
