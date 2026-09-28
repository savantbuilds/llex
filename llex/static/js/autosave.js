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
    // Autosave in flight, so a slow write cannot be started twice by the quiet
    // timer and the flush the user just asked for.
    this.inFlight = null;
    // True while a conflict is being settled, so neither the timers nor a manual
    // save can touch the document while it is being replaced or overwritten.
    this.suspended = false;
  }

  /** Start watching. Called once, after the editor exists. */
  start() {
    this.stop();
    // The bare globals rather than `window.`: they are the same functions in a
    // browser, and this class is also exercised under Node, where `window` does
    // not exist at all.
    this.timer = setInterval(() => this.tick(), IDLE_MS);
    this.watchTimer = setInterval(() => this.checkConflict(), WATCH_MS);
  }

  /** Stop watching and cancel any pending tick. */
  stop() {
    if (this.timer !== null) clearInterval(this.timer);
    if (this.watchTimer !== null) clearInterval(this.watchTimer);
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
    if (this.stopped || this.suspended || !this.state.dirty) return null;
    return this.flush();
  }

  /**
   * Write the document now.
   *
   * One write at a time. Two overlapping saves of the same document interleave:
   * whichever response arrives last clears `dirty`, so an edit made *during* the
   * second save is reported as saved while never having reached the file. A
   * caller that arrives while a write is running joins that write instead of
   * starting another.
   *
   * @returns {Promise<object | null>} The API response, or null on failure.
   */
  async flush() {
    if (!this.state.dirty) return null;
    if (this.inFlight) return this.inFlight;
    this.inFlight = this._write().finally(() => {
      this.inFlight = null;
    });
    return this.inFlight;
  }

  /** The actual save, with no re-entrancy handling. @private */
  async _write() {
    // Read once, here, and compared again when the response comes back. An edit
    // made while the write was in flight is *not* in `sent`; clearing `dirty` on
    // that response would claim it had been saved when it was never written, and
    // the next tick would skip it because the document looked clean.
    const sent = this.editor.getHTML();
    let response;
    try {
      response = await this.api.autosave(sent, this.state.title);
    } catch (error) {
      this._reportError(error);
      return null;
    }
    this.saves += 1;
    if (response && response.status === 'saved') {
      if (this.editor.getHTML() === sent) {
        this.state.dirty = false;
      }
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
    if (this.stopped || this.suspended || !this.state.fileName) return null;
    let result;
    try {
      result = await this.api.conflict();
    } catch {
      // A failed probe is not worth interrupting the user for; the next one runs
      // in ten seconds.
      return null;
    }
    if (result && result.status === 'conflict') {
      // Reported, not resolved: which version wins is the user's decision, and
      // making it for them is the last-writer-wins this exists to prevent. The
      // timers stop so nothing overwrites either version while they decide.
      flash(this.status, result.detail, 0);
      this.stop();
      if (this.onConflict) this.onConflict(result);
    }
    return result;
  }

  /** Resume writing, once a conflict has been settled. @private */
  _resume() {
    if (!this.stopped) this.start();
  }

  /**
   * Reload the file from disk, discarding what is on screen.
   *
   * One of the two answers to a reported conflict. Editing stops for the
   * duration: the content on screen is being replaced wholesale, and a keystroke
   * landing in that window would be silently thrown away with the old document.
   *
   * @returns {Promise<object | null>} The API response, or null on failure.
   */
  async acceptDiskVersion() {
    if (this.suspended) return null;
    this.suspended = true;
    this.stop();
    try {
      const response = await this.api.acceptDisk();
      // A failure here is the user's fault being invisible, so the reason is
      // reported rather than swallowed and the old content left on screen.
      if (!response || response.status !== 'reloaded') return null;
      this.editor.commands.setContent(response.html || '');
      this.state.dirty = false;
      if (response.document) {
        this.state.fileName = response.document.file_name || this.state.fileName;
        this.state.title = response.document.title || this.state.title;
      }
      return response;
    } catch (error) {
      this._reportError(error);
      return null;
    } finally {
      this.suspended = false;
      this._resume();
    }
  }

  /**
   * Keep what is on screen and overwrite the file.
   *
   * The other answer. The version on disk is a backup rather than a casualty:
   * the server keeps a bounded history, so this is recoverable.
   *
   * @returns {Promise<object | null>} The API response, or null on failure.
   */
  async keepMine() {
    if (this.suspended) return null;
    this.suspended = true;
    this.stop();
    try {
      // `dirty` is forced because a conflict can be reported on a document this
      // window has not touched, and the user has still just chosen to keep it.
      this.state.dirty = true;
      // Through `flush`, not `_write`: this goes to the file like any other save
      // and must join one already in flight rather than racing it. Calling the
      // inner method would open a second write to the same file, and the slower
      // response would then be the one to decide whether the document is clean.
      return await this.flush();
    } finally {
      this.suspended = false;
      this._resume();
    }
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
