"""p27: 板块联动

脚本路径: K:/QuestDB_test/strategy/plugins/p27_sector_linkage.py
用途: 基于板块联动评分的选股策略
依赖: ctx.linkage_alerts / ctx.linkage_thresholds (来自 compute/k6_linkage.py)
入库: qd_decisions (由 runner 写入)

阈值来源 (动态, 不硬编码):
  - ctx.linkage_thresholds: k6_linkage 根据全市场评分分位数动态计算 (P90机会/P15风险)
  - 回退: 从 k6_linkage._load_config() 读取 YAML 配置的 fixed 阈值

个股角色:
  - 龙头: 涨幅最高 AND 成交额最大 → 重点出击
  - 中军: 涨幅>3% AND 成交额前30% → 趋势股，回踩20日线低吸
  - 补涨: 涨幅>0 AND 涨幅<板块平均 → 提前埋伏，快进快出
  - 跟风: 其他上涨个股 → 规避
"""

from typing import List, Dict, Any

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

import logging
_logger = logging.getLogger(__name__)


def _safe_float(v, default=0.0) -> float:
    """安全转换浮点数"""
    try:
        r = float(v)
    except (TypeError, ValueError):
        return default
    if r != r:  # NaN check
        return default
    return r


@StrategyRegistry.register
class SectorLinkageStrategy(StrategyBase):
    name = 'sector_linkage'
    version = '1.0'

    def required_fields(self):
        # 依赖 k6_linkage 计算的 ctx 数据
        return ['linkage_alerts']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []

        # 优先用动态阈值筛选好的 alerts (k6_linkage 已用分位数阈值筛选)
        alerts = getattr(ctx, 'linkage_alerts', None)
        if alerts is None:
            return decisions

        opportunities = alerts.get('opportunity', [])
        if not opportunities:
            return decisions

        # 按评分排序，Top1 限制 (避免刷屏, 符合 ≤2条/分钟 频控)
        sorted_opp = sorted(opportunities, key=lambda x: -x.get('score', 0))

        for opp in sorted_opp[:1]:  # 每轮最多推送 1 个机会 (频控)
            leader_code = opp.get('leader_code', '')
            leader_name = opp.get('leader_name', leader_code)
            leader_change = _safe_float(opp.get('leader_change', 0))
            score = _safe_float(opp.get('score', 0))
            block_name = opp.get('block_name', opp.get('block_code', ''))
            threshold = _safe_float(opp.get('threshold', 0))

            if not leader_code:
                continue

            # 观察名单: 板块联动龙头只呈现, 不占 buy 频控额度
            # (龙头常已涨停买不进; 且避免与 p28 的 buy 叠加超过 ≤2条/分钟)
            decisions.append(Decision(
                action='observe',
                code=leader_code,
                strategy=self.name,
                reason=f'板块联动{block_name}{score:.0f}分(动态阈值{threshold:.0f}) '
                       f'龙头{leader_name}+{leader_change:.2f}%(观察)',
                position_pct=0,
                stop_loss=0,
                stop_profit=0,
                price=0,
                score=score,
            ))

        return decisions

    def get_risk_alerts(self, ctx) -> List[Dict[str, Any]]:
        """获取风险预警（不产生决策，仅提示）

        Returns:
            List[Dict]: [{block_code, block_name, score, reason}]
        """
        alerts = getattr(ctx, 'linkage_alerts', None)
        if alerts is None:
            return []

        risk_list = alerts.get('risk', [])
        return [{
            'block_code': r.get('block_code', ''),
            'block_name': r.get('block_name', ''),
            'score': _safe_float(r.get('score', 0)),
            'net_inflow': _safe_float(r.get('net_inflow', 0)),
            'reason': f'板块联动{r.get("score", 0):.0f}分（弱势）',
        } for r in risk_list]
