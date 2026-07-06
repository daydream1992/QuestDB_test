-- 23_positions_v2.sql
-- 持仓表 v2 (替代/补充现有 qd_positions)
-- 写入: strategy/portfolio.py::persist_open / persist_close
-- 读取: strategy/portfolio.py::load_from_db
-- 说明:
--   - 每次开仓/平仓/状态变更都写一行, status 区分 open/closed
--   - DEDUP UPSERT KEYS(updated_time, code, status): 同时刻同code同状态幂等
--   - 回测/复盘: SELECT * WHERE status='closed' AND entry_time > ... 算历史绩效

CREATE TABLE IF NOT EXISTS qd_positions_v2 (
    updated_time      TIMESTAMP,    -- 写入时间
    code              VARCHAR,
    direction         VARCHAR,      -- 'long' (A 股只能多)
    size_pct          DOUBLE,       -- 仓位 % (0-100)
    shares            BIGINT,       -- 股数 (100 的倍数)
    entry_price       DOUBLE,
    entry_time        TIMESTAMP,
    sector            VARCHAR,
    stop_loss_pct     DOUBLE,       -- 止损 % (正数)
    stop_profit_pct   DOUBLE,       -- 止盈 % (正数)
    entry_alpha       DOUBLE,       -- 入场时 alpha_score
    entry_rank        INT,          -- 入场时全市场排名
    status            VARCHAR,      -- 'open' / 'closed'
    realized_pnl      DOUBLE,       -- 平仓时填
    close_price       DOUBLE,
    close_time        TIMESTAMP,
    close_reason      VARCHAR       -- '止损'/'止盈'/'alpha衰减'/'手动'
) TIMESTAMP(updated_time) PARTITION BY DAY
DEDUP UPSERT KEYS(updated_time, code, status);
