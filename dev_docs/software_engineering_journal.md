# LLex software engineering journal

A record of what changed and, more usefully, why. Written for the next person
who has to make a decision here.

---

## 2026-03-30 — Foundation

The original premise: reimagine the project as a word processor that matches MS
Word's behaviour while integrating a local LLM the way AI code editors do. The
working tree was minimal, so the first iteration optimised for a clean
architecture over feature completeness, with dependencies kept to the standard
library where possible.

Decisions at the time:

1. **Domain model** — a `Document` holding metadata, styles and JSON
   persistence, with serialisation kept out of the UI.
2. **UI shell** — Tkinter, for a lightweight editing canvas.
3. **LLM bridge** — a stub exposing `summarize`, `rewrite_with_tone` and
   `outline` so the integration surface was visible from the start.
4. **Launch** — `llex/main.py` with a `llex` console script.

The journal entry for the implementation pass records a `DraftEditor`
enforcing an 816×1056 pixel page and a `PageWidget`/`PagedEditorContainer`
pagination system. The next two entries are what happened to that.

---

## 2026-04 — Migration to a web front end

**Context.** The Tkinter and then PyQt6 editors had a cursor-overlap bug and
multi-widget layout problems. `dev_docs/core_architecture.md` is emphatic that
layout, pagination, navigation and save/load must be bulletproof *before* any
LLM feature is added, because "if the basic text formatting, pagination,
navigation, or save/load states are brittle, AI integrations will only amplify
the chaos."

**Decision.** Move to a browser-based editor: FastAPI serving a local API,
pywebview as the shell, TipTap (vanilla JS) for the editor core. Commit
`c41c180`, "Remove deprecated ui framework in favor of html web interface".
`inspirations/05_Tech_Stack_Migration_Strategy.md` records the reasoning: a
pageless physical layout, a React-free translation of the Ribbon, and no
collab infrastructure.

**What survived.** The architecture. The browser does own the document, the
pagination engine measures rendered layout, and the LLM bridge is a contract
that can be swapped. That is the shape the project still has.

**What did not.** Everything that reached across the boundary by reaching into
a widget tree. `llex/export.py` walked a `QTextDocument` via
`blockFormat()` and `fragment().charFormat()`; `llex/ui_tk.py` remained in the
tree unreferenced. Neither could ever have worked once the editor was a webview,
and neither was removed, so the repository carried a PyQt6 dependency and an
eight-item Download menu with no implementation behind any of it.

---

## 2026-09 — Correctness and honesty pass

**Context.** The project could not be installed, could not be run, and had
never been executed end to end. The work below was driven by making the thing
actually start, then by tests that read real output back.

### The distribution did not work

`[tool.setuptools.packages.find] where = ["llex"]` pointed at a nested
`llex/llex` package that does not exist, so `pip install .` produced an empty
wheel. Runtime dependencies were wrong at both ends: `PyQt6` was declared but
only used by dead modules, while `fastapi`, `uvicorn`, `pydantic` and the
webview shell were import-time requirements that were never declared. A clean
checkout died with `ModuleNotFoundError: No module named 'fastapi'`.

The webview package is published as `pywebview`; the declared name `webview`
resolves to an unrelated, deprecated project.

**Decision.** `pyproject.toml` becomes the single source of truth.
`requirements.txt`, which had already drifted, is deleted.

### Save reported success on failure

`/api/save` caught every exception and returned `200 OK` with
`{"status": "error"}` in the body. The frontend ignored the status and set its
label to "Saved!" — so a rejected write was indistinguishable from a successful
one. Every failure mode was being reported as an HTTP 200.

**Decision.** Errors raise `HTTPException` with a real status code, and the
frontend checks it. 404 for a missing file, 422 for an unreadable one, 501 when
a native dialog is needed but no window is attached, 503 when the assistant has
no backend, 502 when a backend answered with something unusable.

### The local API was unauthenticated

Binding to `127.0.0.1` is not isolation. Any page in another tab can send
requests to `http://127.0.0.1:<port>`, and DNS rebinding lets an attacker's
domain resolve to loopback. Either path would have exposed the open document
and allowed writes to the user's disk.

**Decision.** Host header validation (rejects rebinding, since the attacker
controls the resolving name), a 256-bit per-session bearer token required on
every `/api` route, and no wildcard CORS.

### The assistant was lying

`LocalLLMBridge.rewrite_with_tone` called `random.choice`, so identical input
produced different output on every invocation — the opposite of what a document
editor can tolerate, and enough to corrupt a file on retry. Every method
returned a bracket-wrapped placeholder, which the frontend then inserted into
the user's document while implying a model had produced it.

**Decision.** Two backends behind one contract: a real OpenAI-compatible client
(covering Ollama, llama.cpp, LM Studio and vLLM with no new dependency), and a
deterministic extractive engine for when no model is configured. The extractive
engine is extractive on purpose — it cannot hallucinate, so an offline LLex
summarises predictably instead of inventing content.

Tasks that genuinely require generation now raise
`AssistantUnavailableError` instead of returning fiction, and the sidebar says
so: "No local model connected. Summarize and Outline work offline; Rewrite,
Ask and Scaffolds need a model."

Failures split into two kinds, which the single exception had conflated:
`AssistantUnavailableError` (nothing to talk to — may fall back) and
`AssistantResponseError` (a backend replied badly — must be surfaced). Without
that split, a model returning empty text silently degraded to heuristics and a
wrong model name was papered over.

### Exports

`llex/export.py` walked a `QTextDocument`. It is replaced by eight writers
driven by a tolerant structural parser, `llex.markup`, built for the purpose.
`Exporter.render()` returns bytes and performs no I/O, so every format is
testable without touching the filesystem, and `SUPPORTED_FORMATS` is the single
list behind both the API and the Download menu.

PDF is deliberately absent, and the reason is recorded in the module: producing
one faithfully from HTML needs a layout and rasterisation engine, which is the
dependency the project moved away from, and any pure-Python approximation would
emit documents whose pagination did not match the editor.

### The front end had never run

`editor.js` was one 690-line file containing two independent `DOMContentLoaded`
handlers. The second could not see the `editor` const declared in the first, so
"Execute Scaffolds" threw a `ReferenceError` — the feature had never worked,
despite a commit message claiming it did. Around a dozen further controls were
dead: File > New, all eight Download entries, Edit, View, Settings (no open
handler and no cancel handler), Summarize, Rewrite, Ask, and three mini-toolbar
buttons. The status bar was hardcoded to "Ready".

**Decision.** One module per concern under `llex/static/js/`, each with explicit
dependencies.

### Pagination could lose text

Two defects, both invisible in normal use:

- **A loaded document was never re-paginated.** Page breaks in a saved file were
  computed at a different zoom, with different fonts, possibly on another
  machine, and `.page` is `overflow: hidden`. A document could therefore open
  with text that was neither visible nor selectable. Loading now collapses every
  page to one and re-flows from measured layout.
- **A page whose single block could not fit was abandoned** by a
  `childCount <= 1` guard, clipping that text away permanently. Such a page is
  now marked `data-overfull` and allowed to grow, so the content stays readable
  and selectable and the next block continues on a fresh page.

The document-position arithmetic was also off by one, so the slice began inside
the block's opening token. `planReflow()` is now a pure function of the document
and the measured overflow, which is what makes that arithmetic testable without
a browser — and worth testing, because getting it wrong corrupts a document
without raising.

### Three bugs that only a real browser would find

The JavaScript `\w` class is ASCII-only, even with the `u` flag, so a wholly
non-Latin document reported **0 words** while the Python side — whose `\w` is
Unicode-aware — counted it correctly. `stepFontSize` sent an off-scale size to
the extreme of the scale and could not parse the `pt`-suffixed values its own
dropdown produced. And the outline listed only heading levels 3–6, hiding a
document's top two heading levels from its own table of contents.

A headless-DOM test that boots the real server and evaluates the real bundle
found three more: the application died during boot because TipTap namespaces
extension storage by extension name, so `editor.storage.stats` was always
undefined; `setReady` wrote to the status bar's `<footer>`, deleting the
statistics span and the zoom indicator; and StarterKit v3 already bundles
Underline, so registering it again installed a duplicate extension.

### Two ports, and a free-port check that was backwards

Running the desktop app for the first time — the first time anyone had — showed
`ERR_UNSAFE_PORT` on `http://127.0.0.1:1`. Four problems:

- Ports the embedded browser refuses to navigate to were accepted, producing a
  bare browser error page instead of a program error.
- Privileged ports were accepted, where binding may fail or need elevation.
- `_is_free` set `SO_REUSEADDR`, which on Windows does not mean "reuse a
  TIME_WAIT address" — it lets a *second* socket bind an address another
  process is already listening on. The check reported busy ports as free, which
  is the opposite of its purpose and could point LLex at another process's
  server.
- A refused `--port` was silently replaced, leaving the user wondering why the
  app was not where they put it.

`describe_port_problem()` now distinguishes each reason, an explicit port is
honoured or refused, and the process exits 2 with an explanation.

---

## Principles that held up

1. **Make it run first.** Almost every defect above was invisible to inspection
   and obvious the moment something executed. The first version of the launcher
   had a race between the server starting and the window navigating; the front
   end had never been loaded at all.
2. **Read the output back.** "The export produced bytes" is not the same claim as
   "the export is a valid `.odt`", and the difference is a test that reopens the
   file with the library that defines it.
3. **Separate decisions from effects.** `planReflow()` is pure, which is why the
   bug that would corrupt a document is caught by a unit test instead of by a
   user. `Exporter.render()` does no I/O for the same reason.
4. **Report failure as failure.** The three worst defects — a save that reported
   success while failing, an assistant that invented text, a port check that
   reported busy ports as free — were all failures to be honest about a negative
   result.
5. **Two implementations must not drift.** Word counting exists in both
   languages, so both test suites pin the same expectations, including the case
   that caught the `\w` bug.

## Open questions

- **Multi-page heading handling.** A heading must not be orphaned at the foot of
  a page, and the schema has no concept of a "keep with next" attribute. The
  engine currently only moves whole blocks, so `break-after: avoid` in CSS does
  not influence pagination.
- **Widow and orphan control** is likewise unmodelled.
- **Scaffold failure recovery.** A batch reports per-item status, but a partial
  failure leaves the document in a mixed state with no undo affordance beyond
  the single transaction.
- **Large documents.** The engine moves one block per animation frame, so a very
  large paste cascades visibly. Whether that is progress or noise is a judgement
  call that has not been made deliberately.
