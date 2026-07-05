# e2e_mock.py 已迁移

`e2e_mock.py` 已迁移到 `_deprecated/e2e_legacy/mock.py`。

## 取回方式

```bash
# 单文件取回
cp _deprecated/e2e_legacy/mock.py e2e_mock.py

# 或从 git 历史取回（推荐，可指定历史 commit）
git checkout <commit> -- e2e_mock.py
```

## 为什么不删

按 2026-07-05 整理约定："不删除，走 _deprecated"——万一需要回归 mock 端到端
演示（含 5 只股票 240 分钟 mock 数据 → qd_snapshots_realtime → K 线合成 → 指标 → 信号 → 飞书），
可一键取回。

## 依赖关系（取回时需要同时恢复）

`e2e_mock.py` 走旧版流水线，依赖以下文件（**这些文件继续保留在原位**，无需取回）：

- `1_collect/qd_00_full_snapshot.py` / `qd_01_collect_88fields.py` / `qd_01_pricevol.py`
- `2_kline/qd_02_synth_kline.py`
- `3_indicators/qd_03_indicators.py`
- `4_signals/qd_04_signal_lark.py`

新版端到端请用根目录 `e2e.py`（走 `collect/compute/strategy/4_feishu` 全套）。