/**
 * The assistant sidebar.
 *
 * Every action here can genuinely fail -- most often because no local model is
 * configured -- so the UI reports the real reason instead of implying success.
 * That distinction was previously lost: a placeholder string was inserted into
 * the user's document while looking like model output.
 */

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

  /** The current selection, or the whole document when nothing is selected. */
  function subject() {
    const { from, to } = editor.state.selection;
    if (from === to) return editor.getText();
    return editor.state.doc.textBetween(from, to, '\n');
  }

  function say(message, kind = '') {
    if (!output) return;
    output.textContent = message;
    output.dataset.kind = kind;
  }

  /**
   * Run an assistant action and place its result.
   *
   * @param {() => Promise<{result: string}>} action
   * @param {'replace'|'append'} placement
   * @param {string} emptyMessage
   */
  async function run(action, placement, emptyMessage) {
    const source = subject().trim();
    if (!source) {
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
      if (placement === 'replace' && !editor.state.selection.empty) {
        editor.chain().focus().insertContentAt(editor.state.selection, result).run();
        say('Replaced the selection.', 'ok');
      } else {
        say(result, 'ok');
      }
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

  return { describe, say, subject, run };
}
