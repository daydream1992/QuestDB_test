# testv10.2 盘中端到端验证清单

> 2026-08-10 五路 agent 审核 + 9 HIGH/12 MEDIUM 修复后更新。盘外全绿,盘中验真实数据 + stage 切换 + subscribe 回调 + 各表/预警/机会事件。
> 跑法: `python testv10.2/radar_main.py --push` (生产模式, 真写飞书+真发预警)。日志: `logs/testv10.2_radar_YYYYMMDD.log`。

## 0. 盘前准备
- [ ] 通达信客户端**已打开**(COM 必须, 否则 "请确认是否打开通达信客户端")
- [ ] `config/.env`: `LARK_WEBHOOK_URL`(预警卡) + 飞书 `APP_ID/APP_SECRET`(多维表) 已配
- [ ] `settings.SENTIMENT_BITABLE_APP_TOKEN = 'ToVFbNgEqaTjmZs1feZcEVVYnab'`(已固化, 勿改)
- [ ] 盘前 dry-run 自检各模块:
  - `python testv10.2/sentiment_monitor.py`(情绪打印)
  - `python testv10.2/rotation.py`(轮动自检)
  - `python testv10.2/stock_ranking.py`(个股榜自检)
  - `python testv10.2/open_monitor.py --simulate`(subscribe 方案A 模拟)
  - `python testv10.2/auction_monitor.py`(竞价采集, 盘外残留值)
  - `python testv10.2/opportunity_engine.py`(机会引擎自检)
  - `python testv10.2/blindspot_monitor.py`(盲区自检)
  - `python testv10.2/ladder_tracker.py`(涨停梯队自检)
- [ ] 9:10 前启动 `radar_main.py --push`(非交易时段等待, **9:15 自动进 auction** — 门控已修)

## 1. 竞价段 9:15-9:25 (auction_monitor) — **门控修复后首次验证**
- [ ] stage 切到 `auction`(**生产模式竞价段首次可跑**, 日志 fetch auction_raw)
- [ ] 竞价榜日志: `📋 竞价 | HH:MM | 涨停板块N只 一字候选N只 采集Ns`
- [x] **单位已固化**: OpenAmo=**元** / OpenAmoPre1·CJJEPre1·CJJEPre3·FCAmo·OpenZTBuy=**万元**。竞价昨量比 = `(OpenAmo/1e4) ÷ OpenAmoPre1`
- [ ] **OpenAmoPre1 9:15 验**: ①个股非零合理 ②880001=0 是"板块指数此字段不填充"还是"昨日数据未更新"
- [ ] 一字候选数 > 0(给 open_monitor 订阅; 若 0, open 段回退 WATCHLIST)
- [ ] 采集耗时 < 120s
- [ ] 飞书表 `v10.2竞价 YYYY-MM-DD` 写入(**生产日首次非空**, 修复前恒空)

## 2. 开盘段 9:30-9:45 (open_monitor + subscribe_hq)
- [ ] 9:30 stage 切 `open`, `open_mon.start(候选)`(**竞价一字候选应非空** — 传导链修复后闭环)
- [ ] subscribe_hq 返回 ErrorId=0
- [ ] 回调频率: 日志 `[回调 HH:MM:SS.mmm] code` 开盘秒级密集
- [ ] **开盘拉升触发**: `🚀 开盘拉升` + send_warn + on_open_surge(lane1)
- [ ] unsubscribe_hq(触发后释放名额)
- [ ] **COM 共存**: radar 开盘段快速循环(0.1s) + 降频轮询(15s)

## 3. 盘中段 9:45-14:00 (sentiment + rotation + ranking + alert + opportunity + blindspot + ladder)
- [ ] stage 切 `intraday`
- [ ] **sentiment 表** `v10.2情绪`: 1min/行, 8 维度合理 (**日级留存修复**: 炸板股跌出 9.5% 仍计入炸板, 封板率不被高估)
- [ ] **sentiment 时段表** `v10.2情绪时段`: 早/午/后/尾 4 行聚合(开/收/高/低/趋势/涨停峰值)
- [ ] **rotation 表** `v10.2概念轮动`/`行业轮动`: 3min/行
- [ ] **stock_ranking 表** `v10.2个股榜`: 2min/行, Top5(**含全市场补钻**: 池外最强票也进榜)
- [ ] **机会事件**(lane0): 🟢新主线 + 🔥趋势确认 + 🚀龙头封板(前排排序: 首封>连板>封单)
- [ ] **涨停梯队表** `v10.2涨停梯队`: 炸板/回封事件落表, 概念/行业映射 + 计数(**首次封板 vs 回封区分**)
- [ ] **盲区补盲**: 6s 回调检测 Top20 FCAmo 状态变更(**速率修复**: 主动行情不积压)
- [ ] **alert_engine 跳水预警**(lane1): `🔴 大盘跳水预警`(封板率跌/炸板升/亏钱比/综合分跌 + L2/L3)
- [ ] **分桶验证**: 机会(lane0)和预警(lane1)各 ≤1/min, 负向不饿死正向
- [ ] **故障隔离验证**(可选): 故意让某模块抛异常, 其他照跑

## 4. 尾盘段 14:00-15:00 (tail_monitor)
- [ ] stage 切 `tail`
- [ ] tail 炸板检测: `📉 尾盘 | 封板候选N 炸板M`(**基线修复**: 首轮设基线 + 掉候选清理)
- [ ] **on_blast_alert**(lane1): `💥 尾盘炸板预警`
- [ ] rotation/sentiment/stock_ranking 继续盘中逻辑

## 5. 收盘段 15:00 (force_push 定格) — **close 可达修复后首次验证**
- [ ] 15:00 stage 切 `close`, **close_done 只跑一次** run_one_round(is_close=True)
- [ ] sentiment/rotation force_push 定格(各表最后一行)
- [ ] **尾盘时段聚合 flush**(修复: 尾盘行不丢, 显式写最后一行)
- [ ] 15:00 定格完成后 radar 退出(close_done + 时间到)

## 6. 收盘后核查
- [ ] 飞书 UI 核 **8+1 张表**: `v10.2情绪` / `情绪时段` / `概念轮动` / `行业轮动` / `个股榜` / `竞价` / `尾盘` / `涨停梯队`
  - 字段名/类型/值合理(档位单选色、时间格式、数字精度)
  - 行数: 情绪~240/时段4/轮动各~80/个股榜~120/竞价~5/尾盘~60/梯队(事件数)
- [ ] 日志无未捕获异常/Traceback
- [ ] 通达信客户端无卡死/冲突报警

## 7. 风险 / 降级方案
| 风险 | 现象 | 降级 |
|---|---|---|
| subscribe COM 冲突 | 回调异常/轮询卡死 | 开盘段纯轮询: 注释 open_mon.start |
| 竞价采集超时 | >120s | 缩 `AUCTION_BOARD_MARKET`(只概念) 或 `AUCTION_BOARD_BUDGET` |
| 候选 >100 | subscribe 上限 | 现截断[:100] |
| 某模块持续挂 | 该表空 | per-module try 隔离; 查日志修该模块 |
| 竞价 fetch_auction 挂 | 竞价榜空 | fetch_bundle 已 try 降级, 不拖垮整轮 |
| 全市场补钻超时 | 钻取变慢 | 缩 `DRILL_GLOBAL_TOP_N`(20→10) |

## 8. 问题记录 (盘中填)
- 时段 / 现象 / 日志片段 / 处置:
-
-

**审核修复记录 (2026-08-10, 五路 agent):**
- 9 HIGH 修复: ①竞价段门控(is_trading_time 不含 9:15, 已改 get_stage) ②close 段 15:00 不直接退(先定格) ③drop 探照灯极性反转(ZAF≤-2 而非 -2~0) ④fetch_bundle 故障隔离 ⑤机会去重先于推送确认(永久吞事件) ⑥first_limit 假时间(基线不记首封) ⑦process_pending 速率(30→时间预算 5s) ⑧9.5% 样本截断(日级留存) ⑨FCb/EverZTCount 进 drilled
- MEDIUM 修复: 分桶(机会 lane0/预警 lane1 各≤1min) / 首封 vs 回封区分 / tail 基线+清理 / 双零 loss_ratio / 尾盘时段 flush / 死配置清理(OPEN_BURST_*/PUSH_DEDUP_TTL/DIVE_ALERT_MAX_PER_MIN)
- 提效: 全市场 pct Top20 补钻(池外最强票) / 龙头首封排序(首封>连板>封单)
- 盘后实测 (04:25): 全链路 24.2s, 池35, 钻取 167+2 全局补钻, 自检全绿
