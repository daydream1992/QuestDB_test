"""
板块联动推断验证脚本
用法: python tests/validate_linkage_concept.py
目的: 用现有 QuestDB 数据跑一次推断，看结果是否合理
"""

import sys
import os
import io
import pandas as pd
from datetime import datetime, timedelta
# 设置 UTF-8 编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime
from lib.qdb import connect, query_df, cutoff
from lib.relation_graph import get_sector_stocks, get_stock_name, _sector_meta, load_from_json
from compute.k6_linkage import calc_linkage_score, _get_size_tier, _calc_dynamic_thresholds, _load_config

# ----------------------------------------------------------------------
# 评分配置（可调整）
# ----------------------------------------------------------------------
WEIGHTS = {
    'net_inflow': 0.40,      # 资金净流入
    'zt_count': 0.25,        # 涨停家数
    'ladder_height': 0.20,   # 连板高度
    'leader_change': 0.15,   # 龙头涨幅
}

THRESHOLDS = {
    'net_inflow_full': 1e9,   # 1亿满分（原10亿过高）
    'zt_count_full': 5,       # 5家满分
    'ladder_full': 3,         # 3板满分
    'leader_full': 5.0,       # 5%满分
}

# calc_linkage_score 复用 compute.k6_linkage 的版本 (支持板块规模分层)


def classify_stock_role(stocks_data):
    """
    个股角色分类（简化版）
    stocks_data: {code: {'change': 涨幅, 'amount': 成交额, 'name': 名称}}
    """
    if not stocks_data:
        return {'龙头': [], '中军': [], '补涨': [], '跟风': []}

    # 按成交额排序
    sorted_by_amount = sorted(stocks_data.items(), key=lambda x: -x[1].get('amount', 0))
    top3_amount = [x[0] for x in sorted_by_amount[:3]]

    # 按涨幅排序
    sorted_by_change = sorted(stocks_data.items(), key=lambda x: -x[1].get('change', -999))

    roles = {'龙头': [], '中军': [], '补涨': [], '跟风': []}

    # 计算板块平均涨幅
    changes = [x[1].get('change', 0) for x in stocks_data.items()]
    avg_change = sum(changes) / len(changes) if changes else 0

    for code, data in stocks_data.items():
        change = data.get('change', 0)
        amount = data.get('amount', 0)
        name = data.get('name', code)

        # 下跌个股不分类
        if change < 0:
            continue

        is_leader_candidate = (code in top3_amount) and (change == sorted_by_change[0][1].get('change', 0))
        is_zhongjun = (change > 3) and (code in [x[0] for x in sorted_by_amount[:len(sorted_by_amount)//3]])

        if is_leader_candidate:
            roles['龙头'].append(f"{name}({code}) +{change:.2f}%")
        elif is_zhongjun:
            roles['中军'].append(f"{name}({code}) +{change:.2f}%")
        elif change < avg_change:
            roles['补涨'].append(f"{name}({code}) +{change:.2f}%")
        else:
            roles['跟风'].append(f"{name}({code}) +{change:.2f}%")

    return roles


def main():
    print("=" * 70)
    print("板块联动推断验证")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()

    con = connect()
    # 查找最新数据时间
    time_sql = "SELECT MAX(flow_time) as t FROM qd_sector_flow"
    time_df = query_df(con, time_sql)
    if not time_df.empty and pd.notna(time_df['t'].iloc[0]):
        latest_time = time_df['t'].iloc[0]
        # 使用时间范围查询（最近5分钟）
        start_time = (latest_time - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%S')
        end_time = (latest_time + timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%S')
        print(f"📅 使用最新数据时间: {latest_time}")
        print(f"   查询时间范围: {start_time} ~ {end_time}")
    else:
        start_time = cutoff(minutes=-5)
        end_time = cutoff()
        latest_time = datetime.now()
        print(f"📅 使用当前时间范围")

    try:
        # 先加载板块成分关系（_sector_meta 需要先初始化才能取板块名称）
        print("📊 加载板块成分关系...")
        load_from_json()
        print(f"   加载完成")

        # ----------------------------------------------------------------------
        # 1. 读取板块资金流
        # ----------------------------------------------------------------------
        print("📊 读取板块资金流...")
        sector_flow_sql = f"""
            SELECT code, main_net
            FROM qd_sector_flow
            WHERE flow_time >= '{start_time}' AND flow_time <= '{end_time}'
            ORDER BY main_net DESC
            LIMIT 50
        """
        sector_flow_df = query_df(con, sector_flow_sql)
        sector_flow = []
        for _, row in sector_flow_df.iterrows():
            code = row['code']
            # 从 _sector_meta 获取板块名称
            meta = _sector_meta.get(code, {})
            name = meta.get('sector_name', code) if meta else code
            sector_flow.append((code, name, row['main_net']))
        print(f"   读取到 {len(sector_flow)} 个板块")

        # ----------------------------------------------------------------------
        # 2. 读取板块快照（涨停家数）
        # ----------------------------------------------------------------------
        print("📊 读取板块快照...")
        sector_snapshot_sql = f"""
            SELECT code, Outside, Inside
            FROM qd_sector_snapshot
            WHERE snapshot_time >= '{start_time}' AND snapshot_time <= '{end_time}'
        """
        sector_snapshot_df = query_df(con, sector_snapshot_sql)
        sector_snapshot = {row['code']: {'zt_count': row['Outside'], 'dt_count': row['Inside']}
                          for _, row in sector_snapshot_df.iterrows()} if not sector_snapshot_df.empty else {}
        print(f"   读取到 {len(sector_snapshot)} 个板块快照")

        # ----------------------------------------------------------------------
        # 3. 读取个股连板数据（用昨天的数据）
        # ----------------------------------------------------------------------
        print("📊 读取个股连板数据（昨天收盘）...")
        # 计算昨天日期
        latest_dt = latest_time if isinstance(latest_time, datetime) else datetime.strptime(str(latest_time)[:19], '%Y-%m-%d %H:%M:%S')
        yesterday = (latest_dt - timedelta(days=1)).strftime('%Y-%m-%d')

        ladder_sql = f"""
            SELECT code, EverZTCount
            FROM qd_stock_daily
            WHERE date = '{yesterday}'
            AND EverZTCount > 0
        """
        ladder_df = query_df(con, ladder_sql)
        stock_ladder = {row['code']: {'lian_ban': int(row['EverZTCount']) if pd.notna(row['EverZTCount']) else 0}
                        for _, row in ladder_df.iterrows()} if not ladder_df.empty else {}
        print(f"   读取到 {len(stock_ladder)} 只连板股（数据日期：{yesterday}）")

        # ----------------------------------------------------------------------
        # 4. 读取个股涨跌幅和成交额 (从 qd_stock_snapshot)
        # ----------------------------------------------------------------------
        print("📊 读取个股涨跌幅和成交额...")
        # 先读快照
        snapshot_sql = f"""
            SELECT code, Now, LastClose, Amount
            FROM qd_stock_snapshot
            WHERE snapshot_time >= '{start_time}' AND snapshot_time <= '{end_time}'
            AND Amount > 0
            ORDER BY Amount DESC
        """
        snapshot_df = query_df(con, snapshot_sql)

        # 再读 intraday（可能时间不完全一致）
        intraday_sql = f"""
            SELECT code, ZAF, FCAmo, FzAmo, snapshot_time
            FROM qd_stock_intraday
            WHERE snapshot_time >= '{start_time}' AND snapshot_time <= '{end_time}'
        """
        intraday_df = query_df(con, intraday_sql)

        # 合并数据
        stocks = {}
        for _, row in snapshot_df.iterrows():
            code = row['code']
            # 计算 ZAF (涨幅)
            if pd.notna(row.get('Now')) and pd.notna(row.get('LastClose')) and row['LastClose'] > 0:
                zaf = (row['Now'] / row['LastClose'] - 1) * 100
            else:
                zaf = 0

            # 获取股票名称
            name = get_stock_name(code)

            stocks[code] = {
                'code': code,
                'name': name,
                'change': zaf,
                'amount': row['Amount'],
            }

        print(f"   读取到 {len(stocks)} 只个股")

        # ----------------------------------------------------------------------
        # 5. 板块成分关系已在最开始加载
        # ----------------------------------------------------------------------

        # ----------------------------------------------------------------------
        # 6. 计算联动评分
        # ----------------------------------------------------------------------
        print()
        print("=" * 70)
        print("📈 板块联动评分 Top 20")
        print("=" * 70)
        print(f"{'排名':<4} {'板块名称':<20} {'评分':<6} {'净流入(亿)':<10} {'涨停':<4} {'连板':<4} {'龙头':<12}")
        print("-" * 70)

        results = []

        for block_code, block_name, main_net in sector_flow[:20]:
            # 获取板块数据
            snapshot = sector_snapshot.get(block_code, {})
            zt_count = snapshot.get('zt_count', 0)

            # 获取板块成分股
            components = get_sector_stocks(block_code)
            if not components:
                print(f"   跳过 {block_code} ({block_name}): 无成分股数据")
                continue

            # 提取成分股代码列表
            component_codes = {c['code'] for c in components}

            # 筛选板块内的个股数据
            block_stocks = {code: data for code, data in stocks.items() if code in component_codes}
            if not block_stocks:
                print(f"   跳过 {block_code} ({block_name}): 无个股数据 ({len(components)} 成分股)")
                continue

            # 找龙头（涨幅最大）
            sorted_by_change = sorted(block_stocks.items(), key=lambda x: -x[1]['change'])
            leader_change = sorted_by_change[0][1]['change'] if sorted_by_change else 0

            # 找最高连板
            max_ladder = 0
            if stock_ladder:  # 如果有连板数据
                for code in component_codes:
                    if code in stock_ladder:
                        max_ladder = max(max_ladder, stock_ladder[code].get('lian_ban', 0))

            # 板块规模分层 → 涨停满分门槛 (大盘/小盘不一致)
            component_count = len(components)
            tier_name, zt_count_full = _get_size_tier(component_count, _load_config())

            # 计算评分 (用板块规模对应的涨停门槛)
            score = calc_linkage_score(main_net, zt_count, max_ladder, leader_change, zt_count_full)

            # 角色分类
            roles = classify_stock_role(block_stocks)

            results.append({
                'block_code': block_code,
                'block_name': block_name,
                'score': score,
                'net_inflow': main_net / 1e8,
                'zt_count': zt_count,
                'ladder': max_ladder,
                'leader_change': leader_change,
                'leader': sorted_by_change[0][1]['name'] if sorted_by_change else '-',
                'roles': roles,
                'tier': tier_name,
                'component_count': component_count,
            })

        # 按评分排序
        results.sort(key=lambda x: -x['score'])

        # 动态阈值 (基于全市场评分分布)
        all_scores = {r['block_code']: r['score'] for r in results}
        opp_threshold, risk_threshold = _calc_dynamic_thresholds(all_scores, _load_config())
        print(f"\n📈 动态阈值: 机会≥{opp_threshold} | 风险≤{risk_threshold} (样本{len(results)}板块)\n")

        # 输出 Top 20
        for i, r in enumerate(results[:20], 1):
            leader_str = f"{r['leader']} +{r['leader_change']:.2f}%" if r['leader'] != '-' else '-'
            print(f"{i:<4} {r['block_name']:<14} {r['score']:>6.1f} {r['net_inflow']:>8.2f} {r['zt_count']:>4} {r['ladder']:>4} {leader_str:<14} [{r['tier']}({r['component_count']})]")

        # ----------------------------------------------------------------------
        # 7. 输出角色分类详情（Top 5 板块）
        # ----------------------------------------------------------------------
        print()
        print("=" * 70)
        print("🎯 个股角色分类详情（Top 5 板块）")
        print("=" * 70)

        for r in results[:5]:
            print()
            print(f"【{r['block_name']}】评分: {r['score']:.1f} | 净流入: {r['net_inflow']:.2f}亿")
            roles = r['roles']
            if roles['龙头']:
                print(f"  🐉 龙头: {', '.join(roles['龙头'][:3])}")
            if roles['中军']:
                print(f"  🐂 中军: {', '.join(roles['中军'][:5])}")
            if roles['补涨']:
                print(f"  🐅 补涨: {', '.join(roles['补涨'][:5])}")

        # ----------------------------------------------------------------------
        # 8. 输出预警信号
        # ----------------------------------------------------------------------
        print()
        print("=" * 70)
        print("🚨 预警信号")
        print("=" * 70)

        # 机会预警
        opportunities = [r for r in results if r['score'] >= opp_threshold and r['leader_change'] >= 5]
        if opportunities:
            print(f"🟢 机会预警 (动态阈值≥{opp_threshold}):")
            for r in opportunities[:5]:
                print(f"   {r['block_name']}({r['score']:.0f}分) [{r['tier']}] 龙头: {r['leader']} +{r['leader_change']:.2f}%")
        else:
            print("🟢 机会预警: 无符合条件板块")

        print()

        # 风险预警
        risks = [r for r in results if r['score'] <= risk_threshold or r['net_inflow'] < -20]
        if risks:
            print(f"🔴 风险预警 (动态阈值≤{risk_threshold}):")
            for r in risks[:5]:
                reason = f"评分{r['score']:.0f}分" if r['score'] <= risk_threshold else f"净流出{abs(r['net_inflow']):.2f}亿"
                print(f"   {r['block_name']} {reason}")
        else:
            print("🔴 风险预警: 无符合条件板块")

        print()
        print("=" * 70)
        print("✅ 推断验证完成")
        print("=" * 70)

    finally:
        con.close()


if __name__ == '__main__':
    main()
