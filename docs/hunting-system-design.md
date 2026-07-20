# 板块联动吃肉系统 (2026-07-14 实现)

> 从"板块联动分析"进化到"强势板块聚焦 × 票型分类 × 4种信号"的吃肉系统。
> 核心: 不推已涨停龙头(买不进), 推可操作的启动初期/补涨/低吸。
>
> **2026-07-14 落地现状**: p27 已移 `_deprecated/`（板块联动观察改由 k6 直写 qd_sector_linkage + 飞书 bitable 承接），p28 是唯一吃肉信号出口；k6/k7/p28 已由 intraday_loop 60s 块接通。

## 一、架构 (3层信号链)

```
k6 板块联动 (磨刀/筛猎场)          → ctx.linkage_scores / linkage_metrics
     ↓ 60s/轮
k7 票型分类 (情绪票/趋势票/混合)    → ctx.stock_types
     ↓ 60s/轮, 只对强势板块成分股
p28 信号检测 (打猎/抓买点)          → 4种信号 × 2票型差异化阈值
     ↓ 板块聚焦过滤
分层输出: 🔴buy(立即行动) / 🟡observe(关注埋伏)
```

## 二、文件清单

### 新增
| 文件 | 用途 |
|------|------|
| `compute/k7_stock_type.py` | 票型分类器 (情绪/趋势/混合) + 概念热度 |
| `strategy/plugins/p27_sector_linkage.py` | ~~板块联动观察名单~~ (2026-07-14 废弃, 由 k6 + bitable 取代) |
| `strategy/plugins/p28_sector_focus_signal.py` | **吃肉主模块**: 4种信号检测 |
| `ddl/26_sector_linkage.sql` | 板块联动表 |
| `tests/test_stock_type.py` | 票型分类测试 |
| `tests/test_signal_chain.py` | 端到端信号链测试 |
| `tests/validate_linkage_concept.py` | 板块联动验证 (历史数据) |

### 修改
| 文件 | 改动 |
|------|------|
| `compute/k6_linkage.py` | 动态阈值(P90/P15) + 板块规模分层 + **单位修复(万元)** |
| `runner/intraday_loop.py` | 集成 k6→k7, ctx 挂 stock_types/linkage_* |
| `feishu/bitable_writer.py` | write_linkage_row + 飞书API超时5s + 板块中文名 |
| `config/strategies.yaml` | 新增 linkage/stock_type/stock_signal 配置块 |

## 三、4种信号 × 票型差异化阈值

⚠️ **情绪票和趋势票阈值完全不同** (用户核心要求)

| 信号 | 情绪票(激进/游资) | 趋势票(稳健/机构) | 输出 |
|------|------------------|------------------|------|
| **surge急涨** | 5min≥3% 快进快出 | 5min≥1.5% 趋势确认 | buy |
| **补涨埋伏** | 板块涨停≥3家+自身<3% | 板块强+自身<2% | observe |
| **龙头低吸** | 回踩5日线+涨幅<5% | 回踩5日线+主力回流 | observe |
| **大单跟单** | Zjl>1000万(游资) | Zjl>5000万(机构) | buy(sentiment)/observe(trend) |

## 四、票型分类维度 (k7)

| 维度 | 情绪票 | 趋势票 | 字段 | 单位 |
|------|--------|--------|------|------|
| 流通市值 | <100亿 | >200亿 | Ltsz | **亿元** |
| 换手率 | >8% | 2-8% | fHSL | % |
| 量比 | >2 | <2 | fLianB | 倍 |
| 股性 | 爱涨停>10次 | 少涨停 | EverZTCount | 次 |
| 弹性 | Beta>1.3 | Beta<1.2 | BetaValue | - |
| 龙虎榜 | 游资上榜 | 机构上榜 | qd_lhb_detail.broker_type | - |

加权评分, 分差>2 才明确分类, 否则"混合"。

## 五、⚠️ 关键单位修正 (重要!)

发现 tqcenter 字段单位 (源自 `_deprecated/参考编码用-导出全景快照.py`):
- **Zjl (主买净额) = 万元** (非元)
- **main_net (板块主力净流入) = 万元** (Zjl聚合)
- **Ltsz (流通市值) = 亿元**

k6 原配置 `net_inflow_full=1e9`(按元) 导致资金权重40%**完全失效**(板块净流入1500万/1e9≈0)。
已修正为 `net_inflow_full=10000`(=1亿元, 万元单位)。**资金分激活后评分更准**。

## 六、如何调参 (无需改代码)

全部参数在 `config/strategies.yaml`:

```yaml
linkage:        # 板块联动评分权重/门槛/动态阈值分位数
stock_type:     # 票型分类阈值 (市值/换手/量比/Beta)
stock_signal:   # 4种信号阈值 (surge/补涨/低吸/大单)
```

调参流程:
1. 改 strategies.yaml
2. `python tests/test_signal_chain.py` 用历史数据验证
3. 盘中实盘观察, 微调

## 七、测试验证结果 (2026-07-09 历史数据)

```
强势板块成分股: 4181只 → focus池有数据: 90只
票型: 情绪6/趋势27/混合57

🔴 buy: 深中华A(情绪票大单流入0.23亿)  ← 信号链工作
🟡 observe: 35条 (中兴通讯/中芯国际/中际旭创等大单+低吸)
```

## 八、⚠️ 已知限制 (待决策)

### 1. focus池覆盖限制 (重要)
`qd_stock_snapshot` 只采**重点500只**(c2 focus池), 强势板块4181成分股仅90只有实时数据。
→ **小盘急涨股(如300656.SZ 5min+5.8%)可能不在focus池, 捕捉不到**。
→ 决策点: 是否扩大采集范围? 或在 compute 层加"强势板块异动扫描"(直接扫全市场板块成分股)?

### 2. surge静态测试不触发
surge是盘中实时信号(早盘/板块启动时), 14:52尾盘静态数据无急涨票属正常。
→ 需盘中实盘验证 surge 触发。

### 3. 连板数据用昨日
盘中无实时连板, 用 qd_stock_daily.EverZTCount(昨日)。当日新连板要等盘后。

## 九、待用户决策

1. **focus池问题**: 扩大采集 vs 加异动扫描层 (见限制1)
2. **信号阈值**: 用真实盘中数据调参 (surge/大单阈值可能需微调)
3. **飞书推送**: p28 的 buy 信号接入飞书推送 (目前只入库 qd_decisions)

## 十、模块依赖链

```
runner/intraday_loop.py (60s块)
  ├─ k6_linkage.run → ctx.linkage_scores/metrics/thresholds
  ├─ k7_stock_type.run → ctx.stock_types (依赖 linkage_scores)
  └─ 遍历 StrategyRegistry
       ├─ p27 (板块联动观察名单, 消费 linkage_alerts)
       └─ p28 (吃肉信号, 消费 stock_types + linkage + snapshot_focus_df)
```
