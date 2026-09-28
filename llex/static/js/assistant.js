/**
 * The assistant sidebar.
 *
 * Every action here can genuinely fail -- most often because no local model is
 * configured -- so the UI reports the real reason instead of implying success.
 * That distinction was previously lost: a placeholder string was inserted into
 * the user's document while looking like model output.
 */

import { flash } from './dom.js';

/** Tones offered for rewriting, matching the backend's `Tone`. */
export const TONES = ['professional', 'friendly', 'technical', 'concise', 'persuasive'];

/**
 * @param {import('@tiptap/core').Editor} editor
 * @param {{api: object, status: HTMLElement|null, panel: HTMLElement|null}} options
 */
export function createAssistant(editor, options) {
  const { api, status } = options;
  const panel = options.panel;
  const promptField = document.getElementById('llm-prompt');
  const toneSelect = document.getElementById('llm-tone');
  const output = document.getElementById('llm-output');
  const assistantStatus = document.getElementById('llm-status');

  /**
   * The current selection as a plain range, captured once.
   *
   * Captured *before* any `await`. Reading `editor.state.selection` after the
   * model has answered meant that a user who clicked somewhere else while
   * waiting had their rewrite applied over whatever they had moved to — silent
   * data loss caused entirely by a slow model.
   *
   * @returns {{from: number, to: number, text: string, empty: boolean, inline: boolean}}
   */
  function currentRange() {
    const { $from, $to, from, to } = editor.state.selection;
    return {
      from,
      to,
      text: from === to ? '' : editor.state.doc.textBetween(from, to, '\n'),
      empty: from === to,
      // Both ends of the selection share one text block, so the selection is
      // inside a sentence rather than spanning paragraphs. Captured here for the
      // same reason the range is: it describes the selection the user made, not
      // whatever the caret is doing when the model answers.
      inline: $from.parent === $to.parent,
    };
  }

  /** The current selection, or the whole document when nothing is selected. */
  function subject() {
    const range = currentRange();
    if (range.empty) return editor.getText();
    return range.text;
  }

  function say(message, kind = '') {
    if (!output) return;
    output.textContent = message;
    output.dataset.kind = kind;
  }

  /**
   * The model's text as editor content.
   *
   * A plain string would be parsed as *HTML* by `insertContentAt`, so a rewrite
   * containing `&` or an angle bracket would be silently mangled. Text nodes
   * rather than markup mean nothing is interpreted.
   *
   * An inline selection stays inline. Handing `insertContentAt` a paragraph for a
   * word inside a sentence makes it split the sentence in three — "the meeting
   * was" / the rewrite / "to friday" — which is not what replacing a word in a
   * sentence is supposed to do. A rewrite that really did come back as several
   * paragraphs is still shown in full in the output box, and is one undo away.
   *
   * @param {string} text
   * @param {boolean} inline
   * @returns {object[]}
   */
  function asContent(text, inline) {
    const clean = text.replace(/\r\n/g, '\n').trim();
    if (!clean) return [];
    if (inline) {
      return [{ type: 'text', text: clean.replace(/\n+/g, ' ') }];
    }
    const paragraphs = clean
      .split(/\n{2,}/)
      .map((paragraph) => paragraph.trim())
      .filter(Boolean);
    if (paragraphs.length === 0) return [];
    return paragraphs.map((paragraph) => ({
      type: 'paragraph',
      content: [{ type: 'text', text: paragraph }],
    }));
  }

  /**
   * Run an assistant action and place its result.
   *
   * @param {() => Promise<{result: string}>} action
   * @param {'replace'|'append'} placement
   * @param {string} emptyMessage
   */
  async function run(action, placement, emptyMessage) {
    // Taken before anything can await, so the replacement lands where the user
    // pointed rather than wherever the caret happens to be afterwards.
    const range = currentRange();
    const source = (range.empty ? editor.getText() : range.text).trim();
    if (!source) {
      say(emptyMessage, 'warn');
      return;
    }
    // A replace action with nothing selected has nothing to replace, and the
    // previous fallback sent the whole document to the model and then printed
    // the result in a box -- so the button labelled "Rewrite Selection" quietly
    // did something else entirely, and the "select the text" message could never
    // appear.
    if (placement === 'replace' && range.empty) {
      say(emptyMessage, 'warn');
      return;
    }

    say('Working…', 'busy');
    try {
      const { result } = await action();
      if (!result || !result.trim()) {
        say('The assistant returned nothing.', 'warn');
        return;
      }
      if (placement === 'replace') {
        const content = asContent(result, range.inline);
        if (content.length === 0) {
          say('The assistant returned nothing.', 'warn');
          return;
        }
        editor
          .chain()
          .insertContentAt({ from: range.from, to: range.to }, content)
          .setTextSelection(range.from)
          .run();
        // The result is shown as well as inserted: if it went somewhere the user
        // did not expect, they can see what it was and undo it.
        say(result, 'ok');
        flash(status, `Replaced ${range.text.length} characters`, 3000);
        return;
      }
      say(result, 'ok');
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      say(
        error && error.isOffline
          ? `${message} Summarize and Outline still work without a model.`
          : message,
        'error',
      );
    }
  }

  const wire = (id, handler) => {
    const button = document.getElementById(id);
    if (button) button.addEventListener('click', () => handler(button));
  };

  wire('btn-summarize', (button) =>
    run(() => api.summarize(subject()), 'append', 'There is no text to summarize.'),
  );
  // Labelled Rewrite Selection because it does exactly that: it replaces the
  // selection, and says so when there is not one rather than quietly taking
  // the whole document instead.
  wire('btn-rewrite', (button) =>
    run(
      () => api.rewrite(subject(), toneSelect ? toneSelect.value : 'professional'),
      'replace',
      'Select the text you want rewritten.',
    ),
  );
  wire('btn-outline', () =>
    run(() => api.outline(subject()), 'append', 'There is no text to outline.'),
  );

  const askButton = document.getElementById('btn-ask');
  const submitQuestion = () => {
    const question = promptField ? promptField.value.trim() : '';
    if (!question) {
      say('Type a question first.', 'warn');
      return;
    }
    run(() => api.ask(subject(), question), 'append', 'The document is empty.');
  };
  if (askButton) askButton.addEventListener('click', submitQuestion);
  if (promptField) {
    promptField.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        submitQuestion();
      }
    });
  }

  /**
   * Reflect the assistant backend's state so the panel does not offer
   * capabilities that cannot work.
   * @param {{backend: string, online: boolean, offline_capabilities?: string[]}} description
   */
  function describe(description) {
    if (!assistantStatus) return;
    const online = Boolean(description && description.online);
    const backend = (description && description.backend) || 'unknown';
    assistantStatus.textContent = online
      ? `Local model: ${backend}`
      : 'No local model connected. Summarize and Outline work offline; Rewrite, Ask and Scaffolds need a model.';
    assistantStatus.dataset.online = online ? 'true' : 'false';

    document.querySelectorAll('[data-needs-model]').forEach((control) => {
      control.disabled = !online;
      control.title = online ? '' : 'Connect a local model to use this.';
    });
    if (toneSelect) toneSelect.disabled = !online;
  }

  return { describe, say, subject, currentRange, asContent, run };
}
