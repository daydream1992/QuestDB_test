-- ============================================================
-- 表名: qd_code_registry
-- 脚本路径: K:\QuestDB_prod\ddl\00_registry.sql
-- 用途: 全市场代码注册表, 自动发现新股/新板块
-- 数据源: tqcenter get_sector_list + get_stock_list_in_sector
-- 时间戳: last_seen (最近一次发现该代码的时间)
-- 映射: code → tdx_code + code_type + market
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_code_registry (
    code            VARCHAR,
    tdx_code        VARCHAR,
    name            VARCHAR,
    code_type       VARCHAR,
    market          VARCHAR,
    first_seen      TIMESTAMP,
    last_seen       TIMESTAMP,
    is_active       BOOLEAN,
    sector_category VARCHAR
) TIMESTAMP(last_seen) PARTITION BY YEAR
DEDUP UPSERT KEYS(last_seen, code);
