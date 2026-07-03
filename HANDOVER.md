# QuestDB_test 开发交接文档

> 本文档面向接手本项目的 AI 或开发者，补充 MAINTENANCE.md 中未覆盖的设计决策和开发上下文。
> MAINTENANCE.md 是运维手册，本文档是开发手册。

---

## 1. 系统架构决策

### 1.1 为什么用 QuestDB 而非 MySQL/PostgreSQL

- **时序优化**：DEDUP UPSERT KEYS 自动去重，无需 WHERE + ORDER BY + LIMIT 1
- **分区自动**：PARTITION BY DAY/WEEK/MONTH 按时间戳自动分区
- **Python 生态**：psycopg2 直连，SQL 与 pandas 混用
- **限制**：不支持 `?` 占位符，必须用 `%s`；不支持 DELETE，历史数据通过重建表清空

### 1.2 为什么用 tqcenter 而非 API

- tqcenter 是通达信本地 COM 组件，数据全、延迟低
- **COM 单进程**：不支持多线程并发，`safe_call` 用锁 + 重试 3 次包装
- **多进程安全**：各 runner 进程独立 init/close，互不影响
- tqcenter 路径由环境变量 `TQCENTER_PATH` 控制，默认 `K:\txdlianghua\PYPlugins\sys`

### 1.3 为什么字段名用中文缩写

tqcenter 返回字段名直接用中文缩写（如 `Zjl`=主力净流入，`ZAF`=实时涨幅），代码保持一致，不翻译。
字段类型全部大写 DOUBLE/BIGINT，VARCHAR 专门标注。

---

## 2. 数据流详解

### 2.1 盘中主循环（intraday_loop）每轮执行顺序

```
轮次 round_idx (从 0 开始，末尾 +1)
│
├─ 10s 采集块（每轮）
│   ├─ c1_pricevol     全场 5568 只（3 字段）
│   ├─ selector        选出 100 只重点池（涨幅=100 量比=0）
│   ├─ c2_snapshot    100 只快照（43 字段）
│   └─ c3_more_info   100 只高频（intraday 模式，写 qd_stock_snapshot）
│
└─ 60s 计算块（round_idx % 6 == 0 时，约每 6 轮 = 1 分钟）
    ├─ c4_kline 1m     全场补拉 1 根 1m K 线
    ├─ c4_kline 5m     全场补拉 1 根 5m K 线
    ├─ k1_indicators    读 qd_kline_5m 最新 10 分钟，算 MACD/BOLL/MA
    ├─ k2_signals       读 qd_indicators，检测金叉/死叉/突破/跌破
    ├─ _build_context   策略上下文：聚合 5 张表数据
    ├─ 遍历 16 策略插件   每个 .evaluate(ctx) → decisions
    ├─ _process_decisions 风控 + 飞书推送
    ├─ _run_sector_flow 板块资金流聚合（写 qd_sector_flow）
    └─ _run_resonance   共振分析（读刚写的 qd_sector_flow）
```

### 2.2 关键设计点

- **c3 intraday 时间戳**：`snapshot_time = now + timedelta(seconds=1)`，比 c2 晚 1 秒，避免同时间戳覆盖
- **共振依赖板块资金流**：`_run_sector_flow` 必须在 `_run_resonance` 之前执行（否则读不到当轮数据）
- **qd_sector_flow 查询用 `code` 列**（板块代码），不是 `block_code`
- **qd_sector_flow 用 `main_net` 字段**（主力净流入），不是 `net_flow`

### 2.3 字段与 DDL 对齐规则

新增字段需要改 **3 处**，缺一不可：

| 位置 | 改什么 |
|------|--------|
| `config/fields.py` | 添加字段定义（名称 + 类型） |
| `ddl/XX_*.sql` | 添加列定义（类型 + PARTITION BY） |
| `collect/c*.py` | `_write_*` 函数的 rows 添加该列的值 |

---

## 3. QuestDB 开发规范

### 3.1 建表语法模板

```sql
CREATE TABLE IF NOT EXISTS qd_xxx (
    code              VARCHAR,           -- 标的代码，主键之一
    snapshot_time     TIMESTAMP,         -- 时间戳列，类型必须 TIMESTAMP
    -- 其他列...
) TIMESTAMP(snapshot_time) PARTITION BY DAY  -- 按天分区
DEDUP UPSERT KEYS(snapshot_time, code);      -- 时间戳+code 去重
```

**注意**：
- 时间戳列类型是 `TIMESTAMP`，不是 `DATETIME`
- `PARTITION BY DAY` 自动按天分区，无需手动维护
- `DEDUP UPSERT KEYS` 是 QuestDB 特有语法，tqcenter 同一标的同一时刻多次写入自动去重

### 3.2 幂等写入（candle 场景）

```python
# 每轮补拉 1 根新 K 线，QuestDB 自动去重同一时刻的重复数据
c4.run(codes, period='5m', count=1, con=con)
```

### 3.3 查询最新一行

```sql
-- QuestDB 特有语法：LATEST ON
SELECT * FROM qd_pricevol LATEST ON snapshot_time PARTITION BY code;

-- pandas 等效（代码中用这个）
df.sort_values('snapshot_time').groupby('code', as_index=False).last()
```

### 3.4 SQL 占位符

QuestDB 只认 `%s`，不能用 `?`：

```python
cur.execute("SELECT * FROM qd_pricevol WHERE code = %s", (code,))
```

---

## 4. 策略插件开发模板

```python
"""pXX: 中文策略名"""
from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

@StrategyRegistry.register
class XxxStrategy(StrategyBase):
    name = 'xxx_strategy'    # 与 strategies.yaml 的 key 必须一致
    version = '1.0'
    enabled = True           # 改 False 可在 yaml 里控制

    def required_fields(self):
        # 本策略需要的 DataFrame 列名（从 ctx 传入前已通过 SELECT 过滤）
        # 列名必须与 DDL 一致（大小写敏感）
        return ['code', 'Now', 'LastClose']

    def evaluate(self, ctx) -> list[Decision]:
        # ctx 包含所有数据：pricevol_df / snapshot_focus_df / more_info_df /
        #                   indicators_df / signals_df / sector_flow_df / ...
        decisions = []
        # ... 策略逻辑 ...
        if condition:
            decisions.append(Decision(
                action='buy',      # buy / sell / hold / watch
                code=code,
                strategy=self.name,
                reason='原因描述',
                score=80.0,
                price=now,
            ))
        return decisions
```

### 4.1 Decision action 类型含义

| action | 含义 |
|--------|------|
| `buy` | 买入信号（风控通过后才真正推送） |
| `sell` | 卖出信号 |
| `hold` | 持仓建议 |
| `watch` | 关注（不入风控，仅推送） |

### 4.2 ctx 各 DataFrame 来源

| ctx 属性 | 来源表 | 时间过滤 |
|---------|--------|---------|
| `pricevol_df` | qd_pricevol | 最新一行（groupby code 取 last） |
| `snapshot_focus_df` | qd_stock_snapshot | 最新一行（focus 池 100 只） |
| `more_info_df` | qd_stock_snapshot | 最新一行（focus 池 intraday 字段） |
| `indicators_df` | qd_indicators | 最近 10 分钟（k1 每轮增量计算） |
| `signals_df` | qd_signals | 最近 10 分钟（k2 每轮增量计算） |
| `sector_flow_df` | qd_sector_flow | 最新一行（_run_sector_flow 当轮写入） |

---

## 5. 常见陷阱

### 5.1 字段名大小写

tqcenter 返回字段名是驼峰 + 大写开头：`Zjl`、`ZAF`、`fHSL`、`MA5Value`
- DataFrame 列名区分大小写
- `Zjl` 和 `zjl` 是两个不同的列

### 5.2 intraday_loop 每轮重新读代码

**不要**在模块级别缓存全场代码列表（如 `all_stocks = _get_all_stock_codes()` 放在循环外）。
每日新股/新板块加入 registry 后，缓存不更新，导致采集遗漏。

正确做法：每次轮询 `_get_all_stock_codes(con)` 从数据库实时读。

### 5.3 scheduler 子进程复用

`scheduler` 启动时会检测 `intraday_loop` 是否已运行：
- 旧版本 `_attach_if_running` 复用旧进程（继承旧代码 bug）
- **当前版本**：杀掉旧子进程，强制拉起新进程（改了返回值逻辑）
- 如果手动停 scheduler 后重启，确保旧 `intraday_loop` 进程已退出

### 5.4 c4 kline 每轮只拉 count=1

盘中 k4 每轮 `count=1`，只补拉最新 1 根 K 线。
历史 K 线由 `daily_init` 或 `_fix_kline` 一次性补拉 48 根（覆盖昨天盘中）。

### 5.5 策略 required_fields 与 DDL 列名不一致

如果 `required_fields()` 返回的列不在 ctx DataFrame 中，策略返回空列表（静默跳过）。
常见原因：DDL 新增了列但字段名拼写错误，或大小写不一致。

---

## 6. 新增字段完整步骤

以新增 `VPin`（资金流向强度）为例：

**Step 1**：`config/fields.py` 的 `STOCK_INTRADAY_FIELDS` 添加
```python
STOCK_INTRADAY_FIELDS = ['Now', 'LastClose', ..., 'VPin']
```

**Step 2**：`ddl/02_snapshot.sql` 的 `qd_stock_snapshot` 添加
```sql
VPin  DOUBLE,
```

**Step 3**：`collect/c3_more_info.py` 的 `_write_intraday` rows 添加值
```python
rows.append((code, ..., vpin_value))
```

**Step 4**（如策略需要）：策略 `required_fields()` 添加 `'VPin'`

---

## 7. 环境变量说明

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `TQCENTER_PATH` | `K:\txdlianghua\PYPlugins\sys` | tqcenter COM 组件路径 |
| `LARK_WEBHOOK_URL` | （飞书群机器人 URL） | 飞书推送 |
| `QDB_HOST` | `127.0.0.1` | QuestDB 地址 |
| `QDB_PORT` | `8812` | QuestDB PG 协议端口 |
| `QDB_USER` | `admin` | QuestDB 用户 |
| `QDB_PASSWORD` | `quest` | QuestDB 密码 |
| `QDB_DBNAME` | `qdb` | QuestDB 数据库名 |

---

## 8. 已知限制

- **tqcenter COM 单进程**：不能在多线程中并发调用；多进程安全
- **QuestDB 不支持 DELETE**：历史数据通过 `_reset_all.py` 重建表清空
- **法定节假日**：市场时钟不查假日历，需要手动停 scheduler
- **龙虎榜**：盘中 `lhb_data` 为 None（数据 T+1 才出），p13/p14 策略盘后才能运行
- **5 档买卖盘**：tqcenter 返回 5 档数组，存储为字符串（JSON 格式），读取需 `json.loads()`

---

## 9. 快速验证改动

改动任何模块后，按以下顺序验证：

```powershell
# 1. 语法检查
python -m py_compile collect/c4_kline.py

# 2. 独立运行该模块
python collect/c4_kline.py --period 5m --count 5

# 3. 端到端验收（全链路）
python _e2e.py

# 4. 观察 scheduler 日志
# logs/runner_intraday_loop_YYYYMMDD.log
```

---

> 本文档随代码演进持续更新。改动架构或新增模块时，同步修订本文件对应章节。
