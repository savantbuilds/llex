# LLex

**Local Language Expression Engine** — a local-first, paginated word processor
with a pluggable local LLM assistant.

LLex renders a document as a stack of real 8.5 × 11 inch pages, the way Word
does, rather than as an infinite scrolling canvas. Text flows onto a new page
when it overflows, page breaks are part of the document (so they are saved,
undone and exported), and the editor prints to paper without reflowing.

No document ever leaves your machine. The assistant talks to a model you run
yourself.

![status: pre-1.0](https://img.shields.io/badge/status-pre--1.0-blue)

---

## What it does

**A paginated editor that behaves like a word processor**

- Hard 8.5 × 11 in pages with configurable margins, laid out in the browser and
  re-flowed from measured layout.
- Ribbons, keyboard shortcuts, a live document outline built from real headings,
  a formatting mini-toolbar, and undo/redo across the whole stack.
- Zoom, focus mode, dark/light theming, and a print stylesheet that emits the
  pages as paper.

**Real document formats**

| Format | Notes |
| --- | --- |
| `.docx` | Office Open XML, with page geometry, margins, headings and lists |
| `.odt` | OpenDocument Text, with nested lists and inline styles |
| `.rtf` | Rich Text Format 1.9, with a colour table and Unicode escapes |
| `.html` | Standalone page with print CSS matching the editor's page setup |
| `.md` | CommonMark, with headings, emphasis, lists and fenced code |
| `.epub` | EPUB 3 with a navigation document and a stable identifier |
| `.txt` | Plain text |
| `.zip` | The standalone HTML page, for sharing |

PDF is deliberately **not** offered. Producing one from HTML faithfully needs a
layout and rasterisation engine — the very dependency this project moved away
from — and any pure-Python approximation would silently emit documents whose
pagination did not match what you see. Print to PDF from the editor instead.

**A local assistant, honestly**

Summarise, outline, rewrite in a named tone, ask a question about the document,
and run a batch of inline **scaffolds** — prompts you attach to highlighted
text that are resolved later.

Without a model configured, summarising and outlining still work using a
deterministic extractive algorithm that selects the most representative
sentences. The operations that genuinely need a model say so instead of
pretending:

> No local model connected. Summarize and Outline work offline; Rewrite, Ask and
> Scaffolds need a model.

---

## Install

Requires **Python 3.10+** and **Node.js 20+**.

```bash
git clone https://github.com/savantpseudoist/llex.git
cd llex

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

npm ci                            # install the front-end toolchain
npm run build                     # bundle the editor (required, see below)
```

`npm run build` produces `llex/static/editor.bundle.js`, which the editor
loads. It is a build artifact and is **not** committed, so the shipped editor
can never silently drift from its source. If you launch without building it,
LLex tells you exactly that instead of showing a blank page.

## Run

```bash
llex                              # or: python -m llex
llex path/to/document.llex         # open an existing document
llex --port 8765                   # pin the port
llex --debug --log-level info      # verbose logging
llex --help
```

LLex starts a local HTTP server on a free loopback port and opens a native
window. The port is chosen at launch and printed, so you can find it:

```
llex: editing Untitled Document
llex: local API on http://127.0.0.1:55472
```

## Connect a local model

LLex speaks the OpenAI-compatible chat-completions API, so anything you already
run works. Set the endpoint and model name:

| Variable | Default | Meaning |
| --- | --- | --- |
| `LLEX_LOCAL_MODEL` | *(unset)* | Base URL, e.g. `http://127.0.0.1:11434/v1` |
| `LLEX_LOCAL_MODEL_NAME` | `local-model` | Model identifier to request |
| `LLEX_LOCAL_MODEL_TIMEOUT` | `120` | Per-request timeout, seconds |

```bash
# Ollama
ollama pull llama3
export LLEX_LOCAL_MODEL=http://127.0.0.1:11434/v1
export LLEX_LOCAL_MODEL_NAME=llama3
llex
```

```bash
# llama.cpp server
./llama-server -m model.gguf --port 8080
export LLEX_LOCAL_MODEL=http://127.0.0.1:8080/v1
llex
```

The assistant sidebar reports which backend is live. Unset the variable and the
offline extractive engine takes over automatically.

---

## How it works

```
llex/
├── main.py          # desktop entry point: free-port binding, startup
│                    # handshake, deterministic shutdown
├── api.py           # the local HTTP API, token-guarded
├── document.py      # the document model and the .llex container
├── markup.py        # tolerant structural parser for the editor's HTML
├── llm.py           # the assistant contract and its two backends
├── export.py        # pure-Python writers for the eight formats
├── templates/       # the editor shell
└── static/js/       # the editor, one module per concern
```

**The browser owns the document.** TipTap renders and edits HTML; the Python
side treats that HTML as authoritative and parses it with `llex.markup` when it
needs structure. There is no second model to keep in sync.

**Page breaks are real.** The document is a sequence of `page` nodes. The
pagination engine measures rendered page height, and moves the last block of an
overflowing page onto the next one. A block too large for any page is allowed to
make its page grow, so its text stays readable and selectable rather than being
clipped out of existence.

**Decisions are separated from the DOM.** `planReflow()` is a pure function of
the document and the measured overflow, which is what makes the document-position
arithmetic testable without a browser — and that arithmetic is worth testing,
because getting it wrong does not throw, it corrupts a document.

### Security

LLex serves HTTP on loopback, which is **not** isolation: any page open in
another tab can send requests to `127.0.0.1:<port>`, and DNS rebinding lets an
attacker's domain resolve to loopback. Three defences apply to every route:

1. **Host header validation** rejects any name that is not loopback.
2. **A 256-bit per-session bearer token**, injected into the served page and
   required on every `/api` call, compared with `secrets.compare_digest`.
3. **No wildcard CORS**, so cross-origin JavaScript cannot read any response.

A document you have open is not reachable from your browser.

### The `.llex` format

UTF-8 JSON with a `format_version` discriminator, so files from a newer LLex are
refused with a clear message instead of being misread, and files from an older
one still open. Saves are atomic: the payload is written to a temporary file in
the destination directory and moved into place, so an interrupted save cannot
destroy the previous good copy.

---

## Development

```bash
pip install -e ".[dev]"   # pytest, mypy, ruff, coverage
npm ci

npm run build             # bundle the editor
npm run watch             # rebuild on change
npm test                  # JavaScript unit tests

pytest                    # Python tests, including the headless boot test
ruff check llex tests
mypy llex
```

`pytest` includes an end-to-end test that boots the real server, serves the real
template, and evaluates the real bundle in a headless DOM to confirm a real
ProseMirror editor is constructed. It skips cleanly if Node, the bundle or
`jsdom` are unavailable.

There is also a static contract suite that checks the template, the script and
the stylesheet agree with each other — that every id the script looks up exists,
that no menu item is dead, and that the page geometry constants match the
backend. Wiring by element id fails silently otherwise: the control just does
nothing.

### Continuous integration

`.github/workflows/ci.yml` runs lint, types, tests and coverage on Python; the
bundle, JavaScript unit tests and headless boot test on the front end; a wheel
and sdist build with the assets verified; and the test suite across Python 3.10
to 3.13.

## Keyboard shortcuts

| | |
| --- | --- |
| `Ctrl/Cmd + N` | New document |
| `Ctrl/Cmd + O` | Open |
| `Ctrl/Cmd + S` | Save |
| `Ctrl/Cmd + Shift + S` | Save as |
| `Ctrl/Cmd + P` | Print |
| `Ctrl/Cmd + B / I / U` | Bold, italic, underline |
| `Ctrl/Cmd + Z / Y` | Undo, redo |
| `Ctrl/Cmd + = / - / 0` | Zoom in, out, reset |

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Start at [CONTRIBUTING.md](CONTRIBUTING.md). Community rules are in
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and security reporting in
[SECURITY.md](SECURITY.md). Design notes and the engineering journal are in
[`dev_docs/`](dev_docs/), and the rationale for the stack is in
[`inspirations/`](inspirations/).
