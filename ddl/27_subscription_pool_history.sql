-- ============================================================
-- 表名: qd_subscription_pool_history
-- 用途: 动态订阅池 add/remove 事件流 (subscribe_pool.py + runner/sub_pool_supervisor 写入)
-- 说明:
--   每条记录一次进入/离开订阅池的事件, 用于反查 "过去 N 分钟这只票为什么
--   被踢出". 每天 ~1000 行 (触发即弃 + 轮换约 300~600 条), 写入频率低,
--   零负担.
--
--   查: 某 code 当日进出订阅池流水
--   SELECT event_time, action, reason FROM qd_subscription_pool_history
--   WHERE code = '002747.SZ' AND event_time > dateadd('d', -1, now())
--   ORDER BY event_time;
--
--   设计要点:
--   - DEDUP UPSERT KEYS(event_time, code, action) 幂等
--     (同一秒同一 code 同一动作不会重复入)
--   - PARTITION BY DAY, 跨日后旧分区可自动 TTL
--   - score / pool_size / rationale 是冗余字段, 写入当下快照方便回溯
-- ============================================================
CREATE TABLE IF NOT EXISTS qd_subscription_pool_history (
    event_time   TIMESTAMP,
    code         VARCHAR,
    action       VARCHAR,           -- 'add' / 'remove'
    reason       VARCHAR,           -- limit_break / break_down / vol_price_div / rotation / manual
    score        DOUBLE,            -- 候选评分 (add 时); 触发评分 (remove 时, 0=无)
    pool_size    INT,               -- 当时订阅池容量 (add/remove 后)
    rationale    VARCHAR            -- 触发细则, 如 "break_drop_pct=3.2, zaf=7.5%->4.1%"
) TIMESTAMP(event_time) PARTITION BY DAY
DEDUP UPSERT KEYS(event_time, code, action);
