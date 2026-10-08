"""Generate real SQLite fixtures containing transparently synthetic records."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from .compare import compare_snapshots
from .io import write_json, write_text
from .report import render_report
from .scan import AuditError, KeySpec, scan_database


def create_demo(output_dir: str | Path) -> dict[str, Path]:
    """Create a before/after database pair and its reports; refuse overwrites."""
    directory = Path(output_dir)
    paths = {name: directory / filename for name, filename in (
        ("before_db", "before.db"), ("after_db", "after.db"),
        ("before_snapshot", "before.json"), ("after_snapshot", "after.json"),
        ("html", "report.html"), ("json", "report.json"),
    )}
    if any(path.exists() for path in paths.values()):
        raise AuditError("Demo files already exist; use a new output directory")
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(paths["before_db"])) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript('''
            CREATE TABLE devices (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE readings (
                id INTEGER PRIMARY KEY,
                device_id INTEGER NOT NULL REFERENCES devices(id),
                sample_key TEXT,
                sensor_value NUMERIC,
                label TEXT,
                payload BLOB
            );
            CREATE TABLE pending_records (id INTEGER PRIMARY KEY, note TEXT);
        ''')
        connection.executemany("INSERT INTO devices VALUES (?, ?)", [(1, "synthetic-node-a"), (2, "synthetic-node-b")])
        connection.executemany("INSERT INTO readings VALUES (?, ?, ?, ?, ?, ?)", [
            (1, 1, "sample-001", 12.5, "lab-demo", b"synthetic-blob"),
            (2, 1, "sample-002", 13, "lab-demo", None),
            (3, 2, "sample-003", None, "pending", None),
            (4, 2, "sample-004", "pending", None, None),
            (5, 1, None, 14.75, "lab-demo", None),
        ])
    # Fixture construction only: the audit scanner never executes migrations.
    source = sqlite3.connect(paths["before_db"])
    target = sqlite3.connect(paths["after_db"])
    try:
        source.backup(target)
        target.execute("PRAGMA foreign_keys = OFF")
        target.execute("ALTER TABLE readings ADD COLUMN calibration_state TEXT DEFAULT 'raw'")
        target.execute("CREATE INDEX readings_device_idx ON readings(device_id)")
        target.execute("UPDATE readings SET sensor_value = NULL WHERE id = 2")
        target.execute("INSERT INTO readings (id, device_id, sample_key, sensor_value, label) VALUES (6, 999, 'sample-002', NULL, 'synthetic-regression')")
        target.commit()
    finally:
        source.close()
        target.close()
    rules = [KeySpec("readings", ("sample_key",))]
    before = scan_database(paths["before_db"], rules)
    after = scan_database(paths["after_db"], rules)
    comparison = compare_snapshots(before, after)
    write_json(paths["before_snapshot"], before)
    write_json(paths["after_snapshot"], after)
    write_json(paths["json"], comparison)
    write_text(paths["html"], render_report(comparison))
    return paths
