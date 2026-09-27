# Contributing to LLex

Thanks for helping. This document is short on purpose; if something is unclear,
open an issue rather than guessing.

## Getting set up

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
npm ci
npm run build
```

`npm run build` is required before `llex` will start — the editor bundle is a
build artifact and is not committed. See the README for why.

## Before you open a pull request

Everything below must pass. CI runs the same commands, so this is the exact
list:

```bash
ruff check llex tests     # lint
mypy llex                 # types, --strict
pytest                    # Python tests, including the headless boot test
npm test                  # JavaScript unit tests
npm run build             # the bundle must build
```

## Where things live

| Concern | Module |
| --- | --- |
| Desktop shell, port binding, startup | `llex/main.py` |
| HTTP API, request models, security | `llex/api.py` |
| Document model, `.llex` format | `llex/document.py` |
| Parsing the editor's HTML | `llex/markup.py` |
| Assistant contract and backends | `llex/llm.py` |
| Document exports | `llex/export.py` |
| The editor | `llex/static/js/` |

Keep each module responsible for one thing. The editor is one module per
concern with explicit dependencies, because the previous single file had two
`DOMContentLoaded` handlers that could not see each other's `editor` and a
feature that had therefore never worked.

## Conventions

- **Type everything.** `mypy --strict` is on. `Any` needs a reason.
- **No bare `except Exception` without a comment** explaining why it is the
  right thing to do. Swallowing an error and returning success is a bug here;
  the frontend needs to be able to tell "saved" from "failed".
- **Comments explain *why*.** The code already says what it does.
- **Pure where it can be.** Anything without a reason to touch the DOM belongs
  in a module that does not, so it can be tested directly.
- **No new runtime dependencies without discussion.** The dependency list is
  short on purpose.

## Tests

A change needs a test that would have caught the bug. Prefer:

- a unit test for logic,
- a static contract test for wiring between the template, script and stylesheet,
- an entry in `tests/test_editor_boot.py` if the change affects whether the
  editor starts at all.

If two implementations must agree — word counting, for instance — pin both to
the same expectations from both test suites, so a change to one cannot pass
while the other drifts.

## Commit messages

Explain what was wrong and why the fix is what it is. A commit that says "fix
pagination" is not reviewable; one that says the page-position arithmetic was off
by one and the slice therefore began inside the block's opening token is.

## Reporting bugs

Include the OS, the Python and Node versions, `llex --version`, and what you
expected. If a document is involved, a `.llex` file that reproduces it is worth
far more than a description.
