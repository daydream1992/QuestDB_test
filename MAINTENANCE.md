# QuestDB_test 维护手册

> 项目根: `K:\QuestDB_test`
> 数据库: QuestDB 9.4.3 (PG 协议端口 8812, Web Console 9000)
> 数据源: tqcenter 通达信量化接口 (`K:\txdlianghua\PYPlugins\sys\tqcenter.py`)

---

## 1. 系统概述

QuestDB_test 是一套基于 QuestDB 时序数据库 + tqcenter 通达信量化接口的 A 股量价分析系统,
覆盖 **采集 → 计算 → 策略 → 推送** 全链路。

- **采集层** (collect/): 调用 tqcenter 拉取价量 / 快照 / 88 字段 / K 线 / 龙虎榜, 写入 QuestDB
- **计算层** (compute/): 基于价量与 K 线计算技术指标 (MACD/BOLL/MA) 与原子信号 (金叉/死叉/突破)
- **策略层** (strategy/): 16 个可热插拔策略插件, 评估后产出 buy/sell/hold 决策
- **调度层** (runner/): 全天自动调度, 按交易时段切换竞价监控 / 盘中主循环 / 盘后更新
- **基础设施** (lib/): QuestDB 连接封装 / tqcenter 客户端 / 交易时钟 / 关系图谱 / 飞书推送

数据流向:

```
tqcenter ──→ collect(c1~c6) ──→ QuestDB(29张表)
                                   │
                                   ├──→ compute(k1~k2) ──→ QuestDB
                                   │
                                   └──→ strategy(16插件) ──→ qd_decisions ──→ 飞书推送
```

---

## 2. 目录结构说明

```
K:\QuestDB_test\
├── ddl/                    # QuestDB 建表 SQL (13 个) + 一键重置脚本
│   ├── 00_registry.sql     # qd_code_registry 标的注册表
│   ├── 01_daily.sql        # qd_*_daily 日级表 (3 张)
│   ├── 02_snapshot.sql     # qd_*_snapshot 快照表 (3 张)
│   ├── 03_pricevol.sql     # qd_pricevol 价量表
│   ├── 04_kline.sql        # qd_kline_1m / qd_kline_5m
│   ├── 05_indicators.sql   # qd_indicators 技术指标
│   ├── 06_signals.sql      # 信号/决策/持仓/评估 (5 张)
│   ├── 07_relation.sql     # 关系图谱 (6 张)
│   ├── 08_flow.sql         # 资金流 (2 张)
│   ├── 09_resonance.sql    # qd_resonance 共振
│   ├── 10_auction.sql      # qd_auction_snapshot 竞价
│   ├── 11_big_order.sql    # qd_big_order L2 大单
│   ├── 12_lhb.sql          # qd_lhb_* 龙虎榜 (2 张)
│   └── _reset_all.py       # 一键重建全部表
├── config/                 # 配置
│   ├── .env                # QuestDB 连接 + tqcenter 路径 + 飞书 webhook
│   ├── strategies.yaml     # 策略开关 + 风控参数 + 拉取频率
│   ├── fields.py           # 3 类标的 × 2 频率 字段定义
│   ├── index_codes.py      # 指数代码固定列表 (~40 只)
│   └── broker_list.py      # 知名游资/机构营业部
├── lib/                    # 基础设施
│   ├── qdb.py              # QuestDB 连接封装 (connect/query_df/executemany_batch)
│   ├── tq_client.py        # tqcenter 客户端 (init/close/safe_call, 线程安全+重试)
│   ├── tq_utils.py         # 代码转换/类型判定/全市场代码/注册表刷新
│   ├── market_clock.py     # 交易时钟 (get_phase 返回 7 个阶段)
│   ├── relation_graph.py   # 板块-个股关系图谱 (JSON 加载 + 同步入库)
│   └── lark.py             # 飞书推送 (push_text/push_decision)
├── collect/                # 采集层
│   ├── c1_pricevol.py      # 全场价量 (10s/轮)
│   ├── c2_snapshot.py      # 快照 (重点 10s + 全场轮换 60s)
│   ├── c3_more_info.py     # 88 字段 (daily/intraday 两模式)
│   ├── c4_kline.py         # K 线直拉 (1m/5m)
│   ├── c5_mapping.py       # 关系图谱加载 (盘前 1 次/天)
│   └── c6_lhb.py           # 龙虎榜 (盘后)
├── compute/                # 计算层
│   ├── k1_indicators.py    # 技术指标 (读 qd_kline_5m → qd_indicators)
│   └── k2_signals.py       # 原子信号 (读 qd_indicators → qd_signals)
├── strategy/               # 策略层
│   ├── base.py             # StrategyBase 抽象基类 + Decision 数据结构
│   ├── context.py          # StrategyContext 一次采集全策略共享
│   ├── registry.py         # StrategyRegistry 热插拔注册器
│   ├── risk.py             # RiskManager 仓位上限 + 止损止盈
│   ├── selector.py         # select_focus_pool 重点池选择
│   ├── resonance.py         # scan_market 共振分析
│   ├── sector_flow.py      # 板块资金流
│   ├── dark_money.py       # 暗资金分析
│   ├── big_order.py        # 大单分析
│   ├── lhb_analyzer.py     # 龙虎榜分析
│   └── plugins/            # 17 个策略插件 (p01, p02, p04..p18, 跳号 p03)
│       ├── p01_zt_daban.py     # 涨停打板
│       ├── p02_zha_fanbao.py   # 炸板反包
│       │   # ~~p03_macd_vol.py~~ 已废弃 (MACD金叉放量, 2026-07-05 删除)
│       ├── p04_break_pressure.py # 突破压力位
│       ├── p05_sector_rotation.py # 板块轮动
│       ├── p06_resonance.py    # 多层共振
│       ├── p07_divergence.py   # 背离预警
│       ├── p08_dark_money.py   # 暗资金异动
│       ├── p09_auction_rush.py # 竞价抢筹
│       ├── p10_auction_gap.py  # 竞价缺口异动
│       ├── p11_auction_close.py # 尾盘竞价异动
│       ├── p12_big_order.py    # 大单跟单
│       ├── p13_lhb_inst.py     # 机构龙虎榜
│       ├── p14_lhb_hotmoney.py # 游资龙虎榜
│       ├── p15_stop_loss.py    # 止损退出
│       └── p16_stop_profit.py  # 止盈退出
├── runner/                 # 调度层
│   ├── scheduler.py        # 总调度器 (全天自动调度)
│   ├── daily_init.py       # 盘前初始化 (09:25)
│   ├── intraday_loop.py    # 盘中主循环 (09:30-15:00, 10s/轮)
│   ├── auction_monitor.py  # 竞价监控 (09:15-09:30 / 14:57-15:00)
│   └── daily_close.py      # 盘后更新 (15:05)
├── logs/                   # 日志目录 (按模块+日期分文件, 保留 30 天)
├── e2e.py                   # 端到端验证脚本
├── requirements.txt        # Python 依赖
└── MAINTENANCE.md          # 本文件
```

---

## 3. 数据库表清单 (35 张表)

> 2026-07-05 整理: 原 §3 写 29 张表, 实际 DDL 已扩到 35 张
> (C8 拆表 + qd_sentiment_* 4 张 + qd_intraday_event + qd_stock_intraday + qd_stock_gpjy)
> 此处按 DDL 文件 00~18 顺序罗列。

所有表名以 `qd_` 前缀, 使用 QuestDB 时序表 + DEDUP UPSERT KEYS 幂等去重。

### 3.1 注册表 (1 张)

| # | 表名 | DDL | 时间戳 | 用途 |
|---|------|-----|--------|------|
| 1 | qd_code_registry | 00 | last_seen | 标的注册表 (股票/板块/指数元数据) |

### 3.2 日级表 (3 张)

| # | 表名 | DDL | 时间戳 | 用途 |
|---|------|-----|--------|------|
| 2 | qd_stock_daily | 01 | date | 个股日级 (50 字段, 含 PE/PB/市值/涨停数) |
| 3 | qd_sector_daily | 01 | date | 板块日级 (15 字段) |
| 4 | qd_index_daily | 01 | date | 指数日级 (10 字段) |

### 3.3 快照表 (3 张)

| # | 表名 | DDL | 时间戳 | 用途 |
|---|------|-----|--------|------|
| 5 | qd_stock_snapshot | 02 | snapshot_time | 个股盘中快照 (43 列含 5 档买卖盘) |
| 6 | qd_sector_snapshot | 02 | snapshot_time | 板块盘中快照 (23 列) |
| 7 | qd_index_snapshot | 02 | snapshot_time | 指数盘中快照 (13 列) |

### 3.4 价量/K线 (3 张)

| # | 表名 | DDL | 时间戳 | 用途 |
|---|------|-----|--------|------|
| 8 | qd_pricevol | 03 | snapshot_time | 全场价量 (LastClose/Now/Volume) |
| 9 | qd_kline_1m | 04 | kline_time | 1 分钟 K 线 (OHLCV) |
| 10 | qd_kline_5m | 04 | kline_time | 5 分钟 K 线 (OHLCV, k1 指标计算源) |

### 3.5 指标与信号 (5 张)

| # | 表名 | DDL | 时间戳 | 用途 |
|---|------|-----|--------|------|
| 11 | qd_indicators | 05 | calc_time | 技术指标 (MACD/BOLL/压力支撑/MA) |
| 12 | qd_signals | 06 | signal_time | 原子信号 (金叉/死叉/突破/跌破) |
| 13 | qd_signal_log | 06 | log_time | 信号频控日志 |
| 14 | qd_decisions | 06 | decision_time | 策略决策 (buy/sell/hold) |
| 15 | qd_positions | 06 | update_time | 持仓 |

### 3.6 策略评估 (1 张)

| # | 表名 | DDL | 时间戳 | 用途 |
|---|------|-----|--------|------|
| 16 | qd_strategy_eval | 06 | eval_time | 策略评估 (信号数/胜率/PNL) |

### 3.7 关系图谱 (6 张)

| # | 表名 | DDL | 用途 |
|---|------|-----|------|
| 17 | qd_sector_meta | 07 | 板块元数据 |
| 18 | qd_stock_industry | 07 | 个股申万三级分类 |
| 19 | qd_map_concept_stock | 07 | 概念-个股映射 |
| 20 | qd_map_region_stock | 07 | 地域-个股映射 |
| 21 | qd_map_style_stock | 07 | 风格-个股映射 |
| 22 | qd_map_index_stock | 07 | 指数-成份股映射 |

### 3.8 资金流与共振 (3 张)

| # | 表名 | DDL | 时间戳 | 用途 |
|---|------|-----|--------|------|
| 23 | qd_sector_flow | 08 | flow_time | 板块资金流 (主力净流入) |
| 24 | qd_money_flow | 08 | flow_time | 个股明暗资金 |
| 25 | qd_resonance | 09 | resonance_time | 共振分析 (板块/指数/MACD/量能共振) |

### 3.9 竞价/大单/龙虎榜 (4 张)

| # | 表名 | DDL | 时间戳 | 用途 |
|---|------|-----|--------|------|
| 26 | qd_auction_snapshot | 10 | auction_time | 竞价快照 (开盘/收盘竞价) |
| 27 | qd_big_order | 11 | order_time | L2 大单事件 |
| 28 | qd_lhb_detail | 12 | lhb_date | 龙虎榜明细 |
| 29 | qd_lhb_broker | 12 | lhb_date | 龙虎榜营业部 (游资/机构/北向识别) |

### 3.10 情绪/异动/拆表/GP (7 张 — 2026-07-05 整理补齐, 2026-07-06 增 18_sentiment_deep)

| # | 表名 | DDL | 时间戳 | 用途 |
|---|------|-----|--------|------|
| 30 | qd_sentiment_snapshot_min | 13 | snapshot_time | 大盘情绪快照 (5 档评级/6 池分类) |
| 31 | qd_sentiment_event_log | 13 | event_time | 情绪变盘事件 (涨跌比翻转/跨越) |
| 32 | qd_sentiment_daily | 13 | trade_date | 情绪日级 (盘后汇总) |
| 33 | qd_intraday_event | 14 | event_time | 盘中异动 (涨速/封板/炸板/资金脉冲) |
| 34 | qd_stock_intraday | 16 | snapshot_time | **C8 拆表** 个股盘中高频字段 (FCAmo/Zjl/Wtb/fHSL 等) |
| 35 | qd_stock_gpjy | 17 | trade_date | GP 系列历史 (连板率/次日红盘率/机构买入) |
| 36 | qd_sentiment_deep | 18 | snapshot_time | 深度情绪分析 (恐慌/贪婪指数/资金情绪/背离综合) |
| 37 | qd_sector_heatmap | 19 | snapshot_time | 板块热力图 + 最强个股梯队 (4组Top5+个股Top3) |
| 38 | qd_ladder_tracker | 20 | snapshot_time | 打板梯队 + 2进3 晋级监控 |

### 3.11 DDL 对账 (2026-07-05 核对, 2026-07-06 更新至 38 张)

**核对结论**: SQL 实际建表 = 文档表数 = **38 张, 完全对齐零差异**。

| 维度 | 数值 |
|------|------|
| DDL 文件数 | 20 个 `.sql` (00~20, 跳 15) |
| SQL `CREATE TABLE` 总数 | 38 |
| 本节 §3.1~§3.10 表数 | 38 |
| 只在 SQL 里、文档缺 | **0** |
| 只在文档里、SQL 没有 | **0** |

**对账方法**: 运行 `python scripts/data_inventory_ddl_audit.py`。重新核对命令:

```bash
python scripts/data_inventory_ddl_audit.py            # 人类可读
python scripts/data_inventory_ddl_audit.py --json     # CI
python scripts/data_inventory_ddl_audit.py --strict   # 任何漂移都失败
```

**对账规则** ([CLAUDE.md §十 文档维护](CLAUDE.md)):

- 改表结构 → 同步本节 §3.x 对应表格 + §3.11 重对账
- 新增 DDL 文件 → 同步更新 [ddl/_reset_all.py](ddl/_reset_all.py) 的 `DDL_FILES` 数组
- 表名映射关系: `qd_<scope>_<type>` (scope=stock/sector/index/map_xxx/sentiment 等)

---


## 4. 脚本清单 (按层分类)

### 4.1 调度层 (runner/)

| 脚本 | 入口 | 执行时机 | 说明 |
|------|------|----------|------|
| scheduler.py | `python runner/scheduler.py` | 全天 | 总调度器, 按时段切换各阶段 |
| daily_init.py | `python runner/daily_init.py` | 09:25 | 盘前初始化 (映射+注册表+日级数据) |
| auction_monitor.py | `python runner/auction_monitor.py` | 09:15-09:30 | 竞价监控 (3-5s/轮) |
| intraday_loop.py | `python runner/intraday_loop.py` | 09:30-15:00 | 盘中主循环 (10s/轮) |
| daily_close.py | `python runner/daily_close.py` | 15:05 | 盘后更新 (日级+龙虎榜+评估) |

### 4.2 采集层 (collect/)

| 脚本 | 入口 | 频率 | 入库表 |
|------|------|------|--------|
| c1_pricevol.py | `python collect/c1_pricevol.py` | 10s | qd_pricevol |
| c2_snapshot.py | `python collect/c2_snapshot.py` | 10s/60s | qd_*_snapshot |
| c3_more_info.py | `python collect/c3_more_info.py` | 10s/60s | qd_*_daily / qd_*_snapshot |
| c4_kline.py | `python collect/c4_kline.py` | 60s | qd_kline_1m / qd_kline_5m |
| c5_mapping.py | `python collect/c5_mapping.py` | 1次/天 | qd_map_* (6 张) |
| c6_lhb.py | `python collect/c6_lhb.py` | 盘后 | qd_lhb_* |

### 4.3 计算层 (compute/)

| 脚本 | 入口 | 频率 | 源表 → 目标表 |
|------|------|------|---------------|
| k1_indicators.py | `python compute/k1_indicators.py` | 10s | qd_kline_5m → qd_indicators |
| k2_signals.py | `python compute/k2_signals.py` | 10s | qd_indicators → qd_signals |

### 4.4 基础设施 (lib/)

| 模块 | 主要函数 |
|------|----------|
| qdb.py | connect / query_df / query_one / executemany_batch |
| tq_client.py | init / close / safe_call / retry |
| tq_utils.py | to_tdx / route_type / fetch_all_codes / refresh_registry |
| market_clock.py | get_phase / is_trading_day / is_trading_time / is_auction_time |
| relation_graph.py | load_from_json / sync_to_db / get_stock_sectors |
| lark.py | push_text / push_decision |

---

## 5. 启动方式

### 5.1 全天自动调度 (推荐)

```powershell
cd K:\QuestDB_test
python runner/scheduler.py
```

scheduler 按交易时段自动切换:
- **09:15-09:30** 启动 `auction_monitor` 子进程 (竞价监控)
- **09:25-09:30** 调用 `daily_init.run()` (盘前初始化, 与竞价并行)
- **09:30** 终止竞价子进程, 启动 `intraday_loop` 子进程 (盘中主循环)
- **15:00** 盘中子进程自行退出, 调用 `daily_close.run()` (盘后更新)
- **非交易日** 跳过, 仅补跑 daily_close 后等待次日

> Ctrl+C 优雅退出, 自动终止所有子进程。

### 5.2 手动执行各阶段

各 runner 均可独立运行 (自带 init/close tqcenter):

```powershell
cd K:\QuestDB_test
python runner/daily_init.py       # 盘前初始化
python runner/auction_monitor.py  # 竞价监控
python runner/intraday_loop.py    # 盘中主循环
python runner/daily_close.py      # 盘后更新
```

各采集/计算模块也可独立运行:

```powershell
python collect/c1_pricevol.py --limit 10    # 测试模式取前 10 只
python collect/c2_snapshot.py --limit 10
python collect/c3_more_info.py --mode daily --limit 10
python collect/c4_kline.py --period 1m --count 48
python compute/k1_indicators.py
python compute/k2_signals.py
```

---

## 6. 配置说明

### 6.1 config/.env

QuestDB 连接 + tqcenter 路径 + 飞书 webhook:

```
LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
QDB_HOST=127.0.0.1
QDB_PORT=8812
QDB_USER=admin
QDB_PASSWORD=quest
QDB_DBNAME=qdb
TQCENTER_PATH=K:\txdlianghua\PYPlugins\sys
```

> 修改后无需重启 QuestDB, Python 进程重启即生效。

### 6.2 config/strategies.yaml

三段配置:

- **strategies**: 每个策略的 `enabled` 开关 + 中文名, 运行时可动态启停
- **risk**: 风控参数
  - `max_total_position`: 最大总仓位 % (默认 80)
  - `max_single_position`: 单只最大仓位 % (默认 30)
  - `max_strategies_per_day`: 每日最多策略触发次数 (5)
  - `push_cooldown_sec`: 飞书推送频控秒数 (300)
- **schedule**: 各环节拉取频率 (秒)
  - `pricevol_interval: 10` / `kline_interval: 60` 等

### 6.3 config/fields.py

3 类标的 × 2 频率的字段写死定义:
- `PRICEVOL_FIELDS`: 价量 3 字段
- `STOCK_SNAPSHOT_FIELDS`: 个股快照 25 项 (含 5 档数组)
- `SECTOR_SNAPSHOT_FIELDS`: 板块快照 21 字段
- `STOCK_DAILY_FIELDS`: 个股日级 50 字段
- `SECTOR_DAILY_FIELDS` / `INDEX_DAILY_FIELDS`: 板块/指数日级
- `STOCK_INTRADAY_FIELDS`: 盘中高频 16 字段
- `DOUBLE_FIELDS` / `BIGINT_FIELDS` / `INT_FIELDS` / `VARCHAR_FIELDS`: 字段类型映射 (DDL 用)

### 6.4 config/index_codes.py

~40 只指数代码 → 中文名称映射。`INDEX_CODE_SET` 用于 `route_type()` 判定标的为指数。

### 6.5 config/broker_list.py

知名游资/机构营业部识别表:
- `FAMOUS_BROKERS`: 营业部名称 → 身份标识 (hot_money_xz / institution / north_sh)
- `BROKER_LABELS`: 身份标识 → 中文标签
- `BROKER_TYPE`: 大类 (游资/机构/北向)

龙虎榜分析时用于识别资金来源。发现新游资及时补充此文件。

---

## 7. 日常维护

### 7.1 每日检查项

1. **QuestDB 运行中**: 浏览器访问 `http://localhost:9000` 能打开 Web Console
2. **tqcenter 可用**: 确认通达信行情软件已登录且 `K:\txdlianghua\PYPlugins\sys\tqcenter.py` 存在
3. **scheduler 日志**: 查看 `logs/runner_scheduler_YYYYMMDD.log` 是否正常推进各阶段
4. **数据新鲜度**: Web Console 执行
   ```sql
   SELECT * FROM qd_pricevol LATEST ON snapshot_time PARTITION BY code;
   SELECT COUNT(*) FROM qd_decisions WHERE decision_time > dateadd('d', -1, now());
   ```
5. **飞书推送**: 检查飞书群是否收到 daily_init / daily_close / 决策通知

### 7.2 日志位置

```
logs/
├── runner_scheduler_YYYYMMDD.log       # 总调度器
├── runner_daily_init_YYYYMMDD.log      # 盘前初始化
├── runner_intraday_loop_YYYYMMDD.log   # 盘中主循环
├── runner_auction_monitor_YYYYMMDD.log # 竞价监控
├── runner_daily_close_YYYYMMDD.log     # 盘后更新
├── c1_pricevol_YYYYMMDD.log            # 各采集模块
├── c2_snapshot_YYYYMMDD.log
├── c3_more_info_YYYYMMDD.log
├── c4_kline_YYYYMMDD.log
├── c5_mapping_YYYYMMDD.log
├── c6_lhb_YYYYMMDD.log
├── k1_indicators_YYYYMMDD.log          # 计算模块
├── k2_signals_YYYYMMDD.log
├── reset_all_YYYYMMDD.log              # 建表
└── e2e_YYYYMMDD.log                    # 端到端验证
```

日志按天滚动, 保留 30 天 (loguru `rotation='1 day', retention='30 days'`)。

### 7.3 常见问题排查

| 现象 | 可能原因 | 排查方法 |
|------|----------|----------|
| `from tqcenter import tq` 失败 | TQCENTER_PATH 路径错误或通达信未启动 | 检查 config/.env 的 TQCENTER_PATH, 确认通达信行情已登录 |
| `psycopg2.OperationalError` 连接拒绝 | QuestDB 未启动或端口不对 | 访问 localhost:9000 确认 Web Console 可用, 检查 QDB_PORT=8812 |
| c1 写入 0 行 | tqcenter 返回空 / 无 tdx_code | 查 c1 日志, 确认 fetch_all_codes 返回非空 |
| k1 指标 0 行 | qd_kline_5m 无数据 | 确认 c4 以 period='5m' 拉过 K 线 (k1 只读 5m 表) |
| 策略无决策 | ctx 数据为空 / 策略全 disabled | 查 intraday_loop 日志的 "ctx 构建完成" 行, 查 strategies.yaml 开关 |
| 飞书未推送 | LARK_WEBHOOK_URL 失效 | 用 curl 测试 webhook, 查 lark.py 日志 |
| scheduler 不切换阶段 | 子进程未退出 / phase 判断异常 | 查 runner_scheduler 日志, 确认系统时间在 Asia/Shanghai |
| QuestDB 报 `UPSERT is not supported` | 表未建 DEDUP UPSERT KEYS | 用 _reset_all.py 重建表 |

---

## 8. 策略增删

### 8.1 新增策略

1. 在 `strategy/plugins/` 新建 `pXX_xxx.py` (文件名以 `p` 开头, XX 为序号)
2. 实现 `StrategyBase` 子类, 用 `@StrategyRegistry.register` 装饰:

```python
"""pXX_策略中文名"""
from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

@StrategyRegistry.register
class MyStrategy(StrategyBase):
    name = 'my_strategy'        # 与 strategies.yaml 的 key 一致
    version = '1.0'
    enabled = True

    def evaluate(self, context):
        # context.pricevol_df / indicators_df / signals_df / graph ...
        decisions = []
        # ... 逻辑 ...
        return decisions
```

3. 在 `config/strategies.yaml` 的 `strategies` 段添加:
```yaml
  my_strategy:
    enabled: true
    name: 我的策略
```

4. 重启 scheduler (或 intraday_loop) 即生效。`load_plugins` 会自动扫描 `p*.py`。

### 8.2 禁用策略

改 `config/strategies.yaml`, 把对应策略的 `enabled: false`:

```yaml
  zt_daban:
    enabled: false
    name: 涨停打板
```

无需改代码, 下次 `load_config` 即生效。运行时也可调 `StrategyRegistry.disable('zt_daban')`。

### 8.3 现有策略清单 (17 个 — p01, p02, p04..p18, 跳号 p03)

> 2026-07-05 整理: 原文档写 "16 个" 列了 17 行 (含 p03_macd_vol)
> 实际 `strategy/plugins/` 目录只有 17 个 (p03 已从 working tree 删除, 待归档决策)
> 替换关系未确认 — 不臆测 p03 的替代策略, 此处仅作废登记

| 插件 | name | 类别 | 说明 |
|------|------|------|------|
| p01_zt_daban | zt_daban | 入场 | 涨停打板 |
| p02_zha_fanbao | zha_fanbao | 入场 | 炸板反包 |
| ~~p03_macd_vol~~ | ~~macd_golden_vol~~ | — | ~~已废弃 (MACD金叉放量)~~ |
| p04_break_pressure | break_pressure | 入场 | 突破压力位 |
| p05_sector_rotation | sector_rotation | 入场 | 板块轮动 |
| p06_resonance | resonance_triple | 入场 | 多层共振 |
| p07_divergence | divergence_warn | 入场 | 背离预警 |
| p08_dark_money | dark_money_anomaly | 入场 | 暗资金异动 |
| p09_auction_rush | auction_rush | 竞价 | 竞价抢筹 |
| p10_auction_gap | auction_gap | 竞价 | 竞价缺口异动 |
| p11_auction_close | auction_close | 竞价 | 尾盘竞价异动 |
| p12_big_order | big_order_pulse | L2 | 大单跟单 |
| p13_lhb_inst | lhb_institution | 龙虎榜 | 机构龙虎榜 |
| p14_lhb_hotmoney | lhb_hotmoney | 龙虎榜 | 游资龙虎榜 |
| p15_stop_loss | stop_loss | 出场 | 止损退出 |
| p16_stop_profit | stop_profit | 出场 | 止盈退出 |

---

## 9. 新股/新板块自动发现

`daily_init` 每日盘前调用 `lib.tq_utils.refresh_registry(con)`:

1. 调 `fetch_all_codes()` 拉取全市场代码 (股票 + 5 类板块 + 指数)
2. 全量 upsert 到 `qd_code_registry` (DEDUP UPSERT KEYS(last_seen, code) 幂等)
3. 新股/新板块自动加入注册表, `first_seen` 记录首次发现时间
4. 后续采集 (c1/c2/c3) 自动覆盖新标的, 无需手动配置

> `is_trading_day` 仅按周一~周五判断, **不查假日历**。法定节假日需手动停 scheduler, 或在 market_clock 中扩展假日表。

---

## 10. 数据库维护

### 10.1 重置表 (一键重建)

```powershell
cd K:\QuestDB_test
python ddl/_reset_all.py
```

按 `00_registry → 12_lhb` 顺序执行所有 DDL。**执行前会先 DROP 所有 `qd_` 开头的旧表**,
强制重建以避免旧表结构与新 DDL 不一致导致 `IF NOT EXISTS` 跳过。
重建后表结构最新, 但**历史数据不保留** (QuestDB 9.4.3 不支持 DELETE, 重建即清空)。

日志: `logs/reset_all_YYYYMMDD.log`

### 10.2 QuestDB Web Console

浏览器访问 `http://localhost:9000`:
- 执行 SQL 查询
- 查看表结构 / 分区 / 列
- 导出查询结果

常用查询:
```sql
-- 查看所有 qd_ 表
SELECT table_name FROM tables() WHERE table_name LIKE 'qd_%' ORDER BY table_name;

-- 某表最新快照
SELECT * FROM qd_pricevol LATEST ON snapshot_time PARTITION BY code LIMIT 5;

-- 今日决策
SELECT * FROM qd_decisions WHERE decision_time > dateadd('d', -1, now());

-- 各策略今日信号数
SELECT strategy_name, COUNT(*) FROM qd_decisions
WHERE decision_time > dateadd('d', -1, now())
GROUP BY strategy_name;
```

### 10.3 表分区

时序表按时间戳自动分区 (DDL 中 `timestamp(...) PARTITION BY DAY/WEEK/MONTH`)。
历史分区如需归档/删除, 在 Web Console 用 `DROP PARTITION` 语法。

---

## 11. 接口能力 (tqcenter 3 个主要采集 API)

| API | 用途 | 调用方式 | 频率 | 返回字段数 | 覆盖范围 | 入库表 |
|-----|------|----------|------|-----------|----------|--------|
| `get_pricevol` | 全场批量价量 | 1 次拿全场 (传 stock_list) | 10s/轮 | 3 (LastClose/Now/Volume) | 股票 + 指数 | qd_pricevol |
| `get_market_snapshot` | 单只实时快照 | 逐只调用 (传 stock_code) | 重点 10s + 全场 60s | 个股 43 / 板块 23 / 指数 13 | 全标的 (按 route_type 分流) | qd_*_snapshot |
| `get_more_info` | 单只 88 字段详情 | 逐只调用 (传 stock_code) | 重点 10s + 全场 60s | 日级 50/15/10, 盘中 16 | 全标的 (按 route_type 分流) | qd_*_daily / qd_*_snapshot |

### 辅助 API

| API | 用途 | 说明 |
|-----|------|------|
| `get_market_data` | K 线直拉 | 传 stock_list + period(1m/5m) + count, 返回 dict{字段: DataFrame} |
| `get_stock_list` | 股票列表 | 传 market + list_type, 返回标准代码列表 |
| `get_sector_list` | 板块列表 | 传 list_type(0~4 对应 行业/概念/地域/风格/指数) |

### 调用约定

- **统一传标准代码** (`000001.SZ`), tqcenter 全系 API 不接受 tdx 格式 (`0#000001`)
- **COM 单进程串行**: tqcenter 是 C++ COM 组件, 不支持多线程并发; `lib.tq_client.safe_call` 用锁 + 自动重试 3 次包装
- **多进程安全**: 各进程独立 `init/close`, 互不影响
- **返回值为字符串**: 由 `psycopg2` 写入 QuestDB 时自动转 DOUBLE/BIGINT

---

## 12. 端到端验收 (`e2e.py`)

### 12.1 用途

一次性验证全链路: **重置表 → 采集 (5 只样本) → 计算 → 策略 → 飞书推送**。
适合改动 DDL / 采集层 / 计算层 / 策略层后做回归。

### 12.2 执行

```powershell
cd K:\QuestDB_test
python e2e.py
```

### 12.3 流程 (13 步)

| 步骤 | 动作 | 入库表 |
|------|------|--------|
| 1 | 重置表 (`ddl/_reset_all.py`, 先 DROP 再 CREATE) | 29 张 |
| 2 | 加载关系图谱 (c5_mapping) | qd_sector_meta / qd_*_map (6 张) |
| 3 | 拉价量 (c1_pricevol, limit=5) | qd_pricevol |
| 4 | 拉快照 (c2_snapshot, focus=5 只) | qd_stock_snapshot |
| 5 | 拉日级 (c3_more_info, mode=daily) | qd_stock_daily |
| 6 | 拉 K 线 1m + 5m (c4_kline, count=48) | qd_kline_1m / qd_kline_5m |
| 7 | 算指标 (k1_indicators, MACD/BOLL/压力支撑/MA) | qd_indicators |
| 8 | 检测信号 (k2_signals, 金叉/死叉/突破/跌破) | qd_signals |
| 9 | 加载策略 (load_plugins + load_config, 16 个) | - |
| 10 | 构建 StrategyContext (读 5 张表) | - |
| 11 | 遍历策略 → decisions | qd_decisions |
| 12 | 打印结果汇总 | - |
| 13 | 飞书推送验证 (信号 + 决策 + 验收通知) | qd_signal_log |

### 12.4 日志

`logs/e2e_YYYYMMDD.log`

---

## 13. 快速上手 (在哪里打开使用)

### 13.1 打开终端

1. **PowerShell**: Win+R 输入 `powershell` 打开
2. **VSCode 终端**: 在 Trae/VSCode 中按 `` Ctrl+` `` 打开集成终端
3. **Windows Terminal**: 推荐使用, 支持多标签

> 避免 PSReadLine 渲染异常: 若终端报 `SetCursorPosition` 错误, 改用 Windows Terminal 或 cmd 即可, 不影响命令执行。

### 13.2 首次使用流程

```powershell
# 1. 安装依赖
cd K:\QuestDB_test
pip install -r requirements.txt

# 2. 配置 config/.env (QuestDB 连接 + 飞书 webhook + tqcenter 路径)
#    默认配置已指向 K:\txdlianghua\PYPlugins\sys

# 3. 启动 QuestDB (如未启动)
#    访问 http://localhost:9000 确认 Web Console 可用

# 4. 启动通达信行情软件并登录 (tqcenter 依赖)

# 5. 一键建表
python ddl/_reset_all.py

# 6. 端到端验收 (5 只样本, 全链路 + 飞书推送)
python e2e.py

# 7. 全天自动调度 (交易日 09:15 前启动)
python runner/scheduler.py
```

### 13.3 日常使用入口

| 场景 | 命令 | 时机 |
|------|------|------|
| 全天自动运行 | `python runner/scheduler.py` | 交易日 09:15 前 |
| 端到端验收 | `python e2e.py` | 改动后回归 |
| 重置表 | `python ddl/_reset_all.py` | DDL 变更后 |
| 手动跑某模块 | `python collect/c1_pricevol.py --limit 10` | 调试 |
| 查 Web Console | 浏览器 `http://localhost:9000` | 查数据/跑 SQL |

### 13.4 注意事项

1. **通达信必须先登录**: tqcenter 依赖通达信行情软件, 未登录则 `from tqcenter import tq` 失败
2. **QuestDB 必须先启动**: PG 协议端口 8812, Web Console 9000
3. **时区必须为 Asia/Shanghai**: scheduler 按交易时段切换, 系统时区错误会导致阶段错乱
4. **_reset_all 会清空数据**: 仅在 DDL 变更或首次部署时使用, 日常不要跑
5. **tqcenter 单进程串行**: 不要在多线程中并发调用, 多进程安全
6. **PG 协议用 `%s` 占位符**: QuestDB 9.4.3 不支持 `?`, 代码已统一用 `%s`
7. **autocommit=True**: QuestDB 跨连接 read-after-write 有延迟, lib.qdb.connect 已默认开启
8. **法定节假日**: market_clock 不查假日历, 节假日需手动停 scheduler
9. **飞书频控**: 同 code+signal_type 5 分钟内只推一次 (qd_signal_log 表)
10. **日志保留 30 天**: loguru 自动滚动, 无需手动清理

---

> 本手册随代码演进持续更新。新增模块/表/策略后请同步修订对应章节。
