"""策略上下文

脚本路径: K:\QuestDB_test\\strategy\\context.py
用途: 一次采集全策略共享的数据容器, 避免各策略重复取数
依赖: pandas, datetime
数据源:
  - pricevol_df       全场价量 (qd_pricevol)
  - snapshot_focus_df 重点快照 (qd_stock_snapshot)
  - more_info_df      88 字段 (qd_stock_snapshot intraday / qd_stock_daily)
  - indicators_df     指标 (qd_indicators)
  - signals_df        原子信号 (qd_signals)
  - graph             关系图谱 (lib.relation_graph 内存映射)
说明:
  - dataclass 统一承载所有策略所需数据, 一次采集多次复用
  - get_stock_data 按 code 聚合个股的价量与指标字段
  - 持仓 positions 为 list[dict], 供风控与出场策略使用
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class StrategyContext:
    timestamp: datetime
    is_trading: bool = False

    # 个股数据
    pricevol_df: Optional[pd.DataFrame] = None       # 全场价量
    snapshot_focus_df: Optional[pd.DataFrame] = None # 重点快照 (c2 快照列 + C8拆表后 merge 进的 intraday 列)
    intraday_df: Optional[pd.DataFrame] = None       # 个股 intraday (qd_stock_intraday, c3 拆出, 原始保留)
    more_info_df: Optional[pd.DataFrame] = None      # 88 字段
    indicators_df: Optional[pd.DataFrame] = None     # 指标
    signals_df: Optional[pd.DataFrame] = None        # 原子信号

    # 关系图谱
    graph: object = None  # RelationGraph

    # 分析结果
    resonance_df: Optional[pd.DataFrame] = None      # 共振分析
    sector_flow_df: Optional[pd.DataFrame] = None    # 板块资金流
    money_flow_df: Optional[pd.DataFrame] = None     # 个股明暗资金
    rotation_signal: Optional[dict] = None           # 轮动信号
    big_order_df: Optional[pd.DataFrame] = None      # 大单事件
    lhb_data: Optional[list] = None                  # 龙虎榜
    auction_df: Optional[pd.DataFrame] = None        # 竞价数据

    # 大盘环境
    index_snapshot: Optional[dict] = None            # 上证/深证/创业板

    # 持仓
    positions: List[dict] = field(default_factory=list)

    # 大盘情绪 (k3_sentiment 写入, p17/p18 + buy 门控消费)
    sentiment: Optional[dict] = None
    emotion_rating: Optional[int] = None       # 0-4 (冰点/低迷/中性/活跃/过热)
    divergence_signals: Optional[list] = None

    def get_stock_data(self, code) -> dict:
        """按 code 查个股全部数据"""
        result = {'code': code}
        if self.pricevol_df is not None and not self.pricevol_df.empty:
            row = self.pricevol_df[self.pricevol_df['code'] == code]
            if not row.empty:
                result.update(row.iloc[0].to_dict())
        if self.indicators_df is not None and not self.indicators_df.empty:
            row = self.indicators_df[self.indicators_df['code'] == code]
            if not row.empty:
                result.update(row.iloc[0].to_dict())
        return result
