"""Provider-shaped function declarations and a strictly validated local dispatcher.

No SDK, model request, network call, shell command or database write is made.
The host application remains responsible for model calls and filesystem access
authorization; this module is not a filesystem sandbox.

Declaration formats, checked against the official documentation:
https://developers.openai.com/api/docs/guides/function-calling
https://api-docs.deepseek.com/api/create-chat-completion/
https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
https://ai.google.dev/gemini-api/docs/function-calling

``openai`` targets Responses, ``deepseek`` targets Chat Completions, ``claude``
targets Messages client tools, and ``gemini`` targets Interactions (not the
different generateContent functionDeclarations envelope).
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Sequence

from .compare import compare_snapshots
from .io import load_snapshot
from .scan import AuditError, KeySpec, scan_database


class ToolError(ValueError):
    """A safe, public error message that does not include input file contents."""


_PROVIDERS = ("openai", "deepseek", "claude", "gemini")

_SCAN_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "database_path": {
            "type": "string",
            "description": "Non-empty path to one existing local SQLite database; directories are not scanned.",
        },
        "candidate_keys": {
            "type": "array",
            "description": "Explicit candidate-key checks; pass [] for none. Any-NULL key rows are excluded.",
            "items": {
                "type": "object",
                "properties": {
                    "table": {"type": "string", "description": "Exact table name in the main schema."},
                    "columns": {
                        "type": "array", "items": {"type": "string"}, "minItems": 1,
                        "description": "Non-empty list of exact column names; preserve order across snapshots.",
                    },
                },
                "required": ["table", "columns"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["database_path", "candidate_keys"],
    "additionalProperties": False,
}

_COMPARE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "before_snapshot_path": {"type": "string", "description": "Non-empty path to the before v1 audit JSON snapshot."},
        "after_snapshot_path": {"type": "string", "description": "Non-empty path to the after v1 audit JSON snapshot."},
    },
    "required": ["before_snapshot_path", "after_snapshot_path"],
    "additionalProperties": False,
}

_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "sqlite_scan_readonly",
        "description": (
            "Inspect an existing SQLite database without changing it; return a versioned JSON snapshot "
            "with full row/NULL/storage-type counts, foreign-key and integrity checks, and selected "
            "candidate-key duplicates. This performs full scans, not sampling or repair. "
            "Schema names, SQL/default literals and diagnostics may be sensitive even though record "
            "payloads and duplicate-key values are omitted."
        ),
        "parameters": _SCAN_PARAMETERS,
    },
    {
        "name": "sqlite_compare_snapshots",
        "description": (
            "Compare before/after SQLite audit JSON snapshots and return JSON schema/count changes "
            "and gate findings. Gate 1 is an ordinary regression outcome, not a tool error; "
            "gate 0 does not prove migration or application semantics. Row growth, more NULLs and "
            "mixed storage types are observations, not automatic health failures. "
            "Returned schema SQL/default literals and diagnostics may be sensitive."
        ),
        "parameters": _COMPARE_PARAMETERS,
    },
)


def _validate_provider(provider: Any) -> None:
    if type(provider) is not str or provider not in _PROVIDERS:
        raise ToolError("Unsupported provider; use openai, deepseek, claude or gemini.")


def tool_definitions(provider: str = "openai") -> list[dict[str, Any]]:
    """Return independent JSON-compatible declarations for the chosen API surface.

    OpenAI Responses gets strict=True. The other providers receive their base
    declaration shape; in particular, DeepSeek's optional Beta strict feature
    is not enabled. Local argument validation is strict for every provider.
    """
    _validate_provider(provider)
    result = []
    for original in _TOOLS:
        tool = deepcopy(original)
        if provider == "claude":
            result.append({"name": tool["name"], "description": tool["description"],
                           "input_schema": tool["parameters"]})
        elif provider == "deepseek":
            result.append({"type": "function", "function": tool})
        else:
            tool["type"] = "function"
            if provider == "openai":
                tool["strict"] = True
            result.append(tool)
    return result


def _object_fields(value: Any, fields: tuple[str, ...], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(fields):
        # Report only known schema field names, never arbitrary request keys/values.
        raise ToolError(f"{label} must be an object with exactly these fields: {', '.join(fields)}.")
    return value


def _path(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip() or "\x00" in value:
        raise ToolError(f"{label} must be a non-empty path string without NUL characters.")
    return value


def _keys(value: Any) -> list[KeySpec]:
    if type(value) is not list:
        raise ToolError("candidate_keys must be an array; use [] for no checks.")
    result = []
    for candidate in value:
        key = _object_fields(candidate, ("table", "columns"), "Each candidate key")
        if type(key["table"]) is not str or not key["table"] or "\x00" in key["table"]:
            raise ToolError("Each candidate-key table must be a non-empty identifier string without NUL.")
        columns = key["columns"]
        if type(columns) is not list or not columns or any(
            type(column) is not str or not column or "\x00" in column for column in columns
        ):
            raise ToolError("Candidate-key columns must be a non-empty array of non-empty identifier strings without NUL.")
        if len(set(columns)) != len(columns):
            raise ToolError("A candidate key must not repeat a column.")
        result.append(KeySpec(key["table"], tuple(columns)))
    if len(set(result)) != len(result):
        raise ToolError("A candidate-key check must not be repeated.")
    return result


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch exactly two read-only operations and return their native JSON data.

    Model arguments must already be decoded to a JSON object. Validation runs
    locally even when a provider claims strict generation. Gate findings remain
    comparison data, including gate 1/2; they are not dispatch exceptions.
    """
    if type(name) is not str or name not in ("sqlite_scan_readonly", "sqlite_compare_snapshots"):
        raise ToolError("Unsupported tool; only the two SQLite audit tools are allowed.")
    if name == "sqlite_scan_readonly":
        values = _object_fields(arguments, ("database_path", "candidate_keys"), "Scan arguments")
        path = _path(values["database_path"], "database_path")
        keys = _keys(values["candidate_keys"])
        try:
            return scan_database(path, keys)
        except (AuditError, OSError, ValueError, RuntimeError) as error:
            raise ToolError("Scan could not complete; check the existing SQLite file and candidate-key names.") from error
    values = _object_fields(arguments, ("before_snapshot_path", "after_snapshot_path"), "Comparison arguments")
    before = _path(values["before_snapshot_path"], "before_snapshot_path")
    after = _path(values["after_snapshot_path"], "after_snapshot_path")
    try:
        return compare_snapshots(load_snapshot(before), load_snapshot(after))
    except (AuditError, OSError, ValueError, RuntimeError) as error:
        raise ToolError("Comparison could not complete; check that both inputs are valid v1 audit snapshots.") from error


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ToolError("Request JSON must not contain duplicate fields.")
        value[key] = item
    return value


def _json_constant(value: str) -> None:
    raise ToolError("Request must use standard JSON without non-finite numbers.")


def _load_request(path: str) -> dict[str, Any]:
    try:
        data = Path(_path(path, "request path")).expanduser().read_text(encoding="utf-8")
        request = json.loads(data, object_pairs_hook=_json_object, parse_constant=_json_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ToolError("Request file could not be read as valid UTF-8 JSON.") from error
    return _object_fields(request, ("name", "arguments"), "Request")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ToolError("Invalid command-line arguments; use --list [--provider NAME] or --request FILE.")


def main(argv: Sequence[str] | None = None) -> int:
    """Print JSON declarations/results; every handled failure prints safe JSON."""
    parser = _Parser(description="Local SQLite function-tool declarations and read-only request dispatcher.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Print tool definitions without calling a model")
    group.add_argument("--request", metavar="FILE", help="Read a JSON object containing name and arguments")
    parser.add_argument("--provider", default="openai", help="openai, deepseek, claude or gemini (default: openai)")
    try:
        args = parser.parse_args(argv)
        _validate_provider(args.provider)
        if args.list:
            result: Any = tool_definitions(args.provider)
        else:
            request = _load_request(args.request)
            result = call_tool(request["name"], request["arguments"])
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return 0
    except (ToolError, OSError, ValueError, RuntimeError) as error:
        message = str(error) if isinstance(error, ToolError) else "Local tool processing failed; check the inputs."
        print(json.dumps({"error": {"code": "invalid_request", "message": message}}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
