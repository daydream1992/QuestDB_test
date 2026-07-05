# live_5min.py 已迁移

`live_5min.py` 已迁移到 `_deprecated/e2e_legacy/live_5min.py`。

## 取回方式

```bash
cp _deprecated/e2e_legacy/live_5min.py live_5min.py
# 或
git checkout <commit> -- live_5min.py
```

## 这是什么

5 分钟连续采集（writer 3s / synth 30s / indic 60s / signal 30s）+ 飞书推送。
走旧版流水线（动态 importlib 加载 `2_kline/qd_02_*` 等）。

新版盘中持续采集请用根目录 `python runner/scheduler.py`（走 `compute/k1_indicators` 等）。

## 依赖

`live_5min.py` 依赖以下旧版脚本（**继续保留在原位**）：

- `2_kline/qd_02_synth_kline.py`
- `3_indicators/qd_03_indicators.py`
- `4_signals/qd_04_signal_lark.py`