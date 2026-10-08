"""Run the adjacent bundled SQLite Audit Kit package without installation."""

import sys
from pathlib import Path

# A skill may be installed in a read-only directory; do not create bytecode caches.
sys.dont_write_bytecode = True

if sys.version_info < (3, 10):
    print("sqlite-migration-audit requires Python 3.10 or newer", file=sys.stderr)
    raise SystemExit(2)

# Resolve the adjacent bundle explicitly, including Python's isolated (-I) mode.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlite_audit.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
