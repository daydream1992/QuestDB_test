# QuestDB_test — 项目索引

A 股盘中实时量化监控与**信号呈现**系统（不做自动交易）。
通达信 tqcenter 拉数据 → QuestDB 单库 35 张表 → 16 个策略插件 + 异动订阅器 → 飞书三通道推送。

## 怎么启动

```bash
# 盘中持续（推荐 — 唯一活跃调度器）
python runner/scheduler.py

# 端到端验证（HANDOVER §9 推荐）
python e2e.py

# 旧版端到端家族（已废弃, 通过引导访问）
python _deprecated/e2e_legacy/root_entry_scripts/e2e_legacy.py mock
python _deprecated/e2e_legacy/root_entry_scripts/e2e_legacy.py real
python _deprecated/e2e_legacy/root_entry_scripts/e2e_legacy.py live_5min
```

## 业务线入口

| 业务线 | 根目录 | 调度器 |
|---|---|---|
| 采集 | `collect/c1..c6_*.py` | 由 runner 触发 |
| 计算 | `compute/k1..k3,k5_*.py` | 60s/轮（k4/k6/k7 预留位） |
| 策略 | `strategy/plugins/p01..p18.py` | 60s/轮（cross_section/ 预留位） |
| 推送 | `feishu/` | 决策产出后即时 |
| 调度 | `runner/{scheduler,daily_init,auction_monitor,intraday_loop,daily_close}.py` | 全天 |

## 文档

| 文件 | 用途 |
|---|---|
| `HANDOVER.md` | **开发手册**（约定、陷阱、4 步法、字段-DDL 对齐规则） |
| `MAINTENANCE.md` | **运维手册**（35 张表、风险参数、e2e 13 步） |
| `ARCHITECTURE_REVIEW.md` | **体检报告**（C1-C8 / H1-H8 修复状态） |
| `CLAUDE.md` | **架构契约**（10+ 节：目录分工/命名/import 方向/数据动态化/策略预留位） |

## 目录结构（v5 — 2026-07-05 整理后）

```
collect/        ← 采集 (c1..c6)
compute/        ← 计算 (k1..k3,k5; k4/k6/k7 预留)
strategy/       ← 策略骨架 + plugins/
feishu/         ← 推送 (Webhook/Sheet/Bitable/Doc)
runner/         ← 调度 (5 个)
lib/            ← 基础设施 (qdb / tq_client / market_clock / lark / notify_dedup / ...)
config/         ← 配置
ddl/            ← 17 个 DDL + _reset_all.py
scripts/        ← 维护 (data_inventory_*.py / verify_tables.py / market_data_*.py)
tests/          ← 公共 test_<desc>.py 命名
data/           ← 数据快照
├── market_data/  ← 板块个股映射 (新版, lib.relation_graph DEFAULT_JSON_DIR)
└── snapshots/    ← 临时快照
docs/           ← 文档 (含 通达信量化平台说明书/)
logs/           ← 运行时日志
_deprecated/    ← 冻结目录
├── old_pipeline/   ← 旧版流水线 (1_collect/2_kline/3_indicators/4_signals)
├── e2e_legacy/     ← 旧版端到端 (mock.py/real.py/live_5min.py)
├── inventory/      ← 盘点产物 (data_inventory.json)
├── ddl_minimal/    ← 探索期简化版 DDL
├── session_handover_2026-07-03.md
├── legacy_tests/   ← _verify_rw_consistency
├── legacy_data/    ← 老版指数板块个股映射快照
├── empty_dirs/     ← 3 个空壳占位
├── probes/         ← 5 个探针
└── markers/        ← H7 占位
```

## 数据流时间线

```
09:15  集合竞价   → auction_monitor (3-5s 轮询)
09:25  撮合       → daily_init (拉全场 88 字段 + 48 根 K 线历史 + 加载新版映射)
09:30  盘中主循环  → intraday_loop (10s 块: 采集)
                   → 60s 块: 指标 + 16 策略 + 飞书推送
11:30  午休       → intraday_loop 内部等待
13:00  午后       → intraday_loop 继续
15:00  收盘       → daily_close (日级 + 龙虎榜 + 策略日报 + market_data 刷新)
16:00  校验       → scripts/verify_tables.py
非交易日          → scheduler idle 300s/次
```

## 关键约束

- **不做自动交易**：情绪/风控只呈现建议，不替用户决策
- **人类 ≤ 2 条/分钟**：推送是稀缺资源
- **涨停判定**：`FCAmo > 0`（不是 `Now >= ZTPrice`）
- **QuestDB**：`TIMESTAMP + PARTITION BY DAY + DEDUP UPSERT KEYS + %s 占位符`
- **tqcenter COM 单进程**：所有调用走 `lib.tq_client.safe_call`（锁 + 3 次重试）
- **数据源可动态更新**：`data/market_data/manifest.json` + `lib.market_data_loader`
- **策略预留位**：k4/k6/k7 / strategy/cross_section/ 禁止占用

详细见 `HANDOVER.md §1-8`、`MAINTENANCE.md`、`CLAUDE.md §11-13`。