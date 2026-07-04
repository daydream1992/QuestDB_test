# _smoke_test.py 已迁移到这里

此文件已不再活跃。原位置 `1_collect/_smoke_test.py` 仅作历史保留。

## 注意事项

本探针依赖同级的 `qd_01_collect_88fields.py`（也属于旧版采集器）。
恢复运行时需要**同时**把以下文件搬回原位：

```bash
cp _deprecated/probes/_smoke_test.py 1_collect/_smoke_test.py
# qd_01_collect_88fields.py 也必须就位
```

业务代码无任何引用。
