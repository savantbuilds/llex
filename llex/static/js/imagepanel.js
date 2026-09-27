/**
 * The image panel: pick a file, size it, choose how text wraps, set alt text.
 *
 * A bar rather than a dialog, for the same reason find is: the image is inserted
 * immediately at a sensible size and the panel then adjusts it, so there is one
 * step instead of a wizard.
 *
 * Every change here re-measures the page. An image's height is what decides where
 * the next page break falls, so a resize that did not repaginate would leave the
 * document visibly wrong until the next edit.
 */

import { flash } from './dom.js';
import {
  SIZE_PRESETS,
  WRAP_MODES,
  describeImage,
  fitImage,
  isSafeImageSource,
  readImageAttributes,
  readImageFile,
  resizeImage,
} from './images.js';
import { printableWidth } from './image-limits.js';

/** How much of the printable width the size presets are labelled against. */
const SIZE_PRESET_CSS_WIDTH = 40;

export class ImagePanel {
  /**
   * @param {{editor: object, paginator: object, status: HTMLElement|null}} options
   */
  constructor({ editor, paginator, status }) {
    this.editor = editor;
    this.paginator = paginator;
    this.status = status;
    // The ribbon's image button is what opens this, so it is also what closes it
    // via its own handler; the panel only needs to be able to find it.
    this.picker = document.getElementById('btn-image');
    this.panel = document.getElementById('image-panel');
    this.fileInput = document.getElementById('image-file');
    this.dropZone = document.getElementById('image-drop');
    this.widthInput = document.getElementById('image-width');
    this.altInput = document.getElementById('image-alt');
    this.sizePresets = document.getElementById('image-size-presets');
    this.wrapSelect = document.getElementById('image-wrap');
    this.displaySelect = document.getElementById('image-display');
    this.statusLine = document.getElementById('image-status');
    this._bind();
  }

  _bind() {
    this.picker?.addEventListener('click', () => this.open());
    this.fileInput?.addEventListener('change', (event) => {
      const file = event.target.files?.[0];
      if (file) this.insertFile(file);
      // Clear it so choosing the same file twice fires `change` again.
      event.target.value = '';
    });
    this.widthInput?.addEventListener('input', () => this.applyWidth());
    this.altInput?.addEventListener('input', () => this.applyAlt());
    this.wrapSelect?.addEventListener('change', () => this.applyWrap());
    this.displaySelect?.addEventListener('change', () => this.applyDisplay());

    if (this.dropZone) {
      this.dropZone.addEventListener('dragover', (event) => {
        event.preventDefault();
        this.dropZone.dataset.over = 'true';
      });
      this.dropZone.addEventListener('dragleave', () => {
        delete this.dropZone.dataset.over;
      });
      this.dropZone.addEventListener('drop', (event) => {
        event.preventDefault();
        delete this.dropZone.dataset.over;
        const file = event.dataTransfer?.files?.[0];
        if (file) this.insertFile(file);
      });
    }

    document.getElementById('image-close')?.addEventListener('click', () => this.close());
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.isOpen()) this.close();
    });
    // Selection changes decide whether the panel is editing an image, so it has
    // to follow the caret rather than only follow clicks.
    this.editor.on('selectionUpdate', () => this.sync());
  }

  isOpen() {
    return Boolean(this.panel && !this.panel.hidden);
  }

  open() {
    if (this.panel) this.panel.hidden = false;
    this.sync();
    (this.fileInput || this.widthInput)?.focus();
  }

  close() {
    if (this.panel) this.panel.hidden = true;
  }

  /** The printable width, from the geometry the stylesheet is using now. */
  get printableWidth() {
    const styles = getComputedStyle(document.documentElement);
    const pageWidth = Number.parseFloat(styles.getPropertyValue('--page-width')) || 816;
    const left = Number.parseFloat(styles.getPropertyValue('--margin-left')) || 0;
    const right = Number.parseFloat(styles.getPropertyValue('--margin-right')) || 0;
    return printableWidth(pageWidth, { left, right });
  }

  /**
   * Read a dropped or chosen file, then insert it.
   *
   * The file is decoded before insertion so the space can be reserved at the
   * right size on the first paint; see `images.js` for why that matters to
   * pagination.
   *
   * @param {File} file
   */
  async insertFile(file) {
    let read;
    try {
      read = await readImageFile(file);
    } catch (error) {
      flash(this.status, error.message || 'that file could not be read', 4000);
      return null;
    }
    if (!isSafeImageSource(read.src)) {
      flash(this.status, 'that image address is not allowed', 4000);
      return null;
    }

    const size = fitImage(read, this.printableWidth);
    this.editor
      .chain()
      .focus()
      .setImage({ src: read.src, alt: file.name, width: size.width, height: size.height })
      .run();

    this.open();
    this.sync();
    flash(
      this.status,
      `Inserted ${file.name} at ${size.width} by ${size.height} pixels`,
      3000,
    );
    // The height of this block just changed, so the breaks below it are stale.
    this.paginator?.schedule();
    return size;
  }

  /** The image the selection is in, or null. */
  current() {
    return readImageAttributes(this.editor);
  }

  /** Point the panel at whatever is selected. */
  sync() {
    const image = this.current();
    const hasImage = Boolean(image);
    if (this.statusLine) {
      this.statusLine.textContent = hasImage
        ? `${image.width ?? '?'} by ${image.height ?? '?'} pixels`
        : 'Choose a file, or drop an image here.';
    }
    for (const element of [this.widthInput, this.altInput, this.wrapSelect, this.displaySelect]) {
      if (element) element.disabled = !hasImage;
    }
    if (!hasImage) {
      if (this.widthInput) this.widthInput.value = '';
      if (this.altInput) this.altInput.value = '';
      return;
    }
    if (this.widthInput) this.widthInput.value = String(image.width ?? '');
    if (this.altInput) this.altInput.value = image.alt ?? '';
    if (this.wrapSelect) this.wrapSelect.value = image.float;
    if (this.displaySelect) this.displaySelect.value = image.display;
  }

  _withImage(run) {
    if (!this.current()) {
      flash(this.status, 'Select an image first', 2500);
      return false;
    }
    run();
    // Every one of these changes the page's height, so re-measure rather than
    // waiting for the next edit to notice.
    this.paginator?.schedule();
    return true;
  }

  applyWidth() {
    const image = this.current();
    if (!image) return;
    const requested = Number.parseFloat(this.widthInput?.value ?? '');
    if (!Number.isFinite(requested) || requested <= 0) return;
    const size = resizeImage(
      { width: image.width || requested, height: image.height || requested },
      requested,
      this.printableWidth,
    );
    this._withImage(() => {
      this.editor.commands.updateAttributes('image', size);
      if (this.statusLine) {
        this.statusLine.textContent = `${size.width} by ${size.height} pixels`;
      }
    });
  }

  applyAlt() {
    this._withImage(() => {
      this.editor.commands.updateAttributes('image', { alt: this.altInput?.value ?? '' });
    });
  }

  applyWrap() {
    this._withImage(() => {
      this.editor.commands.updateAttributes('image', { float: this.wrapSelect?.value || 'none' });
    });
  }

  applyDisplay() {
    this._withImage(() => {
      this.editor.commands.updateAttributes('image', { display: this.displaySelect?.value || 'block' });
    });
  }

  /** Scale to a fraction of the printable width, keeping the aspect ratio. */
  applyPreset(fraction) {
    const image = this.current();
    if (!image) {
      flash(this.status, 'Select an image first', 2500);
      return;
    }
    const target = Math.round(this.printableWidth * fraction);
    this._withImage(() => {
      const size = resizeImage(
        { width: image.width || target, height: image.height || target },
        target,
        this.printableWidth,
      );
      this.editor.commands.updateAttributes('image', size);
      if (this.widthInput) this.widthInput.value = String(size.width);
      if (this.statusLine) {
        this.statusLine.textContent = `${size.width} by ${size.height} pixels`;
      }
    });
  }

  /** Alt text that a screen reader can use, derived from the geometry. */
  describeSelected() {
    const image = this.current();
    return image ? describeImage(image) : null;
  }
}

/**
 * @param {{editor: object, paginator: object, status: HTMLElement|null}} options
 * @returns {ImagePanel}
 */
export function createImagePanel(options) {
  return new ImagePanel(options);
}

export { SIZE_PRESETS, WRAP_MODES, SIZE_PRESET_CSS_WIDTH };
