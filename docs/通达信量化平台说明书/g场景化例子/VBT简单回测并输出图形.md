# VBT 简单回测并输出图形

使用 vectorbt 库对股票进行 MA 均线策略回测，并输出交互式图形。

> **注意**：目前调用的 vectorbt 三方库函数 `vbt.Portfolio.from_signals` 不支持分红送股等权益变动，该 demo 仅做示例。

## 前置安装

```bash
pip install vectorbt -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install plotly
```

## 完整代码示例

```python
import pandas as pd
import vectorbt as vbt
from tqcenter import tq
from datetime import datetime

tq.initialize(__file__)

# 解决 pandas future warning
pd.set_option('future.no_silent_downcasting', True)
pd.set_option('display.float_format', lambda x: f"{x:.10f}".rstrip('0').rstrip('.') if '.' in f"{x:.10f}" else f"{x}")

# ========================= 核心配置（用户可直接修改这里）=========================
target_start = '20250701'  # 【目标回测开始时间】（真正想回测的起始日）
target_end = '20251231'    # 【目标回测结束时间】
stock_code_list = ['688318.SH']     # 股票代码
window = 5         # MA 指标周期（如 MA5、MA10、MA20，改这里自动适配历史数据）
# ==========================================================
start_time = (pd.to_datetime(target_start) - pd.Timedelta(days=window + 10)).strftime('%Y%m%d')

# 1. 获取价格数据
df_real = tq.get_market_data(
    field_list=['Close', 'Open'],
    stock_list=stock_code_list,
    start_time=start_time,
    end_time=target_end,
    dividend_type='front',
    period='1d',
    fill_data=True
)
close_df = tq.price_df(df_real, 'Close', column_names=stock_code_list)
open_df = tq.price_df(df_real, 'Open', column_names=stock_code_list)

# 2. 买卖信号计算与生成
ma5_dynamic = vbt.MA.run(close_df, window=window).ma.ffill()
ma5_dynamic.columns = close_df.columns

entries_df = close_df.vbt.crossed_above(ma5_dynamic).shift(1).fillna(False).astype(bool)
exits_df = close_df.vbt.crossed_below(ma5_dynamic).shift(1).fillna(False).astype(bool)

print(f"\n信号生成统计:")
print(f"买入信号总数: {entries_df.sum().sum()}")
print(f"卖出信号总数: {exits_df.sum().sum()}")

# 3. 执行回测
portfolio = vbt.Portfolio.from_signals(
    close=close_df,             # 净值计算用未复权收盘价
    entries=entries_df,         # 延迟后的买入信号
    exits=exits_df,             # 延迟后的卖出信号
    price=open_df,              # 含滑点的成交价格
    init_cash=100000,           # 初始资金 10 万元
    fees=0.0003,                # 手续费 0.03%（双边）
    freq='D',                   # 日线频率
    size_granularity=100        # A 股最小交易单位 100 股
)

# ========== vbt 绘图 ==========
portfolio[stock_code_list[0]].plot().show()

# 4. 输出回测结果
print(f"\n" + "="*60)
print(f"投资组合回测表现")
print("="*60)
stats_df = portfolio.stats()
print(stats_df)

print(f"\n" + "="*60)
print(f"投资组合回测记录")
print("="*60)
trades_df_original = portfolio.trades.records_readable.copy()
print(trades_df_original.to_string())
```

## 功能说明

| 步骤 | 说明 |
|------|------|
| 1 | 获取股票历史日 K 线数据（前复权），多取 `window + 10` 天用于计算均线 |
| 2 | 计算 MA 均线，收盘价上穿均线买入，下穿均线卖出（信号延迟一天，防止未来函数） |
| 3 | 使用 `vbt.Portfolio.from_signals` 执行回测 |
| 4 | 输出回测统计指标和交易记录，并显示交互式图形 |

## 核心配置参数

| 参数 | 说明 |
|------|------|
| `target_start` | 目标回测开始时间 |
| `target_end` | 目标回测结束时间 |
| `stock_code_list` | 股票代码列表 |
| `window` | MA 指标周期（如 5、10、20） |
| `init_cash` | 初始资金（默认 10 万元） |
| `fees` | 手续费率（默认 0.03% 双边） |

## 涉及 API

| API | 用途 |
|-----|------|
| `tq.initialize(__file__)` | 初始化通达信量化平台 |
| `tq.get_market_data()` | 获取历史 K 线数据 |
| `tq.price_df()` | 将行情数据转为 DataFrame 格式 |