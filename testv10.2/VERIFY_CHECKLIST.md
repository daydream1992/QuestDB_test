# testv10.2 盘中端到端验证清单

> 6 phase 重构后首次真实交易时段实测。盘外全绿,盘中验真实数据 + stage 切换 + subscribe 回调 + 各表/预警。
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
- [ ] 9:10 前启动 `radar_main.py --push`(非交易时段等待, 9:15 自动进 auction)

## 1. 竞价段 9:15-9:25 (auction_monitor)
- [ ] stage 切到 `auction`(日志/data_provider fetch auction_raw)
- [ ] 竞价榜日志: `📋 竞价 | HH:MM | 涨停板块N只 一字候选N只 采集Ns`
- [x] **单位已固化** (DYNAINFO(15)/10000 铁证 + 实测校准, 详见 memory): OpenAmo=**元** / OpenAmoPre1·CJJEPre1·CJJEPre3·FCAmo·OpenZTBuy=**万元**。竞价昨量比 = `(OpenAmo/1e4) ÷ OpenAmoPre1`(无量纲)
- [ ] **OpenAmoPre1 9:15 验**: ①个股非零合理(验证字段对个股有效) ②880001(板块指数)=0 是"板块指数此字段不填充"还是"昨日数据未更新"
- [ ] **OpenFDE 字段补验**(文档未列, get_more_info 有无 OpenFDE): 盘中探针 `tq.get_more_info(code,[])['OpenFDE']`
- [ ] 一字候选数 > 0(给 open_monitor 订阅; 若 0, open 段回退 WATCHLIST)
- [ ] 采集耗时 < 120s(竞价 10min 窗口; 超→缩 AUCTION_BOARD_MARKET 或预算)
- [ ] 飞书表 `v10.2竞价 YYYY-MM-DD` 写入, 字段对(涨停板块数/放量Top/候选)

## 2. 开盘段 9:30-9:45 (open_monitor + subscribe_hq)
- [ ] 9:30 stage 切 `open`, `open_mon.start(候选)`(竞价一字候选 or WATCHLIST)
- [ ] subscribe_hq 返回 ErrorId=0(订阅成功)
- [ ] **回调频率**: 日志 `[回调 HH:MM:SS.mmm] code` 开盘秒级密集(若稀疏→tqcenter 推送问题)
- [ ] process_pending 消化: 队列积压不爆涨(日志/手动看 qsize)
- [ ] **开盘拉升触发**(现价相对 Open>5%): `🚀 开盘拉升 name(code)` + `send_warn: ErrorId=0` + 飞书卡 on_open_surge
- [ ] unsubscribe_hq(触发后释放名额)
- [ ] **COM 共存**: radar 开盘段快速循环(0.1s) + 降频轮询(15s), 回调与轮询不卡死
- [ ] radar 降频轮询 run_one_round 正常(meso+sentiment+rotation)

## 3. 盘中段 9:45-14:00 (sentiment + rotation + stock_ranking + alert_engine)
- [ ] stage 切 `intraday`
- [ ] **sentiment 表** `v10.2情绪`: 1min/行, 综合分/档位/各原始数据(涨跌/涨跌停/炸板/成交/主力/封板率/连板/风向/热门)合理
- [ ] **rotation 表** `v10.2概念轮动` / `v10.2行业轮动`: 3min/行, Top 板块 + 新晋/加速切换
- [ ] **stock_ranking 表** `v10.2个股榜`: 2min/行, Top5 个股综合分(池内 drilled)
- [ ] **alert_engine 跳水预警**(真实异动或合成): `🔴 大盘跳水预警` on_dive_alert(封板率跌/炸板升/亏钱比/综合分跌 + L2领跌板块 + L3炸板龙头)
- [ ] **故障隔离验证**(可选): 故意让某模块抛异常, 确认其他模块 + alert 不受影响

## 4. 尾盘段 14:00-15:00 (tail_monitor)
- [ ] stage 切 `tail`
- [ ] tail 炸板检测: `📉 尾盘 | 封板候选N 炸板M`(FCAmo 曾封→现开)
- [ ] **on_blast_alert**(blast_n>0): `💥 尾盘炸板预警` 飞书卡
- [ ] rotation/sentiment/stock_ranking 继续盘中逻辑(tail 段也跑)

## 5. 收盘段 15:00 (force_push 定格)
- [ ] 15:00 stage 切 `close`, **close_done 只跑一次** run_one_round(is_close=True)
- [ ] sentiment/rotation force_push 绕门固定格(各表最后一行收盘值)
- [ ] 15:00 后 radar 退出(TRADING_CLOSE)或 close 段后续轮 sleep 跳过

## 6. 收盘后核查
- [ ] 飞书 UI 核 6 张表: `v10.2情绪` / `概念轮动` / `行业轮动` / `个股榜` / `竞价` / `尾盘`
  - 字段名/类型/值合理(档位单选色、时间格式、数字精度)
  - 行数: 情绪~240/概念轮动~80/行业轮动~80/个股榜~120/竞价~5/尾盘~60
- [ ] 日志 `logs/testv10.2_radar_YYYYMMDD.log` 无未捕获异常/Traceback
- [ ] 通达信客户端无卡死/冲突报警

## 7. 风险 / 降级方案
| 风险 | 现象 | 降级 |
|---|---|---|
| subscribe COM 冲突 | 回调异常/轮询卡死 | 开盘段纯轮询: 改 `OPEN_POLL_INTERVAL=15`, 注释 open_mon.start |
| 竞价采集超时 | >120s | 缩 `AUCTION_BOARD_MARKET`(只概念) 或 `AUCTION_BOARD_BUDGET` |
| 候选 >100 | subscribe 上限 | 现截断[:100]; 后续实现 unsubscribe 滚动订阅 |
| 某模块持续挂 | 该表空 | per-module try 已隔离; 查日志修该模块, 其他照跑 |
| COM 集合竞价挂 (9:25-9:30) | tqcenter busy | 已知, radar 非交易门控应跳过; 若 force 模式手动避 |

## 8. 问题记录 (盘中填)
- 时段 / 现象 / 日志片段 / 处置:
-
-

**盘后 dry-run 记录 (2026-08-10 01:12, 非交易日, 全链路绿, 不推飞书):**
- `radar_main --force --rounds 1`: meso 578板/命中47 | 全A 5549只 2.9s | 钻取72股 2.4s | 新入池20板 | 情绪63.0偏暖 | 24.2s/轮
- 竞价链 38s < 120s 预算 ✓; 一字候选100上限截断 ✓; 轮动/个股榜/尾盘/开盘方案A合成自检 ✓; 预警引擎跳水4原因+L2/L3定位+炸板 ✓
- ⚠️ **盘后残留值** (非故障, 9:15 竞价才真实, 明日对照): ①`涨停板块336只` (盘后 Outside 残留) ②放量Top `昨量比×50.8` (OpenAmo 盘后存全天累计额, 比值失真)
- 盘外各模块在 off 段正确跳过/降级 (5时段门控符合预期)

---
**核心一句话**: 跑 `radar_main.py --push`, 盯 5 时段 stage 切换 + 各表写入 + subscribe 回调 + alert 预警。出问题查 `logs/` + 本清单风险表。
