"""p28: 强势板块聚焦 × 票型分类 × 4种信号 (吃肉系统)

脚本路径: K:/QuestDB_test/strategy/plugins/p28_sector_focus_signal.py
用途: 在强势板块内, 按票型(情绪/趋势)用不同阈值捕捉4种买入时机信号
依赖:
  ctx.snapshot_focus_df  - 重点池个股实时数据 (Now/Before5MinNow/MA5/fHSL/Zjl/ZAF)
  ctx.linkage_scores     - 板块联动评分 (板块聚焦过滤)
  ctx.linkage_metrics    - 板块详情 (涨停数/龙头涨幅)
  ctx.stock_types        - 票型分类 (k7 产出: sentiment/trend/mixed)
入库: qd_decisions (由 runner 写入)

核心理念 (用户):
  - 板块联动是"磨刀"(筛猎场), 信号捕捉才是"打猎"(吃肉)
  - 情绪票和趋势票阈值完全不同
  - 不推送已涨停龙头(买不进), 推可操作的启动初期/补涨/低吸

4种信号 (按票型差异化阈值):
  ┌──────────┬─────────────────────┬──────────────────────┐
  │ 信号     │ 情绪票(激进)        │ 趋势票(稳健)         │
  ├──────────┼─────────────────────┼──────────────────────┤
  │ surge急涨│ 5min≥3% 快进快出    │ 5min≥1.5% 趋势确认   │
  │ 补涨埋伏 │ 板块涨停潮+自身滞涨 │ 板块上涨+自身落后    │
  │ 龙头低吸 │ 回踩5日线+未大涨    │ 回踩5日线+主力回流   │
  │ 大单跟单 │ Zjl>1000万(游资)    │ Zjl>5000万(机构)     │
  └──────────┴─────────────────────┴──────────────────────┘

输出分层 (Decision.action):
  buy      → 🔴 立即行动 (情绪票 surge/大单, 启动初期, 飞书秒推)
  observe  → 🟡 关注埋伏 (补涨/低吸/趋势票, 多维表格记录等时机)

频控: 每轮最多输出 2 条 buy (≤2条/分钟 人类注意力上限)
"""

import os
import sys
from typing import List

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry
from lib.relation_graph import get_stock_sectors

import logging
_logger = logging.getLogger(__name__)

# 默认信号阈值 (YAML stock_signal 覆盖)
# ⚠️ 单位: Zjl=万元, 涨幅=%
_DEFAULT_CFG = {
    'surge': {
        'sentiment_min': 3.0,   # 情绪票5分钟涨速≥3%
        'trend_min': 1.5,       # 趋势票≥1.5%
        'exclude_zt': True,     # 排除已涨停(买不进)
    },
    'butian': {  # 补涨埋伏
        'sentiment_sector_zt': 3,    # 情绪票: 板块涨停≥3家
        'sentiment_self_zaf_max': 3.0,  # 且自身涨幅<3%
        'trend_sector_score_ratio': 0.8,  # 趋势票: 板块评分≥机会阈值*0.8
        'trend_self_zaf_max': 2.0,   # 且自身涨幅<2%
    },
    'dixi': {  # 龙头低吸
        'ma5_deviation': 0.02,   # 距5日线2%内视为回踩
        'sentiment_zaf_max': 5.0,  # 情绪票: 涨幅<5%(没大涨)
        'trend_zjl_min': 1000,     # 趋势票: 主力回流Zjl>1000万
    },
    'dadan': {  # 大单跟单
        'sentiment_zjl_min': 1000,  # 情绪票: Zjl>1000万(游资, 万元)
        'trend_zjl_min': 5000,      # 趋势票: Zjl>5000万(机构, 万元)
    },
    'output': {
        'max_buy_per_round': 2,   # 每轮最多2条buy(频控)
    },
}

_CFG = None


def _load_cfg():
    global _CFG
    if _CFG is not None:
        return _CFG
    try:
        import yaml
        yaml_path = os.path.join(_PROJ_ROOT, 'config', 'strategies.yaml')
        if os.path.exists(yaml_path):
            with open(yaml_path, 'r', encoding='utf-8') as f:
                all_cfg = yaml.safe_load(f) or {}
            ss = all_cfg.get('stock_signal', {})
            if ss:
                _CFG = {
                    k: {**_DEFAULT_CFG.get(k, {}), **(ss.get(k) or {})}
                    for k in set(list(_DEFAULT_CFG.keys()) + list(ss.keys()))
                }
                return _CFG
    except Exception as e:
        _logger.warning('p28 配置加载失败: %s', e)
    _CFG = _DEFAULT_CFG
    return _CFG


def _sf(v, default=0.0):
    try:
        r = float(v)
        if r != r:
            return default
        return r
    except (TypeError, ValueError):
        return default


def _is_strong_sector(code, linkage_scores, opp_threshold):
    """个股是否属于强势板块 (板块聚焦过滤)"""
    if not linkage_scores:
        return None
    sectors = get_stock_sectors(code)
    best_block = None
    best_score = 0
    for s in sectors:
        bc = s.get('block_code', '')
        if bc in linkage_scores:
            sc = linkage_scores[bc]
            if sc > best_score:
                best_score = sc
                best_block = s
    # 阈值放宽到机会阈值的60% (让更多板块进入聚焦)
    if best_block and best_score >= opp_threshold * 0.6:
        return {'block_code': best_block['block_code'],
                'block_name': best_block.get('block_name', best_block['block_code']),
                'score': best_score}
    return None


@StrategyRegistry.register
class SectorFocusSignalStrategy(StrategyBase):
    name = 'sector_focus_signal'
    version = '1.0'

    def required_fields(self):
        # snapshot_focus_df 合并了 snapshot + intraday 字段
        return ['Now', 'Before5MinNow', 'MA5Value', 'fHSL', 'Zjl', 'ZAF']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        cfg = _load_cfg()

        snap = getattr(ctx, 'snapshot_focus_df', None)
        if snap is None or snap.empty:
            return decisions

        stock_types = getattr(ctx, 'stock_types', {}) or {}
        linkage_scores = getattr(ctx, 'linkage_scores', {}) or {}
        linkage_metrics = getattr(ctx, 'linkage_metrics', {}) or {}
        thresholds = getattr(ctx, 'linkage_thresholds', {}) or {}
        opp_threshold = thresholds.get('opportunity', 50.0)

        needed = ['code', 'Now', 'Before5MinNow', 'MA5Value', 'fHSL', 'Zjl', 'ZAF', 'FCAmo']
        if not all(c in snap.columns for c in needed):
            return decisions

        # 每只股票最新一行
        if 'snapshot_time' in snap.columns:
            df = snap.sort_values('snapshot_time').groupby('code', as_index=False).last()[needed]
        else:
            df = snap.groupby('code', as_index=False).last()[needed]

        buy_count = 0
        max_buy = cfg.get('output', {}).get('max_buy_per_round', 2)

        signals = []  # 先收集所有信号, 再按优先级排序输出

        for _, r in df.iterrows():
            code = r['code']

            # 板块聚焦过滤: 必须在强势板块
            strong = _is_strong_sector(code, linkage_scores, opp_threshold)
            if not strong:
                continue

            # 票型
            type_info = stock_types.get(code, {})
            stock_type = type_info.get('type', 'mixed')

            now = _sf(r['Now'])
            before5 = _sf(r['Before5MinNow'])
            ma5 = _sf(r['MA5Value'])
            hsl = _sf(r['fHSL'])
            zjl = _sf(r['Zjl'])  # 万元
            zaf = _sf(r['ZAF'])
            fcamo = _sf(r['FCAmo'])

            # 已涨停排除 (买不进)
            is_zt = fcamo > 0

            block_name = strong['block_name']
            block_score = strong['score']

            # ---- 信号1: surge急涨 ----
            surge_cfg = cfg.get('surge', {})
            if before5 > 0 and not (is_zt and surge_cfg.get('exclude_zt', True)):
                chg5 = (now / before5 - 1) * 100
                surge_min = surge_cfg.get(f'{stock_type}_min', 2.0) if stock_type != 'mixed' else 2.0
                if chg5 >= surge_min:
                    signals.append({
                        'priority': 1,  # surge 最高优先级
                        'action': 'buy',
                        'code': code,
                        'signal': 'surge急涨',
                        'stock_type': stock_type,
                        'block_name': block_name,
                        'reason': f'强势板块{block_name}({block_score:.0f}分) {stock_type}票 '
                                  f'5分钟急涨{chg5:.2f}%',
                        'score': min(100, 60 + chg5 * 5),
                    })
                    continue  # 一只票只出一个信号

            # ---- 信号2: 补涨埋伏 ----
            butian_cfg = cfg.get('butian', {})
            sector_zt = linkage_metrics.get(strong['block_code'], {}).get('zt_count', 0)
            is_butian = False
            if stock_type == 'sentiment':
                if sector_zt >= butian_cfg.get('sentiment_sector_zt', 3) and zaf < butian_cfg.get('sentiment_self_zaf_max', 3.0):
                    is_butian = True
            elif stock_type == 'trend':
                if block_score >= opp_threshold * butian_cfg.get('trend_sector_score_ratio', 0.8) and zaf < butian_cfg.get('trend_self_zaf_max', 2.0):
                    is_butian = True
            if is_butian and not is_zt:
                # 文案按票型: 情绪票看涨停潮, 趋势票看板块评分
                if stock_type == 'sentiment':
                    reason = f'强势板块{block_name}(涨停{sector_zt}家) 情绪票滞涨 涨幅{zaf:.2f}% 可埋伏'
                else:
                    reason = f'强势板块{block_name}(评分{block_score:.0f}) 趋势票落后 涨幅{zaf:.2f}% 可埋伏'
                signals.append({
                    'priority': 3,
                    'action': 'observe',
                    'code': code,
                    'signal': '补涨埋伏',
                    'stock_type': stock_type,
                    'block_name': block_name,
                    'reason': reason,
                    'score': min(100, 50 + sector_zt * 2),
                })
                continue

            # ---- 信号3: 龙头低吸 (回踩5日线) ----
            dixi_cfg = cfg.get('dixi', {})
            if ma5 > 0:
                dev = abs(now - ma5) / ma5
                if dev <= dixi_cfg.get('ma5_deviation', 0.02) and not is_zt:
                    dixi_ok = False
                    # 情绪票低吸: 没大涨 + 回踩有承接(主力净流入>阈值)
                    if stock_type == 'sentiment' and zaf < dixi_cfg.get('sentiment_zaf_max', 5.0) \
                            and zjl > dixi_cfg.get('sentiment_zjl_min', 500):
                        dixi_ok = True
                    # 趋势票低吸: 主力机构承接(>5000万)
                    elif stock_type == 'trend' and zjl > dixi_cfg.get('trend_zjl_min', 5000):
                        dixi_ok = True
                    if dixi_ok:
                        signals.append({
                            'priority': 2,
                            'action': 'observe',
                            'code': code,
                            'signal': '龙头低吸',
                            'stock_type': stock_type,
                            'block_name': block_name,
                            'reason': f'{block_name} {stock_type}票回踩5日线 '
                                      f'(偏离{dev*100:.2f}%) 主力Zjl{zjl/1e4:.2f}亿',
                            'score': min(100, 55 + (1 - dev) * 20),
                        })
                        continue

            # ---- 信号4: 大单跟单 ----
            dadan_cfg = cfg.get('dadan', {})
            zjl_min = dadan_cfg.get(f'{stock_type}_zjl_min', 2000) if stock_type != 'mixed' else 2000
            if zjl > zjl_min and not is_zt:
                signals.append({
                    'priority': 2 if stock_type == 'trend' else 1,
                    'action': 'buy' if stock_type == 'sentiment' else 'observe',
                    'code': code,
                    'signal': '大单跟单',
                    'stock_type': stock_type,
                    'block_name': block_name,
                    'reason': f'强势板块{block_name} {stock_type}票主力流入{zjl/1e4:.2f}亿',
                    'score': min(100, 55 + zjl / 1e4),
                })

        # 按优先级 + 评分排序
        signals.sort(key=lambda x: (x['priority'], -x['score']))

        # 输出: buy 受频控, observe 全部记录
        for sig in signals:
            if sig['action'] == 'buy':
                if buy_count >= max_buy:
                    continue  # buy 频控
                buy_count += 1
            decisions.append(Decision(
                action=sig['action'],
                code=sig['code'],
                strategy=self.name,
                reason=sig['reason'],
                position_pct=10 if sig['action'] == 'buy' else 0,
                stop_loss=5 if sig['action'] == 'buy' else 0,
                stop_profit=10 if sig['action'] == 'buy' else 0,
                price=0,
                score=sig['score'],
            ))

        if decisions:
            _logger.info('p28 信号: %d条 (buy=%d, observe=%d)',
                         len(decisions), sum(1 for d in decisions if d.action == 'buy'),
                         sum(1 for d in decisions if d.action == 'observe'))
        return decisions
