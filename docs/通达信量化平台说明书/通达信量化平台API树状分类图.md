# 🗂️ 通达信量化平台 API 树状分类图

> 基于 `k:\tdxdata-master\通达信量化平台说明书` 中的全部接口文档整理，便于快速定位与使用。

---

## 一、API 接口总览（树状结构）

``````
tqcenter.tq
  初始化与通用工具类
    initialize(__file__)                     # 初始化（所有策略必调）
    exec_to_tdx(url)                         # 调用客户端功能（跳转/指标/版面等）
    get_trading_dates()                      # 获取交易日列表
    get_match_stkinfo(key_word)              # 检索证券信息（关键字搜索）
    download_file(file_type, market)         # 下载特定数据文件
    refresh_cache(market, kline_type)        # 刷新行情缓存

  行情类信息
    K线与价量数据
       get_market_data(stock_list, period, start_time, end_time, count, dividend_type, fill_data)
                                             # 获取K线行情（支持分钟/日/周/月等周期 + 复权）
       get_pricevol(stock_list, count)     # 批量获取价量数据
   
    实时与快照
       get_market_snapshot(stock_code, field_list)
                                              # 获取单股快照（现价/五档/涨速/买卖盘等）
   
    证券基本信息
       get_stock_info(stock_code, field_list)
                                             # 获取证券基本信息（名称/交易单位/分类标识等）
       get_more_info(stock_code, field_list)
                                             # 获取股票更多信息
       get_relation(stock_code)            # 获取股票所属板块
   
    股本数据
       get_gb_info(stock_code, field_list) # 获取每天的股本数据
       get_gb_info_by_date(stock_code, date, field_list)
                                              # 根据时间段获取股本数据
   
    新股申购
        get_ipo_info(field_list)            # 获取新股申购信息

  财务类数据
    专业财务数据
       get_financial_data(stock_list, field_list, start_time, end_time, report_type)
                                             # 获取指定时间段专业财务数据（FN1~FN580+）
       get_financial_data_by_date(stock_list, field_list, date, report_type)
                                              # 获取指定日期专业财务数据
   
    单股财务数据
       get_gp_one_data(stock_code, date, field_list)
                                              # 获取股票的单个财务数据
   
    交易数据（市场/板块/股票三个维度）
        市场交易数据
           get_scjy_value(stock_list, field_list, start_time, end_time)
                                             # 获取市场交易数据
           get_scjy_value_by_date(stock_list, field_list, date)
                                              # 获取指定日期市场交易数据
       
        板块交易数据
           get_bkjy_value(stock_list, field_list, start_time, end_time)
                                             # 获取板块交易数据
           get_bkjy_value_by_date(stock_list, field_list, date)
                                              # 获取指定日期板块交易数据
       
        股票交易数据
            get_gpjy_value(stock_list, field_list, start_time, end_time)
                                              # 获取股票交易数据
            get_gpjy_value_by_date(stock_list, field_list, date)
                                               # 获取指定日期股票交易数据

  分类板块
    get_sector_list(list_type)              # 获取A股板块代码列表
    get_stock_list_in_sector(sector_code)   # 获取板块成份股
    get_stock_list(market_code)             # 获取系统分类成份股（如A股全部/ETF/可转债等）

  客户端操作类（自定义板块管理）
    get_user_sector()                       # 获取自定义板块列表
    send_user_block(block_code, stocks, show)
                                              # 添加/更新自定义板块成份股（ZXG = 自选股）
    clear_sector(block_code)                # 清空自定义板块成份股
    create_sector(block_code, block_name)   # 创建自定义板块
    delete_sector(block_code)               # 删除自定义板块
    rename_sector(block_code, block_name)   # 重命名自定义板块

  ETF / 可转债信息
    get_kzz_info(stock_code, field_list)    # 获取可转债信息（转股价/溢价率/强赎价等）
    get_trackzs_etf_info(stock_code, field_list)
                                               # 获取跟踪指数的ETF信息

  通达信公式调用（与客户端公式系统双向互通）
    公式管理
       formula_get_all(formula_type)       # 获取指定种类的公式列表
       formula_get_info(formula_type, formula_code)
                                              # 获取指定公式信息
   
    单股公式计算
       formula_zb(formula_name, formula_arg, stock_code, stock_period, count, dividend_type)
                                             # 调用指标公式计算（单股）
       formula_xg(formula_name, formula_arg, stock_code, stock_period, count, dividend_type)
                                             # 调用选股公式计算（单股）
       formula_exp(formula_name, formula_arg, stock_code, stock_period, count, dividend_type)
                                              # 调用专家系统公式计算（单股）
   
    批量公式计算（推荐使用，无需预设置）
       formula_process_mul_xg(formula_name, formula_arg, return_count, return_date, stock_list, stock_period, start_time, end_time, count, dividend_type)
                                             # 批量调用选股公式（多股并行）
       formula_process_mul_zb(formula_name, formula_arg, xsflag, return_count, return_date, stock_list, stock_period, start_time, end_time, count, dividend_type)
                                              # 批量调用指标公式（多股并行）
   
    数据双向互通
       formula_set_data(stock_code, time_list, data_list, count)
                                             # 向通达信公式设置数据（Python -> 公式）
       formula_set_data_info(info_type, stock_code, time, data_value)
                                             # 向通达信公式设置数据信息
       formula_get_data(stock_code, time, info_type)
                                              # 获取公式中的设置数据（公式 -> Python）
   
    数据格式化
        formula_format_data(data_dict, format_type)
                                               # 格式化K线数据

  实时订阅与推送
    subscribe_hq(stock_list, callback)      # 订阅股票实时更新（最多100只，回调驱动）
    unsubscribe_hq(stock_list)              # 取消订阅股票实时更新
    get_subscribe_hq_stock_list()           # 获得当前订阅列表

  消息与信号发送（与客户端交互）
     send_message(msg_str)                    # 发送消息到通达信客户端TQ策略界面
                                               # （使用 | 或 \n 可以换行/分条显示）
     send_warn(stock_list, time_list, price_list, close_list, volum_list, bs_flag_list, warn_type_list, reason_list, count)
                                               # 发送预警信号到客户端预警系统
     send_file(file)                         # 发送文件到客户端（支持txt/pdf/html）
                                               # （文件放于 .\PYPlugins\file\ 可只传文件名）
     send_bt_data(stock_code, time_list, data_list, count)
                                                # 发送回测数据到客户端（最多16个指标/时间点）
``````

---

## 二、快速使用模板

``````python
from tqcenter import tq

# 1. 初始化（必须）
tq.initialize(__file__)

# 2. 常用操作示例
# -- 获取K线 --
kline = tq.get_market_data(
    stock_list=['600519.SH'],
    period='1d', count=60,
    dividend_type='front')

# -- 获取快照 --
snapshot = tq.get_market_snapshot('600519.SH')

# -- 获取财务数据 --
finance = tq.get_financial_data(
    stock_list=['600519.SH'],
    field_list=['FN193', 'FN194'],
    start_time='20240101')

# -- 批量公式选股 --
result = tq.formula_process_mul_xg(
    formula_name='UPN',
    formula_arg='3',
    stock_list=['600519.SH', '000001.SZ'],
    stock_period='1d', count=5,
    dividend_type=1)

# -- 板块管理 --
tq.send_user_block(block_code='ZXG', stocks=['600519.SH'])

# -- 发送消息 --
tq.send_message('策略运行完成 | 共筛选出 5 只股票')
``````

---

## 三、核心分类速查表

| 分类 | 典型用途 | 关键接口 |
|------|----------|---------|
| **行情数据** | 技术分析、回测 | `get_market_data`、`get_market_snapshot`、`get_pricevol` |
| **财务数据** | 基本面选股、估值 | `get_financial_data`（支持 FN1~FN580+ 字段） |
| **公式系统** | 调用通达信内置/自定义公式 | `formula_process_mul_xg`、`formula_process_mul_zb`（批量推荐） |
| **板块管理** | 自选股/自定义板块维护 | `send_user_block`（ZXG=自选股）、`create_sector` |
| **实时订阅** | 实时监控、预警触发 | `subscribe_hq` + 回调函数 |
| **消息推送** | 结果回显到客户端 | `send_message`、`send_warn`、`send_file` |
| **客户端交互** | 跳转版面/指标/股票 | `exec_to_tdx` |

---

## 四、常用常量速查

### 4.1 市场类型后缀（股票代码后缀 -> 数值）

| 后缀 | 数值 | 说明 |
|------|------|------|
| `.SZ` | 0 | 深圳交易所 |
| `.SH` | 1 | 上海交易所 |
| `.BJ` | 2 | 北京交易所 |
| `.NQ` | 44 | 新三板 |
| `.HK` | 31 | 香港交易所 |
| `.US` | 74 | 美国股票 |
| `.CSI` | 62 | 中证指数 |
| `.CNI` | 102 | 国证指数 |

### 4.2 period 周期类型

| 取值 | 说明 |
|------|------|
| `1m`、`5m`、`15m`、`30m` | 分钟线 |
| `1h` | 60 分钟（1 小时） |
| `1d` | 日线（最常用） |
| `1w` | 周线 |
| `1mon` | 月线 |
| `1q` | 季线 |
| `1y` | 年线 |
| `tick` | 分笔 |

### 4.3 dividend_type 复权类型

| 名称 | 取值(str) | 批量公式中数值 |
|------|-----------|---------------|
| 不复权 | `none` | 0 |
| 前复权 | `front` | 1 |
| 后复权 | `back` | 2 |

### 4.4 order_type 订单类型

| 名称 | 数值 | 说明 |
|------|------|------|
| `STOCK_BUY` | 0 | 买入 |
| `STOCK_SELL` | 1 | 卖出 |
| `CREDIT_FIN_BUY` | 69 | 融资买入 |
| `CREDIT_SLO_SELL` | 70 | 融券卖出 |
| `FUTURE_OPEN_LONG` | 101 | 期货开多 |
| `FUTURE_OPEN_SHORT` | 102 | 期货开空 |
| `FUTURE_CLOSE_LONG` | 103 | 期货平多 |
| `FUTURE_CLOSE_SHORT` | 104 | 期货平空 |

### 4.5 price_type 价格类型

| 名称 | 数值 | 说明 |
|------|------|------|
| `PRICE_MY` | 0 | 自填价 |
| `PRICE_SJ` | 1 | 市价 |
| `PRICE_ZTJ` | 2 | 涨停价/笼子上限 |
| `PRICE_DTJ` | 3 | 跌停价/笼子下限 |

### 4.6 Status 委托状态

| 名称 | 数值 | 说明 |
|------|------|------|
| `WTSTATUS_NOCJ` | 1 | 未成交 |
| `WTSTATUS_PARTCJ` | 2 | 部分成交 |
| `WTSTATUS_ALLCJ` | 3 | 全部成交 |
| `WTSTATUS_BCBC` | 4 | 部分成交部分撤单 |
| `WTSTATUS_ALLCD` | 5 | 全部撤单 |

### 4.7 exec_to_tdx 常用功能串

| 功能串 | 说明和示例 |
|--------|-----------|
| `breed_1#688318` | 跳转到指定股票（1#=沪市，0#=深市，2#=京市；代码前加 `-` 可模糊匹配） |
| `zb_MACD` | 切换到 MACD 指标 |
| `padcode_XX` | 进入用户定制版面（后面是版面简称） |
| `ZXG` | 跳转到自选股列表 |
| `SORT67` | 打开综合排行榜（67） |
| `dlghttp://url` | 对话框方式打开网页 |
| `MAINQH` | 显示为主力期货合约 |

---

## 五、接口分组索引

### A 行情类信息
- 获取K线行情 get_market_data         a行情类信息/获取K线行情.md
- 获取快照数据 get_market_snapshot      a行情类信息/获取快照数据.md
- 批量获取价量 get_pricevol            a行情类信息/批量获取价量.md
- 获取证券基本信息 get_stock_info       a行情类信息/获取证券基本信息.md
- 获取股票更多信息 get_more_info        a行情类信息/获取股票更多信息.md
- 获取股票所属板块 get_relation         a行情类信息/获取股票所属板块.md
- 获取每天的股本数据 get_gb_info        a行情类信息/获取每天的股本数据.md
- 根据时间段获取股本数据 get_gb_info_by_date  a行情类信息/根据时间段获取股本数据.md
- 获取新股申购信息 get_ipo_info         a行情类信息/获取新股申购信息.md

### B 财务类数据
- 获取专业财务数据 get_financial_data             b财务类数据/获取专业财务数据.md
- 获取指定日期专业财务数据 get_financial_data_by_date  b财务类数据/获取指定日期专业财务数据.md
- 获取股票的单个财务数据 get_gp_one_data           b财务类数据/获取股票的单个财务数据.md
- 获取市场交易数据 get_scjy_value                  b财务类数据/获取市场交易数据.md
- 获取指定日期市场交易数据 get_scjy_value_by_date   b财务类数据/获取指定日期市场交易数据.md
- 获取板块交易数据 get_bkjy_value                  b财务类数据/获取板块交易数据.md
- 获取指定日期板块交易数据 get_bkjy_value_by_date   b财务类数据/获取指定日期板块交易数据.md
- 获取股票交易数据 get_gpjy_value                  b财务类数据/获取股票交易数据.md
- 获取指定日期股票交易数据 get_gpjy_value_by_date   b财务类数据/获取指定日期股票交易数据.md

### C 分类板块
- 获取A股板块代码列表 get_sector_list           c分类板块/获取A股板块代码列表.md
- 获取板块成份股 get_stock_list_in_sector         c分类板块/获取板块成份股.md
- 获取系统分类成份股 get_stock_list               c分类板块/获取系统分类成份股.md

### D 客户端操作类
- 自定义板块管理 get_user_sector/send_user_block/...  d客户端操作类/自定义板块管理.md

### E ETF / 可转债
- 获取可转债信息 get_kzz_info                     e ETF可转债/获取可转债信息.md
- 获取跟踪指数的ETF信息 get_trackzs_etf_info       e ETF可转债/获取跟踪指数的ETF信息.md

### F 调用通达信公式
- 批量调用通达信公式 formula_process_mul_xg / formula_process_mul_zb  f 调用通达信公式/批量调用通达信公式.md
- 调用通达信公式进行计算 formula_zb / formula_xg / formula_exp  f 调用通达信公式/调用通达信公式进行计算.md
- 获取指定种类的公式列表 formula_get_all           f 调用通达信公式/获取指定种类的公式列表.md
- 获取指定公式信息 formula_get_info                f 调用通达信公式/获取指定公式信息.md
- 向通达信公式设置数据 formula_set_data            f 调用通达信公式/向通达信公式设置数据.md
- 向通达信公式设置数据信息 formula_set_data_info    f 调用通达信公式/向通达信公式设置数据信息.md
- 获取公式中的设置数据 formula_get_data            f 调用通达信公式/获取公式中的设置数据.md
- 格式化K线数据 formula_format_data               f 调用通达信公式/格式化K线数据.md

### G 通用函数
- 初始化 initialize / 订阅行情 subscribe_hq / 等  通用函数/通用函数.md
- 获取交易日列表 get_trading_dates                通用函数/获取交易日列表.md
- 检索证券信息 get_match_stkinfo                  通用函数/检索证券信息.md
- 下载特定数据文件 download_file                   通用函数/下载特定数据文件.md
- 刷新行情缓存 refresh_cache                       通用函数/刷新行情缓存.md
- 发送消息与信号 send_message / send_warn / send_file / send_bt_data  通用函数/发送消息与信号.md
- 调用客户端功能 exec_to_tdx                       通用函数/调用客户端功能.md
