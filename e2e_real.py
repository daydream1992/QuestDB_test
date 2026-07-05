# e2e_real.py 已迁移

`e2e_real.py` 已迁移到 `_deprecated/e2e_legacy/real.py`。

## 取回方式

```bash
cp _deprecated/e2e_legacy/real.py e2e_real.py
# 或
git checkout <commit> -- e2e_real.py
```

## 依赖关系（取回时无需恢复其他文件）

`e2e_real.py` 走真实 tqcenter 数据 + 旧版流水线。依赖以下文件（**这些文件继续保留在原位**）：

- `3_indicators/qd_03_indicators.py`
- `4_signals/qd_04_signal_lark.py`

新版端到端（真实数据）请用根目录 `e2e.py`。