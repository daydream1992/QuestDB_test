# CLAUDE.md 完整规范（低频段，按需查阅）

> 本文件是 [CLAUDE.md](../CLAUDE.md) 精简版的完整补充。CLAUDE.md 只保留高频约束（每轮 system 注入），本文供 Claude 在写代码 / 改表 / commit 时按需 Read。

## A. 命名约定（完整版）

### 文件命名

| 类型 | 命名 | 示例 |
|---|---|---|
| 采集模块 | `cN_<desc>.py` | `c1_pricevol.py` |
| 计算模块 | `kN_<desc>.py` | `k1_indicators.py` |
| 策略插件 | `pNN_<desc>.py`（NN 是 2 位数字，跳号允许） | `p01_zt_daban.py` |
| 横截面插件 | `cspNN_<desc>.py`（预留位） | — |
| 表名 | `qd_<scope>_<type>` | `qd_stock_intraday` |
| DDL 文件 | `NN_<desc>.sql` | `16_stock_intraday.sql` |
| 测试 | `test_<desc>.py` 或 `test_<desc>_<variant>.py` | `test_3proc.py` |
| 文档 | `<scope>.md` 或 `<scope>_README.md` | — |

### 目录命名

- 业务目录**纯英文**：`collect/compute/strategy/runner/feishu/lib/ddl/config/scripts/tests/docs/logs/data`
- 中文目录**仅限**已存在的：`docs/通达信量化平台说明书/` `指数板块个股映射/` `市场数据模块/`
- **不再新建数字前缀目录**（`1_collect/2_kline/...` 是历史坑）
- **不再新建下划线前缀目录**（`_deprecated/` 是唯一例外）

## B. import 方向完整版

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

**禁止**：`lib/` 引用业务模块；业务模块循环引用；`import _deprecated.*`

## C. commit 风格

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

**scope**：`collect/compute/strategy/runner/feishu/lib/ddl/scripts/tests/docs/deprecated`

**颗粒度**：一个 commit 一个语义动作；目录迁移每个目录一个 commit；重命名 git 自动识别 R。

**历史保留**：不删除已入库文件，改用 `_deprecated/` 收容（详见 `_deprecated/README.md`）。

## D. 数据动态化（前瞻架构）

### D.1 数据分层

```
data/
├── market_data/              ← 板块/个股静态映射（基础数据）
│   ├── 市场数据/             ← 14 个 JSON（名称/板块/行业/概念/...）
│   ├── manifest.json         ← 元信息：来源/更新时间/版本/刷新策略
│   └── 模块说明.md           ← schema 文档
├── snapshots/                ← 临时离线快照（按日期戳，可清理）
└── refresh_log/              ← 刷新日志（增量同步状态）
```

### D.2 加载器约定

> ⚠️ **当前未实现**：`lib/market_data_loader.py` 尚未创建。业务代码当前加载走 `lib.relation_graph.load_from_json`（被 `collect/c5_mapping` 与 `runner/intraday_loop` 引用）。以下为前瞻设计，开工时再实现。

```python
# 业务代码统一通过 lib.market_data_loader 加载，禁止直接读 JSON
from lib.market_data_loader import load_market_data

data = load_market_data(refresh='auto')  # 'auto' | 'cache' | 'force'
```

### D.3 刷新入口（前瞻）

- `scripts/refresh_market_data.py` — 手动/定时刷新入口
- `runner/daily_close.py` 末尾调用一次（自动）
- 失败不阻断（fallback 到旧版本 + 日志告警）

### D.4 新增数据文件的 4 步法

1. 写入 `data/market_data/<scope>/<file>.json`
2. 在 `manifest.json` 注册：source/updated_at/refresh_strategy/schema_ref
3. 在 `lib.market_data_loader.load_X()` 加加载函数（强类型校验）
4. 业务代码 `from lib.market_data_loader import load_X` 使用

## E. 策略层预留位（前瞻架构）

**未来 3 个独立模块**（不在本批次实现，但架构已锁）：

### E.1 大盘情绪模块（k4_sentiment 预留位）

- **当前**：`compute/k3_sentiment.py` 只做"实时情绪快照"
- **未来**：`compute/k4_sentiment.py` 做"深度大盘情绪"（历史曲线/周期/相关性）
- 数据源：`qd_sentiment_*` + `qd_index_*` 历史

### E.2 板块资金模块（k6_sector_capital 预留位）

- **当前**：`strategy/sector_flow.py` 只算"板块资金流聚合"
- **未来**：`compute/k6_sector_capital.py` 做"板块资金深度模型"
- 数据源：`qd_sector_flow` + `qd_money_flow` + `qd_big_order` + 行情指数历史

### E.3 个股梯队模块（k7_stock_ladder 预留位）

- **当前**：没有梯队识别
- **未来**：`compute/k7_stock_ladder.py` 做"个股梯队深度模型"
- 数据源：`qd_signals` + `qd_decisions` + `qd_resonance` + 历史涨停家数

### E.4 横截面策略插件目录（strategy/cross_section/ 预留）

- **当前**：`strategy/plugins/` 都是单标的插件
- **未来**：`strategy/cross_section/` 放横截面插件
- 命名：`cspNN_<desc>.py`
- 注册：`@StrategyRegistry.register(scope='cross_section')`

### E.5 命名位占用规则

- `compute/kN_xxx.py` 中 `k4`/`k6`/`k7` 是**预留位**，禁止占用
- `strategy/cross_section/` 是**预留子目录**，禁止用作其他用途

## F. 增量同步模式（数据动态化扩展）

```python
# lib/market_data_loader.py 未来扩展
def load_with_delta(target: str, since: str) -> DeltaResult:
    """增量加载（since 时间戳之后的变化）"""
    # 返回: {"added": [...], "modified": [...], "removed": [...]}
```

- 触发时机：`scripts/refresh_market_data.py` 检测到 `manifest.updated_at` 超过 24h
- 落盘位置：`data/market_data/<scope>/<file>.delta.json`（带时间戳）
- 业务影响：横截面策略（k6/k7/csp*）必须消费 delta 才能跟住市场变化

## G. 变更记录

| 日期 | 变更 | 来源 |
|---|---|---|
| 2026-07-05 | 初版入库 | 整理批次 #11 |
| 2026-07-05 | 增 §11 数据动态化 + §12 策略层预留位 + §13 增量同步 | 用户指令：数据可更新/预留 3 模块 |
| 2026-07-05 | § 二 / 目录结构同步 v5 整理后状态 (#12~#19) | 4_feishu→feishu / 数据切换 / 旧版流水线归档 |
| 2026-07-14 | CLAUDE.md 精简 + 本文件按需查阅 | token 优化 #1 |