# `_deprecated/empty_dirs/` — 历史空壳目录占位

按 CLAUDE.md § 二 业务目录纯英文规范：
- `2_compute/` 与 `2_kline/` 同名，命名冲突，且不含任何文件
- `3_verify/` 是 3 级验证流水线的空占位
- `config/mapping/` 是 config 子目录的空占位

3 个目录在 2026-07-05 整理批次 #17 删除（用 `rmdir`，空目录 git 不跟踪）。
DEPRECATED.md 占位文档保留在此处作历史标记。

## 取回方式

```bash
# 恢复单个空目录
mkdir 2_compute && touch 2_compute/.gitkeep
cp _deprecated/empty_dirs/2_compute_DEPRECATED.md 2_compute/DEPRECATED.md
```

## 新版对应物

- `2_compute/`（旧）→ 已删除，命名冲突消失（`2_kline/` 也已迁 `_deprecated/`）
- `3_verify/`（旧）→ 已删除，无对应物
- `config/mapping/`（旧）→ 已删除，无对应物

CLAUDE.md § 三.4 命名约定：业务目录禁止数字前缀、无意义的子目录。