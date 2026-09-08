"""Canonical module entry: python -m tko

Both entry paths share the same lifecycle:

    python -m tko  →  tko.__main__  →  tko.runtime.entrypoint.main
    tko (console)  →  tko.runtime.entrypoint.main

Do not add logic here. All CLI, lock, credentials, and bot lifecycle
belong exclusively in tko.runtime.entrypoint.
"""

from __future__ import annotations

import sys

from tko.runtime.entrypoint import main

if __name__ == "__main__":
    sys.exit(main())
