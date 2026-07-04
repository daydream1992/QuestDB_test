# 接口能力边界系统性探测任务 —— 总结报告

> 生成时间: 2025-07-01
> 目录: `k:\通达信量化平台说明书\probe_scripts\`
> 执行入口: `python run_all_probes.py`（无人值守模式）
> 输出目录: `probe_scripts\csv_outputs\*.csv`

---

## 一、任务目标

本次任务对 TQ（通达信量化平台）的全部核心数据接口进行系统性探测，覆盖：

1. **历史数据回溯窗口 —— 每个主要接口分别测试 30/60/90/120/180/250/365 天窗口的可返回记录
2. **批量 vs 单条获取 —— 对提供 `get_*_value` vs `get_*_value_by_date` 等成对接口进行同一天数据一致性校验
3. **跨接口数据一致性 —— 例如 `get_more_info` vs `get_relation` vs `get_stock_info`；`get_scjy_value` vs `get_market_snapshot` vs `get_pricevol` 等
4. **接口字段覆盖率 —— 每个主要接口 field_list=[] 的全量字段返回
5. **不同市场/板块覆盖 —— 主板/创业板/科创板/ETF/可转债/指数

## 二、探测脚本总览（按编号）

| # | 脚本文件 | 探测接口 | 主要任务
|---|----------|---------|--------
| 01 | `probe_01_sector_list_and_constituent.py` | `get_sector_list`, `get_stock_list_in_sector` | 板块列表 + 成份股映射，测试板块类型矩阵、跨接口一致性
| 02 | `probe_02_market_snapshot.py` | `get_market_snapshot`, `get_market_snapshot_by_date` | 行情快照字段全量、单日期 vs 区间、循环批量 vs 单条
| 03 | `probe_03_pricevol_vs_snapshot.py` | `get_pricevol` vs `get_market_snapshot` | 价格/成交量批量 vs 快照一致性、跨市场矩阵
| 04 | `probe_04_gb_info_history.py` | `get_gb_info`, `get_gb_info_by_date` | 股本历史窗口（30/60/90/120/180/250/365）
| 05 | `probe_05_ipo_info.py` | `get_ipo_info` | 新股申购：ipo_type × ipo_date 模式矩阵
| 06 | `probe_06_more_info_vs_relation.py` | `get_more_info`, `get_relation`, `get_stock_info` | 三接口字段交叉对比
| 07 | `probe_07_match_stkinfo.py` | `get_match_stkinfo` | 模糊检索：关键字命中率、max_count 生效
| 08 | `probe_08_financial_vs_one.py` | `get_financial_data`, `get_gp_one_data` | 财务字段一致性（report_type=announce_time vs tag_time）
| 09 | `probe_09_scjy_value.py` | `get_scjy_value`, `get_scjy_value_by_date` | 市场交易：单日期 vs 区间、市场矩阵
| 10 | `probe_10_gpjy_value.py` | `get_gpjy_value`, `get_gpjy_value_by_date` | 个股交易：4 类市场 × 7 窗口
| 11 | `probe_11_bkjy_value.py` | `get_bkjy_value`, `get_bkjy_value_by_date` | 板块交易：板块类型矩阵、一致性
| 12 | `probe_12_user_sector.py` | `get_user_sector`, `get_user_sector_by_code` | 自定义板块：成份股 vs 通用 get_stock_list_in_sector
| 13 | `probe_13_kzz_info.py` | `get_kzz_info` | 可转债：字段覆盖
| 14 | `probe_14_etf_trackzs.py` | `get_trackzs_etf_info` | ETF 跟踪指数：代码映射
| 15 | `probe_15_stock_info_full.py` | `get_stock_info` | 证券基本信息：全量字段、不同市场字段差异
| 16 | `probe_16_formula_list_info.py` | `formula_get_all`, `formula_get_info` | 公式清单（指标225/选股107/专家系统15）+ MACD/KDJ/BOLL/UPN/CCI 元信息
| 17 | `probe_17_formula_single_calc.py` | `formula_set_data_info`, `formula_zb`, `formula_xg`, `formula_exp` | 单股三步流程：set→MACD指标/UPN选股/CCI专家系统
| 18 | `probe_18_formula_batch_mul.py` | `formula_process_mul_zb`, `formula_process_mul_xg` | 批量多股并行（无需 set），批量 vs 单股一致性
| 19 | `probe_19_formula_data_cycle.py` | `formula_set_data`, `formula_get_data`, `formula_format_data` | get_market_data→format→set→get 数据闭环
| 20 | `probe_20_formula_anomaly_diag.py` | (诊断) | 深挖异常：MACD暖机(count≥100才稳) + get_data字段名(Data→Value)
| 21 | `probe_21_macd_golden_cross.py` | `formula_process_mul_zb`(MACD) | MACD金叉全市场选股(批量纯读, count=100暖机)
| 22 | `probe_22_sector_workflow.py` | `create_sector`/`send_user_block`/`get_stock_list_in_sector`/`delete_sector` | 板块CRUD工作流(DRY_RUN默认, 修正3处文章参数错误)

## 三、各接口探测维度

### 3.1 历史回溯窗口矩阵

所有需要历史回溯的接口都测试以下窗口（天）: 30, 60, 90, 120, 180, 250, 365。

测试股票/板块/指数均覆盖：
- `get_gb_info_by_date`（股本）
- `get_scjy_value_by_date`（市场交易）
- `get_gpjy_value_by_date`（个股交易）
- `get_bkjy_value_by_date`（板块交易）

输出 CSV 中 `csv_outputs/*_window_matrix.csv` 和 `*_market_matrix.csv`

### 3.2 单日期 vs 区间一致性

提供成对接口：
- `get_gb_info(stock_code, date_list=[d], count=1)` vs `get_gb_info_by_date(start=d, end=d)`
- `get_scjy_value` vs `get_scjy_value_by_date`
- `get_gpjy_value` vs `get_gpjy_value_by_date`
- `get_bkjy_value` vs `get_bkjy_value_by_date`

输出 CSV: `*_single_vs_range.csv

### 3.3 跨接口一致性

- `get_more_info` vs `get_relation` vs `get_stock_info`（基础字段名称/市值等）
- `get_pricevol` vs `get_market_snapshot`（价格/成交量）
- `get_financial_data` vs `get_gp_one_data`（财务数据 vs 单条财务）

输出 CSV: `*_consistency.csv / `*_cross.csv

### 3.4 接口参数模式矩阵

- `get_ipo_info`: ipo_type × ipo_date 交叉组合（仅新股、仅新发债、新股+新债）
- `get_financial_data`: report_type=announce_time vs tag_time
- `get_match_stkinfo`: max_count=1/5/20/50/100/200/500/1000

### 3.5 字段覆盖率

- `get_stock_info`, `get_more_info`, `get_kzz_info`, `get_trackzs_etf_info` 等

输出各接口全量字段探测，按市场（SH/SZ）聚合字段集合对比

## 四、关键结论要点（在实际运行后填充）

### 4.1 历史回溯窗口上限

| 接口 | 测试窗口上限 | 备注 |
|-----|-----------|------|
| `get_gb_info_by_date | 待执行后填 |  |
| `get_scjy_value_by_date | 待执行后填 |  |
| `get_gpjy_value_by_date | 待执行后填 |  |
| `get_bkjy_value_by_date | 待执行后填 |  |

### 4.2 单日期 vs 区间一致性

| 接口 | 同一日期返回一致比例 | 不一致字段 |
|-----|------------------|---------|
| `get_gb_info | 待执行后填 |  |
| `get_scjy_value | 待执行后填 |  |
| `get_gpjy_value | 待执行后填 |  |
| `get_bkjy_value | 待执行后填 |  |

### 4.3 字段覆盖率

| 接口 | 全量字段数 | 空值率 | 典型返回字段 |
|-----|----------|--------|-----------|
| `get_stock_info` | 待执行后填 |  |  |
| `get_more_info` | 待执行后填 |  |  |
| `get_kzz_info` | 待执行后填 |  |  |
| `get_trackzs_etf_info` | 待执行后填 |  |  |

### 4.4 公式类接口探测结论（probe_16~19，已实跑 2026-06-30）

| 接口 | 实测结果 | 备注 |
|-----|---------|------|
| `formula_get_all` | 技术指标 225 / 条件选股 107 / 专家系统 15 个 | 三类公式清单可完整列出 |
| `formula_get_info` | MACD/KDJ/BOLL/UPN/CCI 均内置(isSys=1)，参数定义齐全 | MACD=平滑异同平均线(3参), UPN=连涨数天(1参), CCI=CCI专家系统 |
| `formula_zb`/`_xg`/`_exp` 单股 | ✅ set_data_info→计算 流程通，茅台 MACD_DIF=-24.06 / CCI 触发买入 | 单股须先 set_data_info 设置上下文 |
| `formula_process_mul_zb`/`_xg` 批量 | ✅ 一次 21ms 拿 5 只，无需 set | 批量比单股逐只快 |
| `formula_format_data` | ✅ get_market_data→格式化 字段齐全(Date/Amount万元/Volume/Close/Open/High/Low) | Amount 单位是万元 |
| `formula_set_data` | ✅ ErrorId=0 设置成功 | — |
| `formula_get_data` | ✅ 读 `Value` 字段(文档误写 Data)，三种 set 方式都读回5条 | 文档字段名过时(Data→Value)，非 API 问题 |

**✅ 两处异常根因已查明（probe_20 诊断，均非 API bug）：**
1. **批量 vs 单股 MACD_DIF 不一致** = MACD 趋势指标**暖机**问题。count=30 不一致(-24.06 vs -30.72)，count=250 **完全一致**(-30.72)。单股受 `set_data_info(count=N)` 限制只给 N 根、MACD 没收敛(暂态值)；批量接口内部预热更充分。**count≥100 才稳定**（文章强调"K线数量不足会导致与客户端不一致"即此）。
2. **formula_get_data 读空** = 文档字段名过时。数据在 `Value` 字段（文档写的 `Data` 已废弃）。读 `Value` 即有完整5条数据，probe_19 闭环已验证 set→get 的 Close 完全一致。

### 4.5 场景探针结论（probe_21~22，g场景化例子，2026-06-30）

| 探针 | 结论 |
|-----|------|
| probe_21 MACD金叉 | ✅ 批量选股链路通，300只 159ms 筛出 8 只金叉(DIF上穿DEA)；count=100 暖机稳定(印证 probe_20) |
| probe_22 板块工作流 | ✅ `--write` 真实CRUD全通(create/send/delete 均 ErrorId=0)；读成分股**自定义板块需 `block_type=1`**(bt=0只读系统板块, 文档未说明) |
| probe_23 VBT回测 | ⏸ 搁置(vectorbt 未安装，需 `pip install vectorbt`) |

**⚠️ g 场景化文章 3 处参数错误（照抄必报错，探针已修正）：**
1. `send_user_block` 参数是 **`stock_list`** 非 `stocks`
2. `get_stock_list_in_sector` 参数是 **`block_code`** 非 `sector_code`
3. `formula_zb`/`formula_get_data` 返回 **`Value`** 字段非 `Data`（文章 MACD金叉 for循环版用过时 `Data`，跑不通；批量版结构对）

### 4.6 版本更新探测结论（probe_23~24，tqcenter changelog，2026-06-30）

| 更新点 | 探测结果 |
|-----|------|
| 新后缀 `.CSI` 中证指数 | ✅ 000300/000905/000852.CSI 可取数 |
| 新后缀 `.HG` 宏观数据 | ✅ CPI/GDP/PMI.HG 可取数（搜"CPI"确认 CPI.HG 真实存在） |
| 新后缀 `.CFF` 中金所期货 | ✅ **IF300.CFF** 可取数(88字段)；⚠️代码是 IF300 非推测的 IF2506 |
| 新后缀 `.QHZ` 期货指数 | ⏸ 未找到真实代码（推测的"IF主连.QHZ"取空） |
| 新函数 `get_relation` | ✅ 茅台返回45个板块(行业/地区/概念/自定义4类，含 BlockCode/BlockName/BlockType/GPNume) |
| 新函数 `exec_to_tdx` | ✅ 调用通(ErrorId=0，参数是 url) |
| 新函数 `formula_process_mul_exp` | ✅ CCI 批量专家系统返回 ENTERLONG/EXITLONG |
| `count=-1`+start/end | ✅ 按区间返回（MACD 06/01-06/20 返14条，均在区间内） |
| `get_match_stkinfo` 全品种 | ✅ 返回多后缀(SH/SZ/**CFF**/OF/OT/**CSI**)，搜"沪深300"含 IF300.CFF |
| 880096/097/098 成份股 | ✅ **bt=0 读 1629只**（系统行业板块用 bt=0；自定义板块才用 bt=1，见 probe_22） |
| `get_market_data` 未上市代码容错 | ✅ 混合非法代码(999999.XX)不报错（修复有效） |
| 期货期权下单 / 多头持仓 | ⏸ 本机未登录交易账户，无法测 |

**⚠️ block_type 使用规则（probe_22+24 综合）：**
- 系统板块（如通达信88、880xxx 行业板块）→ `block_type=0`
- 用户自定义板块（create_sector 创建的）→ `block_type=1`（加 BKCODE. 前缀）

## 五、执行方法

```bash
cd k:\通达信量化平台说明书\probe_scripts
python run_all_probes.py
```

每个脚本独立运行，互不影响。单个脚本错误不影响其他脚本。
