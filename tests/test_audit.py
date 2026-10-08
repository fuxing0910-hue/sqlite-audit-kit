from __future__ import annotations

from contextlib import closing, redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sqlite_audit import AuditError, KeySpec, compare_snapshots, scan_database
from sqlite_audit.__main__ import main
from sqlite_audit.demo import create_demo
from sqlite_audit.io import load_snapshot, validate_snapshot, write_json
from sqlite_audit.report import render_report
from sqlite_audit.scan import quote_identifier
import sqlite_audit.scan as scan_module


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def database(self, script: str = "CREATE TABLE things (id INTEGER, value);") -> Path:
        path = self.directory / "source.db"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript(script)
        return path

    def test_missing_and_empty_files_are_not_created_or_initialized(self):
        missing = self.directory / "absent.db"
        with self.assertRaises(AuditError):
            scan_database(missing)
        self.assertFalse(missing.exists())
        empty = self.directory / "empty.db"
        empty.touch()
        with self.assertRaises(AuditError):
            scan_database(empty)
        self.assertEqual(empty.stat().st_size, 0)

    def test_non_database_is_rejected(self):
        path = self.directory / "text.db"
        path.write_text("this is not SQLite")
        with self.assertRaises(AuditError):
            scan_database(path)

    def test_storage_types_nulls_empty_tables_and_binary_privacy(self):
        path = self.database("CREATE TABLE records (value); CREATE TABLE empty (id INTEGER);")
        secret_blob = b"DO-NOT-EXPORT-MY-PAYLOAD"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executemany("INSERT INTO records VALUES (?)", [(None,), (3,), (3.5,), ("hello",), (secret_blob,)])
        snapshot = scan_database(path)
        validate_snapshot(snapshot)
        records = next(table for table in snapshot["tables"] if table["name"] == "records")
        self.assertEqual(records["rows"], 5)
        self.assertEqual(records["columns"][0]["types"], {"blob": 1, "integer": 1, "null": 1, "real": 1, "text": 1})
        self.assertEqual(records["columns"][0]["nulls"], 1)
        empty = next(table for table in snapshot["tables"] if table["name"] == "empty")
        self.assertEqual(empty["rows"], 0)
        comparison = compare_snapshots(snapshot, snapshot)
        self.assertEqual(comparison["gate"]["status"], "pass")
        self.assertNotIn(secret_blob.decode(), json.dumps(snapshot))
        self.assertNotIn("hello", json.dumps(snapshot))
        self.assertIn("empty", render_report(comparison))

    def test_quoted_hostile_identifiers_and_safe_html(self):
        name = 'device " <script>alert(1)</script>'
        column = 'x"; DROP TABLE innocent;--'
        path = self.database(f"CREATE TABLE {quote_identifier(name)} ({quote_identifier(column)} INTEGER); CREATE TABLE innocent (id INTEGER);")
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executemany(f"INSERT INTO {quote_identifier(name)} VALUES (?)", [(1,), (1,)])
        snapshot = scan_database(path, [KeySpec(name, (column,))])
        table = next(table for table in snapshot["tables"] if table["name"] == name)
        self.assertEqual(table["keys"][0]["duplicate_groups"], 1)
        self.assertIn("innocent", [item["name"] for item in snapshot["tables"]])
        html = render_report(compare_snapshots(snapshot, snapshot))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Content-Security-Policy", html)

    def test_composite_duplicates_exclude_any_null(self):
        path = self.database("CREATE TABLE t (a, b);")
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executemany("INSERT INTO t VALUES (?, ?)", [(1, 2), (1, 2), (1, 2), (1, 3), (None, 2), (None, 2), (1, None)])
        key = scan_database(path, [KeySpec("t", ("a", "b"))])["tables"][0]["keys"][0]
        self.assertEqual(key, {"columns": ["a", "b"], "duplicate_groups": 1, "extra_rows": 2, "excluded_null_rows": 3})

    def test_invalid_keys_and_limits(self):
        path = self.database()
        for spec in ("things:", "things", ":id", "things:id,id"):
            with self.assertRaises(AuditError):
                KeySpec.parse(spec)
        for rules in ([KeySpec("absent", ("id",))], [KeySpec("things", ("absent",))],
                      [KeySpec("things", ("id",)), KeySpec("things", ("id",))]):
            with self.assertRaises(AuditError):
                scan_database(path, rules)
        for limit in (-1, 1001, True):
            with self.assertRaises(AuditError):
                scan_database(path, fk_detail_limit=limit)

    def test_deterministic_scan_does_not_modify_database(self):
        path = self.database("CREATE TABLE t (id INTEGER PRIMARY KEY, x TEXT); CREATE INDEX t_x ON t(x); INSERT INTO t VALUES (1, 'a');")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        first = scan_database(path)
        second = scan_database(path)
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertNotIn(str(path), json.dumps(first))
        self.assertEqual(first["tables"][0]["schema"]["indexes"][0]["name"], "t_x")

    def test_foreign_key_count_bounded_details_and_no_rowids(self):
        path = self.database("CREATE TABLE parent (id INTEGER PRIMARY KEY); CREATE TABLE child (parent_id REFERENCES parent(id)); INSERT INTO child VALUES (11), (12), (13);")
        fk = scan_database(path, fk_detail_limit=1)["foreign_keys"]
        self.assertTrue(fk["checked"])
        self.assertEqual(fk["violation_count"], 3)
        self.assertEqual(fk["by_table"], [{"table": "child", "violations": 3}])
        self.assertEqual(len(fk["details"]), 1)
        self.assertTrue(fk["details_truncated"])
        self.assertNotIn("rowid", fk["details"][0])
        self.assertEqual(scan_database(path, fk_detail_limit=0)["foreign_keys"]["violation_count"], 3)

    def test_invalid_fk_definition_is_reported_as_incomplete(self):
        path = self.database("CREATE TABLE p (id INTEGER); CREATE TABLE c (p REFERENCES p(id));")
        snapshot = scan_database(path)
        self.assertFalse(snapshot["foreign_keys"]["checked"])
        validate_snapshot(snapshot)
        self.assertEqual(compare_snapshots(snapshot, snapshot)["gate"]["exit_code"], 2)

    def test_virtual_and_shadow_tables_are_explicitly_excluded(self):
        path = self.database("CREATE TABLE regular (id INTEGER);")
        try:
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE VIRTUAL TABLE documents USING fts5(body)")
        except sqlite3.OperationalError:
            self.skipTest("SQLite build does not include FTS5")
        if sqlite3.sqlite_version_info < (3, 37, 0):
            with self.assertRaises(AuditError):
                scan_database(path)
            return
        snapshot = scan_database(path)
        self.assertEqual([table["name"] for table in snapshot["tables"]], ["regular"])
        self.assertIn({"name": "documents", "kind": "virtual"}, snapshot["unsupported_tables"])
        self.assertTrue(any(table["kind"] == "shadow" for table in snapshot["unsupported_tables"]))
        self.assertEqual(compare_snapshots(snapshot, snapshot)["gate"]["exit_code"], 2)

    def test_demo_has_two_real_regressions(self):
        paths = create_demo(self.directory / "demo")
        before, after = load_snapshot(paths["before_snapshot"]), load_snapshot(paths["after_snapshot"])
        comparison = compare_snapshots(before, after)
        self.assertEqual({item["kind"] for item in comparison["regressions"]}, {"duplicate_key", "foreign_key_violations"})
        self.assertEqual(comparison["gate"]["exit_code"], 1)
        self.assertEqual(after["foreign_keys"]["violation_count"], 1)
        readings = next(item for item in comparison["tables"] if item["table"] == "readings")
        self.assertEqual(readings["rows"]["delta"], 1)
        self.assertTrue(readings["schema_changes"])
        with self.assertRaises(AuditError):
            create_demo(self.directory / "demo")

    def test_row_null_and_mixed_type_changes_do_not_fail_gate(self):
        path = self.database("CREATE TABLE t (x); INSERT INTO t VALUES (1);")
        before = scan_database(path)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("INSERT INTO t VALUES (NULL), ('text')")
        comparison = compare_snapshots(before, scan_database(path))
        self.assertEqual(comparison["gate"]["status"], "pass")
        self.assertEqual(comparison["tables"][0]["columns"][0]["nulls"]["delta"], 1)

    def test_key_coverage_difference_never_silently_passes(self):
        path = self.database()
        before = scan_database(path, [KeySpec("things", ("id",))])
        comparison = compare_snapshots(before, scan_database(path))
        self.assertEqual(comparison["gate"]["status"], "incomplete")

    def test_per_table_fk_increase_detected_when_global_count_unchanged(self):
        path = self.database("CREATE TABLE p (id PRIMARY KEY); CREATE TABLE a (p REFERENCES p(id)); CREATE TABLE b (p REFERENCES p(id)); INSERT INTO a VALUES (1);")
        before = scan_database(path)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DELETE FROM a")
            connection.execute("INSERT INTO b VALUES (2)")
        after = scan_database(path)
        comparison = compare_snapshots(before, after)
        self.assertEqual(comparison["foreign_keys"]["delta"], 0)
        self.assertEqual(comparison["regressions"][0]["table"], "b")

    def test_snapshot_validation_rejects_bad_metrics_version_and_nan(self):
        snapshot = scan_database(self.database())
        variants = []
        bad = copy.deepcopy(snapshot); bad["schema_version"] = 2; variants.append(bad)
        bad = copy.deepcopy(snapshot); bad["tables"][0]["rows"] = True; variants.append(bad)
        bad = copy.deepcopy(snapshot); bad["tables"][0]["columns"][0]["types"]["text"] = 4; variants.append(bad)
        for bad in variants:
            with self.assertRaises(AuditError):
                validate_snapshot(bad)
        file = self.directory / "bad.json"
        for raw in ('{"kind": NaN}', '{"kind": 1, "kind": 2}', '[1, 2]', '{'):
            file.write_text(raw, encoding="utf-8")
            with self.assertRaises(AuditError):
                load_snapshot(file)

    def test_cli_refuses_input_overwrites_and_has_expected_exit_codes(self):
        path = self.database()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["scan", str(path), "--output", str(path)]), 2)
            paths = create_demo(self.directory / "cli-demo")
            before, after = str(paths["before_snapshot"]), str(paths["after_snapshot"])
            self.assertEqual(main(["compare", before, after, "--html", before]), 2)
            self.assertEqual(main(["compare", before, after, "--html", str(self.directory / "new.html"), "--fail-on-regression"]), 1)
            self.assertEqual(main(["compare", before, before, "--json", str(self.directory / "same.json"), "--fail-on-regression"]), 0)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_validation_survives_optimized_python(self):
        bad = self.directory / "bad.json"
        bad.write_text('{"kind": "sqlite-audit-snapshot", "schema_version": 2}', encoding="utf-8")
        result = subprocess.run([sys.executable, "-O", "-m", "sqlite_audit", "compare", str(bad), str(bad),
                                 "--json", str(self.directory / "out.json")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.directory / "out.json").exists())

    def test_hardlink_output_aliases_are_rejected(self):
        path = self.database()
        alias = self.directory / "alias.json"
        try:
            os.link(path, alias)
        except OSError:
            self.skipTest("Filesystem does not permit hardlinks")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["scan", str(path), "--output", str(alias)]), 2)
        snapshot_path = self.directory / "snapshot.json"
        write_json(snapshot_path, scan_database(path))
        snapshot_alias = self.directory / "snapshot-alias.json"
        os.link(snapshot_path, snapshot_alias)
        html_path = self.directory / "report.html"
        json_path = self.directory / "report.json"
        html_path.write_text("sentinel")
        os.link(html_path, json_path)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["compare", str(snapshot_path), str(snapshot_path), "--html", str(snapshot_alias)]), 2)
            self.assertEqual(main(["compare", str(snapshot_path), str(snapshot_path), "--html", str(html_path), "--json", str(json_path)]), 2)
        self.assertEqual(html_path.read_text(), "sentinel")

    def test_generated_columns_and_without_rowid(self):
        path = self.database("CREATE TABLE t (id INTEGER PRIMARY KEY, amount INTEGER, doubled INTEGER GENERATED ALWAYS AS (amount * 2) STORED) WITHOUT ROWID; INSERT INTO t(id, amount) VALUES (1, 7);")
        table = scan_database(path)["tables"][0]
        self.assertEqual(table["rows"], 1)
        generated = next(column for column in table["schema"]["columns"] if column["name"] == "doubled")
        self.assertEqual(generated["hidden"], 3)
        metric = next(column for column in table["columns"] if column["name"] == "doubled")
        self.assertEqual(metric["types"]["integer"], 1)

    def test_wal_writer_does_not_mix_scan_states(self):
        path = self.database("CREATE TABLE t (id INTEGER); INSERT INTO t VALUES (1);")
        writer = sqlite3.connect(path)
        try:
            mode = writer.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode != "wal":
                self.skipTest("SQLite cannot use WAL on this filesystem")
            original_schema = scan_module._schema
            def mutate_after_first_read(connection, name, sql):
                writer.execute("INSERT INTO t VALUES (2)")
                writer.commit()
                return original_schema(connection, name, sql)
            with patch.object(scan_module, "_schema", side_effect=mutate_after_first_read):
                snapshot = scan_database(path)
            self.assertEqual(snapshot["tables"][0]["rows"], 1)
            self.assertEqual(snapshot["tables"][0]["columns"][0]["types"]["integer"], 1)
            self.assertEqual(writer.execute("SELECT COUNT(*) FROM t").fetchone()[0], 2)
        finally:
            writer.close()

    def test_uri_special_characters_and_added_removed_tables(self):
        path = self.database("CREATE TABLE old (id);")
        special = self.directory / "database # percent %.db"
        path.rename(special)
        before = scan_database(special)
        with closing(sqlite3.connect(special)) as connection, connection:
            connection.execute("DROP TABLE old")
            connection.execute("CREATE TABLE new (id)")
        comparison = compare_snapshots(before, scan_database(special))
        self.assertEqual(comparison["summary"]["tables_added"], ["new"])
        self.assertEqual(comparison["summary"]["tables_removed"], ["old"])
        self.assertEqual(comparison["gate"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
