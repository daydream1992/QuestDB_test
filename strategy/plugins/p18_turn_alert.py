"""p18: 变盘预警

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p18_turn_alert.py
用途: 消费 ctx.sentiment.events (k3 跨帧变盘检测), 变盘时提示减仓/观望
依赖: ctx.sentiment (dict, 含 events 列表)
入库: qd_decisions (watch 市场级提示)
说明:
  - 变盘事件由 k3_sentiment.check_turn 检测 (涨停骤降/涨跌比翻转/情绪跨越)
  - k3 已对变盘事件直接 feishu.push_text 推送; 本插件入库记录 + 给持仓侧参考
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_MARKET_CODE = '000001.SH'


@StrategyRegistry.register
class TurnAlertStrategy(StrategyBase):
    name = 'turn_alert'
    version = '1.0'

    def required_fields(self):
        return []  # 用 ctx.sentiment.events, 非 df 列

    def evaluate(self, ctx) -> List[Decision]:
        if not ctx.sentiment:
            return []
        events = ctx.sentiment.get('events') or []
        if not events:
            return []
        descs = '; '.join(e.get('description', '') for e in events[:3])
        return [Decision(
            action='watch', code=_MARKET_CODE, strategy=self.name,
            reason=f'变盘预警: {descs} 建议减仓/观望',
            score=70.0,
        )]
