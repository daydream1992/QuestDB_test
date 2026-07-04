# 通达信量化平台 API 返回字段中英文映射汇总

> 基于全部接口的返回字段整理，便于数据处理和字段快速查询。

---

## 一、行情类接口返回字段映射

### 1.1 get_market_data（获取K线行情）

| 字段(英文) | 中文 | 说明 |
|------------|------|------|
| `Date` | 日期 | 交易日期 |
| `Time` | 时间 | 交易时间 |
| `Open` | 开盘价 | 开盘价 |
| `High` | 最高价 | 最高价 |
| `Low` | 最低价 | 最低价 |
| `Close` | 收盘价 | 收盘价 |
| `Volume` | 成交量 | 成交量（股） |
| `Amount` | 成交额 | 成交金额（元） |
| `ForwardFactor` | 前复权因子 | dividend_type=none 时有效 |
| `VolInStock` | 持仓量 | 期货数据时有值 |

### 1.2 get_market_snapshot（获取快照数据）

| 字段(英文) | 中文 |
|------------|------|
| `ItemNum` | 快照笔数 |
| `LastClose` | 前收盘价 |
| `Open` | 开盘价 |
| `Max` | 最高价 |
| `Min` | 最低价 |
| `Now` | 现价 |
| `Volume` | 总手 |
| `NowVol` | 现手 |
| `Amount` | 总成交金额 |
| `Inside` | 内盘/跌停家数 |
| `Outside` | 外盘/涨停家数 |
| `TickDiff` | 笔涨跌 |
| `InOutFlag` | 内外盘标志 |
| `Jjjz` | 基金净值 |
| `Buyp` | 买价（5档） |
| `Buyv` | 买量（5档） |
| `Sellp` | 卖价（5档） |
| `Sellv` | 卖量（5档） |
| `UpHome` | 上涨家数 |
| `DownHome` | 下跌家数 |
| `Before5MinNow` | 5分钟前价格 |
| `Average` | 均价 |
| `XsFlag` | 价格小数位数 |
| `Zangsu` | 涨速 |
| `ZAFPre3` | 3日涨幅 |

### 1.3 get_stock_info（获取证券基本信息）

**基本信息：**

| 字段(英文) | 中文 |
|------------|------|
| `Name` | 证券名称 |
| `Unit` | 交易单位 |
| `VolBase` | 量比的基量 |
| `MinPrice` | 最小价格变动 |
| `XsFlag` | 价格小数位数 |
| `Fz` | 开收市时间（4段） |
| `DelayMin` | 延时分钟数 |
| `QHVolBaseRate` | 期货期权的每手乘数 |
| `HKVolBaseRate` | 港股/日股/新加坡股每手股数 |

**分类标识：**

| 字段(英文) | 中文 |
|------------|------|
| `BelongHS300` | 是否属于沪深300 |
| `BelongHasKQZ` | 是否含可转债 |
| `BelongRZRQ` | 是否融资融券标的 |
| `BelongHSGT` | 是否属于沪深股通 |
| `IsHKGP` | 是否是港股 |
| `IsQH` | 是否是期货 |
| `IsQQ` | 是否是期权 |
| `IsSTGP` | 是否是ST股票 |
| `IsQuitGP` | 是否退市整理板股票 |
| `TodayDRFlag` | 当天是否除权除息 |
| `HSStockKind` | 沪深京品种类型（0指数/1主板/2北证/3创业板/4科创板/5B股/6债券/7基金/8权证/9其它/10非沪深京） |

**股本与资产（万元）：**

| 字段(英文) | 中文 |
|------------|------|
| `ActiveCapital` | 流通股本（万股） |
| `J_zgb` | 总股本（万股） |
| `J_bg` | B股（万股） |
| `J_hg` | H股（万股） |
| `J_ldzc` | 流动资产（万元） |
| `J_gdzc` | 固定资产（万元） |
| `J_wxzc` | 无形资产（万元） |
| `J_ldfz` | 流动负债（万元） |
| `J_cqfz` | 少数股东权益（万元） |
| `J_zbgjj` | 资本公积金（万元） |
| `J_jzc` | 股东权益/净资产（万元） |

**收入与利润：**

| 字段(英文) | 中文 |
|------------|------|
| `J_yysy` | 营业收入 |
| `J_yycb` | 营业成本 |
| `J_yszk` | 应收账款 |
| `J_yyly` | 营业利润 |
| `J_tzsy` | 投资收益 |
| `J_jyxjl` | 经营现金净流量 |
| `J_zxjl` | 总现金净流量 |
| `J_ch` | 存货 |
| `J_lyze` | 利润总额 |
| `J_shly` | 税后利润 |
| `J_jly` | 净利润 |
| `J_wfply` | 未分配利润 |

**每股指标：**

| 字段(英文) | 中文 |
|------------|------|
| `J_jyl` | 净资产收益率 |
| `J_mgwfp` | 每股未分配 |
| `J_mgsy` | 每股收益（折算为全年） |
| `J_mgsy2` | 季报每股收益 |
| `J_mggjj` | 每股公积金 |
| `J_mgjzc` | 每股净资产 |
| `J_mgjzc2` | 季报每股净资产 |
| `J_gdqyb` | 股东权益比 |
| `J_gdrs` | 股东人数 |
| `J_HalfYearFlag` | 报告期月份（3,6,9,12） |

**其他信息：**

| 字段(英文) | 中文 |
|------------|------|
| `J_start` | 上市日期 |
| `tdx_dycode` | 通达信地域代码 |
| `tdx_dyname` | 通达信地域 |
| `rs_hycode_sim` | 通达信行业代码 |
| `rs_hyname` | 通达信行业 |
| `blockzscode` | 所属行业板块指数代码 |
| `underly_setcode` | 标的市场代码（如ETF跟踪的指数） |
| `underly_code` | 标的代码（如ETF跟踪的指数代码） |

### 1.4 get_more_info（获取股票更多信息）

**基本与形态：**

| 字段(英文) | 中文 |
|------------|------|
| `MainBusiness` | 主营构成 |
| `SafeValue` | 安全分 |
| `ShineValue` | 亮点数 |
| `ShapeValue` | 短期形态+中期形态+长期形态编号 |
| `TPFlag` | 停牌标识 |
| `ZTPrice` | 涨停价 |
| `DTPrice` | 跌停价 |
| `HqDate` | 行情日期 |

**成交量与市值：**

| 字段(英文) | 中文 |
|------------|------|
| `fHSL` | 换手率 |
| `fLianB` | 量比 |
| `Wtb` | 委比 |
| `Zsz` | 总市值（亿） |
| `Ltsz` | 流通市值（亿） |
| `vzangsu` | 量涨速 |
| `Fzhsl` | 分钟换手率 |
| `FzAmo` | 2分钟金额（万元） |
| `FreeLtgb` | 自由流通股本（万） |

**涨幅类：**

| 字段(英文) | 中文 |
|------------|------|
| `VOpenZAF` | 抢筹涨幅 |
| `ZAF` | 涨幅 |
| `ZAFYesterday` | 昨日涨幅 |
| `ZAFPre2D` | 前天涨幅 |
| `ZAFPre5` | 5日涨幅 |
| `ZAFPre10` | 10日涨幅 |
| `ZAFPre20` | 20日涨幅 |
| `ZAFPre30` | 30日涨幅 |
| `ZAFPre60` | 60日涨幅 |
| `ZAFYear` | 年初至今涨幅 |
| `ZAFPreMyMonth` | 本月来涨幅 |
| `ZAFPreOneYear` | 一年来涨幅 |
| `ConZAFDateNum` | 连涨天数 |

**资金流向：**

| 字段(英文) | 中文 |
|------------|------|
| `Zjl` | 主买净额（万元） |
| `Zjl_HB` | 主力净流入（万元） |
| `TotalBVol` | 总买量 |
| `TotalSVol` | 总卖量 |
| `BCancel` | 总撤买量 |
| `SCancel` | 总撤卖量 |
| `L2TicNum` | L2逐笔成交数 |
| `L2OrderNum` | L2逐笔委托数 |

**涨停封板：**

| 字段(英文) | 中文 |
|------------|------|
| `FCAmo` | 封单额（万元） |
| `FCb` | 封成比 |
| `OpenAmo` | 开盘金额（万元）（A股和板块指数有效） |
| `OpenZTBuy` | 竞价涨停买入金额（万元） |
| `OpenAmoPre1` | 昨开盘金额（万元） |
| `OpenVolPre1` | 昨开盘量 |
| `CJJEPre1` | 昨成交额（万元） |
| `CJJEPre3` | 3日成交额（万元） |
| `FDEPre1` | 昨封单额（万元） |
| `FDEPre2` | 前封单额（万元） |
| `ZTGPNum` | 板块指数的涨停家数 |
| `LastStartZT` | 几天 |
| `LastZTHzNum` | 几板 |
| `EverZTCount` | 连板天数 |
| `YearZTDay` | 年涨停天数 |

**价格与估值：**

| 字段(英文) | 中文 |
|------------|------|
| `MA5Value` | 5日均价 |
| `HisHigh` | 52周最高 |
| `HisLow` | 52周最低 |
| `IPO_Price` | 发行价 |
| `More_YJL` | ETF/LOF溢价率 |
| `BetaValue` | 贝塔系数 |
| `DynaPE` | 动态市盈率 |
| `MorePE` | 市盈率 |
| `StaticPE_TTM` | 市盈率（TTM） |
| `DYRatio` | 股息率 |
| `PB_MRQ` | 市净率（MRQ） |

**类型标识：**

| 字段(英文) | 中文 |
|------------|------|
| `IsT0Fund` | 是否是T+0基金 |
| `IsZCZGP` | 是否是注册制A股 |
| `IsKzz` | 是否是可转债 |
| `Kzz_HSCode` | 可转债对应的正股代码 |
| `QHMainYYMM` | 主力合约关联的月份（期货） |
| `Yield` | 应计利息（债券）/占款天数（回购） |

**财务指标：**

| 字段(英文) | 中文 |
|------------|------|
| `KfEarnMoney` | 扣非净利润（万元） |
| `RDInputFee` | 研发费用（万元） |
| `CashZJ` | 货币资金（万元） |
| `PreReceiveZJ` | 合同负债（万元） |
| `OtherQYJzc` | 其它权益工具（万元） |
| `StaffNum` | 员工人数 |

**关键日期：**

| 字段(英文) | 中文 |
|------------|------|
| `RecentGGJYDate` | 最近北上大额交易日 |
| `RecentHGDate` | 最近回购预案日 |
| `RecentIncentDate` | 最近股权激励预案日 |
| `NoticeDate_Recent` | 最近业绩预告日 |
| `RecentReleaseDate` | 最近解禁日 |
| `RecentDZDate` | 最近定增日 |
| `ReportDate` | 最近财报公告日期 |
| `ZTDate_Recent` | 近2年最近涨停板日期 |
| `DTDate_Recent` | 近2年最近跌停板日期 |
| `TopDate_Recent` | 近2年最近龙虎榜日期 |
| `StopJYDate_Recent` | 最近停牌日期 |

### 1.5 其他行情接口（get_pricevol/get_relation/get_gb_info/get_ipo_info）

**get_pricevol（批量获取价量）：**

| 字段(英文) | 中文 |
|------------|------|
| `LastClose` | 前收盘价 |
| `Now` | 现价 |
| `Volume` | 成交量 |

**get_relation（获取股票所属板块）：**

| 字段(英文) | 中文 |
|------------|------|
| `BlockCode` | 板块代码 |
| `BlockName` | 板块名称 |
| `BlockType` | 板块类型（行业/概念/地区/风格/指数） |
| `GPNume` | 成份股数量 |

**get_gb_info / get_gb_info_by_date（股本数据）：**

| 字段(英文) | 中文 |
|------------|------|
| `Date` | 日期（YYYYMMDD） |
| `Zgb` | 总股本（股） |
| `Ltgb` | 流通股本（股） |

**get_ipo_info（获取新股申购信息）：**

| 字段(英文) | 中文 |
|------------|------|
| `Code` | 证券代码 |
| `Name` | 证券名称 |
| `SGDate` | 申购日期 |
| `SGPrice` | 申购价格 |
| `SGCode` | 申购代码 |
| `MaxSG` | 申购上限 |
| `PE_Issue` | 发行市盈率 |


---

## 二、财务类接口返回字段映射

### 2.1 get_finance_data（获取财务数据）

| 字段(英文) | 中文 |
|------------|------|
| `RDate` | 报告期 |
| `RType` | 报告类型（1中报/2三季报/3年报/4一季报/5半年/6三季度/7全年） |
| `TotalCapitalStock` | 总股本（股） |
| `CirculateA` | A股流通股本 |
| `CirculateB` | B股流通股本 |
| `CirculateH` | H股流通股本 |
| `TotalAssets` | 总资产 |
| `CurrentAssets` | 流动资产 |
| `FixedAssets` | 固定资产 |
| `IntangibleAssets` | 无形资产 |
| `Liability` | 总负债 |
| `CurrentLiability` | 流动负债 |
| `LongTermLiability` | 长期负债 |
| `MinorityInterests` | 少数股东权益 |
| `ShareholdersEquity` | 股东权益 |
| `CapitalReserve` | 资本公积金 |
| `UndistributedProfit` | 未分配利润 |
| `OperatingRevenue` | 营业收入 |
| `OperatingCost` | 营业成本 |
| `OperatingProfit` | 营业利润 |
| `InvestmentIncome` | 投资收益 |
| `NetCashFlowFromOperating` | 经营现金净流量 |
| `NetIncreaseInCashAndCashEquivalents` | 总现金净流量 |
| `Inventory` | 存货 |
| `TotalProfit` | 利润总额 |
| `ProfitAfterTax` | 税后利润 |
| `NetProfit` | 净利润 |
| `Roe` | 净资产收益率 |
| `NetProfitPerShare` | 每股收益（折算为全年） |
| `ReportNetProfitPerShare` | 季报每股收益 |
| `CapitalReservePerShare` | 每股公积金 |
| `NetAssetValuePerShare` | 每股净资产 |
| `ReportNavPerShare` | 季报每股净资产 |
| `EquityRatio` | 股东权益比 |
| `TotalShareholders` | 股东人数 |
| `ListingDate` | 上市日期 |

### 2.2 get_express_data（业绩快报）

| 字段(英文) | 中文 |
|------------|------|
| `Date` | 日期 |
| `Revenue` | 营业收入 |
| `NetProfit` | 净利润 |
| `EPS` | 每股收益 |
| `NAPS` | 每股净资产 |
| `ROE` | 净资产收益率 |

### 2.3 get_gross_yield（分红数据）

| 字段(英文) | 中文 |
|------------|------|
| `Date` | 除权除息日期 |
| `Plan` | 分红方案 |
| `BonusRatio` | 送股比例 |
| `TransferRatio` | 转股比例 |
| `DividendRatio` | 派现比例 |
| `PlanDate` | 预案公告日 |
| `GDRDate` | 股东大会公告日 |
| `ExecuteDate` | 实施方案公告日 |

### 2.4 get_right_issue（配股数据）

| 字段(英文) | 中文 |
|------------|------|
| `Date` | 缴款起始日期 |
| `R1Date` | 缴款截止日期 |
| `R2Date` | 配股上市日期 |
| `R3Date` | 股权登记日 |
| `RightIssueRatio` | 配股比例 |
| `RightIssuePrice` | 配股价格 |
| `RightIssueAmount` | 配股数量（万股） |
| `RightIssueCode` | 配股代码 |
| `RightIssueName` | 配股简称 |

### 2.5 get_capital（分红派息（除权））

| 字段(英文) | 中文 |
|------------|------|
| `Date` | 除权除息日期 |
| `DRDate` | 股权登记日 |
| `DRPlan` | 分红方案 |
| `BonusRatio` | 送股比例 |
| `TransferRatio` | 转股比例 |
| `DividendRatio` | 派现比例 |
| `DRType` | 分红类型 |


---

## 三、板块管理接口返回字段映射

### 3.1 get_board_list（获取板块列表）

| 字段(英文) | 中文 |
|------------|------|
| `BoardCode` | 板块代码 |
| `BoardName` | 板块名称 |
| `BoardType` | 板块类型 |
| `GPNume` | 成份股数量 |

### 3.2 get_board_stock_list（获取板块成份股）

| 字段(英文) | 中文 |
|------------|------|
| `Code` | 股票代码 |
| `Name` | 股票名称 |

### 3.3 get_board_history（获取板块历史行情）

| 字段(英文) | 中文 |
|------------|------|
| `Date` | 日期 |
| `Time` | 时间 |
| `Open` | 开盘价 |
| `High` | 最高价 |
| `Low` | 最低价 |
| `Close` | 收盘价 |
| `Volume` | 成交量 |
| `Amount` | 成交额 |
| `ConstituentCount` | 成分股数量 |
| `UpCount` | 上涨家数 |
| `DownCount` | 下跌家数 |
| `EqualCount` | 平盘家数 |
| `LeadCode` | 领涨股代码 |
| `LeadName` | 领涨股名称 |
| `LeadZAF` | 领涨股涨幅 |


---

## 四、可转债 / ETF 类接口返回字段映射

### 4.1 get_kzz_data（可转债数据）

| 字段(英文) | 中文 |
|------------|------|
| `Code` | 可转债代码 |
| `Name` | 可转债名称 |
| `HSCode` | 正股代码 |
| `HSName` | 正股名称 |
| `ZQDate` | 转股起始日 |
| `ZQEndDate` | 转股截止日 |
| `HS_Price` | 正股现价 |
| `HG_Price` | 转股价 |
| `ZZG` | 纯债价值 |
| `ZZB` | 转股价值 |
| `PremRate` | 溢价率 |
| `StockConvergenceRate` | 股性活跃度 |
| `DuraTime` | 剩余年限（年） |
| `ConvRate` | 转股比例/每张可转债转股数量 |
| `PayRate` | 票面利率（%） |
| `LeftYear` | 剩余期限 |
| `KzzScale` | 发行规模（亿） |
| `CurrScale` | 余额规模（亿） |
| `ChangePriceDate` | 已调整转股价次数 |
| `StockRedeemDate` | 强赎最后交易日 |
| `PutBackPrice` | 回售价格 |
| `PutBackDate` | 回售起始日 |
| `CreditRating` | 信用评级 |
| `ConvertibleBondType` | 可转债类型 |

### 4.2 get_etf_info（ETF基金信息）

| 字段(英文) | 中文 |
|------------|------|
| `Code` | ETF代码 |
| `Name` | ETF名称 |
| `FundName` | 基金全称 |
| `Manager` | 管理人 |
| `Trustee` | 托管人 |
| `IssueDate` | 发行日期 |
| `ListDate` | 上市日期 |
| `EstablishDate` | 成立日期 |
| `EndDate` | 到期日期 |
| `IndexCode` | 跟踪指数代码 |
| `IndexName` | 跟踪指数名称 |
| `InvestStyle` | 投资风格 |
| `FundType` | 基金类型 |
| `Unit` | 交易单位 |
| `MinPrice` | 最小价格变动 |
| `ManagementFee` | 管理费率（%） |
| `CustodyFee` | 托管费率（%） |
| `SubscriptionFee` | 申购费率（%） |
| `RedemptionFee` | 赎回费率（%） |

### 4.3 get_etf_nav（ETF净值）

| 字段(英文) | 中文 |
|------------|------|
| `Date` | 日期 |
| `UnitNav` | 单位净值 |
| `AccNav` | 累计净值 |
| `ReturnRatio` | 日增长率（%） |
| `PremRate` | 折溢率（%） |


---

## 五、公式调用接口返回字段映射

### 5.1 evaluate_formula / run_formula（执行公式）

> 公式接口返回数据由公式中使用的变量名决定，以下是通达信常用公式函数在 DataFrame 中生成的列名。

| 字段(英文) | 中文 | 说明 |
|------------|------|------|
| `Code` | 股票代码 |  |
| `Date` | 日期 |  |
| `OPEN` | 开盘价 | 原始价 OHLC |
| `HIGH` | 最高价 | 原始价 OHLC |
| `LOW` | 最低价 | 原始价 OHLC |
| `CLOSE` | 收盘价 | 原始价 OHLC |
| `VOL` | 成交量 |  |
| `AMOUNT` | 成交额 |  |
| `MA5` | 5日均线 | MA(CLOSE,5) |
| `MA10` | 10日均线 | MA(CLOSE,10) |
| `MA20` | 20日均线 | MA(CLOSE,20) |
| `MA60` | 60日均线 | MA(CLOSE,60) |
| `EMA12` | 12日指数均线 | EMA(CLOSE,12) |
| `EMA26` | 26日指数均线 | EMA(CLOSE,26) |
| `DIFF` | DIF（MACD） | EMA12-EMA26 |
| `DEA` | DEA（MACD） | EMA(DIFF,9) |
| `MACD` | MACD柱 | 2*(DIFF-DEA) |
| `K` | KDJ K值 |  |
| `D` | KDJ D值 |  |
| `J` | KDJ J值 |  |
| `RSV` | 未成熟随机值 |  |
| `RSI6` | 6日RSI |  |
| `RSI12` | 12日RSI |  |
| `BOLL` | 布林中轨 |  |
| `UPPER` | 布林上轨 |  |
| `LOWER` | 布林下轨 |  |
| `SAR` | 抛物转向 |  |
| `OBV` | 能量潮 |  |
| `CCI` | 顺势指标 |  |
| `ATR` | 真实波幅 |  |
| `BBI` | 多空指数 |  |
| `WR` | 威廉指标 |  |
| `MTM` | 动力指标 |  |
| `DMA` | 平均线差 |  |
| `ASI` | 振动升降指标 |  |
| `BIAS6` | 6日乖离率 |  |
| `PSY` | 心理线 |  |
| `CR` | 中间意愿指标 |  |
| `VR` | 成交量变异率 |  |
| `BRAR` | 情绪指标BRAR |  |


---

## 六、订阅 / 消息接口返回字段映射

### 6.1 subscribe_tick / unsubscribe_tick（订阅/取消订阅实时行情）

| 字段(英文) | 中文 |
|------------|------|
| `Code` | 股票代码 |
| `Name` | 股票名称 |
| `Time` | 时间 |
| `Now` | 现价 |
| `LastClose` | 昨收 |
| `Open` | 今开 |
| `High` | 最高 |
| `Low` | 最低 |
| `Volume` | 成交量 |
| `Amount` | 成交额 |
| `ZFA` | 涨幅 |
| `AvgPrice` | 均价 |
| `Buyp1` | 买一价 |
| `Buyv1` | 买一量 |
| `Buyp2` | 买二价 |
| `Buyv2` | 买二量 |
| `Buyp3` | 买三价 |
| `Buyv3` | 买三量 |
| `Buyp4` | 买四价 |
| `Buyv4` | 买四量 |
| `Buyp5` | 买五价 |
| `Buyv5` | 买五量 |
| `Sellp1` | 卖一价 |
| `Sellv1` | 卖一量 |
| `Sellp2` | 卖二价 |
| `Sellv2` | 卖二量 |
| `Sellp3` | 卖三价 |
| `Sellv3` | 卖三量 |
| `Sellp4` | 卖四价 |
| `Sellv4` | 卖四量 |
| `Sellp5` | 卖五价 |
| `Sellv5` | 卖五量 |

### 6.2 get_message / check_message（获取消息）

| 字段(英文) | 中文 |
|------------|------|
| `MsgID` | 消息唯一标识 |
| `MsgType` | 消息类型 |
| `MsgTime` | 消息时间 |
| `Title` | 消息标题 |
| `Content` | 消息内容 |
| `Priority` | 优先级 |
| `From` | 消息来源 |
| `ReadFlag` | 是否已读 |


---

## 七、客户端操作接口返回字段映射

### 7.1 login / logout（登录 / 登出）

| 字段(英文) | 中文 |
|------------|------|
| `Status` | 状态码（0成功） |
| `Message` | 状态信息 |
| `SessionID` | 会话ID |
| `UserID` | 用户ID |
| `LoginTime` | 登录时间 |

### 7.2 get_account_info（账户信息）

| 字段(英文) | 中文 |
|------------|------|
| `Account` | 资金账号 |
| `CustomerName` | 客户姓名 |
| `TotalAssets` | 总资产 |
| `AvailableCash` | 可用资金 |
| `FrozenCash` | 冻结资金 |
| `MarketValue` | 持仓市值 |
| `ProfitLoss` | 盈亏 |
| `ProfitRate` | 盈亏比例 |
| `HoldCount` | 持仓数量 |

### 7.3 get_position_list（持仓列表）

| 字段(英文) | 中文 |
|------------|------|
| `Code` | 证券代码 |
| `Name` | 证券名称 |
| `HoldVolume` | 持仓数量 |
| `AvailableVolume` | 可用数量 |
| `CostPrice` | 成本价 |
| `CurrentPrice` | 现价 |
| `MarketValue` | 市值 |
| `ProfitLoss` | 盈亏金额 |
| `ProfitRate` | 盈亏比例 |
| `FrozenVolume` | 冻结数量 |

### 7.4 get_order_list / get_deal_list（委托 / 成交）

| 字段(英文) | 中文 |
|------------|------|
| `OrderID` | 委托编号 |
| `Code` | 证券代码 |
| `Name` | 证券名称 |
| `OrderTime` | 委托时间 |
| `OrderVolume` | 委托数量 |
| `OrderPrice` | 委托价格 |
| `OrderType` | 委托类型（买入/卖出） |
| `DealVolume` | 成交数量 |
| `DealPrice` | 成交均价 |
| `DealAmount` | 成交金额 |
| `Status` | 委托状态 |
| `StatusText` | 状态描述 |
| `CancelFlag` | 撤单标记 |


---

## 八、通用 / 初始化接口返回字段映射

### 8.1 init / deinit（初始化 / 反初始化）

| 字段(英文) | 中文 |
|------------|------|
| `Status` | 状态码（0成功） |
| `Message` | 状态信息 |
| `Version` | 版本号 |
| `APILevel` | API等级 |

### 8.2 get_security_list（获取证券列表）

| 字段(英文) | 中文 |
|------------|------|
| `Code` | 证券代码 |
| `Name` | 证券名称 |
| `Market` | 所属市场 |
| `Type` | 证券类型 |

### 8.3 get_market_list（获取市场列表）

| 字段(英文) | 中文 |
|------------|------|
| `MarketCode` | 市场代码 |
| `MarketName` | 市场名称 |
| `MarketType` | 市场类型 |

### 8.4 get_trade_date（获取交易日）

| 字段(英文) | 中文 |
|------------|------|
| `Date` | 日期（YYYYMMDD） |
| `IsTradeDate` | 是否交易日 |

### 8.5 get_server_time（服务器时间）

| 字段(英文) | 中文 |
|------------|------|
| `ServerTime` | 服务器时间（HHMMSS） |
| `LocalTime` | 本地时间 |


---

## 附录：常见常量与枚举

| 常量(英文) | 值 | 中文含义 |
|------------|----|----------|
| `TDX_SH` | 1 | 上海市场 |
| `TDX_SZ` | 0 | 深圳市场 |
| `TDX_BJ` | 2 | 北京市场 |
| `TDX_HK` | 3 | 香港市场 |
| `MIN1` | 0 | 1分钟K线 |
| `MIN5` | 1 | 5分钟K线 |
| `MIN15` | 2 | 15分钟K线 |
| `MIN30` | 3 | 30分钟K线 |
| `HOUR1` | 4 | 1小时K线 |
| `DAY` | 9 | 日线 |
| `WEEK` | 10 | 周线 |
| `MONTH` | 11 | 月线 |
| `YEAR` | 12 | 年线 |
| `BUY` | 0 | 买入 |
| `SELL` | 1 | 卖出 |
| `CANCEL` | 2 | 撤单 |
| `ORDER_LIMIT` | 1 | 限价委托 |
| `ORDER_MARKET` | 2 | 市价委托 |
| `STATUS_SUCCESS` | 0 | 成功 |
| `STATUS_FAIL` | 1 | 失败 |
| `STATUS_PENDING` | 2 | 待处理 |
| `STATUS_FILLED` | 3 | 已成交 |
| `STATUS_PARTIAL` | 4 | 部分成交 |
| `STATUS_CANCELED` | 5 | 已撤单 |
