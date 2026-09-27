/** Small DOM helpers shared across the editor modules. */

/** @param {string} id @returns {HTMLElement|null} */
export const byId = (id) => document.getElementById(id);

/**
 * Resolve one or more elements by id, warning about any that are missing.
 *
 * A missing element is a build error, not a runtime condition to tolerate, so
 * it is reported loudly once at startup instead of failing silently later.
 *
 * @param {...string} ids
 * @returns {HTMLElement[]}
 */
export function require(...ids) {
  return ids.map((id) => {
    const element = byId(id);
    if (!element) console.error(`llex: missing element #${id}`);
    return element;
  });
}

/** @param {ParentNode} root @param {string} selector */
export const all = (root, selector) => Array.from(root.querySelectorAll(selector));

/**
 * Attach a listener only if the element exists.
 * @param {HTMLElement|null} element
 * @param {string} type
 * @param {(event: Event) => void} handler
 */
export function on(element, type, handler) {
  if (element) element.addEventListener(type, handler);
}

/**
 * Briefly show a status message.
 * @param {HTMLElement|null} element
 * @param {string} message
 * @param {number} [ms]
 */
export function flash(element, message, ms = 2200) {
  if (!element) return;
  const previous = element.textContent;
  element.textContent = message;
  element.dataset.flashing = 'true';
  clearTimeout(element._llexFlash);
  element._llexFlash = setTimeout(() => {
    element.textContent = previous;
    delete element.dataset.flashing;
  }, ms);
}

/** Swap the text of a menu item while an action is in flight. */
export function busyLabel(element, working, workingText) {
  if (!element) return;
  if (working) {
    element.dataset.idleLabel = element.textContent;
    element.textContent = workingText;
    element.setAttribute('aria-busy', 'true');
  } else {
    if (element.dataset.idleLabel) element.textContent = element.dataset.idleLabel;
    delete element.dataset.idleLabel;
    removeAttribute(element, 'aria-busy');
  }
}

function removeAttribute(element, name) {
  if (element && element.hasAttribute(name)) element.removeAttribute(name);
}

/**
 * Guard a click handler against double activation and disable the control.
 * @param {HTMLElement|null} element
 * @param {() => Promise<unknown>} task
 */
export async function once(element, task) {
  if (!element || element.disabled) return undefined;
  element.disabled = true;
  try {
    return await task();
  } finally {
    element.disabled = false;
  }
}

/** Debounce a function by `ms` of inactivity. */
export function debounce(fn, ms) {
  let timer = 0;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

/** True when the platform uses a Command key for shortcuts. */
export const IS_APPLE =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '');

/** Whether the platform's primary modifier is held. */
export function hasModifier(event) {
  return IS_APPLE ? event.metaKey : event.ctrlKey;
}

/** Human-readable label for a modifier combination. */
export function combo(event) {
  return `${hasModifier(event) ? (IS_APPLE ? 'Cmd' : 'Ctrl') : ''}+${event.shiftKey ? 'Shift+' : ''}${event.key.toLowerCase()}`;
}
