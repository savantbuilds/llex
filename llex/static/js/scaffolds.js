/**
 * The scaffold pipeline.
 *
 * A scaffold is an inline prompt attached to a highlighted span. The user
 * highlights text, adds a scaffold from the context menu, and later runs the
 * batch; each fragment is replaced with generated text.
 *
 * The previous implementation kept this in a *second* `DOMContentLoaded`
 * handler whose closure could not see the `editor` created in the first one, so
 * clicking "Execute Scaffolds" threw a `ReferenceError` and the feature had
 * never worked.
 */

/**
 * @typedef {object} Scaffold
 * @property {string} id
 * @property {string} instruction
 * @property {string} text
 * @property {number} start
 * @property {number} end
 */

/**
 * Collect the scaffolds in a document, merging adjacent runs that share an id
 * so one scaffold spanning several nodes is executed once.
 *
 * @param {import('@tiptap/pm/model').Node} doc
 * @returns {Scaffold[]}
 */
export function collectScaffolds(doc) {
  /** @type {Map<string, Scaffold>} */
  const found = new Map();

  doc.descendants((node, pos) => {
    const mark = node.marks.find((candidate) => candidate.type.name === 'scaffold');
    if (!mark) return;

    const id = mark.attrs.id;
    if (!id) return;

    const existing = found.get(id);
    if (existing) {
      existing.text += node.textContent;
      existing.end = pos + node.nodeSize;
      return;
    }
    found.set(id, {
      id,
      instruction: mark.attrs.instruction || '',
      text: node.textContent,
      start: pos,
      end: pos + node.nodeSize,
    });
  });

  return Array.from(found.values()).filter((item) => item.text.trim().length > 0);
}

/**
 * Order replacement edits from the end of the document backwards.
 *
 * Each replacement changes the length of the document, so applying edits in
 * document order would shift the positions of everything after the first one
 * and silently replace the wrong text. Sorting by descending start position
 * makes each edit independent of the ones applied before it.
 *
 * @param {{id: string, text?: string}[]} results
 * @param {Scaffold[]} targets
 * @returns {{target: Scaffold, text: string}[]}
 */
export function orderScaffoldEdits(results, targets) {
  const byId = new Map(targets.map((target) => [target.id, target]));
  return results
    .map((result) => ({ result, target: byId.get(result.id) }))
    .filter((entry) => entry.target)
    .filter((entry) => typeof entry.result.text === 'string' && entry.result.text !== '')
    .sort((a, b) => b.target.start - a.target.start)
    .map((entry) => ({ target: entry.target, text: entry.result.text }));
}

/**
 * Apply the ordered edits to a transaction.
 *
 * The scaffold mark is removed before the text is replaced, so a failure part
 * way through cannot leave orphaned prompts behind.
 *
 * @param {import('@tiptap/pm/transform').Transaction} tr
 * @param {{target: Scaffold, text: string}[]} edits
 * @returns {import('@tiptap/pm/transform').Transaction} The same transaction.
 */
export function applyScaffoldEdits(tr, edits) {
  const markType = tr.doc.type.schema.marks.scaffold;
  for (const { target, text } of edits) {
    if (markType) tr.removeMark(target.start, target.end, markType);
    tr.insertText(text, target.start, target.end);
  }
  return tr;
}

/**
 * Run every scaffold in the document.
 *
 * @param {import('@tiptap/core').Editor} editor
 * @param {{run: (scaffolds: object[]) => Promise<{results: object[]}>}} api
 * @returns {Promise<{total: number, applied: number, failures: object[]}>}
 */
export async function runScaffolds(editor, api) {
  const targets = collectScaffolds(editor.state.doc);
  if (targets.length === 0) {
    return { total: 0, applied: 0, failures: [] };
  }

  const response = await api.run(
    targets.map((target) => ({
      id: target.id,
      text: target.text,
      instruction: target.instruction,
    })),
  );

  const results = (response && response.results) || [];
  const failures = results.filter((result) => result.status && result.status !== 'ok');
  const edits = orderScaffoldEdits(results, targets);

  if (edits.length === 0) {
    return { total: targets.length, applied: 0, failures };
  }

  editor.view.dispatch(applyScaffoldEdits(editor.state.tr, edits));
  return { total: targets.length, applied: edits.length, failures };
}
