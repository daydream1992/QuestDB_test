# `_deprecated/ddl_minimal/` — 探索期简化版 DDL

## 这是什么

`qd_ddl.sql` 是 2026-07-02 探索期的**简化版 DDL**，只取 `tq.get_more_info` 实际返回的字段，
共 8 张表（`qd_snapshots_full` / `qd_kline_1m` / `qd_kline_5m` / `qd_snapshots_realtime` /
`qd_indicators` / `qd_signals` / `qd_signal_log` / `qd_market_snapshot_full`）。

与项目正式 DDL（`ddl/00_registry.sql` ~ `ddl/17_stock_gpjy.sql`，**35 张表**）相比：
- 表数 8 vs 35（严重落后）
- 不被 `ddl/_reset_all.py` 引用（其 `DDL_FILES` 列表不包含此文件）
- 字段集合是 2026-07-02 探测期快照，不含后续 88 字段接入、C8 拆表、情绪三表、GP 表等增量

## 取回方式

```bash
cp _deprecated/ddl_minimal/qd_ddl.sql qd_ddl.sql
```

或从 git 历史：

```bash
git checkout <commit-before-removal> -- qd_ddl.sql
```

## 替代物

正式 DDL 体系请用 `ddl/` 目录：

- `ddl/_reset_all.py` — 一键重建（推荐入口）
- `ddl/00..17_*.sql` — 17 个分层 SQL（35 张表）

如需"最小化探索版"——`ddl/qd_ddl_minimal.sql` 仍保留在 `ddl/` 下作为 alias。