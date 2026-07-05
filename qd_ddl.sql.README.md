# qd_ddl.sql 已迁移

`qd_ddl.sql` 在 2026-07-05 整理中已正式收进 `ddl/qd_ddl_minimal.sql`。

## 当前状态

- 根目录 `qd_ddl.sql` 仍为 alias 副本（内容与 `ddl/qd_ddl_minimal.sql` 同步）
- 权威副本位于 `ddl/qd_ddl_minimal.sql`

## 为什么这样安排

- `ddl/_reset_all.py` 是项目正式的建表入口，列举 `00_registry.sql ~ 17_stock_gpjy.sql`，并不包含根目录 `qd_ddl.sql`
- 根目录 `qd_ddl.sql` 是 2026-07-02 期间的探索性简化版（只取 `tq.get_more_info` 实际返回字段）
- 把该文件正式收进 `ddl/` 后命名 `*_minimal.sql`，表明它是"最小化探索版本"，区别于 `00..17` 的系列号

## 如何使用

```bash
# 直接执行（不通过 ddl/_reset_all.py，因为重置脚本不包含此文件）
psql -h 127.0.0.1 -p 8812 -U admin -d qdb -f ddl/qd_ddl_minimal.sql

# 通过 Python 重置脚本（推荐主项目流程）
python ddl/_reset_all.py
```

## 取回方式

如需保留根目录 alias：

```bash
cp ddl/qd_ddl_minimal.sql qd_ddl.sql
```
