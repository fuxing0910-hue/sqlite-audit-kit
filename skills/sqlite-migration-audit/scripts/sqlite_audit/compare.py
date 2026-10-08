"""Compare measurements without assigning an arbitrary health score."""

from __future__ import annotations

from typing import Any

from .io import validate_snapshot


def _delta(before: int | None, after: int | None) -> dict[str, int | None]:
    return {"before": before, "after": after,
            "delta": after - before if before is not None and after is not None else None}


def _schema_changes(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[dict[str, Any]]:
    if before is None or after is None:
        return [{"kind": "table_added" if before is None else "table_removed"}]
    result = []
    for category, name_field, label in (("columns", "name", "column"), ("indexes", "name", "index")):
        old = {item[name_field]: item for item in before["schema"][category]}
        new = {item[name_field]: item for item in after["schema"][category]}
        for name in sorted(old.keys() | new.keys()):
            if old.get(name) != new.get(name):
                result.append({"kind": label + "_changed", "name": name,
                               "before": old.get(name), "after": new.get(name)})
    if before["schema"]["foreign_keys"] != after["schema"]["foreign_keys"]:
        result.append({"kind": "foreign_keys_changed", "before": before["schema"]["foreign_keys"],
                       "after": after["schema"]["foreign_keys"]})
    if before["schema"]["sql"] != after["schema"]["sql"]:
        result.append({"kind": "table_sql_changed", "before": before["schema"]["sql"],
                       "after": after["schema"]["sql"]})
    return result


def compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return a stable, versioned comparison and a narrowly defined regression gate."""
    validate_snapshot(before)
    validate_snapshot(after)
    old_tables = {table["name"]: table for table in before["tables"]}
    new_tables = {table["name"]: table for table in after["tables"]}
    changes = []
    regressions = []
    incomplete = []
    for name in sorted(old_tables.keys() | new_tables.keys()):
        old, new = old_tables.get(name), new_tables.get(name)
        schema_changes = _schema_changes(old, new)
        old_columns = {col["name"]: col for col in old["columns"]} if old else {}
        new_columns = {col["name"]: col for col in new["columns"]} if new else {}
        columns = []
        for column_name in sorted(old_columns.keys() | new_columns.keys()):
            old_col, new_col = old_columns.get(column_name), new_columns.get(column_name)
            columns.append({"name": column_name,
                            "nulls": _delta(old_col["nulls"] if old_col else None,
                                            new_col["nulls"] if new_col else None),
                            "null_rate_before": old_col["nulls"] / old["rows"] if old_col and old["rows"] else None,
                            "null_rate_after": new_col["nulls"] / new["rows"] if new_col and new["rows"] else None,
                            "types_before": old_col["types"] if old_col else None,
                            "types_after": new_col["types"] if new_col else None})
        old_keys = {tuple(key["columns"]): key for key in old["keys"]} if old else {}
        new_keys = {tuple(key["columns"]): key for key in new["keys"]} if new else {}
        if old and new and old_keys.keys() != new_keys.keys():
            incomplete.append(f"Key-check coverage differs for table {name}; repeat the same --key rules")
        key_metrics = []
        for names in sorted(old_keys.keys() | new_keys.keys()):
            old_key, new_key = old_keys.get(names), new_keys.get(names)
            # A checked key on a newly created table has a known zero-table baseline.
            old_groups = old_key["duplicate_groups"] if old_key else (0 if old is None else None)
            old_extra = old_key["extra_rows"] if old_key else (0 if old is None else None)
            new_groups = new_key["duplicate_groups"] if new_key else None
            new_extra = new_key["extra_rows"] if new_key else None
            key_metrics.append({"columns": list(names), "duplicate_groups": _delta(old_groups, new_groups),
                                "extra_rows": _delta(old_extra, new_extra),
                                "excluded_null_rows": _delta(old_key["excluded_null_rows"] if old_key else None,
                                                             new_key["excluded_null_rows"] if new_key else None)})
            if old_groups is not None and new_groups is not None and (
                new_groups > old_groups or new_extra > old_extra
            ):
                regressions.append({"kind": "duplicate_key", "table": name, "columns": list(names),
                                    "message": f"Duplicate key counts increased in {name} ({', '.join(names)})",
                                    "groups": _delta(old_groups, new_groups),
                                    "extra_rows": _delta(old_extra, new_extra)})
        changed = old != new
        changes.append({"table": name,
                        "status": "added" if old is None else "removed" if new is None else "changed" if changed else "unchanged",
                        "schema_changes": schema_changes,
                        "rows": _delta(old["rows"] if old else None, new["rows"] if new else None),
                        "columns": columns, "keys": key_metrics})
    old_fk, new_fk = before["foreign_keys"], after["foreign_keys"]
    if old_fk["checked"] and new_fk["checked"]:
        old_counts = {item["table"]: item["violations"] for item in old_fk["by_table"]}
        new_counts = {item["table"]: item["violations"] for item in new_fk["by_table"]}
        for name in sorted(old_counts.keys() | new_counts.keys()):
            old_count, new_count = old_counts.get(name, 0), new_counts.get(name, 0)
            if new_count > old_count:
                regressions.append({"kind": "foreign_key_violations", "table": name,
                                    "message": f"Foreign-key violation count increased in {name}",
                                    "violations": _delta(old_count, new_count)})
    else:
        incomplete.append("Foreign-key checks could not complete in one or both snapshots")
    if not after["integrity"]["ok"]:
        regressions.append({"kind": "integrity_failure", "message": "The after snapshot failed SQLite integrity_check"})
    if not before["integrity"]["ok"]:
        incomplete.append("The before snapshot failed SQLite integrity_check")
    if before["unsupported_tables"] or after["unsupported_tables"]:
        incomplete.append("Virtual/shadow tables are outside the audited scope")
    gate_status = "regression" if regressions else "incomplete" if incomplete else "pass"
    return {"kind": "sqlite-audit-comparison", "schema_version": 1,
            "summary": {"tables_added": sorted(new_tables.keys() - old_tables.keys()),
                        "tables_removed": sorted(old_tables.keys() - new_tables.keys()),
                        "schema_changed_tables": [item["table"] for item in changes if item["schema_changes"]],
                        "regression_count": len(regressions)},
            "gate": {"status": gate_status, "exit_code": {"pass": 0, "regression": 1, "incomplete": 2}[gate_status],
                     "incomplete_reasons": incomplete},
            "regressions": regressions, "tables": changes,
            "foreign_keys": {"before": old_fk, "after": new_fk,
                             "delta": new_fk["violation_count"] - old_fk["violation_count"]
                             if old_fk["checked"] and new_fk["checked"] else None},
            "integrity": {"before": before["integrity"], "after": after["integrity"]},
            "unsupported_tables": {"before": before["unsupported_tables"], "after": after["unsupported_tables"]}}
