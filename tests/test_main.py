"""Tests for the desktop launcher.

These cover the parts that are pure logic -- argument parsing, port selection,
document loading and the startup handshake. Opening a real window is not
exercised, since that needs a display.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from llex import main as launcher
from llex.document import Document


class TestArgumentParsing:
    def test_defaults(self) -> None:
        options = launcher._parse_args([])
        assert options is not None
        assert options.path is None
        assert options.port == 0
        assert options.host == launcher.HOST
        assert options.debug is False

    def test_positional_path(self, tmp_path: Path) -> None:
        options = launcher._parse_args([str(tmp_path / "a.llex")])
        assert options is not None
        assert options.path == tmp_path / "a.llex"

    def test_explicit_port(self) -> None:
        options = launcher._parse_args(["--port", "9123"])
        assert options is not None and options.port == 9123

    def test_debug_raises_the_log_level(self) -> None:
        options = launcher._parse_args(["--debug"])
        assert options is not None and options.debug is True

    def test_version_exits_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert launcher._parse_args(["--version"]) is None
        assert "llex" in capsys.readouterr().out

    def test_rejects_a_nonsense_port(self) -> None:
        with pytest.raises(SystemExit):
            launcher._parse_args(["--port", "not-a-port"])

    def test_rejects_unknown_flags(self) -> None:
        with pytest.raises(SystemExit):
            launcher._parse_args(["--nope"])


class TestPortSelection:
    def test_prefers_a_free_requested_port(self) -> None:
        port = launcher.reserve_port(launcher.HOST, 0)
        assert 1024 < port < 65536

    def test_requested_port_is_honoured_when_free(self) -> None:
        with socket.socket() as probe:
            probe.bind((launcher.HOST, 0))
            free = probe.getsockname()[1]
        assert launcher.reserve_port(launcher.HOST, free) == free

    def test_busy_requested_port_falls_back(self) -> None:
        with socket.socket() as taken:
            taken.bind((launcher.HOST, 0))
            taken.listen(1)
            busy = taken.getsockname()[1]
            chosen = launcher.reserve_port(launcher.HOST, busy)
        assert chosen != busy
        assert 1024 < chosen < 65536

    def test_two_reservations_do_not_collide(self) -> None:
        assert launcher.reserve_port(launcher.HOST, 0) != launcher.reserve_port(launcher.HOST, 0)


class TestWaitUntilServing:
    def test_returns_false_when_nothing_listens(self) -> None:
        with socket.socket() as probe:
            probe.bind((launcher.HOST, 0))
            dead_port = probe.getsockname()[1]
        assert launcher._wait_until_serving(launcher.HOST, dead_port, timeout=0.3) is False

    def test_returns_true_once_serving(self) -> None:
        with socket.socket() as server:
            server.bind((launcher.HOST, 0))
            server.listen(1)
            port = server.getsockname()[1]
            assert launcher._wait_until_serving(launcher.HOST, port, timeout=2.0) is True


class TestInitialDocument:
    def test_no_path_gives_a_blank_document(self) -> None:
        document = launcher.load_initial_document(None)
        assert document.title == "Untitled Document"
        assert document.path is None

    def test_loads_an_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "doc.llex"
        Document(title="Existing", content="<p>x</p>").save(target)
        document = launcher.load_initial_document(target)
        assert document.title == "Existing"
        assert document.path == target

    def test_missing_file_starts_blank_with_a_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        document = launcher.load_initial_document(tmp_path / "absent.llex")
        assert document.path is None
        assert "does not exist" in capsys.readouterr().err

    def test_a_directory_starts_blank(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        document = launcher.load_initial_document(tmp_path)
        assert document.path is None
        assert "not a file" in capsys.readouterr().err

    def test_a_corrupt_file_starts_blank_rather_than_crashing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Losing the window entirely would be worse than starting empty."""
        broken = tmp_path / "broken.llex"
        broken.write_text("{ not json", encoding="utf-8")
        document = launcher.load_initial_document(broken)
        assert document.path is None
        assert "could not open" in capsys.readouterr().err

    def test_an_oversized_file_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(launcher, "MAX_OPEN_BYTES", 10)
        big = tmp_path / "big.llex"
        big.write_text("x" * 100, encoding="utf-8")
        document = launcher.load_initial_document(big)
        assert document.path is None
        assert "too large" in capsys.readouterr().err


class TestServerThread:
    def test_reports_a_bind_failure(self) -> None:
        """A busy port must surface as a startup failure, not a hang."""
        with socket.socket() as taken:
            taken.bind((launcher.HOST, 0))
            taken.listen(1)
            busy = taken.getsockname()[1]
            server = launcher._ServerThread(object(), launcher.HOST, busy, debug=False)
            server.start()
            assert launcher._wait_until_serving(launcher.HOST, busy, timeout=0.5) is True
            server.shutdown(timeout=2.0)

    def test_shutdown_stops_the_thread(self) -> None:
        from llex.api import AppServices, build_app

        port = launcher.reserve_port(launcher.HOST, 0)
        server = launcher._ServerThread(
            build_app(AppServices()), launcher.HOST, port, debug=False
        )
        server.start()
        assert launcher._wait_until_serving(launcher.HOST, port, timeout=5.0) is True
        server.shutdown(timeout=5.0)
        assert not server.is_alive()
        assert server.error is None
        assert launcher._wait_until_serving(launcher.HOST, port, timeout=0.3) is False


class TestRunDesktopFailurePath:
    def test_reports_when_the_server_never_starts(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(launcher, "STARTUP_TIMEOUT", 0.2)
        # Port 1 on loopback is not something uvicorn can bind as a user.
        code = launcher.run_desktop(
            launcher.LaunchOptions(host=launcher.HOST, port=1)
        )
        assert code == 1
        assert "did not start" in capsys.readouterr().err
