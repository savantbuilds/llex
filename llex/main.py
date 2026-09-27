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

#: How many candidate ports to try before giving up.
_PORT_ATTEMPTS: int = 16

#: Poll interval while waiting for the server.
_POLL_INTERVAL: float = 0.05

#: Default editor window size.
DEFAULT_WIDTH: int = 1280
DEFAULT_HEIGHT: int = 860

#: A file larger than this is probably a mistake to load into an editor.
MAX_OPEN_BYTES: int = 64 * 1024 * 1024

#: Ports the embedded Chromium/WebView2 refuses to navigate to.
#:
#: Asking it for one of these produces a bare ``ERR_UNSAFE_PORT`` page with no
#: indication of what went wrong, so they are treated as unusable rather than
#: discovered by the user. The list is Chromium's; the single-digit entries are
#: the well-known services it refuses on principle.
BLOCKED_PORTS: frozenset[int] = frozenset(
    {
        1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25,
        37, 42, 43, 53, 69, 77, 79, 87, 95,
        101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135,
        137, 139, 143, 161, 179, 389, 427, 465,
        512, 513, 514, 515, 526, 530, 531, 532, 540, 548, 554, 556, 563, 587, 601,
        636, 989, 990, 993, 995, 1719, 1720, 1723, 2049, 3659, 4045, 5060, 5061,
        6000, 6566, 6665, 6666, 6667, 6668, 6669, 6697, 10080,
    }
)

#: Below this, binding needs elevated privileges on Windows and POSIX.
PRIVILEGED_PORT_LIMIT: int = 1024

#: Upper bound on a port number, from the IANA registry.
MAX_PORT: int = 65535


class PortUnavailableError(RuntimeError):
    """Raised when a port cannot serve the editor's webview."""


def describe_port_problem(host: str, port: int) -> str | None:
    """Why ``port`` cannot be used, or ``None`` if it is fine.

    Distinguishes the reasons a user needs to hear about, because "it didn't
    work" is not actionable.
    """
    if not 1 <= port <= MAX_PORT:
        return f"{port} is not a valid port number"
    if port < PRIVILEGED_PORT_LIMIT:
        return (
            f"port {port} is privileged; ports below {PRIVILEGED_PORT_LIMIT} "
            "require elevated privileges"
        )
    if port in BLOCKED_PORTS:
        return f"port {port} is blocked by the embedded browser (ERR_UNSAFE_PORT)"
    if not _is_free(host, port):
        return f"port {port} is already in use"
    return None


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
    """Return a port the webview can actually be pointed at.

    A port is only acceptable if it is bindable *and* not on the embedded
    browser's blocked list. When ``preferred`` is unusable the reason is
    reported rather than silently substituting a different port: a user who asked
    for a specific port needs to be told it was refused, not left wondering why
    the app is on some other port.
    """
    if preferred:
        problem = describe_port_problem(host, preferred)
        if problem is None:
            return preferred
        raise PortUnavailableError(f"cannot use port {preferred}: {problem}")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        for _ in range(_PORT_ATTEMPTS):
            port = int(probe.getsockname()[1])
            if port >= PRIVILEGED_PORT_LIMIT and port not in BLOCKED_PORTS:
                return port
            # Astronomically unlikely, but a refused port must never be
            # returned: the webview would show a bare ERR_UNSAFE_PORT page.
            probe.bind((host, 0))
    raise PortUnavailableError(
        "could not find a usable port; every candidate was blocked by the browser"
    )


def _is_free(host: str, port: int) -> bool:
    """Whether ``port`` can be bound right now.

    ``SO_REUSEADDR`` is deliberately *not* set. On Windows it does not mean
    "reuse a TIME_WAIT address" as it does on POSIX -- it allows a second socket
    to bind an address another process is already listening on. Setting it here
    made a busy port look free, which is the exact opposite of the check's
    purpose and let LLex open a window pointed at someone else's server.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
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

    try:
        port = reserve_port(options.host, options.port)
    except PortUnavailableError as exc:
        # Opening a window anyway would show a bare browser error page with no
        # indication of the cause, so refuse and say why.
        print(f"llex: {exc}", file=sys.stderr)
        if not options.port:
            print("llex: try again, or pass --port to choose one yourself", file=sys.stderr)
        return 2

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
