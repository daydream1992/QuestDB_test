"""p17: 大盘情绪

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p17_market_emotion.py
用途: 消费 ctx.sentiment (k3_sentiment 产出), 情绪极端时呈现市场级提示
依赖: ctx.emotion_rating (0-4) + ctx.sentiment (dict)
入库: qd_decisions (watch/warn 市场级提示, code=上证指数占位)
说明:
  - 系统定位是"呈现事实"非自动交易: 情绪只作建议呈现, 不拦截策略 buy
    (策略 buy 照常入库推送, 由用户综合判断后自行决策)
  - 不依赖 ctx 各 df 列, required_fields 返回 [] (H1 护栏按 df 列校验, 本插件用 ctx 属性)
  - 低迷/冰点 → 建议空仓观望; 过热 → 高位风险提示
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
            # 冰点/低迷: 呈现事实 + 建议空仓 (不拦截 buy, 由用户自行决策)
            return [Decision(
                action='watch', code=_MARKET_CODE, strategy=self.name,
                reason=f'大盘情绪{emo}(冰点/低迷) 涨停{ctx.sentiment.get("zt_cnt", 0)} '
                       f'涨跌比{ctx.sentiment.get("udr", 0):.2f} 建议空仓观望',
                score=self.normalize_score(float(order), 0, 4),
            )]
        if order >= 4:
            # 过热: 高位风险提示 (高潮兑现风险, 警惕接盘)
            return [Decision(
                action='warn', code=_MARKET_CODE, strategy=self.name,
                reason=f'大盘情绪{emo}(过热) 高潮兑现风险 注意减仓警惕接盘',
                score=self.normalize_score(float(order), 0, 4),
            )]
        return []
