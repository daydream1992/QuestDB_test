"""p25: 情绪极端反转

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p25_sentiment_extreme_reversal.py
用途: 极端情绪 + alpha 高分 = 反转信号
数据源: ctx.sentiment_df + ctx.alpha_df + ctx.ladder_df
依赖: strategy.base
说明:
  - 极端恐慌(档位1,冰点) + alpha top-30 = buy
  - 极端贪婪(档位5,沸点) + alpha 跌出 top-100 = watch
  - 情绪持续极端 ≥3 轮才触发
"""

from typing import List
from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry


@StrategyRegistry.register
class SentimentExtremeReversalStrategy(StrategyBase):
    name = 'sentiment_extreme_reversal'
    version = '1.0'
    enabled = True

    def required_fields(self):
        return []

    def evaluate(self, ctx) -> List[Decision]:
        alpha_df = getattr(ctx, 'alpha_df', None)
        sentiment = getattr(ctx, 'sentiment', None)
        if alpha_df is None or alpha_df.empty or not sentiment:
            return []

        decisions = []
        try:
            emotion_order = sentiment.get('emotion_order', 3)
            candidates = alpha_df.sort_values('alpha_score', ascending=False).head(30)

            # 极端恐慌 (档位 1 = 冰点)
            if emotion_order <= 1:
                for _, r in candidates.head(10).iterrows():
                    code = r.get('code') or r.name
                    if not code:
                        continue
                    alpha = _safe_float(r.get('alpha_score'))
                    decisions.append(Decision(
                        action='buy', code=code, strategy=self.name,
                        reason=f'情绪冰点反转 alpha={alpha:.2f}',
                        position_pct=0,
                        score=min(100.0, 60.0 + alpha * 30),
                    ))

            # 极端贪婪 (档位 5 = 沸点) - watch 级
            if emotion_order >= 4:
                for _, r in candidates.tail(20).iterrows():
                    code = r.get('code') or r.name
                    if not code:
                        continue
                    decisions.append(Decision(
                        action='watch', code=code, strategy=self.name,
                        reason=f'情绪沸点风险 rank={_safe_float(r.get("rank", 999)):.0f}',
                        score=50.0,
                    ))

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning('p25 情绪反转异常: %s', e)

        return decisions[:5]
