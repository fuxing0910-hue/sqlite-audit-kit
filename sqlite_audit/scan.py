"""Exact measurements, collected inside one read-only transaction."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable


class AuditError(ValueError):
    """An input cannot be audited safely or consistently."""


@dataclass(frozen=True)
class KeySpec:
    """A user-selected candidate key; rows with any NULL are excluded."""

    table: str
    columns: tuple[str, ...]

    @classmethod
    def parse(cls, value: str) -> KeySpec:
        table, separator, columns = value.partition(":")
        names = tuple(columns.split(","))
        if not separator or not table or not all(names):
            raise AuditError("--key expects table:column[,column]")
        if len(set(names)) != len(names):
            raise AuditError("A key must not repeat a column")
        return cls(table, names)


def quote_identifier(name: str) -> str:
    """Quote one SQLite identifier, including embedded double quotes."""
    return '"' + name.replace('"', '""') + '"'


def _schema(connection: sqlite3.Connection, name: str, sql: str) -> dict[str, Any]:
    quoted = quote_identifier(name)
    columns = [
        {"name": row[1], "declared_type": row[2], "not_null": bool(row[3]),
         "primary_key_position": row[5], "hidden": row[6], "default_sql": row[4]}
        for row in connection.execute(f"PRAGMA main.table_xinfo({quoted})")
    ]
    indexes = []
    for row in connection.execute(f"PRAGMA main.index_list({quoted})"):
        index_name = row[1]
        index_sql_row = connection.execute(
            "SELECT sql FROM main.sqlite_schema WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        index_columns = [
            {"position": part[0], "name": part[2], "descending": bool(part[3]),
             "collation": part[4], "key": bool(part[5])}
            for part in connection.execute(
                f"PRAGMA main.index_xinfo({quote_identifier(index_name)})"
            )
        ]
        indexes.append({"name": index_name, "unique": bool(row[2]), "origin": row[3],
                        "partial": bool(row[4]), "sql": index_sql_row[0] if index_sql_row else None,
                        "columns": index_columns})
    foreign_keys = [
        {"id": row[0], "sequence": row[1], "parent": row[2], "from": row[3],
         "to": row[4], "on_update": row[5], "on_delete": row[6], "match": row[7]}
        for row in connection.execute(f"PRAGMA main.foreign_key_list({quoted})")
    ]
    return {"sql": sql, "columns": columns,
            "indexes": sorted(indexes, key=lambda item: item["name"]),
            "foreign_keys": sorted(foreign_keys, key=lambda item: (item["id"], item["sequence"]))}


def _key_measurement(connection: sqlite3.Connection, table: str, key: KeySpec) -> dict[str, Any]:
    names = [quote_identifier(name) for name in key.columns]
    present = " AND ".join(f"{name} IS NOT NULL" for name in names)
    missing = " OR ".join(f"{name} IS NULL" for name in names)
    groups, extra_rows = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(n - 1), 0) FROM ("
        f"SELECT COUNT(*) AS n FROM {quote_identifier(table)} WHERE {present} "
        f"GROUP BY {', '.join(names)} HAVING COUNT(*) > 1)"
    ).fetchone()
    excluded = connection.execute(
        f"SELECT COUNT(*) FROM {quote_identifier(table)} WHERE {missing}"
    ).fetchone()[0]
    return {"columns": list(key.columns), "duplicate_groups": groups,
            "extra_rows": extra_rows, "excluded_null_rows": excluded}


def _foreign_keys(connection: sqlite3.Connection, tables: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    by_table: Counter[str] = Counter()
    try:
        # Check supported tables individually: virtual/shadow tables remain explicitly excluded.
        for table in tables:
            for row in connection.execute(
                f"PRAGMA main.foreign_key_check({quote_identifier(table['name'])})"
            ):
                by_table[row[0]] += 1
                if len(details) < limit:
                    # Deliberately omit rowids and record values.
                    details.append({"table": row[0], "parent": row[2], "constraint_id": row[3]})
    except sqlite3.DatabaseError as error:
        return {"checked": False, "violation_count": None, "by_table": [], "details": [],
                "details_truncated": False, "error": str(error)}
    count = sum(by_table.values())
    return {"checked": True, "violation_count": count,
            "by_table": [{"table": name, "violations": count} for name, count in sorted(by_table.items())],
            "details": details, "details_truncated": count > len(details), "error": None}


def scan_database(path: str | Path, keys: Iterable[KeySpec] = (), *, fk_detail_limit: int = 100) -> dict[str, Any]:
    """Scan an existing local database. No SQL that modifies the database is issued.

    All row counts and type/key distributions are full scans, not samples. A scan
    may be expensive on a large database. Table and column names are SQL-quoted.
    """
    database = Path(path).expanduser().resolve()
    if not database.is_file():
        raise AuditError(f"Database does not exist or is not a file: {database}")
    if database.stat().st_size == 0:
        raise AuditError("An empty file is not an initialized SQLite database")
    if type(fk_detail_limit) is not int or not 0 <= fk_detail_limit <= 1000:
        raise AuditError("FK detail limit must be between 0 and 1000")
    rules = tuple(keys)
    for rule in rules:
        if not rule.table or not rule.columns or not all(rule.columns):
            raise AuditError("Keys require a table and at least one column")
        if len(set(rule.columns)) != len(rule.columns):
            raise AuditError("A key must not repeat a column")
    if len(set(rules)) != len(rules):
        raise AuditError("A key check was specified more than once")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        # The first read establishes the snapshot used by all later measurements.
        definitions = connection.execute(
            "SELECT name, sql FROM main.sqlite_schema WHERE type = 'table' "
            "AND name NOT GLOB 'sqlite_*' ORDER BY name"
        ).fetchall()
        classification = {
            row[1]: row[2] for row in connection.execute("PRAGMA main.table_list")
            if row[0] == "main"
        }
        if not classification and any(
            re.match(r"\s*CREATE\s+VIRTUAL\s+TABLE\b", sql or "", re.I)
            for _, sql in definitions
        ):
            raise AuditError("Virtual/shadow table classification needs SQLite 3.37 or newer")
        unsupported = [
            {"name": name, "kind": classification[name]}
            for name, _ in definitions if classification.get(name) in ("virtual", "shadow")
        ]
        supported = [(name, sql) for name, sql in definitions
                     if classification.get(name) not in ("virtual", "shadow")]
        supported_names = {name for name, _ in supported}
        for rule in rules:
            if rule.table not in supported_names:
                raise AuditError(f"Key check refers to an unknown or unsupported table: {rule.table}")
        integrity_messages = [row[0] for row in connection.execute("PRAGMA main.integrity_check(100)")]
        tables: list[dict[str, Any]] = []
        for name, sql in supported:
            schema = _schema(connection, name, sql)
            column_names = {column["name"] for column in schema["columns"]}
            table_keys = sorted((rule for rule in rules if rule.table == name), key=lambda rule: rule.columns)
            for rule in table_keys:
                unknown = set(rule.columns) - column_names
                if unknown:
                    raise AuditError(f"Unknown key column in {name}: {', '.join(sorted(unknown))}")
            rows = connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(name)}").fetchone()[0]
            metrics = []
            for column in schema["columns"]:
                types = dict.fromkeys(("blob", "integer", "null", "real", "text"), 0)
                for storage_type, count in connection.execute(
                    f"SELECT typeof({quote_identifier(column['name'])}), COUNT(*) "
                    f"FROM {quote_identifier(name)} GROUP BY typeof({quote_identifier(column['name'])})"
                ):
                    types[storage_type] = count
                metrics.append({"name": column["name"], "nulls": types["null"], "types": types})
            tables.append({"name": name, "schema": schema, "rows": rows, "columns": metrics,
                           "keys": [_key_measurement(connection, name, rule) for rule in table_keys]})
        foreign_keys = _foreign_keys(connection, tables, fk_detail_limit)
        connection.rollback()
        return {"kind": "sqlite-audit-snapshot", "schema_version": 1,
                "scan": {"mode": "full", "sampled": False, "sqlite_version": sqlite3.sqlite_version,
                         "fk_detail_limit": fk_detail_limit},
                "integrity": {"ok": integrity_messages == ["ok"], "messages": integrity_messages,
                              "message_limit": 100, "may_be_truncated": len(integrity_messages) == 100},
                "foreign_keys": foreign_keys, "tables": tables, "unsupported_tables": unsupported}
    except sqlite3.DatabaseError as error:
        raise AuditError(f"Cannot scan SQLite database: {error}") from error
    finally:
        if connection is not None:
            connection.close()
