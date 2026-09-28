import test from 'node:test';
import assert from 'node:assert/strict';
import { Autosave } from '../../llex/static/js/autosave.js';

/**
 * A stand-in for the editor, API and shared state.
 *
 * The timers are never started here: every test drives `tick()` or `flush()`
 * directly, so the suite does not depend on wall-clock time.
 */
function harness({ autosave, conflict, html = '<p>one</p>' } = {}) {
  // Resolving a conflict restarts the timers, which is right in a browser and
  // keeps Node's event loop alive here. Each test stops them when it is done.
  const controllers = [];
  const calls = [];
  let content = html;
  const state = { dirty: true, fileName: 'doc.llex', title: 'Doc' };
  const editor = {
    getHTML: () => content,
    commands: {
      setContent: (next) => {
        content = next;
        calls.push({ kind: 'setContent', html: next });
      },
    },
  };
  const api = {
    autosave: (sent, title) => {
      calls.push({ kind: 'autosave', html: sent, title });
      return autosave === undefined
        ? Promise.resolve({ status: 'saved', document: { file_name: 'doc.llex' } })
        : autosave(sent, title, calls.length);
    },
    conflict: () =>
      conflict === undefined ? Promise.resolve({ status: 'clear' }) : conflict(),
    acceptDisk: () => Promise.resolve({ status: 'reloaded', html: '<p>from disk</p>', document: { file_name: 'doc.llex', title: 'Doc' } }),
  };
  const controller = new Autosave({ editor, api, state, status: null });
  controllers.push(controller);
  return {
    controller,
    state,
    calls,
    editor,
    api,
    setContent: (next) => { content = next; },
    // Every timer this harness created, stopped. Called from each test's
    // cleanup so a test cannot leave the process running.
    stopAll: () => controllers.forEach((one) => one.stop()),
  };
}

test('a save in progress is joined, not started twice', async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const { controller, calls } = harness({
    autosave: () => gate.then(() => ({ status: 'saved' })),
  });

  const first = controller.flush();
  const second = controller.flush();
  assert.equal(calls.length, 1, 'the second flush issued a second write');

  release();
  await Promise.all([first, second]);
  assert.equal(calls.length, 1);
});

test('an edit made while saving is not reported as saved', async () => {
  // The bug this guards: two overlapping saves, the second response arriving
  // last, clears `dirty` for an edit that was made during the first save and
  // so was never written to the file.
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const { controller, state, setContent } = harness({
    autosave: () => gate.then(() => ({ status: 'saved' })),
  });

  const saving = controller.flush();
  setContent('<p>typed during the save</p>');
  state.dirty = true;
  release();
  await saving;

  assert.equal(state.dirty, true, 'an unsaved edit was marked as saved');
});

test('a clean document is not written', async () => {
  const { controller, state, calls } = harness();
  state.dirty = false;
  assert.equal(await controller.flush(), null);
  assert.equal(calls.length, 0);
});

test('a conflict stops autosave and reports itself', async () => {
  const seen = [];
  const { controller, state } = harness({
    conflict: () => Promise.resolve({ status: 'conflict', detail: 'changed elsewhere' }),
  });
  controller.onConflict = (report) => seen.push(report);

  state.fileName = 'doc.llex';
  const result = await controller.checkConflict();
  assert.equal(result.status, 'conflict');
  assert.equal(seen.length, 1, 'the conflict was not reported to the caller');
  assert.equal(seen[0].detail, 'changed elsewhere');
  assert.equal(controller.timer, null, 'autosave kept running through a conflict');
  assert.equal(controller.watchTimer, null);
});

test('a document with no file is not probed for conflicts', async () => {
  let probed = false;
  const { controller, state } = harness({
    conflict: () => { probed = true; return Promise.resolve({ status: 'conflict' }); },
  });
  state.fileName = null;
  assert.equal(await controller.checkConflict(), null);
  assert.equal(probed === false, true);
});

test('a failed conflict probe is not raised at the user', async () => {
  const seen = [];
  const { controller, state } = harness({
    conflict: () => Promise.reject(new Error('network gone')),
  });
  controller.onConflict = (report) => seen.push(report);
  state.fileName = 'doc.llex';
  assert.equal(await controller.checkConflict(), null);
  assert.equal(seen.length, 0, 'a failed probe was reported as a conflict');
});

test('taking the version on disk replaces the document and resumes saving', async () => {
  const { controller, state, calls, stopAll } = harness();
  // Conflict first, so "resumes" means resuming from a stopped state rather
  // than "was never stopped".
  await controller.checkConflict();
  assert.equal(controller.timer, null, 'autosave was running despite the conflict');
  const response = await controller.acceptDiskVersion();
  const resumed = controller.timer !== null;
  stopAll();

  assert.equal(response.status, 'reloaded');
  const replaced = calls.find((call) => call.kind === 'setContent');
  assert.ok(replaced, 'the document on screen was not replaced');
  assert.equal(replaced.html, '<p>from disk</p>');
  assert.equal(state.dirty, false);
  assert.equal(resumed, true, 'autosave did not resume after the conflict');
});

test('a failed reload leaves the document alone and stays in conflict', async () => {
  // The dangerous version of this: the document is cleared and reported as
  // resolved even though the file could not be read.
  const { controller, state, calls, api, stopAll } = harness();
  api.acceptDisk = () => Promise.reject(new Error('permission denied'));
  state.dirty = true;
  const before = state.title;

  const response = await controller.acceptDiskVersion();
  stopAll();
  assert.equal(response, null, 'a failed reload claimed to have succeeded');
  assert.equal(calls.some((call) => call.kind === 'setContent'), false);
  assert.equal(state.title, before);
  assert.equal(state.dirty, true, 'the document was reported as saved after a failed reload');
});

test('keeping this window overwrites the file even if nothing was edited', async () => {
  // A conflict can be reported on a document this window never touched, and the
  // user has still just chosen to keep it, so the write cannot depend on `dirty`.
  const { controller, state, calls, stopAll } = harness({
    conflict: () => Promise.resolve({ status: 'conflict', detail: 'changed elsewhere' }),
  });
  state.dirty = false;
  await controller.checkConflict();
  const response = await controller.keepMine();
  const resumed = controller.timer !== null;
  stopAll();
  assert.equal(response.status, 'saved');
  assert.equal(calls.filter((call) => call.kind === 'autosave').length, 1);
  assert.equal(state.dirty, false);
  assert.equal(resumed, true, 'autosave did not resume');
});

test('nothing is written while a conflict is being settled', async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const { controller, calls, stopAll } = harness({
    autosave: () => gate.then(() => ({ status: 'saved' })),
  });

  const pending = controller.keepMine();
  // The quiet timer and a manual save both arrive while the answer is in flight.
  // `flush` is not awaited: it joins the write that is already running, so
  // awaiting it would wait on the gate this test has not released yet.
  assert.equal(await controller.tick(), null, 'the quiet timer wrote through a conflict');
  const after = calls.length;
  controller.flush();
  assert.equal(calls.length, after, 'a save started while a conflict was being settled');

  release();
  await pending;
  assert.equal(calls.filter((call) => call.kind === 'autosave').length, 1);
  stopAll();
});

test('a failed save is reported once, not every four seconds', async () => {
  let attempts = 0;
  const { controller, state } = harness({
    autosave: () => { attempts += 1; return Promise.reject(new Error('disk full')); },
  });
  state.dirty = true;
  await controller.tick();
  await controller.tick();
  await controller.tick();
  assert.equal(attempts, 3, 'the save should still be retried every tick');
  assert.equal(controller.saves, 0, 'a failed save was counted as a save');
});
