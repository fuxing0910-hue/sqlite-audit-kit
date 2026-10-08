# SQLite Audit Kit

**See what changed in a SQLite database, without modifying it.**

[![Tests](https://github.com/fuxing0910-hue/sqlite-audit-kit/actions/workflows/tests.yml/badge.svg)](https://github.com/fuxing0910-hue/sqlite-audit-kit/actions/workflows/tests.yml)

[Download the offline synthetic demo report](https://github.com/fuxing0910-hue/sqlite-audit-kit/releases/download/v0.1.0/sqlite-audit-demo.html) · [Releases](https://github.com/fuxing0910-hue/sqlite-audit-kit/releases)

Take a snapshot before an application update, take another afterward, and compare schema, exact row counts, NULLs, actual storage types and explicitly selected candidate keys. The result is machine-readable JSON and one offline HTML report. No server, API key or runtime dependency is required.

```text
Gate: regression | 2 regression findings
  - Duplicate key counts increased in readings (sample_key)
  - Foreign-key violation count increased in readings
```

The built-in demo produces this result from SQLite databases containing synthetic records. It also shows an added column/index, an empty table, a BLOB column and a NULL-count increase.

## Try it in a minute

Requirements: Python 3.10+ with its standard `sqlite3` module. From this repository:

```sh
git clone https://github.com/fuxing0910-hue/sqlite-audit-kit.git
cd sqlite-audit-kit
python -m sqlite_audit demo --output-dir demo
```

Open `demo/report.html` in your browser. Everything is in that file: dark-theme report, SVG comparison chart, per-column measurements and expandable schema changes. It contains no scripts, trackers, external fonts or network requests.

For an installed command:

```sh
python -m pip install .
sqlite-audit --help
```

Installation uses setuptools to build the package; the application itself has **zero third-party dependencies**. You can use `python -m sqlite_audit` directly without installation.

## Audit your database

```sh
python -m sqlite_audit scan app.db --output before.json \
  --key users:email --key events:device_id,event_id

# Update your application / database with your own tooling, then:
python -m sqlite_audit scan app.db --output after.json \
  --key users:email --key events:device_id,event_id

python -m sqlite_audit compare before.json after.json \
  --html report.html --json report.json --fail-on-regression
```

Use one line per command in PowerShell, or replace shell `\` continuations with PowerShell's backtick. The scanner does not run migrations, repair data or create a missing database. Existing output reports may be replaced; input/output aliases, including hardlinks, are rejected. The demo refuses to replace its generated files.

You may supply `--html`, `--json`, or both to `compare`. Reports are written before a requested regression exit code is returned.

## What is measured

| Measurement | Meaning |
| --- | --- |
| Schema | Declared column types/defaults, generated-column flags, primary-key order, index metadata/DDL and foreign-key definitions |
| Rows | Exact `COUNT(*)` for each supported user table |
| NULLs | Exact NULL count and its share of each table's rows; an empty table has no percentage |
| Storage types | Exact `typeof()` distribution: `null`, `integer`, `real`, `text`, `blob` |
| Selected keys | Number of duplicate groups and excess rows for the `--key` columns |
| Foreign keys | Exact violation counts per supported table; bounded details omit rowids and record values |
| Integrity | SQLite `integrity_check`, with up to 100 diagnostic messages |

All data measurements are **full scans**, not samples. This can be expensive on large databases, especially wide tables or duplicate checks. Each scan opens the file with URI `mode=ro`, enables `PRAGMA query_only`, and reads inside one transaction so a concurrent writer cannot mix different database states within a snapshot. Reads can hold locks or delay WAL checkpoint cleanup; this is not an online monitoring service.

SQLite applies type affinity, so a declared type does not guarantee one storage type. Mixed storage types, changed row counts, additional columns and more NULLs are reported as observations, not automatically labeled unhealthy. Table DDL is compared literally, so formatting-only SQL differences can appear as schema changes.

### Candidate-key semantics

`--key` is opt-in and repeatable. A key must name existing columns on a supported table. A duplicate group contains at least two rows with identical non-NULL key components. **Excess rows** is the sum of `group size − 1`. Rows with any NULL component are excluded and counted separately, consistent with SQLite's usual UNIQUE treatment of NULLs. Comparison never guesses a key from a column name.

Keep the same rules, including column order, in both snapshots. Removing or adding a rule on an existing table makes coverage incomplete. A checked key on a newly added table has a zero-table baseline. Comparisons use counts; they cannot identify whether a different set of records violates a constraint when the counts stay the same. Collation follows the database's own GROUP BY behavior.

The CLI's `table:column[,column]` grammar cannot express names containing its `:`/`,` separators. Other quoted identifiers are supported. For those unusual separator-containing names, use `KeySpec` through the Python API:

```python
from sqlite_audit import KeySpec, scan_database, compare_snapshots

before = scan_database("before.db", [KeySpec("strange:table", ("column,one",))])
after = scan_database("after.db", [KeySpec("strange:table", ("column,one",))])
comparison = compare_snapshots(before, after)
```

### Regression gate and exit codes

Without `--fail-on-regression`, a successfully generated comparison exits 0 even if it contains findings. With the flag:

| Exit | Meaning |
| --- | --- |
| `0` | Comparable checks found no counted regression |
| `1` | FK violations increased in at least one table, selected-key duplicate groups/excess rows increased, or the after integrity check failed |
| `2` | No definite regression was found, but coverage is incomplete: different key rules, an unfinished FK check, unsupported tables or a failed before integrity check |

Invalid inputs, missing files, output collisions and I/O errors also exit 2. A definite regression takes precedence over incomplete coverage; both are included in the report. This is a constraint-count gate, not a guarantee that a migration preserved application semantics. Existing unchanged violations do not count as newly increased violations.

## Scope and report contents

- Audits ordinary tables in the database's `main` schema, including WITHOUT ROWID and generated columns supported by the local SQLite build.
- Virtual and shadow tables are explicitly listed as unsupported. They are not silently treated as empty or healthy. SQLite 3.37+ is needed to classify these; older builds reject scans containing virtual tables.
- Does not open attached databases, load extensions, infer indexes to create or benchmark queries. Databases requiring unavailable custom collations/extensions may not be scannable with standard Python.
- Foreign-key definition errors are recorded as incomplete checks. Corrupt databases that prevent the scan itself from finishing return an error rather than a misleading partial snapshot.
- Snapshots omit timestamps and source paths. JSON is versioned and deterministically ordered for repeatable scans on the same database/runtime. SQLite version and diagnostic limits remain explicit metadata.
- No record payloads, BLOB bytes, duplicate-key values or rowids are exported. Schema names, schema SQL/default literals and SQLite diagnostics are included; review those metadata before sharing a report.
- Every database-derived string is HTML-escaped. Reports are portable, with no JavaScript or hosted dependencies.

FK details default to 100 entries. Set `--fk-detail-limit 0` for counts only, or another value up to 1000; the total is still exact. Integrity diagnostics are bounded by SQLite's 100-message limit and indicate possible truncation.

## Development

```sh
python -m unittest discover -s tests -v
```

Tests cover read-only behavior, consistent WAL snapshots, quoted/hostile identifiers, HTML escaping, storage types, empty/generated/WITHOUT ROWID tables, composite keys, NULL semantics, bounded FK details, virtual-table exclusions, malformed snapshots, hardlink aliases and CLI exit codes. CI is configured for Linux, Windows and macOS on Python 3.10, 3.12 and 3.14; configured coverage is not a claim that every matrix job has already run.

This is an original implementation, developed with AI assistance, using Python's public SQLite interface. Improvements should preserve exact measurements, clearly stated scope and reproducible tests.

## 中文快速开始

这是一个只读 SQLite 审计工具：比较数据库更新前后的结构、行数、NULL、实际存储类型、外键违规以及你指定的重复键。无需第三方运行依赖。

```sh
python -m sqlite_audit demo --output-dir demo
python -m sqlite_audit scan app.db --output before.json --key users:email
python -m sqlite_audit scan app.db --output after.json --key users:email
python -m sqlite_audit compare before.json after.json --html report.html --json report.json --fail-on-regression
```

演示使用明确标注的合成数据，打开 `demo/report.html` 即可查看离线报告。扫描是完整扫描，更新前后要使用相同的 `--key`。行数变化、NULL 增加、混合存储类型只展示事实，不自动判定为错误。启用回归检查时，新增的违规计数返回 1；检查范围不完整返回 2。工具只做审计，不执行迁移。

MIT License · Copyright 2026 Fu Xing
