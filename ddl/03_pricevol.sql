-- ============================================================
-- 表名: qd_pricevol
-- 用途: 全市场批量价量 (轻量高频, 10s/轮)
-- 数据源: tqcenter get_pricevol (1 次调用拿全场)
-- 时间戳: snapshot_time
-- 字段映射 (PascalCase, 与全项目一致; config/fields.py PRICEVOL_FIELDS):
--   code          ← stock_list 中的代码
--   snapshot_time ← datetime.now()
--   LastClose     ← LastClose (前收盘价)
--   Now           ← Now (现价)
--   Volume        ← Volume (累计成交量)
-- 注意: 列名用 PascalCase 与 qd_*_snapshot 等表保持一致 (消费方 selector/
--       策略统一按 Now/LastClose/Volume 读取)。旧版 snake_case (last_close/
--       now/volume) 已废弃 —— `now` 同时是 QuestDB 保留字, 改名消除歧义。
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_pricevol (
    code           VARCHAR,
    snapshot_time  TIMESTAMP,
    LastClose      DOUBLE,
    Now            DOUBLE,
    Volume         BIGINT
) TIMESTAMP(snapshot_time) PARTITION BY DAY
DEDUP UPSERT KEYS(snapshot_time, code);
