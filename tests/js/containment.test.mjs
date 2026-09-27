import test from 'node:test';
import assert from 'node:assert/strict';

import {
  MEASURING_CLASS,
  VIRTUAL_CLASS,
  measuring,
  supportsContainment,
} from '../../llex/static/js/paginator.js';

/** A stand-in for the editor container that records class changes. */
function container() {
  const classes = new Set();
  return {
    classList: {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
    },
    get names() {
      return [...classes];
    },
  };
}

/** Run `body` with `CSS.supports` reporting `answer`. */
function withSupport(answer, body) {
  const original = globalThis.CSS;
  globalThis.CSS = { supports: () => answer };
  try {
    return body();
  } finally {
    globalThis.CSS = original;
  }
}

test('containment is used only where the browser supports it', () => {
  withSupport(true, () => {
    assert.equal(supportsContainment(), true);
  });
  withSupport(false, () => {
    assert.equal(supportsContainment(), false);
  });
});

test('a browser without CSS.supports is treated as unsupported', () => {
  // Not a crash: an optimisation that cannot be detected must degrade.
  const original = globalThis.CSS;
  globalThis.CSS = undefined;
  try {
    assert.equal(supportsContainment(), false);
  } finally {
    globalThis.CSS = original;
  }
});

test('measurement runs with containment suspended', () => {
  // The decisive case. A skipped page reports only its intrinsic size, so
  // measuring with containment on would conclude nothing overflows and
  // pagination would stop working on exactly the long documents it targets.
  withSupport(true, () => {
    const element = container();
    element.classList.add(VIRTUAL_CLASS);

    let classesDuringMeasure = null;
    const result = measuring(element, () => {
      classesDuringMeasure = element.names;
      return 'measured';
    });

    assert.equal(result, 'measured');
    assert.ok(
      classesDuringMeasure.includes(MEASURING_CLASS),
      `expected ${MEASURING_CLASS} during measurement, saw ${classesDuringMeasure}`,
    );
    assert.ok(classesDuringMeasure.includes(VIRTUAL_CLASS));
  });
});

test('containment is restored after measuring', () => {
  withSupport(true, () => {
    const element = container();
    element.classList.add(VIRTUAL_CLASS);
    measuring(element, () => 'ok');
    assert.deepEqual(element.names, [VIRTUAL_CLASS]);
  });
});

test('containment is restored even when measuring throws', () => {
  // An exception here would leave every page unskipped, which is only a
  // performance regression -- but a silent one is worth avoiding.
  withSupport(true, () => {
    const element = container();
    element.classList.add(VIRTUAL_CLASS);
    assert.throws(() =>
      measuring(element, () => {
        throw new Error('measurement failed');
      }),
    );
    assert.deepEqual(element.names, [VIRTUAL_CLASS]);
  });
});

test('nothing is toggled when containment is not in use', () => {
  withSupport(true, () => {
    const element = container();
    let namesDuringMeasure = null;
    measuring(element, () => {
      namesDuringMeasure = element.names;
      return 'measured';
    });
    // Without the virtual class there is nothing to suspend, so the measuring
    // class would be left behind doing nothing.
    assert.deepEqual(namesDuringMeasure, []);
  });
});

test('measurement still works where containment is unsupported', () => {
  withSupport(false, () => {
    const element = container();
    let namesDuringMeasure = null;
    const result = measuring(element, () => {
      namesDuringMeasure = element.names;
      return 'measured';
    });
    assert.equal(result, 'measured');
    assert.deepEqual(namesDuringMeasure, []);
  });
});

test('the value from the measure callback is returned unchanged', () => {
  withSupport(true, () => {
    const element = container();
    element.classList.add(VIRTUAL_CLASS);
    const value = { overflowed: new Set(), counts: new Map() };
    assert.equal(measuring(element, () => value), value);
  });
});
