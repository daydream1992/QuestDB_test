"""p24: 龙虎榜营业部网络

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p24_lhb_broker_network.py
用途: 知名游资营业部多次上榜的票 + alpha 筛选
数据源: QuestDB qd_lhb_detail 表 + ctx.alpha_df + config.broker_list
依赖: strategy.base, config.broker_list
说明:
  - 同一营业部 3 日内上榜 ≥2 次 = 强关注
  - 配合 xs_lhb_broker_rank 因子高分
  - T+1 数据
"""

from typing import List
from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry
from config.broker_list import FAMOUS_BROKERS


@StrategyRegistry.register
class LhbBrokerNetworkStrategy(StrategyBase):
    name = 'lhb_broker_network'
    version = '1.0'
    enabled = True

    def required_fields(self):
        return []

    def evaluate(self, ctx) -> List[Decision]:
        alpha_df = getattr(ctx, 'alpha_df', None)
        if alpha_df is None or alpha_df.empty:
            return []

        decisions = []
        try:
            from lib.qdb import connect, query_df, cutoff

            con = connect()
            try:
                df = query_df(con,
                    "SELECT code, broker_name, buy_amt, sell_amt, lhb_date "
                    "FROM qd_lhb_detail "
                    f"WHERE lhb_date > '{cutoff(days=3)}'")
            finally:
                con.close()

            if df is None or df.empty:
                return []

            # 统计: 同一营业部 3 日内同一票上榜次数
            code_heat = {}
            for _, r in df.iterrows():
                broker = str(r.get('broker_name', ''))
                code = r.get('code')
                if not code or not broker:
                    continue
                # 只看知名游资/机构
                is_famous = any(p in broker for p in FAMOUS_BROKERS)
                if not is_famous:
                    continue
                net = _safe_float(r.get('buy_amt')) - _safe_float(r.get('sell_amt'))
                if net <= 0:
                    continue
                code_heat[code] = code_heat.get(code, 0) + 1

            if not code_heat:
                return []

            # 筛 alpha top-30 且营业部关注度高的票
            for code, heat in sorted(code_heat.items(), key=lambda x: -x[1]):
                if heat < 2:
                    continue
                arow = alpha_df[alpha_df['code'] == code]
                if arow.empty:
                    continue
                rank = int(_safe_float(arow.iloc[0].get('rank', 999)))
                if rank > 30:
                    continue

                decisions.append(Decision(
                    action='buy', code=code, strategy=self.name,
                    reason=f'营业部热{heat}次 rank={rank}',
                    position_pct=0,
                    score=min(100.0, 50.0 + heat * 10 + (30 - rank)),
                ))

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning('p24 营业部网络异常: %s', e)

        return decisions[:3]
