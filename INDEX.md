# QuestDB_test — 项目索引

A 股盘中实时量化监控与**信号呈现**系统（不做自动交易）。
通达信 tqcenter 拉数据 → QuestDB 单库 33 张表 → 16 个策略插件 + 异动订阅器 → 飞书三通道推送。

## 怎么启动

```bash
# 盘中持续（推荐 — 唯一活跃调度器）
python runner/scheduler.py

# 端到端验证（HANDOVER §9 推荐）
python e2e.py

# 旧版端到端家族（已废弃, 通过引导访问）
python e2e_legacy.py mock       # mock 数据
python e2e_legacy.py real       # 真实数据 + 旧路径
python e2e_legacy.py live_5min  # 5 分钟连续采集
```

## 业务线入口

| 业务线 | 根目录 | 调度器 |
|---|---|---|
| 采集 | `collect/c1..c6_*.py` | 由 runner 触发 |
| 计算 | `compute/k1..k3,k5_*.py` | 60s/轮 |
| 策略 | `strategy/plugins/p01..p18.py` | 60s/轮 |
| 推送 | `4_feishu/` | 决策产出后即时 |
| 调度 | `runner/{scheduler,daily_init,auction_monitor,intraday_loop,daily_close}.py` | 全天 |

## 文档三件套

| 文件 | 用途 |
|---|---|
| `HANDOVER.md` | **开发手册**（约定、陷阱、4 步法、字段-DDL 对齐规则） |
| `MAINTENANCE.md` | **运维手册**（29→33 张表、风险参数、e2e 13 步） |
| `SESSION_HANDOVER.md` | ~~交接文档~~ → `_deprecated/session_handover_2026-07-03.md` (会话快照, 已撤) |
| `ARCHITECTURE_REVIEW.md` | **体检报告**（C1-C8 / H1-H8 修复状态） |

## 目录结构

```
collect/        ← 新版采集器 (c1..c6)
compute/        ← 新版计算层 (k1..k3,k5)
strategy/       ← 策略骨架 + 16 个插件
runner/         ← 5 个调度器
4_feishu/       ← 飞书三通道
lib/            ← 通用库 (qdb / tq_client / market_clock / lark / ...)
config/         ← 配置 (.env / strategies.yaml / fields.py / ...)
ddl/            ← 17 个 DDL 文件 + _reset_all.py
scripts/        ← 维护脚本 (data_inventory*.py / verify_tables.py)
tests/          ← 3proc / stress_rw 验证
e2e.py          ← 新版端到端
docs/           ← 说明书 + 盘点产物

1_collect/      ← 旧版采集器 (qd_00/01_*), 仍被 e2e_legacy 引用
2_kline/        ← 旧版 K 线合成
3_indicators/   ← 旧版指标
4_signals/      ← 旧版信号 + 飞书
2_compute/      ← 空壳 (DEPRECATED)
3_verify/       ← 空壳 (DEPRECATED)
config/mapping/ ← 空壳 (DEPRECATED)

_deprecated/    ← 冻结目录 — 探针 + 标记 + 旧版 e2e 家族
指数板块个股映射/  ← 板块个股 JSON 快照 (11 MB)
市场数据模块/      ← 板块个股 JSON 快照
```

## 数据流时间线

```
09:15  集合竞价   → auction_monitor (3-5s 轮询)
09:25  撮合       → daily_init (拉全场 88 字段 + 48 根 K 线历史)
09:30  盘中主循环  → intraday_loop (10s 块: 采集)
                   → 60s 块: 指标 + 16 策略 + 飞书推送
11:30  午休       → intraday_loop 内部等待
13:00  午后       → intraday_loop 继续
15:00  收盘       → daily_close (日级 + 龙虎榜 + 策略日报)
16:00  校验       → scripts/verify_tables.py
非交易日          → scheduler idle 300s/次
```

## 关键约束

- **不做自动交易**：情绪/风控只呈现建议，不替用户决策
- **人类 ≤ 2 条/分钟**：推送是稀缺资源
- **涨停判定**：`FCAmo > 0`（不是 `Now >= ZTPrice`）
- **QuestDB**：`TIMESTAMP + PARTITION BY DAY + DEDUP UPSERT KEYS + %s 占位符`
- **tqcenter COM 单进程**：所有调用走 `lib.tq_client.safe_call`（锁 + 3 次重试）

详细见 `HANDOVER.md §1-8`、`MAINTENANCE.md`。