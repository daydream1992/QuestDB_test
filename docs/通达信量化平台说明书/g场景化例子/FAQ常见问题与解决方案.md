# FAQ：常见问题与解决方案

---

## Q：为什么同一个选股公式，用 `formula_process_mul_xg` 选股的结果比客户端条件选股中得到的结果少？

**A：** 请确认 `formula_process_mul_xg` 中的 `count` 参数是否合理？数据个数要满足公式计算中的数据要求。客户端的条件选股中使用了所有的本地数据。

---

## Q：如何选出分钟内主力净额排名靠前的股票？

**A：** 可以用一定时间间隔获取主力净额输出值，然后用这次值减上次值的差额排序筛选全市场找出来。

### ZLJE 自定义指标公式

```python
{ZLJE 自定义指标}
超B:=L2_AMO(0,0)/10000.0;
大B:=L2_AMO(1,0)/10000.0;
中B:=L2_AMO(2,0)/10000.0;
小B:=L2_AMO(3,0)/10000.0;
超S:=L2_AMO(0,1)/10000.0;
大S:=L2_AMO(1,1)/10000.0;
中S:=L2_AMO(2,1)/10000.0;
小S:=L2_AMO(3,1)/10000.0;
主力净额:(超B+大B)-(超S+大S),NODRAW;
```

### 实现示例完整代码

```python
import sys
import time

sys.path.append('C:/new_tdx_test2025/PYPlugins/user')
from tqcenter import tq

tq.initialize('0303zlje.py')

# 先获取A股全部股票
all_stocks = tq.get_stock_list(market='5')[:100]
# all_stocks=['300911.SZ', '600635.SH', '000890.SZ', '603155.SH', '301448.SZ', '600010.SH', '600011.SH', '600012.SH', '600013.SH', '600014.SH']
print("正在处理，请等待...")
start_date = '20240601'
end_date = '20240630'

# 开始计时
start_time = time.time()

macd_stocks = []
pre_mul_zb_result = {}
mul_zb_result = {}
curr_val = 0
countjs = 1
pre_val = 0
ce_val = 0
# 添加最大循环次数限制，防止无限循环
max_iterations = 10  # 设置最大迭代次数

while countjs <= max_iterations:
    # 保存之前的值
    pre_mul_zb_result = mul_zb_result.copy()  # 使用copy()避免引用问题
    
    # 获取新的值
    mul_zb_result = tq.formula_process_mul_zb(
        formula_name='ZLJE',
        formula_arg='',
        xsflag=6,
        return_count=2,
        return_date=True,
        stock_list=all_stocks,
        stock_period='1d',
        count=-1,
        start_time=start_date,
        end_time=end_date,
        dividend_type=1
    )
    
    print("当前结果:", mul_zb_result)
    print("前一结果:", pre_mul_zb_result)
    
    countjs += 1
    
    # 检查是否有有效的数据
    if mul_zb_result and countjs >= 2:  # 至少需要两次才能比较
        diff_list = []
        for key in mul_zb_result:
            if key != "ErrorId":
                # 安全检查
                if (key in mul_zb_result and 
                    '主力净额' in mul_zb_result[key] and 
                    len(mul_zb_result[key]['主力净额']) >= 1 and 
                    key in pre_mul_zb_result and 
                    '主力净额' in pre_mul_zb_result[key] and 
                    len(pre_mul_zb_result[key]['主力净额']) >= 1):
                    
                    curr_val = mul_zb_result[key]['主力净额'][-1]['Value']
                    pre_val = pre_mul_zb_result[key]['主力净额'][-1]['Value']
                    ce_val = float(curr_val) - float(pre_val)
                    diff_list.append((key, ce_val))
                    
                    print(f"股票 {key}: 当前值={curr_val}, 前值={pre_val}, 差值={ce_val}")
        # 按差值从大到小排序，输出前5名
        if diff_list:
            diff_list.sort(key=lambda x: x[1], reverse=True)
            print("主力净额变化前5名:")
            for i, (code, diff) in enumerate(diff_list[:5], 1):
                print(f"{i}. {code}: {diff:.2f}")
        else:
            print("无有效差值数据")
    
    # 等待一段时间再下一次循环
    time.sleep(180)

print("处理完成")
```

### 策略逻辑说明

| 步骤 | 说明 |
|------|------|
| 定义指标 | 通过 L2_AMO 函数计算主力净额 = (超B+大B) - (超S+大S) |
| 定时获取 | 每 3 分钟通过 `formula_process_mul_zb` 获取主力净额最新值 |
| 差值计算 | 用当前值减上一次值，得到主力资金流入/流出的变化量 |
| 排序筛选 | 按差值从大到小排序，输出前 5 名主力净额变化最大的股票 |
| 循环执行 | 最多执行 10 次循环，可按需调整 `max_iterations` 和 `time.sleep` 间隔 |

---

## Q：客户端日线前复权数据显示与 Python 获取结果不一致？

**A：** 你要获取到全部 K 线信息下的某日前复权数据，必须取全部数据（`count=-1`）。

```python
df = tq.get_market_data(
    field_list=['High', 'Low', 'Open', 'Close'],
    stock_list=['000656.SZ'],
    start_time='',
    end_time='',
    count=-1,
    dividend_type='front',
    period='1d',
)

print(df['High'].loc['2002-04-02'])
print(df['Open'].loc['2002-04-02'])
print(df['Low'].loc['2002-04-02'])
print(df['Close'].loc['2002-04-02'])
```

> 取全数据信息后（`count=-1`）才能和客户端全部 K 线加载下的前复权数据对比。

---

## Q：TQ 打开个股详情页的功能调用怎么写？

**A：** 以下是两种进入个股详情页的方式：

```python
from tqcenter import tq

tq.initialize(__file__)

# 方式一：breed_方式（可指定市场，1#沪市 0#深市 2#京市）
exec_res1 = tq.exec_to_tdx(url='http://www.treeid/breed_1#688318')
print(exec_res1)

# 方式二：code_方式（直接传入代码）
exec_res2 = tq.exec_to_tdx(url='http://www.treeid/code_688318')
print(exec_res2)
```

| 方式 | 格式 | 说明 |
|------|------|------|
| `breed_` | `http://www.treeid/breed_市场#代码` | 可指定市场：`1#` 沪市 `0#` 深市 `2#` 京市 |
| `code_` | `http://www.treeid/code_代码` | 直接传入代码，自动识别市场 |