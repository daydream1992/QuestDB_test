"""知名游资/机构营业部列表

映射: 营业部名称 → 身份标识
用途: 龙虎榜分析时识别游资/机构/北向资金
维护: 持续更新, 发现新游资及时补充
"""

FAMOUS_BROKERS = {
    # === 游资 ===
    '东方财富证券拉萨团结路第二营业部': 'hot_money_xz',
    '东方财富证券拉萨东环路第二营业部': 'hot_money_xz',
    '东方财富证券拉萨东环路营业部': 'hot_money_xz',
    '华鑫证券深圳分公司': 'hot_money_hz',
    '华泰证券总部': 'hot_money_ht',
    '国泰君安证券上海江苏路营业部': 'hot_money_zg',
    '财通证券杭州体育场路营业部': 'hot_money_hz',
    '中国银河证券绍兴营业部': 'hot_money_zx',
    '中信证券上海溧阳路营业部': 'hot_money_zx',
    '国信证券深圳泰然九路营业部': 'hot_money_gs',
    '招商证券深圳蛇口工业三路营业部': 'hot_money_zs',
    '平安证券深圳深南东路罗湖商务中心营业部': 'hot_money_pa',
    '申万宏源证券上海闵行区营业部': 'hot_money_sw',
    '海通证券杭州解放路营业部': 'hot_money_ht2',
    '光大证券宁波解放南路营业部': 'hot_money_gd',
    '银河证券绍兴营业部': 'hot_money_zx',
    '方正证券杭州保椒路营业部': 'hot_money_fz',
    '浙商证券杭州杭大路营业部': 'hot_money_zs2',

    # === 机构 ===
    '机构专用': 'institution',

    # === 北向资金 ===
    '沪股通专用': 'north_sh',
    '深股通专用': 'north_sz',
}

BROKER_LABELS = {
    'hot_money_xz': '拉萨天团',
    'hot_money_hz': '杭州游资',
    'hot_money_zg': '上海游资',
    'hot_money_zx': '绍兴游资',
    'hot_money_ht': '华泰总部',
    'hot_money_gs': '国信泰然',
    'hot_money_zs': '招商蛇口',
    'hot_money_pa': '平安深南',
    'hot_money_sw': '申万宏源',
    'hot_money_ht2': '海通杭州',
    'hot_money_gd': '光大宁波',
    'hot_money_fz': '方正杭州',
    'hot_money_zs2': '浙商杭大',
    'institution': '机构',
    'north_sh': '沪股通',
    'north_sz': '深股通',
}

BROKER_TYPE = {
    'hot_money': '游资',
    'institution': '机构',
    'north': '北向',
}
