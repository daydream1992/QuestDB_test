---
name: strategy-params-tuning-guide
description: 策略模块参数调参指南：selector/resonance/big_order/dark_money/sector_flow/lhb/p06
metadata: 
  node_type: memory
  type: reference
  originSessionId: bdb7ec19-e905-4a5a-a79a-46c9d12f4a6e
---

# 策略模块参数调参指南

## 1. selector.py 选股器

### 核心参数

| 参数 | 当前值 | 含义 | 调参建议 |
|------|--------|------|----------|
| `TOP_N` | 100 | 各维度筛选前 N 只 | 增大→池子大信号多；减小→聚焦热门 |
| `NEAR_ZT_THRESHOLD` | 0.01 (1%) | 接近涨停阈值 | 0.5%~2% |

### 筛选维度

| 维度 | 字段 | 逻辑 |
|------|------|------|
| 涨幅前 N | `Now/LastClose - 1` | 涨幅排名 |
| 量比前 N | `Volume` | 成交量排名（无均量，用绝对量代理） |
| 换手前 N | `fHSL` | 换手率排名 |
| 连板梯队 | `fLianB > 0` | 只要有连板 |
| 接近涨停 | `(ZTPrice - Now) / ZTPrice < 1%` | 距涨停价 1% 以内 |

### 调参场景

```python
# 激进模式：扩大池子
TOP_N = 150
NEAR_ZT_THRESHOLD = 0.02  # 2%

# 保守模式：聚焦最强
TOP_N = 50
NEAR_ZT_THRESHOLD = 0.005  # 0.5%
```

---

## 2. resonance.py 共振模块

### 核心参数

| 参数 | 当前值 | 含义 | 调参建议 |
|------|--------|------|----------|
| `_MAX_CHANGE_THRESHOLD` | 30.0 (%) | 异常涨跌幅过滤阈值 | 超过 ±30% 视为异常/停牌 |
| `_DIVERGENCE_THRESHOLD` | 1.0 (%) | 背离触发阈值 | 涨跌幅超过 1% 才判背离 |
| 大盘指数 | `000001.SH` | 上证指数 | 可改为深证/创业板 |

### 共振分数计算

```
三层同涨 → 90+
两层涨 → 60
三层同跌 → 10
两层跌 → 25
其余 → 40

加分 = 平均幅度 × 2
最高 100 分
```

### 板块匹配优先级

1. **概念板块 (880xxx)** ← 优先，与 qd_sector_snapshot 匹配
2. **行业板块 (881xxx)** ← 回退，可能不匹配
3. **首个板块** ← 最终回退

### 调参场景

```python
# 严格模式
_MAX_CHANGE_THRESHOLD = 20.0  # 更严格过滤
_DIVERGENCE_THRESHOLD = 2.0   # 需要更大涨跌幅才判背离

# 宽松模式
_MAX_CHANGE_THRESHOLD = 50.0
_DIVERGENCE_THRESHOLD = 0.5
```

---

## 3. big_order.py 大单监控

### 核心参数

| 参数 | 当前值 | 含义 | 调参建议 |
|------|--------|------|----------|
| `BIG_THRESHOLD` | 1,000,000 (100万) | 大单阈值 | 增大→过滤小单 |
| `HUGE_THRESHOLD` | 5,000,000 (500万) | 巨单阈值 | - |
| `SUPER_THRESHOLD` | 10,000,000 (1000万) | 超大单阈值 | - |

### 方向判定逻辑

```
价格↑ + Zjl > 0 → 主动买入
价格↓ + Zjl < 0 → 主动卖出
其余 → 中性
```

### 调参场景

```python
# 保守：只关注大单
BIG_THRESHOLD = 5_000_000      # 500万起步
HUGE_THRESHOLD = 20_000_000    # 2000万
SUPER_THRESHOLD = 50_000_000   # 5000万

# 激进：连小单也关注
BIG_THRESHOLD = 500_000        # 50万
HUGE_THRESHOLD = 2_000_000     # 200万
SUPER_THRESHOLD = 5_000_000    # 500万
```

---

## 4. dark_money.py 明暗资金

### 核心参数（权重）

| 参数 | 当前值 | 含义 | 调参建议 |
|------|--------|------|----------|
| `_W_CANCEL` | 0.3 | 撤单差分权重 | 暗资金敏感度 |
| `_W_IMBALANCE` | 0.001 | 订单不平衡权重 | 5档买卖量差 |
| `_W_WTB_FCAMO` | 0.01 | 委托买卖比×大单成交额权重 | 综合资金流 |

### 计算公式

```
net_flow = zjl
          + cancel_diff × 0.3
          + order_imbalance × 0.001
          + wtb × fcamo × 0.01
```

其中：
- `order_imbalance = ΣBuyv1-5 - ΣSellv1-5`（5档委买-委卖量）
- `buy_pressure = ΣBuyp×Buyv`（加权委买压力）
- `sell_pressure = ΣSellp×Sellv`（加权委卖压力）

### 调参场景

```python
# 重视主力资金
_W_CANCEL = 0.5      # 撤单权重提高
_W_IMBALANCE = 0.002
_W_WTB_FCAMO = 0.02

# 重视订单流
_W_CANCEL = 0.1
_W_IMBALANCE = 0.005
_W_WTB_FCAMO = 0.005
```

---

## 5. sector_flow.py 板块资金流

### calc_sector_flow 输出

| 字段 | 含义 |
|------|------|
| `stock_count` | 板块内股票数 |
| `total_amount` | 总成交额 |
| `net_flow` | 主力净流入 |
| `up/down/flat_count` | 涨跌平家数 |
| `avg_change` | 均涨幅 |
| `flow_strength` | 强度 = net_flow / total_amount |

### detect_rotation 轮动检测

| type | 条件 |
|------|------|
| `inflow_accelerate` | 净流入 + delta > 0 |
| `outflow_accelerate` | 净流出 + delta < 0 |
| `inflow_decelerate` | 净流入 + delta < 0 |
| `outflow_decelerate` | 净流出 + delta > 0 |
| `stable` | delta = 0 |

### 调参场景

```python
# 判断板块强度（无直接参数，通过阈值筛选）
flow_strength > 0.1  # 流入占比 > 10%
flow_strength < -0.1  # 流出占比 > 10%
```

---

## 6. lhb_analyzer.py 龙虎榜

### 席位分类

| type | 含义 |
|------|------|
| `hot_money` | 游资席位 |
| `institution` | 机构席位 |
| `north` | 北向席位 |

### 调参（需修改 config/broker_list.py）

```python
# config/broker_list.py
FAMOUS_BROKERS = {
    '拉萨团结路': 'hot_money_1',
    '机构专用': 'institution',
    '深股通': 'north',
    # 添加更多席位...
}
```

### 回看窗口

| 参数 | 当前值 | 含义 |
|------|--------|------|
| `days` | 3 | 回看天数（容错周末/节假日） |

---

## 7. p06_resonance.py 共振三重策略

### 核心参数

| 参数 | 当前值 | 含义 | 调参建议 |
|------|--------|------|----------|
| `_SCORE_MIN` | 70.0 | 共振总分阈值 | 提高→更严格 |
| `_STOCK_CHANGE_MIN` | 2.0 (%) | 个股涨幅阈值 | 提高→只做强势 |

### 决策参数

| 参数 | 当前值 | 含义 |
|------|--------|------|
| `position_pct` | 10 | 建议仓位 10% |
| `stop_loss` | 5 | 止损 5% |
| `stop_profit` | 10 | 止盈 10% |

### 调参场景

```python
# 激进
_SCORE_MIN = 60.0
_STOCK_CHANGE_MIN = 1.0
position_pct = 15
stop_loss = 7

# 保守
_SCORE_MIN = 80.0
_STOCK_CHANGE_MIN = 3.0
position_pct = 5
stop_loss = 3
```

---

## 快速调参对照表

| 场景 | selector TOP_N | resonance 分数 | big_order 阈值 | p06 仓位 |
|------|---------------|---------------|----------------|----------|
| 激进 | 150 | 60 | 50万 | 15% |
| 均衡 | 100 | 70 | 100万 | 10% |
| 保守 | 50 | 80 | 500万 | 5% |

---

## 调试技巧

1. **先单独测试函数**，不要一次跑整个 pipeline
2. **打印中间变量**，确认数据流向
3. **用 limit 限制数据量**，加快测试速度
4. **对比调参前后的信号数量**，评估影响
