# `qd_ddl.sql` — 已迁移

此文件在 2026-07-05 整理中已迁移至 `_deprecated/ddl_minimal/qd_ddl.sql`。

## 为什么迁

- `qd_ddl.sql` 是 2026-07-02 探索期简化版（8 张表）
- 与正式 DDL（`ddl/00..17_*.sql`，**35 张表**）严重重复
- `ddl/_reset_all.py` 不引用此文件
- 根目录应是业务入口（`e2e.py` `INDEX.md`），不该放 DDL

## 新位置

```
_deprecated/ddl_minimal/qd_ddl.sql
```

## 如何使用正式 DDL

```bash
# 正式一键重建（推荐）
python ddl/_reset_all.py

# 或最小化 alias
psql -h 127.0.0.1 -p 8812 -U admin -d qdb -f ddl/qd_ddl_minimal.sql
```

## 取回方式

```bash
cp _deprecated/ddl_minimal/qd_ddl.sql qd_ddl.sql
```

详见 `_deprecated/ddl_minimal/README.md`。