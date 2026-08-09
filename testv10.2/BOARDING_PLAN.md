# testv10.2 双轨策略 — 9:30 极速突击打板 实现计划

> ⚠️ **未实现蓝图 (2026-08-10 标记)**: 本计划的 boarding_strategy.py + on_boarding_pioneer/team 2 卡 + 5 闸过滤**均未实现**。当前机会事件走 opportunity_engine 3 正卡。**别按本文找代码**。

> 来源: planner agent (2026-08-07)。在防守型看盘底座上叠加打板策略, **底座零侵入**。

## 底座不动 — 证据
禁改 (单进程/scan_universe/drill_stocks/board_pool流转/calc_sector_score/原4卡≤2/min/signal_extractor) 全不触动。
改动面仅 4 处最小挂钩: publisher(+2方法+1桶) / radar_main(+3行) / settings(+常量) / 新建 boarding_strategy.py。

## 新模块: boarding_strategy.py
- `calc_momentum_score(zt_count, board_zaf, strong_count, board_amount, refs)`: 0.4*涨停 + 0.3*涨幅 + 0.2*(>5%家数) + 0.1*金额, 归一化用 meso_radar._norm 同款。
- `is_strong_resonance(ms, rows, top_n=10, overlap_thr=0.85)`: 概念(880)/行业(881) 成分股重叠≥85% 且都在各自分类 momentum Top10 → {行业code: [共振概念code]}。
- `fetch_inout_and_ztprice(codes)`: 对预筛后≤30候选一次快照耀 InOutFlag(0=Buy) + ZTPrice(一字板判定)。drill_stocks 未取且禁改 → 独立补取。
- `hard_filter_for_boarding(drilled, inout_zt, prev_zjl, ...)`: 5道闸同满足 ①FCAmo>3000万 ②InOutFlag==0(Buy) ③Zjl>0且>prev_zjl×1.5 ④pos_ratio>0.8(距高<20%,上方无套牢) ⑤非一字板(Now>=ZTPrice*0.999 AND fLianB<0.5 剔除)。
- `BoardingStrategy.run(meso_rows, drilled, df, now)`: 维护跨轮 prev_zjl/prev_board_zt, detect_pioneer + detect_team。

## 数据流 (共用底层, 零重复拉取)
radar_main.run_one_round 末尾 +1行: `boarding.run(rows, drilled, df, now)`。
新字段来源: 涨停家数/板块涨幅 ← meso_rows(已有); >5%家数/板块总金额 ← df_universe 算(ΣVolume·Now, 零新增); InOutFlag/ZTPrice ← fetch_inout_and_ztprice(仅≤30候选, 一次快照)。

## 输出耦合 (publisher, 不动原4卡)
- +`boarding_bucket` TokenBucket(5/min) + `_boarding_window`(09:30-09:45) + `_pick_bucket`。
- 09:30-09:45: 打板2卡走 boarding_bucket(5/min独享), 原4卡仍 bucket(2/min), 两桶独立。
- 09:45后: 打板2卡回退 bucket(与原4卡共2/min), 防守优先。
- +`on_boarding_pioneer`(🚨秒板先锋) + `on_boarding_team`(🏆梯队成型)。

## 门控 bypass (与状态机并存)
打板2卡直接判 meso_rows原始数据(ZTGPNum 0→1), 不经 board_pool grace, 秒级触发 (比状态机快~3轮)。dedup 独立实例 key前缀'boarding'。

## 5 个待拍板 (planner 给了默认)
1. Zjl 放大倍数 = **1.5** (×prev_zjl; 首轮无基线降级为只判Zjl>0)
2. 共振 TopN = **10**
3. 一字板判定 = **Now>=ZTPrice×0.999 AND fLianB<0.5** (ZTPrice缺则回退 Open>=LastClose×1.099)
4. 中军(+8%半路) ZAF 区间 = **[6, 9.5]**
5. 归一化 refs = **ZT=10/ZAF=8/STRONG=15/AMOUNT=1e9**

## 落地顺序
Phase1 字段探测(InOutFlag/ZTPrice盘中非空率) → Phase2 calc_momentum_score+is_strong_resonance单测 → Phase3 hard_filter+fetch单测 → Phase4 detect_pioneer/team+跨轮状态 → Phase5 publisher加2卡+独立桶 → Phase6 radar_main挂钩+dry-run → 明早09:30-09:45盘中实测。

## 歧义点 (已定)
- pos_ratio > 0.8 (距高<20%, 上方无套牢) — 用户原话"<20%"为笔误, 已确认>0.8。
- InOutFlag==0=Buy (主动买盘占优)。
- prev_zjl/prev_board_zt 跨轮维护 (同 meso_radar.prev_zaf 模式)。
