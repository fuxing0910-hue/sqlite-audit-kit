# SQLite Audit Kit

[English](README.md) | [简体中文](README.zh-CN.md)

**An update finished. Did SQLite constraint violations increase?**

Capture a snapshot before an application update and another afterward. SQLite Audit Kit compares schema, exact row counts, NULLs, storage types, foreign-key violations, and the candidate keys you specify, then produces JSON and one offline HTML report.

**[Open the synthetic report →](https://fuxing0910-hue.github.io/sqlite-audit-kit/demo.html)** · [Project site](https://fuxing0910-hue.github.io/sqlite-audit-kit/) · [Releases](https://github.com/fuxing0910-hue/sqlite-audit-kit/releases)

[How to compare SQLite databases before and after a migration](https://fuxing0910-hue.github.io/sqlite-audit-kit/sqlite-migration-audit.html) — read-only schema, row-count, NULL, foreign-key and duplicate-key checks, with a local report.

```text
Gate: regression | 2 regression findings
  - Duplicate key counts increased in readings (sample_key)
  - Foreign-key violation count increased in readings
```

The built-in synthetic demo reproduces this result.

- **Read existing databases.** Read-only connections and one transaction per scan produce a consistent snapshot.
- **Measure exact counts.** Full scans cover rows, NULLs, actual storage types, foreign keys, and explicitly selected keys.
- **Use it in automation.** JSON, standalone HTML, and an optional regression exit code; no server, API key, or third-party runtime dependencies.

**Before sharing:** reports omit record payloads but include schema SQL, defaults, and SQLite diagnostics. Full scans can be expensive and hold read locks.

![SQLite Audit Kit report: comparison summary, constraint findings, and column measurements](docs/images/demo-preview.jpg)

## Try it locally

Requires Python 3.10+ with the standard `sqlite3` module.

```sh
git clone https://github.com/fuxing0910-hue/sqlite-audit-kit.git
cd sqlite-audit-kit
python -m sqlite_audit demo --output-dir demo
```

Open `demo/report.html`. The databases and findings are synthetic; the report has no scripts or external assets.

To compare two existing databases, generate snapshots with the same key rules:

```sh
python -m sqlite_audit scan before.db --output before.json --key readings:sample_key
python -m sqlite_audit scan after.db --output after.json --key readings:sample_key
python -m sqlite_audit compare before.json after.json --html report.html --json report.json --fail-on-regression
```

Install with `python -m pip install .` to use `sqlite-audit`. The scanner reads existing files; it does not perform application migrations or repairs.

## Let a coding agent use it

Ask a compatible agent:

> Compare these two SQLite snapshots read-only. Check whether foreign-key violations or duplicates for my specified keys increased, and write a local report.

There are four entry points:

- **CLI:** `scan` existing databases and `compare` saved JSON snapshots.
- **Python API:** `KeySpec`, `scan_database()`, and `compare_snapshots()` for integrations.
- **Function tools:** declaration exports for GPT, DeepSeek, Claude and Gemini API applications, with a validated local dispatcher.
- **Installable skill:** [sqlite-migration-audit](skills/sqlite-migration-audit/SKILL.md), task instructions for choosing the read-only audit workflow from a natural-language request.

Install the skill directly from this repository:

```sh
npx skills add fuxing0910-hue/sqlite-audit-kit --skill sqlite-migration-audit
```

The Skills CLI discovers and installs instructions from the specified repository. Node.js is needed only for that optional installer; skill commands run on Python 3.10+. This direct-install command does not depend on a directory listing or ranking.

See [the agent integration guide](docs/agents.md) for executable routing and installation choices. Python callers can use:

```python
from sqlite_audit import KeySpec, scan_database, compare_snapshots

keys = [KeySpec("readings", ("sample_key",))]
before = scan_database("before.db", keys)
after = scan_database("after.db", keys)
comparison = compare_snapshots(before, after)
```

## Gate semantics and scope

With `--fail-on-regression`, exit `0` means no counted regression in comparable checks, `1` means a counted regression, and `2` means incomplete coverage or invalid input. A definite regression takes precedence over incomplete coverage.

Checks compare constraint counts, not application semantics. Key checks are opt-in and exclude rows with NULL key components. Ordinary `main` tables are supported; virtual and shadow tables are explicitly unsupported. Unsupported or unfinished checks do not imply a healthy database.

[Complete technical reference](docs/reference.md) · [Contributing](CONTRIBUTING.md) · [MIT License](LICENSE)

```sh
python -m unittest discover -s tests -v
```

Original implementation, developed with AI assistance. See the reference for detailed measurements, key semantics, privacy limits, and SQLite compatibility.
