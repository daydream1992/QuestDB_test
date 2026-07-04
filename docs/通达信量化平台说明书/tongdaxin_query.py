"""
通达信量化平台 API 查询工具
用于查询字段和接口的对应关系
"""

# ==================== 行情类信息接口 ====================

MARKET_APIS = {
    "get_market_data": {
        "category": "行情类",
        "description": "获取K线行情",
        "fields": [
            "Date", "Time", "Open", "High", "Low", "Close",
            "Volume", "Amount", "ForwardFactor", "VolInStock"
        ]
    },
    "get_market_snapshot": {
        "category": "行情类",
        "description": "获取快照数据",
        "fields": [
            "ItemNum", "LastClose", "Open", "Max", "Min", "Now",
            "Volume", "NowVol", "Amount", "Inside", "Outside",
            "TickDiff", "InOutFlag", "Jjjz", "Buyp", "Buyv",
            "Sellp", "Sellv", "UpHome", "DownHome", "Before5MinNow",
            "Average", "XsFlag", "Zangsu", "ZAFPre3"
        ]
    },
    "get_stock_info": {
        "category": "行情类",
        "description": "获取证券基本信息",
        "fields": [
            # 基本信息
            "Name", "Unit", "VolBase", "MinPrice", "XsFlag", "Fz",
            "DelayMin", "QHVolBaseRate", "HKVolBaseRate",
            # 分类标识
            "BelongHS300", "BelongHasKQZ", "BelongRZRQ", "BelongHSGT",
            "IsHKGP", "IsQH", "IsQQ", "IsSTGP", "IsQuitGP",
            "TodayDRFlag", "HSStockKind",
            # 股本与资产
            "ActiveCapital", "J_zgb", "J_bg", "J_hg",
            "J_zzc", "J_ldzc", "J_gdzc", "J_wxzc",
            "J_ldfz", "J_cqfz", "J_zbgjj", "J_jzc",
            # 收入与利润
            "J_yysy", "J_yycb", "J_yszk", "J_yyly",
            "J_tzsy", "J_jyxjl", "J_zxjl", "J_ch",
            "J_lyze", "J_shly", "J_jly", "J_wfply",
            # 每股指标
            "J_jyl", "J_mgwfp", "J_mgsy", "J_mgsy2",
            "J_mggjj", "J_mgjzc", "J_mgjzc2",
            "J_gdqyb", "J_gdrs", "J_HalfYearFlag",
            # 其他
            "J_start", "tdx_dycode", "tdx_dyname",
            "rs_hycode_sim", "rs_hyname",
            "blockzscode", "underly_setcode", "underly_code"
        ]
    },
    "get_more_info": {
        "category": "行情类",
        "description": "获取股票更多信息",
        "fields": [
            # 基本与形态
            "MainBusiness", "SafeValue", "ShineValue", "ShapeValue",
            "TPFlag", "ZTPrice", "DTPrice", "HqDate",
            # 成交量与市值
            "fHSL", "fLianB", "Wtb", "Zsz", "Ltsz",
            "vzangsu", "Fzhsl", "FzAmo", "FreeLtgb",
            # 涨幅类
            "VOpenZAF", "ZAF", "ZAFYesterday", "ZAFPre2D",
            "ZAFPre5", "ZAFPre10", "ZAFPre20", "ZAFPre30",
            "ZAFPre60", "ZAFYear", "ZAFPreMyMonth", "ZAFPreOneYear",
            "ConZAFDateNum",
            # 资金流向
            "Zjl", "Zjl_HB", "TotalBVol", "TotalSVol",
            "BCancel", "SCancel", "L2TicNum", "L2OrderNum",
            # 涨停封板
            "FCAmo", "FCb", "OpenAmo", "OpenZTBuy",
            "OpenAmoPre1", "OpenVolPre1", "CJJEPre1", "CJJEPre3",
            "FDEPre1", "FDEPre2", "ZTGPNum", "LastStartZT",
            "LastZTHzNum", "EverZTCount", "YearZTDay",
            # 价格与估值
            "MA5Value", "HisHigh", "HisLow", "IPO_Price",
            "More_YJL", "BetaValue", "DynaPE", "MorePE",
            "StaticPE_TTM", "DYRatio", "PB_MRQ",
            # 类型标识
            "IsT0Fund", "IsZCZGP", "IsKzz", "Kzz_HSCode",
            "QHMainYYMM", "Yield",
            # 财务指标
            "KfEarnMoney", "RDInputFee", "CashZJ", "PreReceiveZJ",
            "OtherQYJzc", "StaffNum",
            # 关键日期
            "RecentGGJYDate", "RecentHGDate", "RecentIncentDate",
            "NoticeDate_Recent", "RecentReleaseDate", "RecentDZDate",
            "ReportDate", "ZTDate_Recent", "DTDate_Recent",
            "TopDate_Recent", "StopJYDate_Recent"
        ]
    },
    "get_pricevol": {
        "category": "行情类",
        "description": "批量获取价量",
        "fields": ["LastClose", "Now", "Volume"]
    },
    "get_relation": {
        "category": "行情类",
        "description": "获取股票所属板块",
        "fields": ["BlockCode", "BlockName", "BlockType", "GPNume"]
    },
    "get_gb_info": {
        "category": "行情类",
        "description": "获取每天的股本数据",
        "fields": ["Date", "Zgb", "Ltgb"]
    },
    "get_gb_info_by_date": {
        "category": "行情类",
        "description": "根据时间段获取股本数据",
        "fields": ["Date", "Zgb", "Ltgb"]
    }
}

# ==================== 财务类数据接口 ====================

FINANCIAL_APIS = {
    "get_financial_data": {
        "category": "财务类",
        "description": "获取专业财务数据",
        "fields_prefix": "FN",
        "fields": [
            # 每股指标 (FN1-FN7)
            "FN1:基本每股收益", "FN2:扣除非经常性损益每股收益", "FN3:每股未分配利润",
            "FN4:每股净资产", "FN5:每股资本公积金", "FN6:净资产收益率", "FN7:每股经营现金流量",
            # 资产类 (FN8-FN40)
            "FN8:货币资金", "FN9:交易性金融资产", "FN10:应收票据", "FN11:应收账款",
            "FN12:预付款项", "FN13:其他应收款", "FN17:存货", "FN21:流动资产合计",
            "FN27:固定资产", "FN33:无形资产", "FN35:商誉", "FN40:资产总计",
            # 负债类 (FN41-FN63)
            "FN41:短期借款", "FN44:应付账款", "FN45:预收款项", "FN54:流动负债合计",
            "FN55:长期借款", "FN63:负债合计",
            # 权益类 (FN64-FN72)
            "FN64:实收资本", "FN68:未分配利润", "FN69:少数股东权益", "FN72:所有者权益合计",
            # 现金流量 (FN98-FN158)
            "FN107:经营活动现金流量净额", "FN119:投资活动现金流量净额", "FN128:筹资活动现金流量净额",
            # 财务指标 (FN159-FN200)
            "FN197:净资产收益率", "FN199:销售净利率", "FN200:总资产净利率",
            # 其他常用字段
            "FN230:营业收入", "FN232:归母净利润", "FN238:总股本", "FN239:已上市流通A股",
            "FN266:自由流通股", "FN242:股东人数"
        ]
    },
    "get_financial_data_by_date": {
        "category": "财务类",
        "description": "获取指定日期专业财务数据",
        "fields_prefix": "FN",
        "note": "字段同 get_financial_data"
    },
    "get_gp_one_data": {
        "category": "财务类",
        "description": "获取股票的单个财务数据",
        "fields": [
            "GO1:发行价", "GO2:总发行数量", "GO3:一致预期目标价",
            "GO4:一致预期T年度", "GO5-T7:T年/T+1/T+2每股收益",
            "GO8-T10:T年/T+1/T+2净利润", "GO11-T13:T年/T+1/T+2营业收入",
            "GO26:最新解禁日", "GO27:最新解禁数量",
            "GO29:最新持股机构家数", "GO30:最新机构持股总量",
            "GO33:最新总股本", "GO34:最新实际流通A股",
            "GO35-GO39:业绩预告相关", "GO40-GO41:业绩快报"
        ]
    },
    "get_gpjy_value": {
        "category": "财务类",
        "description": "获取股票交易数据",
        "fields": [
            "GP01:股东人数", "GP02:龙虎榜买卖", "GP03:融资融券余额",
            "GP14:涨停数据", "GP15:涨跌停状态", "GP16:总市值",
            "GP25:盘前盘后成交量"
        ]
    },
    "get_bkjy_value": {
        "category": "财务类",
        "description": "获取板块交易数据",
        "fields": [
            "BK5:市盈率TTM", "BK6:市净率MRQ", "BK7:市销率TTM",
            "BK9:涨跌数", "BK12:涨停数", "BK13:跌停数"
        ]
    },
    "get_scjy_value": {
        "category": "财务类",
        "description": "获取市场交易数据",
        "fields": [
            "SC01:融资融券余额", "SC03:沪深京涨停股个数", "SC04:沪深京跌停股个数",
            "SC11:大宗交易", "SC15:打板资金", "SC31:涨跌家数"
        ]
    }
}

# ==================== 分类板块接口 ====================

SECTOR_APIS = {
    "get_stock_list": {
        "category": "分类板块",
        "description": "获取系统分类成份股",
        "fields": ["Code", "Name"],
        "note": "list_type=0只返回Code, list_type=1返回Code和Name"
    },
    "get_sector_list": {
        "category": "分类板块",
        "description": "获取A股板块代码列表",
        "fields": ["Code", "Name"],
        "note": "相当于get_stock_list('10')"
    },
    "get_stock_list_in_sector": {
        "category": "分类板块",
        "description": "获取板块成份股",
        "fields": ["Code", "Name"],
        "note": "block_type=0板块指数, block_type=1自定义板块"
    }
}

# 合并所有API
ALL_APIS = {**MARKET_APIS, **FINANCIAL_APIS, **SECTOR_APIS}


def _extract_field_key(field: str) -> str:
    """提取字段名（去掉冒号后的描述）"""
    if ":" in field:
        return field.split(":")[0]
    return field


def _get_fields(api_info: dict) -> list:
    """获取接口的字段列表"""
    if "fields" in api_info:
        return api_info["fields"]
    elif "fields_prefix" in api_info:
        # 对于FN系列，只返回前缀
        return [api_info["fields_prefix"]]
    return []


def find_field_in_apis(field_name: str, case_sensitive: bool = False) -> list:
    """
    查询某字段在哪些接口中出现

    Args:
        field_name: 要查询的字段名
        case_sensitive: 是否区分大小写

    Returns:
        包含该字段的接口列表
    """
    results = []

    for api_name, api_info in ALL_APIS.items():
        fields = _get_fields(api_info)

        # 如果是FN系列，需要特殊处理
        if api_info.get("fields_prefix") == "FN":
            # 检查字段是否以FN开头
            if case_sensitive:
                match = field_name.startswith(api_info["fields_prefix"])
            else:
                match = field_name.upper().startswith(api_info["fields_prefix"])
            if match:
                results.append({
                    "api": api_name,
                    "category": api_info["category"],
                    "description": api_info["description"]
                })
            continue

        for field in fields:
            field_key = _extract_field_key(field)

            if case_sensitive:
                match = field_key == field_name
            else:
                match = field_key.lower() == field_name.lower()

            if match:
                results.append({
                    "api": api_name,
                    "category": api_info["category"],
                    "description": api_info["description"]
                })
                break

    return results


def get_api_fields(api_name: str) -> dict:
    """
    查询某接口返回哪些字段

    Args:
        api_name: 接口名称

    Returns:
        接口信息字典，包含category, description, fields
    """
    return ALL_APIS.get(api_name, None)


def find_duplicates() -> dict:
    """
    检测字段重叠情况

    Returns:
        重叠字段字典，key为字段名，value为出现的接口列表
    """
    field_to_apis = {}

    for api_name, api_info in ALL_APIS.items():
        fields = _get_fields(api_info)

        # 如果是FN系列，添加通配符表示
        if api_info.get("fields_prefix") == "FN":
            if "FN*" not in field_to_apis:
                field_to_apis["FN*"] = []
            field_to_apis["FN*"].append({
                "api": api_name,
                "category": api_info["category"]
            })
            continue

        for field in fields:
            field_key = _extract_field_key(field)

            if field_key not in field_to_apis:
                field_to_apis[field_key] = []

            field_to_apis[field_key].append({
                "api": api_name,
                "category": api_info["category"]
            })

    # 筛选出重复字段
    duplicates = {k: v for k, v in field_to_apis.items() if len(v) > 1}

    return duplicates


def print_duplicates():
    """打印所有字段重叠情况"""
    dups = find_duplicates()
    print(f"\n{'='*60}")
    print(f"字段重叠检测结果 (共 {len(dups)} 个重叠字段)")
    print(f"{'='*60}\n")

    for field, apis in sorted(dups.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"字段: {field}")
        print(f"  出现在 {len(apis)} 个接口:")
        for api_info in apis:
            print(f"    - [{api_info['category']}] {api_info['api']}")
        print()


if __name__ == "__main__":
    print("通达信量化平台 API 查询工具")
    print("="*40)
    print("示例用法:")
    print("  from tongdaxin_query import find_field_in_apis, get_api_fields, find_duplicates")
    print()
    print("  # 查询字段在哪些接口中出现")
    print("  find_field_in_apis('Volume')")
    print()
    print("  # 查询接口返回哪些字段")
    print("  get_api_fields('get_market_data')")
    print()
    print("  # 检测字段重叠")
    print("  find_duplicates()")
    print("  print_duplicates()")
    print()
    print("="*40)
