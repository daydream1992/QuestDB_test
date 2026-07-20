# CLAUDE.md — QuestDB_test 架构规范（精简版）

> 本文件是项目对 Claude Code / AI 助手的**架构契约**。高频约束全在此；低频细节见 `docs/CLAUDE_DETAIL.md`。
> 2026-07-14 精简（原 280 行 → 本文件 ~60 行）。修改前请先看 git log。

## 一、项目定位

A 股盘中实时量化监控与**信号呈现**系统（不做自动交易）。
单一数据源（tqcenter）→ QuestDB 单库 → 5 个策略插件（2026-07-14 瘦身：p01/p04/p08[休眠]/p17/p28）→ 飞书三通道推送。

## 二、目录分工（高频）

| 目录 | 作用 | 命名 |
|---|---|---|
| `collect/` | 数据采集 c1..c6 | `cN_<desc>.py` |
| `compute/` | 数据计算 k1..k7（k4 历史遗留拆 3 文件；**k6 板块联动 / k7 票型分类**为吃肉系统底座） | `kN_<desc>.py` |
| `strategy/` | 策略，含 `plugins/`（单标的）+ `cross_section/`（**预留**） | `pNN_<desc>.py` |
| `runner/` | 调度（5 runner + scheduler） | 不改入口方式 |
| `feishu/` | 飞书三通道 | **不再改名前缀** |
| `lib/` | 通用库 | **不依赖任何业务模块** |
| `ddl/` | QuestDB schema | `NN_<desc>.sql` |
| `config/` | 配置 | `.env`/`*.yaml`/`*.py` |
| `scripts/` | 维护盘点脚本 | `data_inventory_*.py` |
| `tests/` | 验证 | `test_<desc>.py` |
| `docs/` | 文档（**不**放 DDL/盘点产物） | — |
| `logs/` | 运行时日志（gitignore） | — |

冻结目录：`_deprecated/`（**禁止新增依赖**，详见 `_deprecated/README.md`）

**根目录禁止**：业务 `.py` / `.sql` / `.json` 数据 dump / `.md` 文档（除 README/HANDOVER/MAINTENANCE/ARCHITECTURE_REVIEW/INDEX/CLAUDE）/ `.log`

## 三、import 方向（核心约束）

```
runner/    →  collect/, compute/, strategy/, feishu/, lib/, ddl/, config/
collect/   →  lib/, config/
compute/   →  lib/, config/
strategy/  →  lib/, config/, feishu/
feishu/    →  lib/, config/
lib/       →  (无业务依赖)
ddl/       →  lib/
tests/     →  lib/, collect/, compute/, strategy/, feishu/, runner/
```

**禁止**：`lib/` 引用业务模块；业务模块循环引用；`import _deprecated.*`

## 四、风控/推送硬约束（最高优先级）

- 人类注意力 ≤ 2 条/分钟（全局频控）
- 涨停判定：`FCAmo > 0`（**非** `Now >= ZTPrice`）—— p01/intraday_engine 已按此落实
- 系统定位：信号呈现，**不替用户决策**（情绪/风控只呈现建议，不 `continue` 跳过）
- dry-run 模式必须禁推送
- tqcenter 字段名陷阱：拼音缩写易误读（`fLianB` = 量比**非**连板）；查 `docs/通达信量化平台说明书/`

## 五、QuestDB / tqcenter 核心约束

- 时序表模板：`TIMESTAMP(<col>) PARTITION BY DAY DEDUP UPSERT KEYS(<ts>, code)`
- 占位符必须 `%s`，不支持 `?` 或 DELETE
- 字段命名 PascalCase（与 tqcenter 一致），**不翻译**
- tqcenter COM 单进程，所有调用走 `lib.tq_client.safe_call`（锁 + 3 次重试）
- 时区统一用 `lib.qdb.cutoff()` 本地 now，不写 `dateadd(..., now())`

## 六、新增字段 4 步法

1. 改 `config/fields.py` 加字段
2. 改 `ddl/NN_<table>.sql` 加列
3. 改 `collect/cN_<module>.py` 的 `_write_<table>` rows
4. 改相关策略插件的 `required_fields()`

## 七、文档维护

- 改表结构 → 同步 `MAINTENANCE.md §3`（35 张表 + §3.10）
- 改策略插件 → 同步 `MAINTENANCE.md §8.3`（pNN 清单）
- 改业务约定 → 同步 `HANDOVER.md`
- 重大决策 → 写到 memory（`~/.claude/projects/k--QuestDB-test/memory/`）

---

**完整规范**（commit 风格表格、命名细节、k4 多文件说明、import 方向完整版、数据动态化 §11、增量同步 §13）见 [docs/CLAUDE_DETAIL.md](docs/CLAUDE_DETAIL.md)。