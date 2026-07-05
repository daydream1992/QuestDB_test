# 架构自洽性体检报告 — QuestDB_test

> 体检日期：2026-07-03
> 项目根：`K:\QuestDB_test`
> 方法：4 维度并行审查 agent（策略契约 A / 数据流 B / Schema 对齐 C / 调度可靠性 D）+ 对关键断点的亲验裁决。所有 CRITICAL 均有 ≥2 份独立报告或亲验佐证。

---

## 一句话结论

**骨架健康，但"计算→策略"肌腱断裂。** 分层、注册机制、DDL 时序设计、按列名写入这些地基是好的；但 8 处 CRITICAL 导致 **16 个策略中约 13 个永不触发**，系统当前处于"全天采集、几乎不出决策"的空转状态，**不能直接承接新策略开发**。好消息是 P0 阻断项多为"改一个字段名/一个时间窗口"即可让一整片策略复活，投入产出比极高。

---

## 健康面（架构没问题的部分）

| 维度 | 结论 |
|---|---|
| 29 表核对 | ✅ 与 MAINTENANCE §3 清单**零误差**（00:1+01:3+02:3+03:1+04:2+05:1+06:5+07:6+08:2+09:1+10:1+11:1+12:2=29）|
| DDL 规范 | ✅ 全表 `TIMESTAMP+PARTITION BY+DEDUP UPSERT KEYS`，占位符统一 `%s`，无 `?` |
| 策略注册机制 | ✅ 16 插件 `name` ↔ strategies.yaml key **全对齐**，热插拔健全 |
| 写入安全 | ✅ [lib/qdb.py](lib/qdb.py) `executemany_batch` **按列名 INSERT** → 不存在列错位风险（关键安全网）|
| 采集→库链路 | ✅ c1~c6 各有表、有写入，闭合 |
| 竞价子链路 | ✅ p09/p10/p11 相对闭合（auction_monitor 内联写 qd_auction_snapshot）|
| 文档质量 | ✅ MAINTENANCE/HANDOVER 整体高质量（虽有几处漂移，见下）|

---

## CRITICAL（8）— 阻断核心功能

### 组 1：取数链路断裂（修一处复活一片）

**C1. `qd_pricevol` 是全项目唯一 snake_case 表，消费方全按 PascalCase 读 → 价量路径全归零**
- 根因：[collect/c1_pricevol.py:142](collect/c1_pricevol.py#L142) 手写 `['code','snapshot_time','last_close','now','volume']`，绕过 [config/fields.py](config/fields.py) 的 PascalCase 约定；[ddl/03_pricevol.sql](ddl/03_pricevol.sql) 同步小写。其余 28 张表全是 PascalCase。
- 亲验：[strategy/selector.py:67](strategy/selector.py#L67) 读 `r.get('Now')` → 取不到 → `change_pct` 全 0 → `nlargest(100)` 退化为**任意前 100 只**，污染 c2/c3 重点池。
- 波及：selector、resonance、sector_flow、p01/p02/p04/p05/p07/p12/p15/p16 全部价量条件失效。
- 附带风险：`now` 是 QuestDB 保留字（函数名），作列名有歧义。
- **修法**：DDL + c1 + PRICEVOL_FIELDS 统一为 `Now/LastClose/Volume`。

**C2. k1 只读 10 分钟窗口，rolling(20)/ewm(26) 永远算不出 → qd_indicators/qd_signals 全天空**（亲验确认）
- [compute/k1_indicators.py:82-95](compute/k1_indicators.py#L82) `fetch_kline(since_minutes=10)` 注释自承"覆盖 1 个 5m 周期"，但 [k1_indicators.py:124-143](compute/k1_indicators.py#L124) 用 `rolling(20)`/`ewm(span=26)` 且门槛要求四项全非 NaN。10 分钟仅 2 根 K 线 → 全 NaN → 写 0 行。daily_init 补的 48 根历史被 WHERE 过滤掉。
- 这是"增量计算意图 vs pandas 需要完整序列"的经典冲突。
- **修法**：`fetch_kline` 改读最近 ≥26 根（`LIMIT 30`），仍只为最新一根产出。

**C3. `qd_money_flow` / `qd_big_order` 有 DDL 有读取，但全仓库无写入器**（A/B/C 三重印证）
- `dark_money.calc_batch` / `big_order.detect_batch` 从未被任何 runner 调用。
- → ctx.money_flow_df / big_order_df 恒空 → p08、p12 永不触发。
- **修法**：intraday_loop 接入 calc_batch/detect_batch 写库；或无 L2 数据源则禁用 p08/p12 并删读取。

**C4. ctx.positions / rotation_signal / lhb_data 三个字段全仓库无写入方**（C 补充：qd_positions 表本身完全孤立无读写）
- → p05（轮动+持仓）、p13/p14（龙虎榜）、p15/p16（止损止盈）全部恒空；RiskManager 总仓位校验形同虚设（`current_total_position()` 恒 0）。
- **修法**：_build_context 从持仓表加载 positions；60s 块调 `detect_rotation`；daily_close 调 `lhb_analyzer.analyze`。
- **2026-07-05 进展**：
  - ✅ `lhb_data` 已接通：`_build_context` 调 `strategy.lhb_analyzer.build_lhb_data` 从 `qd_lhb_detail` 表聚合（非本节记录的"daily_close 调 analyze"路径——因 p13/p14 注册在盘中策略池，从表读昨日数据更符合 T+1 语义；`analyze(lhb_raw)` 保留供原始数据场景）。p13/p14 现可触发。
  - ✅ gpjy 链已接通：`daily_close` 调 `c5_gpjy.run` 写 `qd_stock_gpjy` → 次日 `_build_context` 读 `ctx.gp_df`。p01 GP 维度生效。
  - ⏳ `positions` / `rotation_signal` 仍待办（p05/p15/p16/RiskManager 仍恒空）。

### 组 2：单点字段/schema 错配（改一行即修复）

**C5. `_run_sector_flow` 从 more_info_df(qd_stock_daily) 取 Zjl，但 Zjl 不在该表**（B 发现）
- [runner/intraday_loop.py:287](runner/intraday_loop.py#L287)。Zjl 在 qd_stock_snapshot（intraday），不在 qd_stock_daily。→ `_run_sector_flow` 每轮 return → qd_sector_flow 盘中永不写 → sector_flow_df 恒空 → p07/resonance 背离/sector_flow 全失效。
- **修法**：改读 snapshot_focus_df（注意先解决 C8）。

**C6. p07 读 `block_code`/`net_flow`，实际列是 `code`/`main_net`**（A/B/C 印证；HANDOVER §2.2 早有警告）
- [strategy/plugins/p07_divergence.py:52](strategy/plugins/p07_divergence.py#L52)。同一仓库 resonance.py 用对了 code/main_net，p07 是复制粘贴漂移。
- **修法**：p07 改读 code/main_net。

**C7. p06 读 `resonance_score/market_change/...`，库存 `total_score/signal_type`**（A/B 印证）
- [strategy/plugins/p06_resonance.py:35,42](strategy/plugins/p06_resonance.py#L35)。`scan_market` 产出列名在 `_run_resonance` 落库时被丢弃。
- **修法**：p06 改读 `total_score`，或落库保留 market_change/sector_change/stock_change 列。

**C8. qd_stock_snapshot 双形态行分裂**（C 独家发现）
- c2 写 43 列快照@T，c3 intraday 写 18 列高频@T+1s，因 DEDUP KEY 含 snapshot_time → 同 code 同秒落成两行：行A 有 Now/Volume/5档但 intraday 列 NULL，行B 有 ZAF/Zjl/fHSL 但 Now/Volume/5档 NULL。`groupby last` 取哪行都有字段丢失。
- HANDOVER §2.2 说"+1s 避免覆盖"——实际效果是行分裂，设计与实现冲突。
- **修法**（需设计决策）：合并单行（去 +1s，验证 QuestDB 列级 UPSERT）或拆成 qd_stock_snapshot + qd_stock_intraday 两表。

---

## HIGH（8）— 契约破裂 / 可靠性

| # | 问题 | 位置 | 修法 |
|---|---|---|---|
| H1 | **required_fields() 是死钩子**——声明"供校验"但零 caller，所有字段错配以"策略静默返回空"逃逸（A/B/D 三方呼应）| [strategy/base.py:42](strategy/base.py#L42) | load_plugins 后对 ctx 列 assert，fail-fast |
| H2 | Decision.stop_loss/stop_profit 被丢弃，出场生命周期断裂 | [runner/intraday_loop.py:65](runner/intraday_loop.py#L65) _DECISION_COLS | 增列入库 + buy 后写持仓表 |
| H3 | HANDOVER §4.1 "watch 仅推送" 为假——watch/warn 只入库不推飞书 | [runner/intraday_loop.py:236](runner/intraday_loop.py#L236) | 扩展推送 action 集合或改文档 |
| H4 | _build_context 先于 sector_flow/resonance 写入，ctx 滞后一轮 | [runner/intraday_loop.py:423→438](runner/intraday_loop.py#L423) | 拆 build，写完 sector_flow 后补刷 ctx |
| H5 | QuestDB 全天单连接无重连，断连后半天空转（每轮异常被吞）| [lib/qdb.py:32](lib/qdb.py#L32) | connect 加 keepalive + OperationalError 重连 |
| H6 | 法定节假日误启动采集写日期错配脏数据（is_trading_day 只看 weekday）| [lib/market_clock.py:16](lib/market_clock.py#L16) | 引入假日历或 FORCE_TRADE_DAY 开关 |
| H7 | tqcenter COM 泄漏——scheduler 主进程 init 不 close + 子进程被 Windows terminate 跳过 finally | [runner/scheduler.py:88,195](runner/scheduler.py#L88) | finally 加 close；子进程用 Job Object |
| H8 | requirements.txt 缺 `pyyaml`（registry `import yaml` 解析 strategies.yaml）| [requirements.txt](requirements.txt) | 加 `pyyaml>=6.0` |

---

## MEDIUM（17）/ LOW（11）概览

高价值项点名：

- **M‑cost_price vs entry_price 不一致**（risk.py/p05 用 cost_price，p15/p16/DDL 用 entry_price）——一旦 C4 positions 接通就爆，潜伏 bug。
- **M‑lark.push_decision 无频控**——同决策每 60s 重复推送 + 重复写库，飞书刷屏。
- **M‑k2 每轮读 qd_indicators 全表无时间过滤**——随运行时长线性退化。
- **M‑loguru 多模块各自 `logger.add` 无 filter**——intraday_loop 进程内每条日志写 7+ 文件，磁盘×7、排障困难。
- **M‑daily_init 在 scheduler 主进程同步执行**——可能挤占 09:30 切换。
- **M‑HqDate 同时在 DOUBLE_FIELDS 和 VARCHAR_FIELDS**——类型映射自相矛盾。
- **M‑INDEX_SNAPSHOT_FIELDS 未在 fields.py 定义**——绕过"三处对齐"规则。
- 其余：.env.example 路径不一致(LOW)、init() 未守卫 _initialized(LOW)、c3 文档串过期(LOW)、PRICEVOL_FIELDS 死常量(LOW) 等。

---

## 文档漂移清单（零代码成本）

1. 5 档买卖盘是独立 20 列（Buyp1-5/.../Sellv1-5），**非 JSON**（HANDOVER §2.2 错；qd_signals.metadata 才是 JSON）。
2. Decision.action 实际产出 `{buy,sell,warn,watch}`，**无 hold**（base.py 注释含 warn，HANDOVER 不含）。
3. more_info_df 实际来自 qd_stock_daily（非 snapshot intraday）——这是 p01/p12 取错表的根因。
4. fields.py 字段数注释漂移（STOCK_DAILY 49 非 50，STOCK_INTRADAY 16）。
5. .env.example 应在 config/ 或注明拷贝到 config/.env。

---

## 能否支撑后续开发：修复路线图

| 阶段 | 内容 | 修完效果 |
|---|---|---|
| **P0 护栏** | H1 required_fields 启动期校验 | 后续修复的错误能 fail-fast 暴露 |
| **P0 阻断** | C1 pricevol 列名 / C2 k1 窗口 / C5 sector_flow Zjl / C6 p07 / C7 p06（多为改一行）| 策略层从"几乎全空"→ p03/p04/p06/p07 + 竞价 p09-11 **能跑** |
| **P1 数据源** | C3 money_flow/big_order 写入器 / C4 positions/rotation/lhb / C8 snapshot 双形态行 | p05/p08/p12/p13/p14/p15/p16 复活 |
| **P2 可靠性** | H5 重连 / H6 假日历 / H7 COM 泄漏 / H8 pyyaml | 无人值守前提 |
| **P3 契约+文档** | H2 出场生命周期 / H3/H4 / 文档漂移 / 其余 MEDIUM | 防回归 + 文档可信 |

**直接回答"能否支撑后续开发"**：架构**设计自洽**（分层清晰、契约定义到位、DDL 规范），但**实现与契约大面积脱节**。最新 commit `a36f761` 修了 DDL 字段名和执行顺序，却没触及 pricevol 大小写、k1 窗口、多张表无写入器这些更深层断裂。**必须先修 P0，否则新策略会沿袭 p01/p12 那种"字段名对得上文档却取不到数、静默返回空"的失败模式。**

---

> 本报告随修复进度更新。P0 修复完成后，对应条目标注 ✅；新发现追加到末尾。
