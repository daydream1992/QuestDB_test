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
| `p02,p05,p06,p07,p09-p16,p18,p20-p27` (根级 `pNN_*.py`) | `strategy/plugins/` | 2026-07-14 瘦身废弃：被吃肉系统 k6/k7/p28 + `risk.check_exit`(p15/p16) 取代 |
| `dark_money.py` | `strategy/` | 2026-07-14 废弃：个股明暗资金计算，`qd_money_flow` 已停写（p08 插件保留休眠） |
| `portfolio.py` | `strategy/` | 2026-07-14 废弃：持仓持久化，现由 `risk.py` 内存 positions + `check_exit` 承担 |
| `factor_store.py` | `compute/` | 2026-07-14 废弃：alpha 落库，`qd_alpha_score` 成休眠表（alpha_engine 纯内存保留） |

## 守卫规则

- 新代码**禁止** `import _deprecated.*` / `from _deprecated import ...`
- `runner/` / `collect/` / `compute/` / `strategy/` / `feishu/` 不得引用本目录
- 本目录的 commit 默认独立、单独 review
- 任何想"复活"本目录某个模块的请求，必须走 [plan] 评审，不能直接拷贝了事

## 取回历史

（每次取回在这里追加一行记录）

| 日期 | 文件 | 取回人 | 原因 |
|---|---|---|---|
