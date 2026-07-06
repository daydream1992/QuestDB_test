-- ============================================================
-- 脚本路径: K:\QuestDB_test\ddl\08_flow.sql
-- 用途: 2 张资金流表 (板块资金流 + 个股明暗资金)
-- 数据源: tqcenter get_money_flow + compute 暗资金计算
-- 时间戳: flow_time (资金流采样时刻)
-- 去重: DEDUP UPSERT KEYS(flow_time, code)
-- ============================================================


-- ------------------------------------------------------------
-- 表名: qd_sector_flow
-- 用途: 板块资金流 (含明暗资金, 60s/轮)
-- 数据源: tqcenter get_money_flow (板块) + compute 暗资金
-- 时间戳: flow_time
-- 字段映射:
--   code        ← 板块代码
--   flow_time   ← datetime.now()
--   main_net    ← 主力净流入 (元)
--   big_net     ← 超大单净流入 (元)
--   mid_net     ← 中单净流入 (元)
--   small_net   ← 小单净流入 (元)
--   dark_money  ← 暗资金净流入 (元, 由大单拆单行为反推)
--   light_money ← 明资金净流入 (元)
--   total_flow  ← 总成交额 (元)
--   net_pct     ← 净流入占比 (%)
-- 去重: (flow_time, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_sector_flow (
    code         VARCHAR,
    flow_time    TIMESTAMP,
    main_net     DOUBLE,
    big_net      DOUBLE,
    mid_net      DOUBLE,
    small_net    DOUBLE,
    dark_money   DOUBLE,
    light_money  DOUBLE,
    total_flow   DOUBLE,
    net_pct      DOUBLE
) TIMESTAMP(flow_time) PARTITION BY DAY
DEDUP UPSERT KEYS(flow_time, code);


-- ------------------------------------------------------------
-- 表名: qd_money_flow
-- 用途: 个股明暗资金 (含 5 档压力差, 10s/轮重点 / 60s/轮全场)
-- 数据源: tqcenter get_money_flow (个股) + compute 暗资金/5档压力差
-- 时间戳: flow_time
-- 字段映射:
--   code                ← 股票代码
--   flow_time           ← datetime.now()
--   main_net            ← 主力净流入 (元)
--   big_order_diff      ← 大单差 (买单-卖单, 元)
--   dark_money          ← 暗资金净流入 (元)
--   light_money         ← 明资金净流入 (元)
--   pressure_diff_5level← 5 档压力差 (买盘压力-卖盘压力, 元)
--   buy_pressure        ← 买盘压力 (5档买盘总金额)
--   sell_pressure       ← 卖盘压力 (5档卖盘总金额)
--   net_flow            ← 净流入 (元)
-- 去重: (flow_time, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_money_flow (
    code                  VARCHAR,
    flow_time             TIMESTAMP,
    main_net              DOUBLE,
    big_order_diff        DOUBLE,
    dark_money            DOUBLE,
    light_money           DOUBLE,
    pressure_diff_5level  DOUBLE,
    buy_pressure          DOUBLE,
    sell_pressure         DOUBLE,
    net_flow              DOUBLE
) TIMESTAMP(flow_time) PARTITION BY DAY
DEDUP UPSERT KEYS(flow_time, code);
