# `_deprecated/old_pipeline/1_collect/` — 旧版采集器

原 `1_collect/qd_NN_*.py` 系列脚本。已被新版 `collect/cN_*.py` 取代。

## 收录

| 原文件 | 状态 |
|---|---|
| `qd_00_full_snapshot.py` | 全场快照（旧版） |
| `qd_01_collect_88fields.py` | 88 字段采集 |
| `qd_01_pricevol.py` | 全场价量 |
| `_probe_api.py` / `_probe_fields.py` / `_probe_market.py` | 探针（真实副本在 `_deprecated/probes/`） |
| `_smoke_test.py` / `_test_write.py` | 烟雾测试 |
| `lib/DEPRECATED.md` | 历史标记（lib/ 是空目录） |

## 取回方式

```bash
# 单文件取回
cp _deprecated/old_pipeline/1_collect/qd_00_full_snapshot.py 1_collect/

# 或从 git 历史
git checkout <commit> -- 1_collect/qd_00_full_snapshot.py
```

## 新版对应物

- `collect/c1_pricevol.py` — 全场价量
- `collect/c2_snapshot.py` — 盘中快照
- `collect/c3_more_info.py` — 88 字段详情

旧版被 `e2e_legacy/mock.py` `e2e_legacy/real.py` `e2e_legacy/live_5min.py` 引用，作为旧版家族的依赖保留。
新代码禁止 `import _deprecated.*`。