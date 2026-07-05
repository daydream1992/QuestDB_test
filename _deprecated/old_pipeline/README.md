# `_deprecated/old_pipeline/{2_kline,3_indicators,4_signals}/`

旧版流水线目录（已被新版 compute/ 取代）。

## 收录

| 原目录 | 文件 | 新版对应物 |
|---|---|---|
| `2_kline/qd_02_synth_kline.py` | 盘中 K 线合成 | `compute/k5_kline_synth.py` |
| `3_indicators/qd_03_indicators.py` | MACD/BOLL 指标 | `compute/k1_indicators.py` |
| `4_signals/qd_04_signal_lark.py` | 信号检测 + 飞书 | `compute/k2_signals.py` + `feishu/` |

每个目录下还有 `logs/<module>_YYYYMMDD.log`（运行时日志快照）。

## 取回方式

```bash
cp _deprecated/old_pipeline/2_kline/qd_02_synth_kline.py 2_kline/
# 或从 git
git checkout <commit> -- 2_kline/qd_02_synth_kline.py
```

## 业务引用

- 旧版 `qd_NN_*.py` 被 `e2e_legacy/{mock,real,live_5min}.py` 引用（已迁 `_deprecated/e2e_legacy/`）
- 业务代码 0 引用
- 新代码禁止 `import _deprecated.*`