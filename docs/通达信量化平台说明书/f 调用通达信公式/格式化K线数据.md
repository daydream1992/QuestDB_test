# 格式化 K 线数据 formula_format_data

格式化 `get_market_data` 获取的 K 线数据。

> `get_market_data` 获取的 K 线数据不能直接用于设置公式参数，须先调用 `formula_format_data` 进行格式化。

## 函数签名

```python
formula_format_data(data_dict: Dict = {})
```

## 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
|------|----------|----------|----------|
| `data_dict` | Y | `Dict` | `get_market_data` 获取格式的 K 线 Dict |

## 返回值说明

`formula_format_data` 返回值为 `List[Dict]`，其中 Dict 的 Key 须有 `["Amount", "Volume", "Close", "Open", "High", "Low"]`，用户可以直接提供符合条件的 List 提供给 `tdx_formula_set_data`。

## 接口使用

```python
from tqcenter import tq

tq.initialize(__file__)

test_md = tq.get_market_data(stock_list=['688318.SH'], count=5, period='1d')
format_md = tq.formula_format_data(test_md)
print(format_md)
```

## 数据样本

```python
{'688318.SH': [
 {'Date': '2026-01-20 00:00:00', 'Amount': 33930.29, 'Volume': 2345401.0, 'Close': 144.4, 'Open': 146.5, 'High': 146.98, 'Low': 142.65},
 {'Date': '2026-01-21 00:00:00', 'Amount': 35841.09, 'Volume': 2472760.0, 'Close': 144.77, 'Open': 144.49, 'High': 146.5, 'Low': 143.1},
 {'Date': '2026-01-22 00:00:00', 'Amount': 41598.79, 'Volume': 2878793.0, 'Close': 143.03, 'Open': 145.0, 'High': 147.0, 'Low': 142.5},
 {'Date': '2026-01-23 00:00:00', 'Amount': 47131.04, 'Volume': 3256538.0, 'Close': 144.39, 'Open': 142.58, 'High': 146.88, 'Low': 142.58},
 {'Date': '2026-01-26 00:00:00', 'Amount': 54141.73, 'Volume': 3761141.0, 'Close': 141.84, 'Open': 143.7, 'High': 146.77, 'Low': 141.8}]}
```