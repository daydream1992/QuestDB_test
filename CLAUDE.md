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

## 十一、数据动态化（前瞻架构）

**问题**：板块/个股映射数据随市场变化（新上市、板块重组、申万分类调整），离线 JSON 不能实时跟进。

### 11.1 数据分层

```
data/
├── market_data/              ← 板块/个股静态映射（基础数据）
│   ├── 市场数据/             ← 14 个 JSON（名称/板块/行业/概念/...）
│   ├── manifest.json         ← 元信息：来源/更新时间/版本/刷新策略
│   └── 模块说明.md           ← schema 文档（已存在）
├── snapshots/                ← 临时离线快照（按日期戳，可清理）
└── refresh_log/              ← 刷新日志（增量同步状态）
```

### 11.2 加载器约定

```python
# 业务代码统一通过 lib.market_data_loader 加载，禁止直接读 JSON
from lib.market_data_loader import load_market_data

data = load_market_data(refresh='auto')  # 'auto' | 'cache' | 'force'
```

**`lib.market_data_loader` 设计要点**：
- 读 `manifest.json` 的 `updated_at` 与 `refresh_strategy`
- `auto` 模式：盘后 16:00 后用新版本，盘中用 cache
- `cache` 模式：始终用内存缓存
- `force` 模式：立即重新读盘（开发/调试用）
- **未来扩展**：支持增量 delta（市场数据模块/全市场数据架构导出.py 已具备此能力）

### 11.3 刷新入口

- `scripts/refresh_market_data.py` — 手动/定时刷新入口
- runner/daily_close.py 末尾调用一次（自动）
- 失败不阻断（fallback 到旧版本 + 日志告警）

### 11.4 新增数据文件的 4 步法

1. 写入 `data/market_data/<scope>/<file>.json`
2. 在 `manifest.json` 注册：source/updated_at/refresh_strategy/schema_ref
3. 在 `lib.market_data_loader.load_X()` 加加载函数（强类型校验）
4. 业务代码 `from lib.market_data_loader import load_X` 使用

## 十二、策略层预留位（前瞻架构）

**未来 3 个独立模块**（不在本批次实现，但架构已锁）：

### 12.1 大盘情绪模块（k4_sentiment 预留位）

- **当前**：`compute/k3_sentiment.py` 只做"实时情绪快照"（5 档评级 + 6 池分类 + 跨帧变盘）
- **未来**：`compute/k4_sentiment.py` 做"深度大盘情绪"（历史情绪曲线/情绪周期/情绪-指数相关性/情绪-涨停家数预测）
- **不冲突**：k3 继续做实时，k4 做深度
- 数据源：`qd_sentiment_*` 表 + `qd_index_*` 历史

### 12.2 板块资金模块（k6_sector_capital 预留位）

- **当前**：`strategy/sector_flow.py` 只算"板块资金流聚合"（流入/流出/加速度）
- **未来**：`compute/k6_sector_capital.py` 做"板块资金深度模型"（主力/北向/融资/ETF 申赎多源融合，资金-价格背离检测，板块轮动预测）
- 数据源：`qd_sector_flow` + `qd_money_flow` + `qd_big_order` + 行情指数历史

### 12.3 个股梯队模块（k7_stock_ladder 预留位）

- **当前**：没有梯队识别
- **未来**：`compute/k7_stock_ladder.py` 做"个股梯队深度模型"（龙头/跟风/卡位/掉队识别，梯队传导路径，梯队生命周期）
- 数据源：`qd_signals` + `qd_decisions` + `qd_resonance` + 历史涨停家数

### 12.4 横截面策略插件目录（strategy/cross_section/ 预留）

- **当前**：`strategy/plugins/` 都是单标的插件（输入是单只股票的 df）
- **未来**：`strategy/cross_section/` 放横截面插件（输入是全市场 df）
- 命名约定：`cspNN_<desc>.py`（cross_section plugin）
- 注册方式：`@StrategyRegistry.register(scope='cross_section')`

### 12.5 命名位占用规则

- `compute/kN_xxx.py` 中 `k4` / `k6` / `k7` 是**预留位**，禁止占用做其他用途
- `strategy/cross_section/` 是**预留子目录**，禁止用作其他用途
- 未来开工时只需新建文件 + 在 `__init__.py` 注册，无需改 CLAUDE.md

## 十三、增量同步模式（数据动态化扩展）

```python
# lib/market_data_loader.py 未来扩展
def load_with_delta(target: str, since: str) -> DeltaResult:
    """增量加载（since 时间戳之后的变化）

    返回: {"added": [...], "modified": [...], "removed": [...]}
    用于: 板块新增股票/板块重组识别
    """
```

- 触发时机：`scripts/refresh_market_data.py` 检测到 `manifest.updated_at` 超过 24h
- 落盘位置：`data/market_data/<scope>/<file>.delta.json`（带时间戳）
- 业务影响：横截面策略（k6/k7/csp*）必须消费 delta 才能跟住市场变化

---

## 附录：变更记录

| 日期 | 变更 | 来源 |
|---|---|---|
| 2026-07-05 | 初版入库 | 整理批次 #11 |
| 2026-07-05 | 增 §11 数据动态化 + §12 策略层预留位 + §13 增量同步 | 用户指令：数据可更新/预留 3 模块 |