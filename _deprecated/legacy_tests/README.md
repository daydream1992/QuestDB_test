# `_deprecated/legacy_tests/` — 历史测试脚本

下划线开头 (`_*`) 的测试文件被归档到这里，符合 CLAUDE.md § 三.4 命名约定。

## 收录

| 文件 | 原位置 | 状态 |
|---|---|---|
| `_verify_rw_consistency.py` | `tests/_verify_rw_consistency.py` | 底座验证 #1（读写一致性 + O3 延迟），H5 修复回归 |
| `test_stress_rw.py` | `tests/test_stress_rw.py` | QuestDB 读写并发压测；0 引用，且依赖已删的 `qd_snapshots_realtime` 表（2026-07-05 随 qd_ddl_minimal.sql 删除） |
| `test_3proc_2rounds.py` | `tests/test_3proc_2rounds.py` | test_3proc.py 的两轮对比版，功能重复 |
| `_h7_marker.txt` | `tests/_h7_marker.txt` | H7 任务占位符（非 .py），自述不活跃、0 引用 |

## 取回方式

```bash
cp _deprecated/legacy_tests/_verify_rw_consistency.py tests/_verify_rw_consistency.py
# 或
git checkout <commit> -- tests/_verify_rw_consistency.py
```

## 为什么归档

下划线开头的 Python 文件按 Python 习惯是"私有/内部"标识——但 `tests/` 是公共验证目录，命名冲突。
归档到 `_deprecated/legacy_tests/` 让 tests/ 只保留公共 `test_*.py` 命名。