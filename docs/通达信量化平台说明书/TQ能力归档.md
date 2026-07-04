# TQ 能力归档（查询用）

> 37 个探针实测沉淀。按"想做什么"索引,快速查能力现状。
> 图例: ✅可用 / ❌不可用 / ⚠️有限制 / ⏸未测或本机无法测
> 查到能力后,详细用法/踩坑看对应探针 + `TQ探测经验总结.md`

---

## A. 行情数据（基础,无溢价）

| 想做什么 | API | 状态 | 入口探针 | 关键限制 |
|---|---|---|---|---|
| 取全A股代码列表 | `get_stock_list(market='5')` | ✅ | tes_002 | 5536只 |
| 取日K线 | `get_market_data(period='1d')` | ✅ | tes_003 | 返回 {字段:DataFrame} |
| 取5分K | `get_market_data(period='5m')` | ❌ | tes_003 | 当前环境返空 |
| 取实时快照 | `get_market_snapshot` / `get_pricevol` | ✅ | probe_02 / probe_03 | — |
| 取单股88字段 | `get_more_info(stock_code)` | ✅ | tes_002 | 单股,不收 stock_list |
| 证券基本信息 | `get_stock_info` | ✅ | probe_15 | — |
| 模糊搜股 | `get_match_stkinfo(key_word)` | ✅ | probe_07 | 全品种(含新后缀) |

---

## B. 资金数据 ⭐⭐⭐ 高溢价

| 想做什么 | API | 状态 | 入口探针 | 关键限制 |
|---|---|---|---|---|
| 盘中主力净流(实时) | `get_more_info(code)['Zjl']` | ✅ | tes_013 | **盘后归零** |
| **盘后主力净额选股** | `formula_process_mul_zb('ZLJE')` | ✅ | tes_011 | 需自建ZLJE公式;L2只存1-2月 |
| 主力买卖盘三档 | `formula_process_mul_zb('ZLMM')` | ✅ | tes_004 | 内置公式,直接可用 |

---

## C. 公式计算 ⭐⭐⭐ 高溢价

| 想做什么 | API | 状态 | 入口探针 | 关键限制 |
|---|---|---|---|---|
| 批量技术指标(全市场) | `formula_process_mul_zb` | ✅ | probe_18 / probe_21 | MACD等需 count≥100 暖机 |
| 批量条件选股 | `formula_process_mul_xg` | ✅ | probe_18 | UPN连涨等内置 |
| 批量专家系统 | `formula_process_mul_exp` | ✅ | probe_23 | CCI,返回ENTERLONG/EXITLONG |
| 单股公式计算 | `formula_zb`/`_xg`/`_exp` | ✅ | probe_17 | 需先 `formula_set_data_info` 设上下文 |
| **Python数据喂回公式引擎** | `formula_set_data` | ✅ | probe_19 | 独特能力,突破"只能用客户端K线" |
| 公式数据读回 | `formula_get_data` | ✅ | probe_19 | 数据在 `Value` 字段(非Data) |
| K线格式化 | `formula_format_data` | ✅ | probe_19 | Amount转万元 |
| 列可用公式清单 | `formula_get_all(type)` | ✅ | probe_16 | 指标225/选股107/专家15 |
| 查公式参数定义 | `formula_get_info(type,code)` | ✅ | probe_16 | — |

---

## D. 板块

| 想做什么 | API | 状态 | 入口探针 | 关键限制 |
|---|---|---|---|---|
| 列系统/自定义板块 | `get_sector_list`/`get_user_sector` | ✅ | tes_002 | — |
| 读板块成份股 | `get_stock_list_in_sector(block_code, block_type)` | ✅ | probe_22 | **系统bt=0 / 自定义bt=1** |
| 查股票所属板块 | `get_relation(stock_code)` | ✅ | probe_23 | 6类:行业/地区/概念/风格/指数/自定义 |
| 创建/删除板块 | `create_sector`/`delete_sector` | ✅ | probe_22 | 写操作 |
| 推股票到客户端板块 | `send_user_block(block_code, stock_list)` | ✅ | probe_22 | 参数 stock_list 非 stocks |
| 推消息到策略管理器 | `send_message(msg)` | ✅ | probe_22 | — |

---

## E. 财务/股本/估值（基础）

| 想做什么 | API | 状态 | 入口探针 | 关键限制 |
|---|---|---|---|---|
| 财务数据 | `get_financial_data` | ✅ | tes_007 | — |
| 股本历史 | `get_gb_info_by_date` | ✅ | probe_04 | Date/Zgb/Ltgb 首字母大写 |
| 个股估值指标 | `get_gpjy_value` | ✅ | probe_10 | — |
| 板块指标 | `get_bkjy_value` | ✅ | probe_11 | — |
| 市场宏观 | `get_scjy_value` | ✅ | probe_09 | — |
| 交易日历 | `get_trading_calendar(start_time,end_time)` | ✅ | tes_006 | 必传 start+end |
| 新股申购 | `get_ipo_info` | ✅ | probe_05 | — |
| 可转债信息 | `get_kzz_info` | ✅ | probe_13 | — |
| ETF跟踪指数 | `get_trackzs_etf_info` | ✅ | probe_14 | — |

---

## F. 交易/客户端交互

| 想做什么 | API | 状态 | 入口探针 | 关键限制 |
|---|---|---|---|---|
| 下单 | `order_stock` | ⏸ | tes_009(dry-run) | 需登录资金账户 |
| 撤单 | `cancel_order_stock` | ⏸ | tes_009 | 同上 |
| 查持仓/委托/资产 | `query_stock_positions/orders/asset` | ⏸ | tes_005 | 同上 |
| 调客户端功能/URL | `exec_to_tdx(url)` | ✅ | probe_23 | — |
| 订阅行情+实时预警 | `subscribe_hq`+`send_warn` | ✅ | tes_012 | ≤100只,长驻 |
| 刷新行情缓存 | `refresh_cache(market='AG',force=True)` | ✅ | tes_002 | 非 refresh_cache(true) |

---

## G. 新市场后缀（changelog 新增）

| 后缀 | 含义 | 状态 | 代表代码 | 备注 |
|---|---|---|---|---|
| `.CSI` | 中证指数 | ✅ | 000300.CSI / 000905.CSI | 可取数 |
| `.CFF` | 中金所期货 | ✅ | **IF300.CFF** | 非合约名IF2506 |
| `.HG` | 宏观数据 | ✅ | CPI.HG / GDP.HG / PMI.HG | — |
| `.QHZ` | 期货指数 | ⏸ | 真实代码待确认 | 推测名取空 |

> 代码格式不限(`check_stock_code_format` 只校验非空)。`market_str_to_int_market`(refresh_cache用)不含这些新后缀。

---

## 价值评级速查（值得投入的排序）

| 评级 | 能力 | 建议 |
|---|---|---|
| ⭐⭐⭐ | 盘后 ZLJE 主力选股 | **最值得做生产化**:接 DuckDB 入库,每日盘后跑 |
| ⭐⭐⭐ | Python↔公式数据互通 | 屠龙技,有另类数据需求时再用 |
| ⭐⭐⭐ | 批量公式引擎(全市场) | 配合选股场景,复用通达信指标体系 |
| ⭐⭐ | 选股→推送板块闭环 | 和⭐⭐⭐组合,形成完整工作流 |
| ⭐⭐ | get_relation 板块归属 | 板块轮动/概念追踪基础 |
| ⭐⭐ | 订阅实时预警 | 盘中场景 |
| ⭐ | 行情/财务/估值 | 基础数据,够用就行,别投入差异化 |

---

## 入口速查

- **想跑盘后主力选股** → `python DB数据库_v2/tes/tes_011_zlje.py --abs --limit 200 --top 20`
- **想跑全API连通性体检** → `python 通达信量化平台说明书/probe_scripts/run_all_probes.py`
- **想查某API怎么用** → `TQ探测经验总结.md` + memory `tqcenter-api-signatures`
- **想查某能力有没有** → 本文档
