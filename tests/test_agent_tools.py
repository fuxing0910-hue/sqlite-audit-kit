from __future__ import annotations

from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from sqlite_audit.agent_tools import ToolError, call_tool, main, tool_definitions
from sqlite_audit.compare import compare_snapshots
from sqlite_audit.demo import create_demo
from sqlite_audit.io import load_snapshot, write_json
from sqlite_audit.scan import KeySpec, scan_database


class AgentToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def database(self) -> Path:
        path = self.directory / "synthetic.db"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute('CREATE TABLE "sample,table" ("sample:key" TEXT, value, payload BLOB)')
            connection.executemany('INSERT INTO "sample,table" VALUES (?, ?, ?)', [
                ("same", 2, b"PRIVATE-RECORD-PAYLOAD"),
                ("same", None, None),
                (None, "text", None),
            ])
        return path

    def request(self, value) -> Path:
        path = self.directory / "request.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def cli(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(arguments)
        self.assertEqual(stderr.getvalue(), "")
        return status, json.loads(stdout.getvalue())

    def test_provider_declarations_use_the_correct_surface_and_strict_schema(self):
        names = {"sqlite_scan_readonly", "sqlite_compare_snapshots"}
        reference = tool_definitions("openai")
        for provider in ("openai", "deepseek", "claude", "gemini"):
            with self.subTest(provider=provider):
                declarations = tool_definitions(provider)
                self.assertEqual(len(declarations), 2)
                extracted = []
                for declaration in declarations:
                    if provider == "deepseek":
                        self.assertEqual(set(declaration), {"type", "function"})
                        self.assertEqual(declaration["type"], "function")
                        function = declaration["function"]
                        self.assertEqual(set(function), {"name", "description", "parameters"})
                        parameters = function["parameters"]
                    elif provider == "claude":
                        self.assertEqual(set(declaration), {"name", "description", "input_schema"})
                        function = declaration
                        parameters = declaration["input_schema"]
                    else:
                        expected = {"type", "name", "description", "parameters"}
                        if provider == "openai":
                            expected.add("strict")
                            self.assertIs(declaration["strict"], True)
                        self.assertEqual(set(declaration), expected)
                        self.assertEqual(declaration["type"], "function")
                        function = declaration
                        parameters = declaration["parameters"]
                    extracted.append(function["name"])
                    self.assertEqual(parameters["type"], "object")
                    self.assertIs(parameters["additionalProperties"], False)
                    self.assertEqual(set(parameters["required"]), set(parameters["properties"]))
                    original = next(tool for tool in reference if tool["name"] == function["name"])
                    self.assertEqual(parameters, original["parameters"])
                    if function["name"] == "sqlite_scan_readonly":
                        nested = parameters["properties"]["candidate_keys"]["items"]
                        self.assertIs(nested["additionalProperties"], False)
                        self.assertEqual(set(nested["required"]), {"table", "columns"})
                self.assertEqual(set(extracted), names)
                json.dumps(declarations, allow_nan=False)

    def test_definition_results_do_not_share_mutable_schema_state(self):
        first = tool_definitions("openai")
        first[0]["parameters"]["properties"].clear()
        self.assertIn("database_path", tool_definitions("openai")[0]["parameters"]["properties"])
        self.assertIn("database_path", tool_definitions("claude")[0]["input_schema"]["properties"])

    def test_scan_returns_native_snapshot_and_leaves_input_unchanged(self):
        path = self.database()
        initial_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        arguments = {"database_path": str(path), "candidate_keys": [{"table": "sample,table", "columns": ["sample:key"]}]}
        snapshot = call_tool("sqlite_scan_readonly", arguments)
        self.assertEqual(snapshot, scan_database(path, [KeySpec("sample,table", ("sample:key",))]))
        self.assertEqual(snapshot["tables"][0]["keys"][0]["duplicate_groups"], 1)
        self.assertEqual(snapshot["tables"][0]["keys"][0]["excluded_null_rows"], 1)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), initial_hash)
        self.assertNotIn("PRIVATE-RECORD-PAYLOAD", json.dumps(snapshot))
        self.assertEqual(call_tool("sqlite_scan_readonly", {"database_path": str(path), "candidate_keys": []})["tables"][0]["keys"], [])

    def test_missing_database_is_never_created_and_directory_is_not_scanned(self):
        missing = self.directory / "missing.db"
        for path in (missing, self.directory):
            with self.assertRaises(ToolError):
                call_tool("sqlite_scan_readonly", {"database_path": str(path), "candidate_keys": []})
        self.assertFalse(missing.exists())

    def test_compare_returns_gate_one_as_data_and_gate_zero_for_unchanged(self):
        paths = create_demo(self.directory / "demo")
        before_path, after_path = paths["before_snapshot"], paths["after_snapshot"]
        initial_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (before_path, after_path)}
        arguments = {"before_snapshot_path": str(before_path), "after_snapshot_path": str(after_path)}
        comparison = call_tool("sqlite_compare_snapshots", arguments)
        self.assertEqual(comparison, compare_snapshots(load_snapshot(before_path), load_snapshot(after_path)))
        self.assertEqual(comparison["gate"]["exit_code"], 1)
        self.assertEqual({item["kind"] for item in comparison["regressions"]}, {"duplicate_key", "foreign_key_violations"})
        unchanged = call_tool("sqlite_compare_snapshots", {"before_snapshot_path": str(before_path), "after_snapshot_path": str(before_path)})
        self.assertEqual(unchanged["gate"]["exit_code"], 0)
        self.assertEqual(initial_hashes, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in initial_hashes})

    def test_incomplete_comparison_gate_is_also_returned_as_data(self):
        path = self.database()
        before, after = self.directory / "before.json", self.directory / "after.json"
        write_json(before, scan_database(path, [KeySpec("sample,table", ("sample:key",))]))
        write_json(after, scan_database(path))
        comparison = call_tool("sqlite_compare_snapshots", {"before_snapshot_path": str(before), "after_snapshot_path": str(after)})
        self.assertEqual(comparison["gate"]["exit_code"], 2)
        self.assertEqual(comparison["gate"]["status"], "incomplete")

    def test_rejects_unknown_tools_providers_and_exact_field_violations(self):
        for provider in ("unknown", "OpenAI", None, [], 1):
            with self.subTest(provider=provider), self.assertRaises(ToolError):
                tool_definitions(provider)
        for name in ("sqlite_repair", "exec", "subprocess.run", None, [], 1):
            with self.subTest(name=name), self.assertRaises(ToolError):
                call_tool(name, {})
        invalid = ({}, {"database_path": "x.db"}, {"database_path": "x.db", "candidate_keys": [], "sql": "DROP TABLE t"},
                   [], None, '{"database_path":"x.db","candidate_keys":[]}')
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ToolError):
                call_tool("sqlite_scan_readonly", arguments)
        for arguments in ({"before_snapshot_path": "a"}, {"before_snapshot_path": "a", "after_snapshot_path": "b", "repair": True}):
            with self.assertRaises(ToolError):
                call_tool("sqlite_compare_snapshots", arguments)

    def test_rejects_empty_paths_bad_key_types_and_repeated_columns(self):
        for path in ("", " ", "\x00", None, 1, True, []):
            with self.subTest(path=path), self.assertRaises(ToolError):
                call_tool("sqlite_scan_readonly", {"database_path": path, "candidate_keys": []})
            with self.assertRaises(ToolError):
                call_tool("sqlite_compare_snapshots", {"before_snapshot_path": path, "after_snapshot_path": "after.json"})
        keys = (None, {}, "t:id", [None], [{"table": "t"}], [{"table": "t", "columns": [], "extra": 1}],
                [{"table": "t", "columns": []}], [{"table": "", "columns": ["id"]}],
                [{"table": "t", "columns": "id"}], [{"table": "t", "columns": [1]}],
                [{"table": "t", "columns": [""]}], [{"table": "t", "columns": ["id", "id"]}],
                [{"table": "t", "columns": ["id"]}, {"table": "t", "columns": ["id"]}])
        for value in keys:
            with self.subTest(keys=value), self.assertRaises(ToolError):
                call_tool("sqlite_scan_readonly", {"database_path": "not-created.db", "candidate_keys": value})

    def test_cli_lists_all_four_provider_shapes_as_json(self):
        for provider in ("openai", "deepseek", "claude", "gemini"):
            with self.subTest(provider=provider):
                status, value = self.cli(["--list", "--provider", provider])
                self.assertEqual(status, 0)
                self.assertEqual(value, tool_definitions(provider))

    def test_cli_successful_request_returns_json_even_when_gate_fails(self):
        paths = create_demo(self.directory / "demo")
        request = self.request({"name": "sqlite_compare_snapshots", "arguments": {
            "before_snapshot_path": str(paths["before_snapshot"]), "after_snapshot_path": str(paths["after_snapshot"]),
        }})
        status, result = self.cli(["--request", str(request)])
        self.assertEqual(status, 0)
        self.assertEqual(result["gate"]["exit_code"], 1)
        invocation = subprocess.run([sys.executable, "-m", "sqlite_audit.agent_tools", "--request", str(request)], capture_output=True, text=True)
        self.assertEqual(invocation.returncode, 0)
        self.assertEqual(json.loads(invocation.stdout), result)

    def test_cli_errors_are_json_and_do_not_echo_file_contents(self):
        secret = "PRIVATE-TEXT-MUST-NOT-LEAK"
        request_path = self.directory / "request.json"
        for raw in (secret, '{"name": "' + secret + '", "arguments": {}}',
                    '{"name":"x","name":"' + secret + '","arguments":{}}',
                    '{"name":"x","arguments":NaN}', '["' + secret + '"]'):
            request_path.write_text(raw, encoding="utf-8")
            status, result = self.cli(["--request", str(request_path)])
            self.assertEqual(status, 2)
            self.assertEqual(result["error"]["code"], "invalid_request")
            self.assertNotIn(secret, json.dumps(result))
        malformed = self.directory / "malformed.json"
        malformed.write_text('{"' + secret + '": 1, "' + secret + '": 2}', encoding="utf-8")
        request = self.request({"name": "sqlite_compare_snapshots", "arguments": {
            "before_snapshot_path": str(malformed), "after_snapshot_path": str(malformed),
        }})
        status, result = self.cli(["--request", str(request)])
        self.assertEqual(status, 2)
        self.assertNotIn(secret, json.dumps(result))
        for arguments in ([], ["--list", "--provider", secret], ["--request"], ["--list", "--request", str(request)], ["--unknown", secret]):
            status, result = self.cli(arguments)
            self.assertEqual(status, 2)
            self.assertNotIn(secret, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
