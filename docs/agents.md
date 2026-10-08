# Agent integration / AI 接入

SQLite Audit Kit reads **explicitly supplied existing SQLite files** and compares audit snapshots before and after an update. It measures schema, exact counts, NULLs, storage types, foreign-key violations and explicitly selected candidate keys. It does not perform migrations or repairs, and a passing count comparison does not prove application correctness.

SQLite Audit Kit 只读检查明确指定的现有数据库，比较更新前后的审计快照。它提供可复核的计数和约束发现，不执行迁移、修复或推断业务语义。

For a complete task example and runnable install path, see [the bilingual SQLite migration audit guide](https://fuxing0910-hue.github.io/sqlite-audit-kit/sqlite-migration-audit.html).

## Choose an entry point

| Environment | Entry point | What must happen first |
| --- | --- | --- |
| Coding agent supporting Agent Skills | `sqlite-migration-audit` skill | Install the skill into that agent's configured skill location. |
| Agent with a shell or Python runtime | CLI or Python API | Install the package, or use a repository checkout. |
| GPT, DeepSeek, Claude or Gemini API application | Function declarations + local dispatcher | The application registers declarations and executes requested calls. |
| Ordinary chat page | Its supported integrations | The host application must expose the tool; publishing a repository does not register it. |

模型和应用是两层：DeepSeek 模型也可运行在支持 Skill 的编程代理里；普通聊天页面的工具权限取决于应用提供的接入方式。

## Install the portable skill

From the project where your agent should use it:

```sh
npx skills add fuxing0910-hue/sqlite-audit-kit --skill sqlite-migration-audit
```

The optional installer needs Node.js. The skill includes its Python implementation; its runner requires only Python 3.10+ with standard `sqlite3`, without pip, a checkout, a model API key or an external service.

查看 [Skills CLI](https://github.com/vercel-labs/skills) 支持的代理，使用 `--agent` 指定安装对象；不加 `-g` 时在当前项目安装。也可下载完整 `skills/sqlite-migration-audit` 文件夹，保留脚本，放入应用支持的 Skill 目录。

Example request after installation:

> Compare these before/after SQLite databases read-only. Check whether foreign-key violations or duplicate keys for readings.sample_key increased, and write a local report.

> 只读比较这两个更新前后的 SQLite 数据库，检查外键违规和 readings.sample_key 重复是否增加，生成本地报告。

Read [SKILL.md](../skills/sqlite-migration-audit/SKILL.md) for runner commands, baseline requirements, gate semantics and coverage limits. Use the same explicitly intended keys in both scans.

## Register function tools in an API application

Install with `python -m pip install .`, then export declarations:

```sh
python -m sqlite_audit.agent_tools --list --provider openai
python -m sqlite_audit.agent_tools --list --provider deepseek
python -m sqlite_audit.agent_tools --list --provider claude
python -m sqlite_audit.agent_tools --list --provider gemini
```

| Provider argument | Declaration format | Official contract |
| --- | --- | --- |
| `openai` | Responses API function tools, strict schema | [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling) |
| `deepseek` | Chat Completions `tools[].function` | [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) |
| `claude` | Messages tool definitions with `input_schema` | [Claude tool definitions](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools) |
| `gemini` | Interactions API function tools | [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling) |

`gemini` targets the **Interactions API**, not the different legacy `generateContent` envelope. `deepseek` does not enable beta strict mode. The application owns API authentication, model selection and tool-result messages.

Two tools are available:

- `sqlite_scan_readonly`: `database_path` plus `candidate_keys` (an empty array disables candidate-key checks). Returns a versioned audit snapshot.
- `sqlite_compare_snapshots`: `before_snapshot_path` and `after_snapshot_path`. Validates both saved snapshots and returns comparison findings and gate metadata.

All parameters are required. A local scan request:

```json
{
  "name": "sqlite_scan_readonly",
  "arguments": {
    "database_path": "demo/before.db",
    "candidate_keys": [{"table": "readings", "columns": ["sample_key"]}]
  }
}
```

Generate a synthetic fixture with `python -m sqlite_audit demo --output-dir demo`, save the request as `request.json`, then run:

```sh
python -m sqlite_audit.agent_tools --request request.json
```

For application integration:

```python
from sqlite_audit.agent_tools import tool_definitions, call_tool

tools = tool_definitions("deepseek")  # Supply these to your API client's tools field.
result = call_tool("sqlite_compare_snapshots", {
    "before_snapshot_path": "demo/before.json",
    "after_snapshot_path": "demo/after.json",
})
```

The application takes a returned function name and arguments, calls this local dispatcher, and sends the JSON result back using the provider's tool-result format. The dispatcher validates names and argument types; it does not execute arbitrary SQL, shell commands or model requests. CLI input/execution errors exit 2. A valid comparison containing regression findings remains a successful tool execution; inspect the returned gate.

必须先把声明放入应用的可用工具列表，模型才可能选择调用。应用应在本地执行白名单函数，检查返回的 gate 和覆盖范围，再按相应 API 格式提交结果。缺少历史基线时，不能把当前数据库冒充为更新前数据。

## Scope, discovery and verification

Scans are full and can be expensive. Existing files are opened read-only; missing files are not created. Record payloads are omitted, but **schema SQL, default literals, names and SQLite diagnostics can remain sensitive**. Decide which metadata may be sent to a model API before forwarding results. Counts do not establish that the same violating records remained unchanged.

精准的 Skill 和函数说明包含任务触发条件、输入、输出与限制。[纯文本资料索引](https://fuxing0910-hue.github.io/sqlite-audit-kit/llms.txt) 方便检索和集成，但不会自动注册到全球模型工具库，也不保证目录收录、搜索排名或调用率。

Tests cover provider envelopes, real synthetic scans/comparisons, read-only behavior and invalid arguments. These are local contract/execution checks; no paid inference was used to measure model selection rates.

```sh
python scripts/sync_skill.py --check
```

[CLI and technical reference](reference.md) · [中文技术参考](reference.zh-CN.md)
