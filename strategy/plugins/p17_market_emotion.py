"""p17: 大盘情绪

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p17_market_emotion.py
用途: 消费 ctx.sentiment (k3_sentiment 产出), 情绪极端时提示; buy 拦截由 _process_decisions 门控
依赖: ctx.emotion_rating (0-4) + ctx.sentiment (dict)
入库: qd_decisions (watch/warn 市场级提示, code=上证指数占位)
说明:
  - 不依赖 ctx 各 df 列, required_fields 返回 [] (H1 护栏按 df 列校验, 本插件用 ctx 属性)
  - 真正的 buy 拦截在 _process_decisions 的 emotion 门控 (emotion_order<=1 冰点/低迷)
  - 本插件产出市场级提示决策 (飞书推送由 k3 变盘事件直接 push_text, 此处仅入库记录)
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_MARKET_CODE = '000001.SH'  # 上证指数 (市场级提示占位 code)


@StrategyRegistry.register
class MarketEmotionStrategy(StrategyBase):
    name = 'market_emotion'
    version = '1.0'

    def required_fields(self):
        return []  # 用 ctx.emotion_rating/ctx.sentiment, 非 df 列

    def evaluate(self, ctx) -> List[Decision]:
        if ctx.emotion_rating is None or not ctx.sentiment:
            return []
        emo = ctx.sentiment.get('emotion', '')
        order = ctx.emotion_rating
        if order <= 1:
            # 冰点/低迷: 建议观望 (buy 已被 _process_decisions 门控拦截)
            return [Decision(
                action='watch', code=_MARKET_CODE, strategy=self.name,
                reason=f'大盘情绪{emo}(冰点/低迷) 涨停{ctx.sentiment.get("zt_cnt", 0)} '
                       f'涨跌比{ctx.sentiment.get("udr", 0):.2f} buy已门控, 建议观望',
                score=float(order),
            )]
        if order >= 4:
            # 过热: 高位风险提示
            return [Decision(
                action='warn', code=_MARKET_CODE, strategy=self.name,
                reason=f'大盘情绪{emo}(过热) 高位风险 注意减仓',
                score=float(order),
            )]
        return []
