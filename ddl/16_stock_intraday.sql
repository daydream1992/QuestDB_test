-- ============================================================
-- 表名: qd_stock_intraday
-- 脚本路径: K:\QuestDB_test\ddl\16_stock_intraday.sql
-- 用途: 个股盘中高频 intraday 字段 (c3 more_info 模式独立表)
-- 数据源: tqcenter get_more_info (STOCK_INTRADAY_FIELDS)
-- 时间戳: snapshot_time (采集时刻)
-- 说明:
--   C8 拆表修复: 原 qd_stock_snapshot 双形态行 (c2@T 快照列 + c3@T+1s intraday列)
--   导致单行不完整。现将 c3 intraday 字段独立成表, c2 快照留 qd_stock_snapshot。
--   消费方按 code+snapshot_time JOIN 两表得完整字段。
--   ⚠️ 关键字段:
--     FCAmo 封单额 — 权威涨跌停判定 (>0涨停/<0跌停/=0未封)
--     Zjl 主力净额 — 主力资金方向
-- 去重: DEDUP UPSERT KEYS(snapshot_time, code)
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_stock_intraday (
    code          VARCHAR,
    snapshot_time TIMESTAMP,
    ZAF           DOUBLE,      -- 涨幅%
    ZTPrice       DOUBLE,      -- 涨停价
    DTPrice       DOUBLE,      -- 跌停价
    fLianB        DOUBLE,      -- 量比 (⚠️非连板数)
    ZTGPNum       DOUBLE,      -- 涨停价挂单数 (个股) / 涨停家数 (板块)
    LastStartZT   VARCHAR,     -- 最近启动涨停
    MA5Value      DOUBLE,      -- 5日均线
    Wtb           DOUBLE,      -- 委比
    fHSL          DOUBLE,      -- 换手率
    Fzhsl         DOUBLE,      -- 主力换手
    FzAmo         DOUBLE,      -- 主力金额
    Zjl           DOUBLE,      -- 主力净额 (判主力方向)
    Zjl_HB        DOUBLE,      -- 主力净额环比 (连续性)
    FCAmo         DOUBLE,      -- 封单额 (权威涨跌停判定: >0涨停/<0跌停/=0未封)
    FCb           DOUBLE,      -- 封成比
    vzangsu       DOUBLE       -- 涨速
) TIMESTAMP(snapshot_time) PARTITION BY DAY
DEDUP UPSERT KEYS(snapshot_time, code);