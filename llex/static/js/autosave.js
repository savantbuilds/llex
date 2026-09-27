/**
 * Autosave, crash recovery, and concurrent-open detection.
 *
 * Three separate problems, kept together because they share a timer and a
 * notion of "there is a file behind this document":
 *
 * 1. **Autosave.** Edits are written to the open file after a quiet period, so
 *    closing the window by mistake costs the last few seconds at most rather
 *    than the file.
 * 2. **Crash recovery.** Autosave only helps a document that already has a file.
 *    A document that has never been saved gets a draft file, because for that
 *    document there is nowhere else the work could be.
 * 3. **Concurrent opens.** Two windows on one file used to mean last-writer-wins,
 *    silently. A digest check on a timer reports the situation instead of
 *    quietly discarding work.
 *
 * The state object is the same one the status bar and the File menu read, so
 * marking the document saved here updates the dirty indicator everywhere without
 * a second source of truth.
 */

import { flash } from './dom.js';

/** Quiet period after the last edit before autosave runs. */
export const IDLE_MS = 4000;

/** How often to compare the file on disk with what we last saw. */
export const WATCH_MS = 10000;

/** Autosave failures are worth saying once, not every four seconds. */
export const ERROR_REPORT_INTERVAL_MS = 60000;

/** Draft file for a document that has never been saved. */
export const DRAFT_NAME = 'llex-draft.llex';

export class Autosave {
  /**
   * @param {object} options
   * @param {import('@tiptap/core').Editor} options.editor
   * @param {object} options.api
   * @param {object} options.state Shared editor state: `{dirty, fileName, title}`.
   * @param {HTMLElement | null} options.status
   * @param {(conflict: object) => void} [options.onConflict] Called when the file changed.
   */
  constructor({ editor, api, state, status, onConflict }) {
    this.editor = editor;
    this.api = api;
    this.state = state;
    this.status = status;
    this.onConflict = onConflict;
    this.timer = null;
    this.watchTimer = null;
    this.lastErrorAt = 0;
    this.saves = 0;
    this.stopped = false;
  }

  /** Start watching. Called once, after the editor exists. */
  start() {
    this.stop();
    this.timer = window.setInterval(() => this.tick(), IDLE_MS);
    this.watchTimer = window.setInterval(() => this.checkConflict(), WATCH_MS);
  }

  /** Stop watching and cancel any pending tick. */
  stop() {
    if (this.timer !== null) window.clearInterval(this.timer);
    if (this.watchTimer !== null) window.clearInterval(this.watchTimer);
    this.timer = null;
    this.watchTimer = null;
  }

  /**
   * Save if the document has changed since the last save.
   *
   * Driven by a timer rather than by transactions, so a burst of typing produces
   * one write instead of one per keystroke.
   *
   * @returns {Promise<object | null>} The API response, or null if nothing to do.
   */
  async tick() {
    if (this.stopped || !this.state.dirty) return null;
    return this.flush();
  }

  /**
   * Write the document now.
   *
   * @returns {Promise<object | null>} The API response, or null on failure.
   */
  async flush() {
    if (!this.state.dirty) return null;
    let response;
    try {
      response = await this.api.autosave(this.editor.getHTML(), this.state.title);
    } catch (error) {
      this._reportError(error);
      return null;
    }
    this.saves += 1;
    if (response && response.status === 'saved') {
      // The server is now the source of truth for "saved", which also updates the
      // file name for a document that had never been written to disk.
      this.state.dirty = false;
      if (response.document) {
        this.state.fileName = response.document.file_name || this.state.fileName;
      }
    }
    return response;
  }

  /**
   * Compare the file on disk against the digest we last saw.
   *
   * Runs on a timer rather than before each save, so the user is warned early
   * enough to decide, and never mid-keystroke.
   *
   * @returns {Promise<object | null>} `null` when there is nothing to report.
   */
  async checkConflict() {
    if (this.stopped || !this.state.fileName) return null;
    let result;
    try {
      result = await this.api.conflict();
    } catch {
      // A failed probe is not worth interrupting the user for; the next one runs
      // in ten seconds.
      return null;
    }
    if (result && result.status === 'conflict') {
      flash(this.status, result.detail, 0);
      if (this.onConflict) this.onConflict(result);
      this.stop();
    }
    return result;
  }

  /**
   * Reload the file from disk, discarding what is on screen.
   *
   * The resolution when a conflict is reported and the user decides the other
   * version is the one to keep.
   */
  async acceptDiskVersion() {
    const response = await this.api.acceptDisk();
    this.editor.commands.setContent(response.html || '');
    this.state.dirty = false;
    if (response.document) {
      this.state.fileName = response.document.file_name || this.state.fileName;
      this.state.title = response.document.title || this.state.title;
    }
    return response;
  }

  /**
   * Report a failure, at most once a minute.
   *
   * Autosave runs unattended, so a persistent problem (a full disk, a revoked
   * permission) would otherwise fill the status bar with the same message
   * forever and bury everything else.
   */
  _reportError(error) {
    const now = Date.now();
    if (now - this.lastErrorAt < ERROR_REPORT_INTERVAL_MS) return;
    this.lastErrorAt = now;
    const detail = error && error.message ? error.message : String(error);
    flash(this.status, `Autosave failed: ${detail}`, 6000);
  }
}

/**
 * Create an autosave controller.
 *
 * @param {{editor: object, api: object, state: object, status: HTMLElement | null, onConflict?: Function}} options
 * @returns {Autosave}
 */
export function createAutosave(options) {
  return new Autosave(options);
}
