# 会话交接 — QuestDB_test 实盘能力建设

> 上一会话日期：2026-07-03
> 上一会话做了：架构体检 → P0 修复 → 大盘情绪 → 实盘订阅 → 采集层验证 → 监测 → k3 修
> 本文档是新会话的入口，读它 + [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) + [HANDOVER.md](HANDOVER.md) 即可接续。

---

## 1. 项目定调（用户已确认的 7 项决策，不要推翻）

1. **QuestDB_test = 实时实盘项目**（QuestDB，10s 主循环）；**K:\DB数据库_v2 = 盘后项目**（DuckDB）。两者**合作**，不合并。
2. **剥离**：实盘能力（实盘异动/竞价/大盘情绪）从 DB 剥离到 Q，**嵌入 intraday_loop 主循环**（不另起独立 daemon——tqcenter COM 单进程，双 daemon 会争用）。
3. **Q 自包含**：移植 pianpao_engine 到 Q，自建盘后特征，运行时**不读** DB。
4. **订阅池** = `config/strategies.yaml` 的 `watchlist` 段。
5. P0 = 完整六项（H1 护栏 + C1/C2/C5/C6/C7）。
6. 框架**温和对齐**（保留 collect/compute/strategy/runner 分层 + c1-c6/k1-k2/p01-p18 编号 + 引入 @meta 自描述头；**不**全盘重组目录）。
7. 分批次执行。

---

## 2. 已完成（6 commit，按时间序）

| commit | 内容 |
|---|---|
| `38a4cbf` | docs: 架构体检报告 ARCHITECTURE_REVIEW.md（8 CRITICAL/8 HIGH 基线）|
| `9f0e914` | fix(P0): 修复策略层取数链路 6 处断裂 + required_fields 护栏（H1+C1/C2/C5/C6/C7）|
| `29319ee` | feat(批2): 大盘情绪监控（k3_sentiment + 3 表 + p17/p18 + buy 情绪门控）|
| `cadb7ff` | feat(批3): 实盘订阅模块（intraday_engine + watchlist + Deduper + limit_rule）|
| `f5a5806` | wip(批4): 竞价规则树引擎 + pianpao 表骨架（**搁置未接入**）|
| `4c88d58` | fix(k3): _safe_float 过滤 NaN |
| `81b08cf` | feat: **k5 当天 K 线本地合成**（tqcenter `get_market_data` 只给历史昨天，今天 K 拉不到 → 用 `qd_stock_snapshot` 高频按分钟桶聚合）|
| `06fedcb` | fix(k5): cutoff 改用 Python 本地 now（修 QuestDB `now()` UTC 时区错位 8h）|
| `0c06eb1` | **fix(tz): 全局时区修复**（18 处 `dateadd(..., now())` → Python 本地 `cutoff()` 工具；_build_context/k1/k2/intraday_engine 等全部）|

---

## 3. 当前系统状态（已实测）

- **采集层完全跑通入库**：c1/c2/c3/c4 单跑 + intraday_loop 组合实跑都验证过（通达信已登录、tqcenter 可用、QuestDB 连接活）。
- **33 张 qd_ 表齐全**（原 29 + sentiment 3 + intraday_event 1）。
- **C1 实战验证**：selector 选出"涨幅=100 量比=100 重点池 199 只"（真实按涨跌幅选，非任意前 100）。
- **C5 实战**：`qd_sector_flow` 写入 579 行（_run_sector_flow 改读 snapshot_focus_df 生效）。
- **批3 实战**：`qd_intraday_event` 42 行异动（intraday_engine 跑通）。
- **k3 NaN 已修**但**未重启验证** sentiment 入库（intraday_loop 已停，待重启跑 k3）。
- 表行数实测（10 分钟）：pricevol 11141 / stock_snapshot 85628 / kline_5m 301764 / indicators 6142 / sector_flow 579 / intraday_event 42 / signals 0 / decisions 0 / sentiment 0。

---

## 4. 监测结论（上一会话跑了 ~10 分钟）

- **稳定性 ✅**：intraday_loop 没崩，采集入库正常，k3 失败被 try/except 捕获。
- **效率**：一轮实际 **60-90s**（c4 全场 7s + k1 算 18 万根 K线 14s + c2 全场慢）。这是 COM 单进程串行的本质节奏，**不是 bug**。
- **10s interval 形同虚设**（`sleep=max(0.5, interval-elapsed)` 实际全速）。不建议盲目调 interval。
- **提速点**（若要更高频）：①c2 收紧 focus/拉长全场轮换 ②k1 改增量（只算有新 K线的 code，而非每轮重算全部 6142 code）。
- **H1 护栏首轮报 34 处**：真缺失（C3/C4 数据源）+ 误报（auction_*/CJJEPre1/macd_hist 当轮 ctx df 空）。

---

## 5. 下一步可执行清单（新会话从这里开始）

### 策略层 C3/C4 接通（让 p05/p08/p12/p13/p14/p15/p16 复活）

函数都已实现，**只是没接入 loop**：

1. ✅ **dark_money → qd_money_flow**（复活 p08）
   - [dark_money.py:117](strategy/dark_money.py#L117) `calc_batch(df_snapshot, df_more_info) → DataFrame`
   - intraday_loop 60s 块调它写 qd_money_flow；**p08 required_fields 对齐**（p08 现要 dark_money/buy_pressure/sell_pressure，calc_batch 输出已对齐 dark_money/buy_pressure/sell_pressure/pressure_diff_5level/net_flow/main_net 全部 6 列可用）
   - **本会话验证**（2026-07-03 22:56，QuestDB 重启后）：手动跑 `_run_money_flow` 等价逻辑 → **5533 行写入 qd_money_flow**；p08 evaluate 跑通，required_fields 0 缺失
   - 已知 caveat：Zjl 全 NaN（c2 c3@T+1s 配对时 intraday 字段无来源）→ main_net=0、dark_money=0、p08 全过滤；等盘中真实数据落库后，p08 自然出 watch
2. ✅ **big_order → qd_big_order**（复活 p12）
   - [big_order.py:100](strategy/big_order.py#L100) `detect_batch(code, frames, mi)` 是**单只多帧**，全场 per code 循环
   - p12 required_fields(order_type/order_level) 对齐 detect 输出(level/direction) — p12 `_is_huge_buy` 已兼容两种 schema
   - **本会话已接通**（commit `4fe8b2b`）：intraday_loop 60s 块 `_run_big_order` 在 `_run_money_flow` 之前调用
   - 验证 (2026-07-03 23:03)：95156 行快照扫描，链路跑通；detect_batch DEBUG 触发（002384/300308/603986 各 1 事件），实际 0 达 100 万阈值——采样间隔 60s + 阈值的固有张力，等盘中真实活跃股数据
3. ✅ **sector_flow.detect_rotation → ctx.rotation_signal**（p05 部分）
   - [sector_flow.py:85](strategy/sector_flow.py#L85) 需 sector_flow_history（≥2 期），intraday_loop 累积
   - **本会话已接通**（commit `4d5fb52`）：模块级 `_SECTOR_FLOW_HISTORY` 维护每板块最近 5 期，每 60s 块调 `_run_rotation` → `ctx.rotation_signal`
   - 时序：`_run_sector_flow` 前移到策略遍历前（产 agg dict 喂 `_run_rotation` → p05 当轮读 ctx.rotation_signal）
   - 验证 (2026-07-03 23:23)：模拟 3 期 inflow_accelerate → inflow_accelerate 链路跑通；真实 qd_sector_flow 1 期（历史不足，盘中累积后自然出信号）
   - 已知 caveat：rotation_signal 是 ctx attribute 不是 df column，H1 跨 df 校验会一直报 missing（已知盲区，p05 自带 insufficient 兜底，不阻断）
4. **positions**（p05/p15/p16）：建 qd_positions 持仓表 + 持仓源（外部券商/手动）—— **需用户定持仓来源**
5. **lhb_analyzer.analyze**（p13/p14）：daily_close 调 → ctx.lhb_data（龙虎榜 T+1，盘后才出）

### 调度推送层

- **H3** [intraday_loop.py](runner/intraday_loop.py) `_process_decisions`：扩展 watch 推送（让 p17/p18 提示触达飞书，配 [notify_dedup.py](lib/notify_dedup.py) 频控）
- **H5** [lib/qdb.py](lib/qdb.py) `connect`：加 keepalive + OperationalError 重连（断连不空转） ✅ **已修**（commit `d8ea329`）
  - libpq keepalives_idle=30/interval=10/count=3 + SQL 层 `_ensure_alive` ping 兜底
  - `_exec_with_reconnect` 包 query_df/executemany_batch/query_one，OperationalError 自动重试 1 次（DEDUP UPSERT 幂等，SELECT 重读新数据 → 安全）
  - **Windows caveat**：当前 psycopg2 驱动不暴露 keepalive 参数给 Python 层（需 PG 客户端 ≥16）；靠 SQL ping 兜底才是核心防线
- **H6** [market_clock.py](lib/market_clock.py)：加 HOLIDAYS set + FORCE_TRADE_DAY 开关（假日数据接 akshare `tool_trade_date_hist_sina`）
- ~~**全局时区**（之前发现的时区错位 bug）~~ ✅ **已修**（commit `0c06eb1`），见顶部"已修复陷阱"
- ✅ **H8 缺 pyyaml** 已加（requirements）
- **H7** [scheduler.py](runner/scheduler.py)：finally 加 tq close + 子进程 Job Object（COM 不泄漏）

### 优化项（可选）
- k1 增量（只算新 K线的 code）
- c2 focus 收紧
- H1 护栏：首轮校验延后到 ctx 各 df 有数据，减误报

---

## 6. 关键陷阱（已踩过/确认的）

- **tqcenter 偶返 NaN**：所有 `_safe_float` 要过滤 `r != r`（k3 已修，其他模块的 _safe_float 建议同步加）。
- **snapshot 双形态行（C8 未修）**：c2@T 写快照列、c3@T+1s 写 intraday 列，同 code 同秒两行。k3/intraday_engine 用 `_merge_dual_rows`（groupby code 取每列非空）合并。彻底修需 c3 去 +1s 或拆表。
- **k1 窗口（C2 已修）**：fetch_kline 用 ROW_NUMBER 取每 code 最近 30 根（QuestDB 支持 window function，已 WebSearch 确认）。
- **骗炮搁置**：auction_engine.py（规则树）+ ddl/15_pianpao.sql 已写但**未接入** auction_monitor；用户说不纠结骗炮。
- **pricevol 列名（C1 已修）**：PascalCase（Now/LastClose/Volume），全项目统一。表已重建。
- **H1 护栏**：在 intraday_loop 首轮 60s 块 build ctx 后调 `StrategyRegistry.validate_required_fields(ctx)`，仅首轮一次。

---

## 7. 新会话启动指令（复制给新 Claude）

> 读 `SESSION_HANDOVER.md` + `ARCHITECTURE_REVIEW.md`。上一会话完成了批0-3 + 采集层验证 + 监测 + k3 修 + **k5 当天 K 线合成** + **全局时区修复**（9 commit）。**时区已统一用 `lib.qdb.cutoff()` 本地 now**，**不要再写 `dateadd(..., now())`**。现在从"第 5 节下一步清单"继续，先做**策略层 C3 第 1 步（dark_money 接通 qd_money_flow + p08 字段对齐）**。Q 自包含、嵌入主循环、不要读 DB数据库_v2。改动前先 `git log --oneline -12` 看历史，每步 py_compile + commit。
