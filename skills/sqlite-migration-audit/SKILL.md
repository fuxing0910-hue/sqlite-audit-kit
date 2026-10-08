---
name: sqlite-migration-audit
description: "Read-only SQLite migration/update audit: compare schema, row counts, NULLs, storage types, foreign keys and selected duplicate keys in before/after snapshots. 只读对比 SQLite 更新前后的结构、行数与约束；不执行迁移或修复。"
---

# SQLite migration audit

Use the bundled runner to inspect an existing SQLite database or compare snapshots before and after a migration/update. It uses Python 3.10+ and the standard library only: no pip installation, network access, repository checkout or external service is needed. Resolve the runner relative to this `SKILL.md`: [scripts/run.py](scripts/run.py).

## Run the requested audit

Use the user's existing database paths or v1 audit snapshots. If a before/after comparison lacks a baseline, report that limitation instead of treating the current database as historical evidence. Select output paths in the task's working/output directory.

Replace `<skill-dir>` with this installed skill folder. The runner accepts the same scan/compare/demo options as SQLite Audit Kit:

```sh
python "<skill-dir>/scripts/run.py" scan "before.db" --output "before.json" --key "users:email"
python "<skill-dir>/scripts/run.py" scan "after.db" --output "after.json" --key "users:email"
python "<skill-dir>/scripts/run.py" compare "before.json" "after.json" --html "report.html" --json "report.json" --fail-on-regression
```

For one current database, run `scan` and explain its current findings. For existing audit JSON snapshots, go straight to `compare`. A comparison needs `--html`, `--json`, or both. Reports are written before the requested gate exit code is returned.

`--key table:column[,column]` is optional and repeatable; use explicitly intended candidate keys, not guessed uniqueness based on names. Use identical rules and column order in both snapshots. Any-NULL key rows are excluded and counted separately. Duplicate groups contain at least two equal non-NULL keys; excess rows sum `group size − 1`. For separator-containing identifiers use the bundled `sqlite_audit.KeySpec` Python API, since the CLI grammar cannot express those names.

To try the workflow without a supplied database:

```sh
python "<skill-dir>/scripts/run.py" demo --output-dir "synthetic-demo"
```

The demo generates synthetic records in a fresh directory and refuses to replace its files. Demo results are not evidence about the user's database.

## Scope and interpretation

- Scans open an existing file using URI `mode=ro`, enable `PRAGMA query_only` and collect all measurements inside one read transaction. Do not execute migrations, repair SQL, database writes or extension loading as part of this skill. The demo writes only its newly generated fixture databases.
- Counts, NULLs, `typeof()` distributions and selected duplicate-key checks are full scans, not samples. Large scans can be expensive; reads may hold locks or delay WAL checkpoint cleanup.
- Ordinary `main` tables are audited. Virtual/shadow tables are explicitly unsupported; SQLite 3.37+ is needed to classify them. Foreign-key definition errors, unsupported tables and different key coverage prevent a complete gate pass. No attached databases or custom extensions are loaded.
- Row growth, schema changes, more NULLs and mixed SQLite storage types are observations, not automatic failures. Do not invent a health score or infer application correctness. Counts cannot identify changed violating records when the counts stay equal.
- FK totals remain exact while details are bounded: `--fk-detail-limit` defaults to 100, accepts 0–1000, and omits rowids/record values. Integrity diagnostics are capped at 100 messages. Snapshot JSON is versioned; unsupported or malformed snapshots return an error.

## Read the gate correctly

Without `--fail-on-regression`, a successfully generated comparison exits 0 even when findings exist. With the flag:

| Exit | Interpretation |
| --- | --- |
| 0 | Comparable checks found no counted regression. Unchanged existing violations can remain. |
| 1 | FK violations increased in a table, selected-key duplicate groups/excess rows increased, or the after integrity check failed. This is an audit finding, not a runner crash. |
| 2 | Invalid input/I/O error, or no definite regression but incomplete coverage: mismatched key rules, unfinished FK checks, unsupported tables or failed before integrity check. |

A definite regression takes precedence over incomplete coverage; the report includes both. Summarize concrete findings and coverage limits, and link the generated artifacts.

Record payloads, BLOB bytes, duplicate-key values and rowids are omitted, but **schema names, schema SQL/default literals and SQLite diagnostics remain in JSON/HTML**. Tell the user to review these metadata before externally sharing a report. The HTML is standalone and contains no scripts or external resources.
