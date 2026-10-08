# SQLite Audit Kit — 完整技术参考

[项目概览](../README.zh-CN.md) · [English](reference.md) · [简体中文](reference.zh-CN.md)


**只读检查 SQLite 数据库，清楚看见更新前后的变化。**



在应用更新前后各生成一份快照，就能比较数据库结构、精确行数、NULL、实际存储类型，以及你明确指定的候选键。结果包含便于程序读取的 JSON 和单个离线 HTML 报告。无需服务器、API 密钥或第三方运行依赖。

```text
Gate: regression | 2 regression findings
  - Duplicate key counts increased in readings (sample_key)
  - Foreign-key violation count increased in readings
```

上面的输出表示发现两项回归：`readings.sample_key` 的重复键计数增加，以及 `readings` 的外键违规计数增加。内置演示使用包含合成记录的 SQLite 数据库，能够复现这份结果，还展示新增的列和索引、空表、BLOB 列以及 NULL 计数增加。

## 一分钟试用

需要 Python 3.10+，并包含标准库的 `sqlite3` 模块。克隆仓库后运行：

```sh
git clone https://github.com/fuxing0910-hue/sqlite-audit-kit.git
cd sqlite-audit-kit
python -m sqlite_audit demo --output-dir demo
```

用浏览器打开 `demo/report.html`。深色报告、SVG 对比图、逐列统计和可展开的结构变化都包含在这一个文件内。报告不包含脚本、跟踪器、外部字体或网络请求。

也可以安装后使用命令行入口：

```sh
python -m pip install .
sqlite-audit --help
```

安装时通过 setuptools 构建软件包；应用本身**没有第三方运行依赖**。无需安装也能直接使用 `python -m sqlite_audit`。

## 审计自己的数据库

```sh
python -m sqlite_audit scan app.db --output before.json \
  --key users:email --key events:device_id,event_id

# 使用自己的工具更新应用或数据库，然后再次扫描：
python -m sqlite_audit scan app.db --output after.json \
  --key users:email --key events:device_id,event_id

python -m sqlite_audit compare before.json after.json \
  --html report.html --json report.json --fail-on-regression
```

在 PowerShell 中，请把每条命令写在一行，或将上面的 shell `\` 换行符替换为 PowerShell 的反引号。扫描器不执行迁移、不修复数据，也不会创建不存在的数据库。已有的输出报告可以被替换，但工具会拒绝输入与输出指向同一文件的情况，包括硬链接。演示命令拒绝覆盖已经生成的演示文件。

`compare` 可以指定 `--html`、`--json` 或同时指定两者。即使启用了回归退出码，报告也会先写入，再返回相应退出码。

## 检查哪些内容

| 检查项 | 含义 |
| --- | --- |
| 数据库结构 | 列的声明类型与默认值、生成列标记、主键列顺序、索引元数据与 DDL，以及外键定义 |
| 行数 | 每个受支持用户表的精确 `COUNT(*)` |
| NULL | 精确 NULL 计数及其占该表行数的比例；空表不计算百分比 |
| 存储类型 | 精确 `typeof()` 分布：`null`、`integer`、`real`、`text`、`blob` |
| 指定的键 | `--key` 列对应的重复分组数与多余行数 |
| 外键 | 每个受支持表的精确违规计数；数量受限的详情省略 rowid 和记录值 |
| 完整性 | SQLite `integrity_check`，最多包含 100 条诊断信息 |

所有数据统计都是**完整扫描（full scan）**，不进行抽样。大型数据库、宽表或重复键检查可能需要较多时间。每次扫描都使用 URI `mode=ro` 打开文件，启用 `PRAGMA query_only`，并在同一个事务内读取数据，避免并发写入让一份快照混入不同时间的数据库状态。读取仍可能持有锁，或延迟 WAL 检查点的清理；这个工具并不是在线监控服务。

SQLite 使用类型亲和性（type affinity），声明类型并不能保证每个值都使用同一种存储类型。混合存储类型、行数变化、新增列和 NULL 增加都作为观察结果展示，不会自动被判为异常。表的 DDL 按原始文本比较，因此只修改 SQL 格式也可能显示为结构变化。

### 候选键的检查语义

`--key` 需要主动指定，可以重复使用。候选键必须指向受支持表中已经存在的列。当至少两行的所有非 NULL 键分量相同时，它们构成一个重复分组。**多余行数（excess rows）**是各重复分组的 `分组行数 − 1` 之和。任一键分量为 NULL 的行会被排除，并单独计数，这与 SQLite 通常对 UNIQUE 中 NULL 的处理方式一致。比较时不会根据列名猜测候选键。

两份快照应使用相同的规则，包括相同的列顺序。在已有表上新增或删除检查规则，会使检查覆盖范围不完整。新建表上指定的键以“原先没有这张表”作为零值基线。比较依据是计数；如果计数没有变化，工具无法识别是否换成了另一组违规记录。排序规则（collation）遵循数据库自身的 GROUP BY 行为。

命令行的 `table:column[,column]` 语法无法表示名称中含有 `:` 或 `,` 分隔符的情况。其他需要引用的标识符可以正常处理。对于名称中含有这些分隔符的表或列，可以通过 Python API 使用 `KeySpec`：

```python
from sqlite_audit import KeySpec, scan_database, compare_snapshots

before = scan_database("before.db", [KeySpec("strange:table", ("column,one",))])
after = scan_database("after.db", [KeySpec("strange:table", ("column,one",))])
comparison = compare_snapshots(before, after)
```

### 回归检查与退出码

未指定 `--fail-on-regression` 时，只要成功生成比较结果，即使发现回归也返回 0。启用该参数后：

| 退出码 | 含义 |
| --- | --- |
| `0` | 可比较的检查没有发现计数层面的回归 |
| `1` | 至少一张表的外键违规增加、指定键的重复分组数或多余行数增加，或者更新后的完整性检查失败 |
| `2` | 没有发现确定的回归，但检查范围不完整：键规则不同、外键检查未完成、包含不受支持的表，或更新前的完整性检查失败 |

无效输入、文件不存在、输出路径冲突以及 I/O 错误也返回 2。确定的回归优先于覆盖范围不完整的状态；报告中会同时展示两者。这个检查依据约束违规计数，不能保证迁移保留了应用的全部语义。已经存在且计数未变的违规，不会被计为新增的违规。

## 支持范围与报告内容

- 审计数据库 `main` 模式中的普通表，包括本地 SQLite 支持的 WITHOUT ROWID 表和生成列。
- 虚拟表（virtual tables）和影子表（shadow tables）会明确列为不受支持，不会被默认为空表或健康表。分类需要 SQLite 3.37+；较旧版本会拒绝扫描含有虚拟表的数据库。
- 不打开附加数据库、不加载扩展、不推断应创建哪些索引，也不对查询进行性能基准测试。依赖不可用的自定义排序规则或扩展的数据库，可能无法使用标准 Python 扫描。
- 外键定义错误会记录为未完成的检查。如果数据库损坏导致扫描本身无法完成，工具返回错误，而不会输出容易造成误解的部分快照。
- 快照省略时间戳和源文件路径。JSON 带有格式版本，并采用确定性的排序，使同一数据库与运行环境上的扫描可以复现。SQLite 版本和诊断数量上限仍作为明确的元数据保留。
- 不导出记录内容、BLOB 字节、重复键的实际值或 rowid。报告仍包含结构名称、结构 SQL、默认值字面量和 SQLite 诊断信息；分享报告前应检查这些元数据。
- 来自数据库的所有字符串都经过 HTML 转义。报告可直接携带和打开，不依赖 JavaScript 或托管服务。

外键详情默认最多显示 100 条。使用 `--fk-detail-limit 0` 可以只保留计数，也可以指定不超过 1000 的其他上限；总计数始终是精确值。完整性诊断受 SQLite 的 100 条信息上限约束，并标注可能截断的情况。

## 开发与测试

```sh
python -m unittest discover -s tests -v
```

测试覆盖只读行为、WAL 下的快照一致性、需要引用或含有恶意内容的标识符、HTML 转义、存储类型、空表、生成列、WITHOUT ROWID 表、复合键、NULL 语义、受限的外键详情、虚拟表排除、无效快照、硬链接别名和 CLI 退出码。CI 配置覆盖 Linux、Windows、macOS，以及 Python 3.10、3.12、3.14；配置了测试矩阵并不代表每个组合都已经运行过。

这是使用 Python 公开 SQLite 接口、在 AI 辅助下开发的原创实现。改进应保留精确统计、明确的支持范围和可复现的测试。

提交问题和拉取请求前，请阅读[贡献指南](../CONTRIBUTING.md)。复现应使用最小合成数据，不要上传真实数据库。

MIT License · Copyright 2026 Fu Xing
