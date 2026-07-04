# TQ 通达信量化平台 探测经验总结

> 累积自两轮系统探测:`DB数据库_v2/tes/`(13探针) + `通达信量化平台说明书/probe_scripts/`(24探针)。
> 最后更新: 2026-06-30。本文是经验总集,详细签名坑见 memory `tqcenter-api-signatures`。

---

## 一、环境准备(必读)

```python
import sys, os
sys.path.append(r'K:\txdlianghua\PYPlugins\user')   # 真实路径(文章的 C:/new_tdx_test2025 不存在)
from tqcenter import tq
tq.initialize(os.path.abspath(__file__))             # 必须绝对路径
# ... 业务 ...
tq.close()                                            # 必须关闭,否则策略管理器卡"运行中"
```
- 通达信客户端**必须已启动并登录**,否则脚本能 init 但取数返空
- 交易类 API 需额外登录资金账号(`stock_account(账号,类型)`),本机未登录返 -1

---

## 二、API 分类速查

| 类 | 核心 API | 备注 |
|---|---|---|
| 行情 | `get_stock_list(market='5')` / `get_market_data` / `get_market_snapshot` / `get_pricevol` | 全A股5536只;get_market_data 返回 {字段:DataFrame} |
| 单股 | `get_more_info(stock_code)` / `get_stock_info` | 88字段;**单股不收 stock_list** |
| 财务 | `get_financial_data` / `get_gb_info_by_date` / `get_gpjy_value` | 股本用 Date/Zgb/Ltgb 首字母大写 |
| 板块 | `get_sector_list` / `get_stock_list_in_sector` / `get_relation` / `create_sector` / `send_user_block` | block_type 规则见坑#5 |
| 公式 | `formula_zb/_xg/_exp`(单股) / `formula_process_mul_zb/_xg/_exp`(批量) / `formula_get_all/info` | 见第四节 |
| 交易 | `order_stock` / `cancel_order_stock` / `query_stock_positions` | tqconst 常量 |

---

## 三、踩坑汇总(照抄文章/文档必错)

| # | 坑 | 正确做法 |
|---|---|---|
| 1 | 路径 `C:/new_tdx_test2025` 不存在 | `K:\txdlianghua\PYPlugins\user` |
| 2 | `refresh_cache(true)` NameError | `refresh_cache(market='AG', force=True)` |
| 3 | `start_date`/`end_date` 不收 | 普遍是 `start_time`/`end_time` |
| 4 | `get_more_info(stock_list=...)` 报错 | 单股 `stock_code=`,逐只调 |
| 5 | `get_stock_list_in_sector` 读自定义板块空 | 系统板块 `block_type=0`;自定义板块 `block_type=1` |
| 6 | `send_user_block(stocks=...)` 报错 | 参数是 `stock_list` |
| 7 | `get_stock_list_in_sector(sector_code=...)` 报错 | 参数是 `block_code` |
| 8 | `if 'Data' in macd_result` 总 False | 返回字段是 `Value` 非 `Data` |
| 9 | MACD 单股 vs 批量 DIF 不一致 | count≥100 才收敛(暖机),count=30 是暂态值 |
| 10 | ZLJE 报"公式不存在" | ZLJE 非内置,需 Ctrl+F 自建 |
| 11 | Zjl 盘后选股全 0 | Zjl 盘后归零,盘后必须用 ZLJE |
| 12 | L2 历史取2024年全空 | L2 只存近期1-2月 |
| 13 | 5分K `period='5m'` 返回空 | 当前环境只日K `'1d'` 可用 |

---

## 四、公式 API 详解(probe_16~20)

### 两条路径
- **单股**: `formula_set_data_info(stock_code, count, dividend_type)` 设上下文 → `formula_zb/xg/exp` 算
- **批量**: `formula_process_mul_zb/xg/exp(stock_list, count, ...)` 直传多股,无需 set,更快

### 三类公式(formula_type)
| type | 调用 | 公式例 | 输出 |
|---|---|---|---|
| 0 技术指标 | `formula_zb` / `process_mul_zb` | MACD/KDJ/BOLL/ZLMM | DIF/DEA/MACD 等数值 |
| 1 条件选股 | `formula_xg` / `process_mul_xg` | UPN(连涨) | UP3=0/1 布尔 |
| 2 专家系统 | `formula_exp` / `process_mul_exp` | CCI | ENTERLONG/EXITLONG 信号 |

### 关键细节
- **返回 `Value` 字段**(非 Data): `r['Value']['DIF']` 或批量 `r[code]['DIF']`
- **count 暖机**: 趋势指标(MACD)需 count≥100 才稳;选股(UPN)count=5 即可
- count=-1 配 start/end 按区间返回;count>0 往前 n 条
- formula_get_all(type) 列公式清单(指标225/选股107/专家系统15)
- 内置可用: MACD/KDJ/BOLL/ZLMM/UPN/CCI;**ZLJE 需自建**

### 数据闭环(probe_19)
`get_market_data` → `formula_format_data`(格式化,Amount转万元) → `formula_set_data` → `formula_get_data`(读回, 在 `Value` 字段)

---

## 五、数据源选择决策

### 主力净额(详见 memory `zlje-vs-zjl-capital-flow`)
| 场景 | 数据源 | 说明 |
|---|---|---|
| 盘中实时监控 | `get_more_info(code)['Zjl']` | 现成不建公式,但**盘后归零** |
| **盘后选股/回测** | `formula_process_mul_zb('ZLJE')` | **需自建ZLJE公式**,盘后不归零,L2只存近1-2月 |

### 新市场后缀(changelog新增)
| 后缀 | 含义 | 代表代码 |
|---|---|---|
| `.CSI` | 中证指数 | 000300.CSI(沪深300) / 000905.CSI(中证500) |
| `.CFF` | 中金所期货 | **IF300.CFF**(股指期货,非合约名IF2506) |
| `.HG` | 宏观数据 | CPI.HG / GDP.HG / PMI.HG |
| `.QHZ` | 期货指数 | 真实代码待确认 |

---

## 六、板块 block_type 规则(文档没说清)

| 板块类型 | block_type | 例 |
|---|---|---|
| 系统板块 | `0` | 通达信88(88只)、880096行业(1629只) |
| 用户自定义板块 | `1` | create_sector 建的(加 BKCODE.前缀) |

`get_relation(stock_code)` 返回股票所属板块,BlockType 含: 行业/地区/概念/风格/指数/自定义。

---

## 七、探针索引(共 37 个)

### `DB数据库_v2/tes/`(13个,实际选股/监控应用)
- **核心**: `tes_011_zlje`(ZLJE盘后选股) / `tes_012_subscribe_warn`(订阅预警长驻) / `tes_000_all`(连通性体检)
- 参考: tes_003(K线)/004(指标)/007(财务)/008(估值)/009(下单dry-run)/013(Zjl盘中)
- 归档: `tes/archive/`(6个早期单接口探针,API字典)

### `通达信量化平台说明书/probe_scripts/`(24个,系统API探测)
| 范围 | 编号 | 内容 |
|---|---|---|
| 行情/财务/板块/ETF | 01-15 | 原有探针 |
| formula 家族 | 16-20 | 清单/单股/批量/数据闭环/异常诊断 |
| 场景 | 21-22 | MACD金叉选股/板块CRUD工作流 |
| 版本更新 | 23-24 | 新市场后缀+新函数/增强逻辑 |

**批跑**: `python run_all_probes.py`(无人值守,单脚本错误不影响其它)

---

## 八、关联 memory(DB数据库_v2/memory/)
- `tqcenter-real-path`: 真实路径 + 初始化范本
- `tqcenter-api-signatures`: API 签名坑(本文的详细版)
- `zlje-vs-zjl-capital-flow`: 主力净额 Zjl vs ZLJE 决策
- `get_relation-api`: 板块归属(含 BlockType 6类)
- `get-more-info-api`: 88 字段表
- `capital-info-api`: 股本 API
