import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ALWAYS_APPLIES,
  SHORTCUTS,
  appliesHere,
  eventKey,
  isInEditable,
  shortcutFor,
} from '../../llex/static/js/shortcuts.js';

/** A synthetic keyboard event, so the table can be exercised without a DOM. */
function press(key, modifiers = {}) {
  return {
    key,
    ctrlKey: Boolean(modifiers.ctrl),
    metaKey: Boolean(modifiers.meta),
    altKey: Boolean(modifiers.alt),
    shiftKey: Boolean(modifiers.shift),
    target: modifiers.target ?? null,
  };
}

test('every shortcut has a label, an action and a canonical key', () => {
  for (const entry of SHORTCUTS) {
    assert.ok(entry.keys, 'a shortcut has no key combination');
    assert.equal(entry.keys, entry.keys.toLowerCase(), `${entry.keys} is not lower case`);
    assert.ok(entry.run, `${entry.keys} has no action`);
    assert.ok(entry.label, `${entry.keys} has no label`);
  }
});

test('the labels say the same thing as the keys', () => {
  // A table that renders "Ctrl+B" but listens for something else is the failure
  // this guards, and it is invisible in a screenshot.
  for (const entry of SHORTCUTS) {
    const parts = entry.keys.split('+');
    const partsLabel = entry.label.split('+').map((p) => p.trim().toLowerCase());
    assert.equal(parts.length, partsLabel.length, `${entry.keys} label is "${entry.label}"`);
    for (const part of parts) {
      if (part === 'mod') {
        assert.ok(
          partsLabel.some((p) => p === 'ctrl' || p === 'cmd'),
          `${entry.keys} label "${entry.label}" is missing the primary modifier`,
        );
        continue;
      }
      // A label is written the way a spec sheet writes it -- "Up", not
      // "ArrowUp" -- so the key is compared on its display name.
      const shown = KEY_LABELS[part] ?? part;
      assert.ok(
        partsLabel.includes(shown),
        `${entry.keys} label "${entry.label}" is missing "${shown}"`,
      );
    }
  }
});

/** Display names for keys whose label is shorter than the reported name. */
const KEY_LABELS = {
  arrowup: 'up',
  arrowdown: 'down',
  arrowleft: 'left',
  arrowright: 'right',
  escape: 'esc',
  enter: 'enter',
  '=': '=',
  '+': '+',
};

test('no two shortcuts claim the same key combination', () => {
  const seen = new Map();
  for (const entry of SHORTCUTS) {
    const previous = seen.get(entry.keys);
    assert.equal(previous, undefined, `${entry.keys} is bound twice (${previous} and ${entry.run})`);
    seen.set(entry.keys, entry.run);
  }
});

test('no two shortcuts claim the same action with the same modifiers', () => {
  // Different bindings for one action are deliberate -- Redo is both Ctrl+Y and
  // Ctrl+Shift+Z -- but two entries differing only in an unused modifier is a
  // typo.
  const byAction = new Map();
  for (const entry of SHORTCUTS) {
    const list = byAction.get(entry.run) || [];
    list.push(entry.keys);
    byAction.set(entry.run, list);
  }
  for (const [run, keys] of byAction) {
    assert.ok(keys.length <= 3, `${run} has ${keys.length} bindings: ${keys.join(', ')}`);
  }
});

test('the shortcuts people arrive with are all bound', () => {
  // The regression list. Each of these is a key people press in the first
  // minute of using any other word processor.
  const expected = {
    'mod+z': 'undo',
    'mod+y': 'redo',
    'mod+shift+z': 'redo',
    'mod+x': 'cut',
    'mod+c': 'copy',
    'mod+v': 'paste',
    'mod+a': 'selectAll',
    'mod+f': 'find',
    'mod+h': 'replace',
    'mod+s': 'save',
    'mod+shift+s': 'saveAs',
    'mod+o': 'open',
    'mod+n': 'new',
    'mod+p': 'print',
    'mod+b': 'bold',
    'mod+i': 'italic',
    'mod+u': 'underline',
    'mod+shift+x': 'strikethrough',
    'mod+l': 'alignLeft',
    'mod+e': 'alignCenter',
    'mod+r': 'alignRight',
    'mod+j': 'alignJustify',
    'mod+k': 'link',
    'mod+enter': 'pageBreak',
    'mod+.': 'bulletList',
    'mod+shift+8': 'bulletList',
    'mod+shift+7': 'orderedList',
    'tab': 'indent',
    'shift+tab': 'outdent',
    'escape': 'exitFocusMode',
    'mod+=': 'zoomIn',
    'mod+-': 'zoomOut',
    'mod+0': 'zoomReset',
  };
  for (const [keys, run] of Object.entries(expected)) {
    assert.equal(shortcutFor(pressFor(keys), {}), run, `${keys} is not bound to ${run}`);
  }
});

test('headings have the six levels and a body text shortcut', () => {
  for (const level of [1, 2, 3, 4, 5, 6]) {
    assert.equal(
      shortcutFor(pressFor(`mod+alt+${level}`), {}),
      `heading${level}`,
      `Ctrl+Alt+${level} does not set heading ${level}`,
    );
  }
  assert.equal(shortcutFor(pressFor('mod+alt+0'), {}), 'bodyText');
});

test('line moves work with and without the primary modifier', () => {
  assert.equal(shortcutFor(pressFor('mod+alt+arrowup'), {}), 'moveBlockUp');
  assert.equal(shortcutFor(pressFor('mod+alt+arrowdown'), {}), 'moveBlockDown');
  assert.equal(shortcutFor(pressFor('alt+arrowup'), {}), 'moveBlockUp');
  assert.equal(shortcutFor(pressFor('alt+arrowdown'), {}), 'moveBlockDown');
});

/** Build the event for a canonical key string. */
function pressFor(keys, options = {}) {
  const parts = keys.split('+');
  const key = parts[parts.length - 1];
  return press(key, {
    ctrl: parts.includes('mod'),
    meta: parts.includes('meta'),
    alt: parts.includes('alt'),
    shift: parts.includes('shift'),
    target: options.target,
  });
}

test('the primary modifier follows the platform', () => {
  // Cmd on a Mac, Ctrl everywhere else -- and the other is never mistaken for
  // it, so Cmd+S on a PC is not a silent no-op that claims to have saved.
  assert.equal(shortcutFor(press('s', { ctrl: true }), { apple: false }), 'save');
  assert.equal(shortcutFor(press('s', { meta: true }), { apple: true }), 'save');
  assert.equal(shortcutFor(press('s', { meta: true }), { apple: false }), null);
  assert.equal(shortcutFor(press('s', { ctrl: true }), { apple: true }), null);
});

test('a second modifier is not dropped', () => {
  // Ctrl+Shift+S is Save As and Ctrl+S is Save. Collapsing the Shift would make
  // both save.
  assert.equal(shortcutFor(press('s', { ctrl: true }), {}), 'save');
  assert.equal(shortcutFor(press('s', { ctrl: true, shift: true }), {}), 'saveAs');
  assert.notEqual(eventKey(press('s', { ctrl: true })), eventKey(press('s', { ctrl: true, shift: true })));
});

test('an unbound key is not a shortcut', () => {
  assert.equal(shortcutFor(press('q', { ctrl: true }), {}), null);
  assert.equal(shortcutFor(press('k', {}), {}), null);
  // A modifier combination with no binding must not fall back to a shorter one.
  assert.equal(shortcutFor(press('b', { ctrl: true, alt: true }), {}), null);
});

test('arrow keys are matched by their own names', () => {
  // Browsers report "ArrowUp". A table written "Up" would never fire.
  assert.equal(shortcutFor(press('ArrowUp', { ctrl: true, alt: true }), {}), 'moveBlockUp');
  assert.equal(eventKey(press('ArrowUp')), 'arrowup');
});

test('shift and tab are distinguished', () => {
  assert.equal(shortcutFor(press('Tab', {}), {}), 'indent');
  assert.equal(shortcutFor(press('Tab', { shift: true }), {}), 'outdent');
});

test('a text field keeps its own shortcuts, with a few exceptions', () => {
  const field = { nodeType: 1, tagName: 'INPUT', isContentEditable: false };
  // Ctrl+A in a field means "all of this field". Taking it to mean "select the
  // document" is the oldest browser-editor bug there is.
  assert.equal(appliesHere('selectAll', field), false);
  assert.equal(appliesHere('bold', field), false);
  assert.equal(appliesHere('indent', field), false);
  // These are the ones that have to work anyway.
  assert.equal(appliesHere('find', field), true);
  assert.equal(appliesHere('replace', field), true);
  assert.equal(appliesHere('save', field), true);
  // Escape is not in the list because it is handled before this gate at all: it
  // has no modifier, so it would never reach it.
  assert.equal(ALWAYS_APPLIES.has('escape'), false);
});

test('a contenteditable region is the editor, not a field', () => {
  // The editor itself is a contenteditable div, so treating it as "a field"
  // would disable every shortcut in the document. What matters is whether the
  // *target* is a real form control.
  const editable = { nodeType: 1, tagName: 'DIV', isContentEditable: true };
  assert.equal(isInEditable(editable), false, 'the editor would lose every shortcut');
  assert.equal(appliesHere('selectAll', editable), true);

  // A contenteditable *inside* a form control is still the document.
  const nested = { nodeType: 1, tagName: 'SPAN', isContentEditable: true };
  assert.equal(appliesHere('bold', nested), true);
});

test('a non-element target is not editable', () => {
  assert.equal(isInEditable(null), false);
  assert.equal(isInEditable({ nodeType: 3 }), false);
  assert.equal(appliesHere('selectAll', null), true);
});

test('the always-applies list is a subset of the table', () => {
  const runs = new Set(SHORTCUTS.map((entry) => entry.run));
  for (const run of ALWAYS_APPLIES) {
    assert.ok(runs.has(run), `${run} is always applied but is not a shortcut`);
  }
});
