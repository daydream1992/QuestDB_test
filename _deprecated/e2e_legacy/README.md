# _deprecated/e2e_legacy/ — 旧版端到端家族

本目录收录项目历史上的端到端脚本。它们都走**旧版流水线**
（`1_collect/qd_NN_*.py` → `2_kline/qd_02_*` → `3_indicators/qd_03_*` → `4_signals/qd_04_*`），
已被新版（`collect/cN_*.py` → `compute/kN_*.py` → `strategy/plugins/*` → `4_feishu/`）取代。

## 收录脚本

| 文件 | 原位置 | 数据源 | 说明 |
|---|---|---|---|
| `mock.py` | `e2e_mock.py` | 5 只 × 240 分钟 mock 价格 | 盘后演示用 |
| `real.py` | `e2e_real.py` | tqcenter 真实数据 | 真实数据 e2e（旧路径） |
| `live_5min.py` | `live_5min.py` | tqcenter 实时 100 只 tick | 5 分钟连续采集 |

## 新版对应物

- **新版端到端**：根目录 `e2e.py`（走 `collect/compute/strategy/4_feishu` 全套，HANDOVER §9 推荐）
- **新版盘中持续采集**：`python runner/scheduler.py`

## 取回方式

```bash
# 单文件取回（恢复原路径）
cp _deprecated/e2e_legacy/mock.py       e2e_mock.py
cp _deprecated/e2e_legacy/real.py       e2e_real.py
cp _deprecated/e2e_legacy/live_5min.py  live_5min.py

# 或从 git 历史取回（推荐）
git checkout <commit> -- e2e_mock.py e2e_real.py live_5min.py
```

## 注意

- 这 3 个脚本里的硬编码路径（如 `Path(r'K:\QuestDB_test\2_kline')`）**未改写**——
  取回后直接可跑（旧版脚本仍在原位，未被本整理影响）
- 旧版流水线脚本（`1_collect/qd_NN_*.py` 等）**不在本目录**，仍在原位，作为"被引用的依赖"保留
- 新代码禁止 `import _deprecated.e2e_legacy.*`