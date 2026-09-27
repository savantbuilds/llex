"""Where LLex keeps its own settings, as opposed to the document's.

A document holds what the document contains. This holds how *this installation*
is set up: which local model to talk to, and so on. Keeping them apart is what
lets one machine serve several documents with different models without touching
any of them.

The store is deliberately small: a JSON file in the platform's per-user config
directory, written atomically, and never allowed to stop the editor opening. A
settings file that cannot be read, cannot be parsed, or cannot be written falls
back to the defaults and is reported rather than raised -- the alternative is an
editor that will not start because of a file the user cannot see.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Final

__all__ = [
    "APP_DIR_NAME",
    "SETTINGS_FILENAME",
    "SettingsStore",
    "config_dir",
    "default_settings_path",
]

APP_DIR_NAME: Final = "LLex"
SETTINGS_FILENAME: Final = "settings.json"

#: Written into the file so a future version can migrate rather than discard.
SETTINGS_VERSION: Final = 1


def config_dir() -> Path:
    """The per-user configuration directory, following platform convention.

    Windows has an established location for exactly this and using it means the
    file is where a user (or a support request) would look for it. Elsewhere the
    XDG variable is honoured, falling back to ``~/.config``.

    The two platforms are separate functions rather than one branch: mypy folds
    ``sys.platform`` to a constant on the host it runs on, and a single function
    containing both branches reports the other as unreachable.
    """
    platform = sys.platform
    return _windows_config_dir() if platform.startswith("win") else _posix_config_dir()


def _windows_config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / "AppData" / "Roaming" / APP_DIR_NAME


def _posix_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / APP_DIR_NAME.lower()
    return Path.home() / ".config" / APP_DIR_NAME.lower()


def default_settings_path() -> Path:
    """Where the settings file lives."""
    return config_dir() / SETTINGS_FILENAME


class SettingsStore:
    """A small JSON settings file, tolerant of everything.

    Args:
        path: Where to keep it. Defaults to the platform config directory.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_settings_path()
        self._cache: dict[str, Any] | None = None
        #: Set when the file could not be read or written, so the UI can say so
        #: instead of the user wondering why their choice did not stick.
        self.last_error: str | None = None

    # -- Reading ----------------------------------------------------------- #

    def read(self) -> dict[str, Any]:
        """Every setting, or an empty dict when the file is unusable.

        The stored ``version`` is a file-format concern rather than a setting, so
        it is not returned: a caller merging or comparing settings should not have
        to know it is there.
        """
        if self._cache is not None:
            return {key: value for key, value in self._cache.items() if key != "version"}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
            self.last_error = None
        except (OSError, ValueError) as exc:
            # A corrupt file is not worth refusing to start over. It is left on
            # disk so the user can recover anything in it by hand.
            raw = {}
            self.last_error = f"could not read {self.path}: {exc}"
        self._cache = raw if isinstance(raw, dict) else {}
        return {key: value for key, value in self._cache.items() if key != "version"}

    def get(self, key: str, default: Any = None) -> Any:
        """One setting."""
        return self.read().get(key, default)

    # -- Writing ----------------------------------------------------------- #

    def write(self, values: dict[str, Any]) -> bool:
        """Replace the settings. True when they were written.

        Written atomically, because a settings file truncated by a crash would
        lose every preference at once -- and this file holds the one the user
        picked deliberately.
        """
        payload = json.dumps(
            {"version": SETTINGS_VERSION, **values},
            indent=2,
            ensure_ascii=False,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as writer:
                    writer.write(payload)
                os.replace(temp_name, self.path)
            except BaseException:
                with contextlib.suppress(OSError):
                    Path(temp_name).unlink()
                raise
        except OSError as exc:
            self.last_error = f"could not write {self.path}: {exc}"
            return False
        self._cache = {"version": SETTINGS_VERSION, **values}
        self.last_error = None
        return True

    def update(self, values: dict[str, Any]) -> bool:
        """Merge into the existing settings and write them back."""
        merged = self.read()
        merged.update(values)
        return self.write({key: value for key, value in merged.items() if key != "version"})
