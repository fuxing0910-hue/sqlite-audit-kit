"""Versioned JSON input and atomic UTF-8 output."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .scan import AuditError


def _reject_constant(value: str) -> None:
    raise AuditError(f"JSON must not contain {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuditError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def load_snapshot(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).expanduser().read_text(encoding="utf-8"),
                           parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AuditError(f"Cannot read snapshot: {error}") from error
    validate_snapshot(value)
    return value


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _require(condition: bool) -> None:
    if not condition:
        raise AuditError("Invalid v1 audit snapshot: unsupported format or inconsistent metrics")


def validate_snapshot(value: Any) -> None:
    """Validate the fields used by comparison; accept additive v1 metadata."""
    try:
        _require(isinstance(value, dict))
        _require(value["kind"] == "sqlite-audit-snapshot")
        _require(type(value["schema_version"]) is int and value["schema_version"] == 1)
        _require(value["scan"]["mode"] == "full" and value["scan"]["sampled"] is False)
        integrity = value["integrity"]
        _require(type(integrity["ok"]) is bool)
        _require(isinstance(integrity["messages"], list) and all(isinstance(x, str) for x in integrity["messages"]))
        _require(integrity["ok"] == (integrity["messages"] == ["ok"]))
        fk = value["foreign_keys"]
        _require(type(fk["checked"]) is bool)
        _require(isinstance(fk["by_table"], list) and isinstance(fk["details"], list))
        _require(type(fk["details_truncated"]) is bool)
        if fk["checked"]:
            _require(_count(fk["violation_count"]) and fk["error"] is None)
            _require(all(isinstance(x["table"], str) and _count(x["violations"]) for x in fk["by_table"]))
            _require(len({x["table"] for x in fk["by_table"]}) == len(fk["by_table"]))
            _require(sum(x["violations"] for x in fk["by_table"]) == fk["violation_count"])
            _require(len(fk["details"]) <= fk["violation_count"])
            _require(fk["details_truncated"] == (len(fk["details"]) < fk["violation_count"]))
            _require(all(isinstance(x["table"], str) and isinstance(x["parent"], str)
                       and _count(x["constraint_id"]) for x in fk["details"]))
        else:
            _require(fk["violation_count"] is None and isinstance(fk["error"], str))
            _require(not fk["by_table"] and not fk["details"] and not fk["details_truncated"])
        tables = value["tables"]
        _require(isinstance(tables, list))
        _require(len({table["name"] for table in tables}) == len(tables))
        for table in tables:
            _require(isinstance(table["name"], str) and _count(table["rows"]))
            schema = table["schema"]
            _require(isinstance(schema["sql"], str))
            _require(isinstance(schema["columns"], list) and schema["columns"])
            schema_names = {column["name"] for column in schema["columns"]}
            _require(all(isinstance(name, str) for name in schema_names))
            _require(len(schema_names) == len(schema["columns"]))
            _require(isinstance(schema["indexes"], list) and isinstance(schema["foreign_keys"], list))
            for column in schema["columns"]:
                _require(isinstance(column["declared_type"], str))
                _require(type(column["not_null"]) is bool)
                _require(_count(column["primary_key_position"]) and _count(column["hidden"]))
                _require(column["default_sql"] is None or isinstance(column["default_sql"], str))
            for index in schema["indexes"]:
                _require(isinstance(index["name"], str) and isinstance(index["columns"], list))
            _require(isinstance(table["columns"], list))
            metric_names = {column["name"] for column in table["columns"]}
            _require(metric_names == schema_names and len(metric_names) == len(table["columns"]))
            for column in table["columns"]:
                _require(_count(column["nulls"]))
                _require(set(column["types"]) == {"blob", "integer", "null", "real", "text"})
                _require(all(_count(count) for count in column["types"].values()))
                _require(sum(column["types"].values()) == table["rows"])
                _require(column["nulls"] == column["types"]["null"])
            _require(isinstance(table["keys"], list))
            key_sets = []
            for key in table["keys"]:
                _require(isinstance(key["columns"], list) and key["columns"])
                _require(all(isinstance(name, str) and name in schema_names for name in key["columns"]))
                _require(len(set(key["columns"])) == len(key["columns"]))
                _require(all(_count(key[name]) for name in ("duplicate_groups", "extra_rows", "excluded_null_rows")))
                _require(key["excluded_null_rows"] <= table["rows"])
                _require(key["duplicate_groups"] <= key["extra_rows"])
                _require(key["duplicate_groups"] + key["extra_rows"] <= table["rows"] - key["excluded_null_rows"])
                key_sets.append(tuple(key["columns"]))
            _require(len(set(key_sets)) == len(key_sets))
        _require(isinstance(value["unsupported_tables"], list))
        _require(all(isinstance(x["name"], str) and x["kind"] in ("virtual", "shadow")
                   for x in value["unsupported_tables"]))
    except (AssertionError, KeyError, TypeError, AttributeError) as error:
        raise AuditError("Invalid v1 audit snapshot: unsupported format or inconsistent metrics") from error


def write_text(path: str | Path, text: str) -> None:
    """Replace the destination atomically, using a temporary sibling file."""
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=destination.parent, prefix=".audit-", delete=False) as stream:
            temporary = stream.name
            stream.write(text)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def write_json(path: str | Path, value: dict[str, Any]) -> None:
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
