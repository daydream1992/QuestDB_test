# _deprecated/ — 冻结目录 · 禁止新增依赖

本目录是项目的"坟墓"。所有内容历史上曾经活跃，**已被新版取代**，但**未删除**——
万一新版失败可一键取回。

## 取回方式

```bash
# 例：恢复 1_collect/_probe_api.py
cp _deprecated/probes/_probe_api.py 1_collect/_probe_api.py
```

## 范围

| 子目录 | 来源 | 状态 |
|---|---|---|
| `probes/` | `1_collect/_probe_*.py`、`_smoke_test.py`、`_test_write.py` | 探测时代产物，业务无引用 |
| `markers/` | `tests/_h7_marker.txt` | H7 任务占位符，无业务含义 |

## 守卫规则

- 新代码**禁止** `import _deprecated.*` / `from _deprecated import ...`
- `runner/` / `collect/` / `compute/` / `strategy/` / `feishu/` 不得引用本目录
- 本目录的 commit 默认独立、单独 review
- 任何想"复活"本目录某个模块的请求，必须走 [plan] 评审，不能直接拷贝了事

## 取回历史

（每次取回在这里追加一行记录）

| 日期 | 文件 | 取回人 | 原因 |
|---|---|---|---|
