"""Read-only SQLite scans and reproducible audit comparisons."""

from .scan import AuditError, KeySpec, scan_database
from .compare import compare_snapshots

__all__ = ["AuditError", "KeySpec", "scan_database", "compare_snapshots"]
__version__ = "0.1.0"
