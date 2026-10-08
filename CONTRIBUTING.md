# Contributing

[English documentation](README.md) | [简体中文文档](README.zh-CN.md)

Bug reports, focused fixes and documentation improvements are welcome. Keep measurements exact, scanning read-only and reported limitations explicit.

## Report a reproducible issue

Please include:

- Your operating system, Python version (`python --version`) and SQLite version (`python -c "import sqlite3; print(sqlite3.sqlite_version)"`).
- The exact command, including every `--key`, `--fk-detail-limit` and comparison flag; replace sensitive paths or names with synthetic equivalents.
- Expected behavior, actual output and the process exit code.
- A minimal synthetic SQL fixture or short Python script that creates the necessary tables and records. Include relevant keys, indexes, collations, generated columns or WAL settings.
- For comparison problems, how you created both snapshots and which rules were used for each. For report-layout issues, add the browser/version and a screenshot made from synthetic data.

**Do not upload real databases, credentials or customer records.** Use synthetic records to preserve the behavior you are reporting. Snapshots and reports omit record payloads but still contain schema names, SQL/default literals and SQLite diagnostics; inspect that metadata before sharing it. A minimal fixture is more useful than a large dump.

## Submit a change

1. Fork the repository and make a focused branch for one problem.
2. Add a regression test for a behavior change, using synthetic fixtures and temporary files. Close SQLite connections explicitly and keep the test valid on Windows, Linux and macOS.
3. Run `python -m unittest discover -s tests -v` from the repository root. For report changes, generate a fresh demo with `python -m sqlite_audit demo --output-dir demo-review` and inspect its HTML; use a new directory to avoid overwriting earlier output.
4. Explain the problem, resulting behavior and commands used for validation in your pull request. Update both README versions when user-visible behavior changes.

Keep the Python 3.10+ standard-library-only runtime, protect read-only and snapshot-consistency behavior, and preserve v1 snapshot compatibility or explicitly document/version a format change. Row growth, NULL increases and mixed storage types should remain observations unless a clearly specified check says otherwise. Do not commit generated `.db` files, environments or private data. Contributions are made under the project's MIT license.

## 中文说明

欢迎提交可复现的问题、范围明确的修复和文档改进。问题报告应包含操作系统、Python/SQLite 版本、完整命令与检查规则、预期和实际结果、退出码，以及创建最小合成数据的 SQL 或 Python 脚本。比较问题请说明两份快照分别如何生成；报告显示问题请注明浏览器版本。

**不要上传真实数据库、密钥或用户记录。** 快照和报告仍包含结构名称、SQL 默认值和诊断元数据，分享前需要检查。修改行为时添加回归测试，运行上面的测试命令，并同步更新两份 README。保留只读、完整扫描、事务一致性和仅使用标准库的运行方式；不要把普通行数、NULL 或存储类型变化直接标成故障。
