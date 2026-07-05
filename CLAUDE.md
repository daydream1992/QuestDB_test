# CLAUDE.md — QuestDB_test 架构规范

> 本文件是项目对 Claude Code / AI 助手的**架构契约**。
> 2026-07-05 整理批次 11 入库。
> 修改前请先看 git log，确认你的改动不破坏已确立的目录分工。

## 一、项目定位

A 股盘中实时量化监控与**信号呈现**系统（不做自动交易）。
单一数据源（tqcenter）→ QuestDB 单库 → 16 个策略插件 → 飞书三通道推送。

## 二、目录分工（不可新增同级目录）

### 业务代码（根目录一级目录，禁止再分二级子业务）

| 目录 | 作用 | 入库约定 |
|---|---|---|
| `collect/` | 数据采集（c1..c6） | `cN_<desc>.py` 命名 |
| `compute/` | 数据计算（k1..k3, k5） | `kN_<desc>.py` 命名 |
| `strategy/` | 策略（含 `plugins/` 放插件） | `base/registry/context/risk/selector` 是骨架；`plugins/pNN_<desc>.py` 是插件 |
| `runner/` | 调度（5 个 runner + scheduler） | 不修改入口方式（每个脚本可独立 `python runner/X.py`） |
| `feishu/` | 飞书三通道 | **不要**再改名前缀（数字前缀是历史坑，已修正） |
| `lib/` | 通用库 | 不依赖 collect/compute/strategy/runner/feishu |
| `ddl/` | QuestDB schema | 文件名 `NN_<desc>.sql`，`_reset_all.py` 一键重建 |
| `config/` | 配置 | `.env`（gitignore）/ `.env.example`（入库）/ `*.yaml` / `*.py` 常量 |
| `scripts/` | 维护/盘点脚本 | `data_inventory_*.py` 系列，硬编码路径常量需更新 |
| `tests/` | 验证 | `test_<desc>.py` 命名，下划线开头的标记文件进 `_deprecated/` |
| `docs/` | 文档 | 不强求整洁，但**不**放 DDL/盘点产物 |
| `logs/` | 运行时日志 | `.gitignore` 忽略 |

### 运行时数据

| 目录 | 作用 |
|---|---|
| `data/snapshots/` | 离线快照（板块个股、市场数据），被 `collect/c5_mapping` 和 `lib/relation_graph` 引用 |

### 冻结目录

| 目录 | 作用 |
|---|---|
| `_deprecated/` | 历史快照，**禁止新增依赖**。详见 `_deprecated/README.md` |

## 三、根目录规范

**根目录允许的文件**（10 个以内）：
- `README.md` / `INDEX.md` — 入口卡片
- `HANDOVER.md` / `MAINTENANCE.md` / `ARCHITECTURE_REVIEW.md` — 三件套
- `CLAUDE.md` — 本文件
- `requirements.txt` — 依赖
- `.env` / `.env.example` / `.gitignore` / `.gitattributes` — 元数据
- `e2e.py` — 唯一业务入口

**根目录禁止**：
- 任何 `*.py` 业务脚本（除 `e2e.py`）—— 进 `runner/` 或 `tests/`
- 任何 `*.sql` —— 进 `ddl/`
- 任何 `*.json` 数据 dump —— 进 `data/snapshots/` 或 `_deprecated/inventory/`
- 任何 `*.md` 文档 —— 进 `docs/`（除上述三件套 + INDEX.md + CLAUDE.md）
- 任何 `*.log` —— 进 `logs/`（自动 gitignore）

## 四、命名约定

### 文件命名

| 类型 | 命名 | 示例 |
|---|---|---|
| 采集模块 | `cN_<desc>.py` | `c1_pricevol.py` |
| 计算模块 | `kN_<desc>.py` | `k1_indicators.py` |
| 策略插件 | `pNN_<desc>.py`（NN 是 2 位数字，跳号允许） | `p01_zt_daban.py` |
| 表名 | `qd_<scope>_<type>` | `qd_stock_intraday` |
| DDL 文件 | `NN_<desc>.sql` | `16_stock_intraday.sql` |
| 测试 | `test_<desc>.py` 或 `test_<desc>_<variant>.py` | `test_3proc.py` |
| 文档 | `<scope>.md` 或 `<scope>_README.md` | `data_inventory.json`（注意 md/json 不混） |

### 目录命名

- 业务目录**纯英文**：`collect/compute/strategy/runner/feishu/lib/ddl/config/scripts/tests/docs/logs/data`
- 中文目录**仅限**已存在的：`docs/通达信量化平台说明书/` `指数板块个股映射/` `市场数据模块/`
- **不再新建数字前缀目录**（`1_collect/2_kline/...` 是历史坑）
- **不再新建下划线前缀目录**（`_deprecated/` 是唯一例外）

## 五、引用约束

### import 方向

```
runner/  →  collect/, compute/, strategy/, feishu/, lib/, ddl/, config/
collect/ →  lib/, config/
compute/ →  lib/, config/
strategy/ → lib/, config/, feishu/
feishu/  →  lib/, config/
lib/     →  (无业务依赖)
ddl/     →  lib/
scripts/ →  lib/, collect/, ddl/
tests/   →  lib/, collect/, compute/, strategy/, feishu/, runner/
```

**禁止**：
- `lib/` 引用任何业务模块
- `collect/compute/strategy/feishu` 互相循环引用
- 任何模块 `import _deprecated.*`

## 六、版本控制约定

### commit 风格

```
<type>(<scope>): <description>

<optional body>
```

| type | 用途 |
|---|---|
| feat | 新功能 |
| fix | bug 修复 |
| refactor | 重构（无行为变化） |
| perf | 性能优化 |
| docs | 文档 |
| test | 测试 |
| chore | 杂项（整理、构建） |

**scope** 用业务线名：`collect/compute/strategy/runner/feishu/lib/ddl/scripts/tests/docs/deprecated`

### commit 颗粒度

- 一个 commit 一个语义动作
- 目录迁移每个目录一个 commit
- 重命名操作 git 自动识别 R

### 历史保留

- 不删除已入库文件，改用 `_deprecated/` 收容
- `_deprecated/` 全量入库，便于恢复
- 取回用 `cp _deprecated/<file> <origin_path>` 或 `git checkout <commit> -- <file>`

## 七、QuestDB / tqcenter 约束（继承自 HANDOVER §1-3）

- 时序表模板：`TIMESTAMP(<col>) PARTITION BY DAY DEDUP UPSERT KEYS(<ts>, code)`
- 占位符必须 `%s`，不支持 `?` 或 DELETE
- 字段命名 PascalCase（与 tqcenter 一致），不翻译
- tqcenter COM 单进程，所有调用走 `lib.tq_client.safe_call`（锁 + 3 次重试）
- 时区统一用 `lib.qdb.cutoff()` 本地 now，不写 `dateadd(..., now())`

## 八、新增字段 4 步法（HANDOVER §6）

1. 改 `config/fields.py` 加字段
2. 改 `ddl/NN_<table>.sql` 加列
3. 改 `collect/cN_<module>.py` 的 `_write_<table>` rows
4. 改相关策略插件的 `required_fields()`

## 九、风控/推送约束（继承自 MAINTENANCE）

- 人类注意力 ≤ 2 条/分钟（全局频控）
- 涨停判定：`FCAmo > 0`（非 `Now >= ZTPrice`）
- 系统定位：信号呈现，不替用户决策（情绪/风控只呈现建议，不 `continue` 跳过）
- dry-run 模式必须禁推送

## 十、文档维护

- 改表结构 → 同步 `MAINTENANCE.md §3`（35 张表 + §3.10）
- 改策略插件 → 同步 `MAINTENANCE.md §8.3`（pNN 清单）
- 改业务约定 → 同步 `HANDOVER.md`
- 重大决策 → 写到 memory（`~/.claude/projects/k--QuestDB-test/memory/`）

---

## 附录：变更记录

| 日期 | 变更 | 来源 |
|---|---|---|
| 2026-07-05 | 初版入库 | 整理批次 #11 |