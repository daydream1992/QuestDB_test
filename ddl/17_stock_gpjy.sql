-- ============================================================
-- 表名: qd_stock_gpjy
-- 脚本路径: K:\QuestDB_test\ddl\17_stock_gpjy.sql
-- 用途: 个股交易数据 (GP系列, 日级历史, tqcenter get_gpjy_value)
-- 数据源: tqcenter get_gpjy_value (需客户端下载股票数据包)
-- 时间戳: date (数据日期, 日级)
-- 说明:
--   GP 是日级历史时序 (每天一条), 今天收盘后才有; 盘后/盘前用 (补历史股性 +
--   盘后精确涨停), 盘中实时涨停仍用 FCAmo (qd_stock_intraday)。
--   高价值字段: GP15 盘后涨跌停状态, GP39 次日红盘率(T+1打板核心),
--   GP40 连板率(补库内连板缺失), GP09 机构买入(主力认可), GP14 开板次数(炸板风险)
-- 去重: DEDUP UPSERT KEYS(date, code)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_stock_gpjy (
    code                  VARCHAR,
    date                  TIMESTAMP,
    gp15_status           DOUBLE,  -- 涨跌停状态: 2涨停/1曾涨停/-2跌停/-1曾跌停
    gp15_seal             DOUBLE,  -- 封单额(万元); 跌停时取负
    gp14_zt_amo           DOUBLE,  -- 涨停金额/板上成交(万元)
    gp14_break_cnt        DOUBLE,  -- 开板次数(炸板风险量化)
    gp38_zt_cnt           DOUBLE,  -- 近1年涨停次数(股性活跃度)
    gp38_premium_cnt      DOUBLE,  -- 近1年溢价5%次数
    gp39_first_seal_rate  DOUBLE,  -- 近1年首板封板率%
    gp39_next_red_rate    DOUBLE,  -- 近1年次日红盘率% (T+1打板命门)
    gp40_lb_rate          DOUBLE,  -- 近1年连板率% (补库内连板缺失)
    gp40_last_zt_time     VARCHAR, -- 最后涨停时间
    gp09_inst_cnt         DOUBLE,  -- 龙虎榜买方机构个数
    gp09_inst_amo         DOUBLE   -- 龙虎榜机构买入金额(万元)
) TIMESTAMP(date) PARTITION BY YEAR
DEDUP UPSERT KEYS(date, code);