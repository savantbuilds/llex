"""LLex -- a local-first, paginated word processor with a local LLM assistant.

The package version is read from installed distribution metadata so
``pyproject.toml`` stays the single source of truth. When running from a source
checkout that has not been installed, the lookup fails and the declared
fallback is used.
"""

from __future__ import annotations

from importlib import metadata

#: Used when the package metadata is unavailable (e.g. a bare source tree).
_FALLBACK_VERSION = "0.2.0"

try:
    __version__ = metadata.version("llex")
except metadata.PackageNotFoundError:  # pragma: no cover - uninstalled checkout
    __version__ = _FALLBACK_VERSION

__all__ = ["__version__"]
