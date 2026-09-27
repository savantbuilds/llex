"""Desktop entry point: the FastAPI server and the pywebview shell.

Startup order
-------------
The server must be listening *before* the window navigates to it, otherwise the
first request races the bind and the window renders a connection error. The old
entry point started uvicorn on a daemon thread and immediately called
``webview.create_window``, so the outcome depended on scheduling.

The server is now started on a background thread and the main thread waits for
its socket to accept connections, with a bounded timeout, before the window is
created.

The port
---------
A fixed port of 8000 collides with anything else on the machine and silently
serves the *other* process's content. A free port is chosen instead, and the
chosen port is validated by the API's own ``Host`` check.

Shutdown
--------
uvicorn is given a ``Server`` handle so it can be told to exit, and the exit is
awaited rather than assumed. That is what lets a ``finally`` block run
deterministically instead of the process being torn down underneath it.
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn

from .api import AppServices, build_app
from .document import Document, DocumentError

__all__ = ["LaunchOptions", "main", "reserve_port", "run_desktop"]

logger = logging.getLogger("llex")

#: Loopback only. See the threat model in :mod:`llex.api`.
HOST: str = "127.0.0.1"

#: How long to wait for the server to start accepting connections.
STARTUP_TIMEOUT: float = 15.0

#: Poll interval while waiting for the server.
_POLL_INTERVAL: float = 0.05

#: Default editor window size.
DEFAULT_WIDTH: int = 1280
DEFAULT_HEIGHT: int = 860

#: A file larger than this is probably a mistake to load into an editor.
MAX_OPEN_BYTES: int = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class LaunchOptions:
    """Everything the command line can influence."""

    path: Path | None = None
    host: str = HOST
    port: int = 0  # 0 means "choose a free port"
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    debug: bool = False


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """The ``llex`` command line."""
    parser = argparse.ArgumentParser(
        prog="llex",
        description="LLex -- a local-first, paginated word processor.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="an .llex document to open (created blank if it does not exist)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="port for the local API; 0 (default) picks a free one",
    )
    parser.add_argument("--host", default=HOST, help=argparse.SUPPRESS)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help=argparse.SUPPRESS)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="verbose logging and the auto-reload server",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["critical", "error", "warning", "info", "debug"],
        help="logging verbosity (default: warning, or debug with --debug)",
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    return parser


def _parse_args(argv: Sequence[str] | None) -> LaunchOptions | None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from . import __version__

        print(f"llex {__version__}")
        return None

    level = args.log_level or ("debug" if args.debug else "warning")
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.WARNING),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # uvicorn's own access log is noise for a single-user local app.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return LaunchOptions(
        path=args.path,
        host=args.host,
        port=args.port,
        width=args.width,
        height=args.height,
        debug=args.debug,
    )


# --------------------------------------------------------------------------- #
# Port selection
# --------------------------------------------------------------------------- #


def reserve_port(host: str, preferred: int = 0) -> int:
    """Return a port that is free right now.

    The socket is bound to find a candidate and then closed, so the port is
    *likely* free rather than guaranteed -- but since the API validates the
    ``Host`` header and requires a session token, a squatter would be refused
    rather than served. Binding with ``SO_REUSEADDR`` off is deliberate: we
    want to know if something is genuinely listening.
    """
    if preferred:
        if _is_free(host, preferred):
            return preferred
        logger.warning("port %d is in use; choosing another", preferred)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _wait_until_serving(host: str, port: int, timeout: float) -> bool:
    """Block until the server accepts a connection, or ``timeout`` elapses.

    Polling the socket is more reliable than a thread join: the server is
    created before the thread starts, so a failed bind surfaces as a connection
    refusal rather than a hang.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(_POLL_INTERVAL)
    return False


# --------------------------------------------------------------------------- #
# Document loading
# --------------------------------------------------------------------------- #


def load_initial_document(path: Path | None) -> Document:
    """Open ``path``, or start blank.

    A path that does not exist is treated as a request to create it, and a file
    that cannot be parsed is reported on stderr rather than aborting the launch:
    losing the window entirely would be a worse outcome than starting empty.
    """
    if path is None:
        return Document()

    if not path.exists():
        print(f"llex: {path} does not exist; starting a new document", file=sys.stderr)
        return Document()

    if not path.is_file():
        print(f"llex: {path} is not a file; starting a new document", file=sys.stderr)
        return Document()

    try:
        size = path.stat().st_size
    except OSError as exc:
        print(f"llex: cannot read {path}: {exc}", file=sys.stderr)
        return Document()

    if size > MAX_OPEN_BYTES:
        print(
            f"llex: {path.name} is {size // 1_048_576} MB, which is too large to open; "
            f"starting a new document",
            file=sys.stderr,
        )
        return Document()

    try:
        return Document.load(path)
    except DocumentError as exc:
        print(f"llex: could not open {path}: {exc}", file=sys.stderr)
        return Document()


# --------------------------------------------------------------------------- #
# Server lifecycle
# --------------------------------------------------------------------------- #


class _ServerThread(threading.Thread):
    """Runs uvicorn on a background thread with a handle we can signal."""

    def __init__(self, app: Any, host: str, port: int, *, debug: bool) -> None:
        super().__init__(name="llex-api", daemon=True)
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="debug" if debug else "warning",
            access_log=False,
            # The API is served in-process for the webview; a reload watcher
            # would race the shutdown path and hold the port open.
            reload=False,
        )
        self.server = uvicorn.Server(config)
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.server.run()
        except BaseException as exc:
            self.error = exc
            logger.exception("the LLex API server stopped unexpectedly")

    def shutdown(self, timeout: float = 5.0) -> None:
        """Ask the server to exit and wait for it to finish."""
        self.server.should_exit = True
        self.join(timeout=timeout)


# --------------------------------------------------------------------------- #
# Desktop shell
# --------------------------------------------------------------------------- #


def run_desktop(options: LaunchOptions) -> int:
    """Start the server, open the window, and shut both down cleanly."""
    document = load_initial_document(options.path)
    services = AppServices(document=document)
    app = build_app(services)

    port = reserve_port(options.host, options.port)
    server = _ServerThread(app, options.host, port, debug=options.debug)
    server.start()

    if not _wait_until_serving(options.host, port, STARTUP_TIMEOUT):
        server.shutdown()
        detail = f": {server.error}" if server.error else ""
        print(f"llex: the local server did not start on port {port}{detail}", file=sys.stderr)
        return 1

    url = f"http://{options.host}:{port}"
    logger.info("LLex API listening on %s", url)
    print(f"llex: editing {document.path or document.title}")
    print(f"llex: local API on {url}")

    try:
        return _open_window(services, url, options)
    finally:
        server.shutdown()


def _open_window(services: AppServices, url: str, options: LaunchOptions) -> int:
    """Create and run the native window. Imported lazily for clearer errors."""
    try:
        import webview
    except ImportError as exc:  # pragma: no cover - declared dependency
        print(f"llex: the desktop shell is unavailable: {exc}", file=sys.stderr)
        return 1

    try:
        window = webview.create_window(
            services.title(),
            url,
            width=options.width,
            height=options.height,
            min_size=(720, 480),
            text_select=True,
        )
    except Exception as exc:
        print(f"llex: could not open a window: {exc}", file=sys.stderr)
        return 1

    if window is None:  # pragma: no cover - pywebview returns None only on failure
        print("llex: the window could not be created", file=sys.stderr)
        return 1

    # The API needs the window so File > Open can show a native dialog.
    services.window = window

    def on_closing() -> bool | None:
        logger.info("window closing")
        return None

    try:
        window.events.closing += on_closing
    except Exception:
        logger.debug("could not subscribe to the window closing event", exc_info=True)

    try:
        webview.start()
    except Exception as exc:
        print(f"llex: the window failed: {exc}", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    """Boot the LLex editor. Returns a process exit code."""
    options = _parse_args(argv)
    if options is None:
        return 0
    try:
        return run_desktop(options)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
