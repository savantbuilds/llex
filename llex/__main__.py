"""Allow ``python -m llex`` in addition to the ``llex`` console script."""

from __future__ import annotations

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
