---
name: resonance-debug-lessons
description: resonance.py 共振模块调试经验：graph=None bug / 板块编码匹配 / 异常值过滤
metadata: 
  node_type: memory
  type: reference
  originSessionId: bdb7ec19-e905-4a5a-a79a-46c9d12f4a6e
---

# resonance.py 调试经验

## 问题现象

共振信号扫描时 `sector_code` 始终为 None，`sec%` 全为 0，高分信号异常。

## 根本原因

### 1. load_from_json() 无返回值

`lib/relation_graph.py` 的 `load_from_json()` **没有 return 语句**，只修改模块级全局变量。
但调用方写了 `graph = load_from_json()`，导致 `graph = None`。

```python
# lib/relation_graph.py
def load_from_json(json_dir=None):
    # ... 加载数据到全局变量 _stock_to_sectors ...
    logger.info('关系图谱加载完成...')  # 没有 return
```

调用方：
```python
graph = load_from_json()  # graph = None！
```

**解决**：不要传 graph 参数，直接调用 `get_stock_sectors()` 即可（它读全局变量）。

---

### 2. 板块编码体系

| 数据源 | 编码 | 类型 |
|--------|------|------|
| qd_sector_snapshot | 880xxx | 概念板块 |
| 关系图谱 concept | 880xxx | 概念板块 ✅ 可匹配 |
| 关系图谱 industry | 881xxx | 行业板块 ❌ 不匹配 |

**匹配优先级**：concept (880xxx) > industry (881xxx)

---

### 3. 异常涨跌幅

停牌/数据错误会导致涨跌幅异常（如 -100%），需要过滤：

```python
_MAX_CHANGE_THRESHOLD = 30.0  # 超过 ±30% 视为异常

def _safe_change(now, lastclose) -> float:
    change = (now - lastclose) / lastclose * 100
    if abs(change) > _MAX_CHANGE_THRESHOLD:
        return 0.0
    return change
```

---

### 4. 数据不足不给高分

共振需要至少 2 层有效数据：

```python
valid_chgs = [x for x in (mkt_chg, sec_chg, stk_chg) if x != 0]
if len(valid_chgs) < 2:
    return 0.0
```

---

## 调试技巧

1. **先单独测试函数**：不要一次跑整个 pipeline，先单独测 `get_stock_sectors()`
2. **检查返回值**：Python 函数没有 return 默认为 None
3. **检查 DataFrame 内容**：`df['code'].values` vs `df['code'].tolist()` 可能不同
4. **打印关键变量**：在循环中打印前几条确认数据流向

---

## 修复清单

1. ✅ 添加 `_MAX_CHANGE_THRESHOLD` 过滤异常涨跌幅
2. ✅ 添加 `sector_df` 参数支持板块数据查询
3. ✅ 移除 `graph` 参数，直接调用 `get_stock_sectors()`
4. ✅ 优先匹配 concept (880xxx)，回退 industry (881xxx)
5. ✅ 数据不足 2 层不给高分

**Why:** 诊断过程：从 symptom（sector_code=None）追踪到 data flow（load_from_json 无返回值），而不是直接改代码。

**How to apply:** 改 resonance 相关代码时，先单独测试 `get_stock_sectors()` 和 `_sector_change()` 是否返回正确值。
