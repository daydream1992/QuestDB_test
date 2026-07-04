# 获取K线行情 get_market_data

根据股票，获取历史行情。

## 函数签名

```python
get_market_data(field_list:    List[str] = [],
                stock_list:    List[str] = [],
                period:        str = '',
                start_time:    str = '',
                end_time:      str = '',
                count:         int = -1,
                dividend_type: Optional[str] = None,
                fill_data:     bool = True) -> Dict
```

## 输入参数

| 参数 | 是否必选 | 参数类型 | 参数说明 |
|------|----------|----------|----------|
| `field_list` | 否 | `List[str]` | 字段筛选，传空则返回全部 |
| `stock_list` | 是 | `List[str]` | 证券代码列表 |
| `period` | 是 | `str` | 周期 |
| `start_time` | 否 | `str` | 起始时间 |
| `end_time` | 否 | `str` | 结束时间 |
| `count` | 否 | `int` | 返回数据个数（每只股票） |
| `dividend_type` | 否 | `str` | 复权类型：`none` 不复权、`front` 前复权、`back` 后复权 |
| `fill_data` | 否 | `bool` | 是否向后填充空缺数据 |

## count 参数说明

**count ≤ 0 或 count 为空时：**

| 情况 | 说明 |
|------|------|
| 有开始日期和结束日期 | 取开始日期到结束日期的数据 |
| 有开始日期，无结束日期 | 从开始日期取到最后一根 K 线 |
| 有结束日期，无开始日期 | 从第一根 K 线取到结束日期 |
| 都无值 | 取全部本地数据 |

**count > 0 时：**

| 情况 | 说明 |
|------|------|
| 有结束日期 | 从结束日期往前取 n 个数据 |
| 无结束日期 | 从最后一根 K 线往前取 n 个数据 |

## 返回数据

返回 `dict`，格式为 `{ field1: value1, field2: value2, ... }`：

- `field1, field2, ...`：数据字段
- `value1, value2, ...`：`pd.DataFrame` 数据集，index 为 stock_list，columns 为 time_list
- 各字段对应的 DataFrame 维度相同、索引相同

### 返回字段

| 字段 | 默认返回 | 数据类型 | 说明 |
|------|----------|----------|------|
| `Date` | 是 | `str` | 日期 |
| `Time` | 是 | `str` | 时间 |
| `Open` | 是 | `str` | 开盘价 |
| `High` | 是 | `str` | 最高价 |
| `Low` | 是 | `str` | 最低价 |
| `Close` | 是 | `str` | 收盘价 |
| `Volume` | 是 | `str` | 成交量 |
| `Amount` | 是 | `str` | 成交额 |
| `ForwardFactor` | 是 | `str` | 前复权因子，当 `dividend_type=none` 时返回有效值 |
| `VolInStock` | 否 | `str` | 持仓量 |

### 注意事项

- 只有 `dividend_type` 传入为 `none` 时，会返回有效的前复权因子 `ForwardFactor`。
- 后复权数据与取的数据个数有关，只在返回的数据中进行后复权。
- 一次最多返回 24000 条数据，要获取完整分钟线需要多次分批获取。
- 返回复权数据时，若该组数据时间内未发生权息变动，则复权价与未复权价相同。
- 期货数据时 `Amount` 为 0，非期货数据时 `VolInStock` 为 0。

## 接口使用

获取 688318.SH 从 2025-12-20 到今为止最新一条日 K 线的不复权数据：

```python
from tqcenter import tq

tq.initialize(__file__)

df = tq.get_market_data(
    field_list=[],
    stock_list=['688318.SH'],
    start_time='20251220',
    end_time='',
    count=1,
    dividend_type='none',
    period='1d',
    fill_data=True
)
print(df)
```

## 数据样本

```python
{'Amount':             688318.SH
 2025-12-24   29394.81,
 'Low':             688318.SH
 2025-12-24      128.0,
 'Date':              688318.SH
 2025-12-24  20251224.0,
 'Volume':             688318.SH
 2025-12-24  2257325.0,
 'Close':             688318.SH
 2025-12-24     131.58,
 'Open':             688318.SH
 2025-12-24     128.01,
 'Time':             688318.SH
 2025-12-24        0.0,
 'High':             688318.SH
 2025-12-24     131.87,
 'ForwardFactor':             688318.SH
 2025-12-24        1.0}
```
