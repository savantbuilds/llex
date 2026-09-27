/**
 * Typed client for the local LLex API.
 *
 * Every request carries the per-session token that the server embedded into the
 * page, and every response is checked. The previous implementation ignored the
 * status code entirely, so a failed save reported "Saved!".
 */

const TOKEN_META = 'llex-api-token';

/** An API call that returned a non-2xx status or an unusable body. */
export class ApiError extends Error {
  /**
   * @param {string} message
   * @param {number} status
   * @param {string} [detail]
   */
  constructor(message, status, detail) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }

  /** True when the feature exists but no local model is configured. */
  get isOffline() {
    return this.status === 503;
  }

  /** True when the request itself was wrong. */
  get isValidation() {
    return this.status === 400 || this.status === 422;
  }
}

function readToken() {
  const meta = document.querySelector(`meta[name="${TOKEN_META}"]`);
  return meta ? meta.getAttribute('content') || '' : '';
}

/**
 * Perform a request against the local API.
 *
 * @param {string} path
 * @param {{method?: string, body?: unknown, signal?: AbortSignal}} [options]
 * @returns {Promise<any>}
 */
async function request(path, options = {}) {
  const { method = 'GET', body, signal } = options;
  const headers = { [TOKEN_HEADER]: readToken() };
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new ApiError(
      'Could not reach the LLex service. Is the editor still starting?',
      0,
      String(cause),
    );
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const detail =
      (payload && (payload.detail || payload.message)) || text || response.statusText;
    throw new ApiError(detail, response.status, detail);
  }
  if (payload === null) {
    throw new ApiError(`Empty response from ${path}`, response.status);
  }
  return payload;
}

const TOKEN_HEADER = 'X-LLex-Token';

export const api = {
  environment: () => request('/api/environment'),
  document: () => request('/api/document'),

  newDocument: () => request('/api/document/new', { method: 'POST' }),
  open: () => request('/api/document/open', { method: 'POST' }),
  save: (html, title) => request('/api/document/save', { method: 'POST', body: { html, title } }),
  saveAs: (html, title) => request('/api/document/save-as', { method: 'POST', body: { html, title } }),
  setContent: (html, title) =>
    request('/api/document/content', { method: 'PUT', body: { html, title } }),

  exportDocument: (html, format) =>
    request('/api/export', { method: 'POST', body: { html, format } }),

  summarize: (text) => request('/api/assistant/summarize', { method: 'POST', body: { text } }),
  rewrite: (text, tone) =>
    request('/api/assistant/rewrite', { method: 'POST', body: { text, tone } }),
  outline: (text) => request('/api/assistant/outline', { method: 'POST', body: { text } }),
  ask: (text, instruction) =>
    request('/api/assistant/ask', { method: 'POST', body: { text, instruction } }),
  runScaffolds: (scaffolds) =>
    request('/api/assistant/scaffolds', { method: 'POST', body: { scaffolds } }),
};
