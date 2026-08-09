"""testv10.2 取数层 (ticker) — 全场预筛 + 个股钻取

脚本路径: K:/QuestDB_test/testv10.2/ticker.py
用途: funnel step1 全场 pricevol 预筛 (pct) + step5 个股钻取 (more_info+snapshot 双调用)。
依赖: lib/tq_client (safe_call), lib/tq_utils (fetch_all_codes 代码表)
克隆: testv10.1/ticker.py (scan_universe + 线程超时双调用模式)
说明: 本模块只取数; 板块打分在 meso_radar, 计算在下游各 monitor (sentiment/rotation/...)。
      init/close 由调用方 (radar_main) 管。
"""

import bootstrap
bootstrap.ensure_paths()

import time  # noqa: E402

import pandas as pd  # noqa: E402
from loguru import logger  # noqa: E402

from lib.tq_client import safe_call  # noqa: E402
from lib.tq_utils import fetch_all_codes  # noqa: E402
from tqcenter import tq  # noqa: E402
import settings as cfg  # noqa: E402

_CODES_CACHE: list[str] | None = None
_CODES_TS = 0.0


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _limit_pct(now_v: float, last_close: float, zt_price: float) -> float:
    """涨停比例 (涨幅/涨停幅度, 0~1): 相对昨收 + 相对涨停价 双近似取更可信的一个。

    目的: 归一化涨幅因子时区分 10% 主板 / 20% 科创创业 / 30% 北证 (绝对 ZAF 截断)。
    反推涨停幅度: 涨停价≈昨收×(1+pct), pct ∈ {10,20,30} (ST 5% 例外, 归 10% 档。
    更精确的幅度是 ZTPrice/LastClose-1; 若 ZTPrice 缺失用涨幅档近似)。
    """
    if last_close <= 0 or now_v <= 0:
        return 0.0
    # 1. 用涨停价直接反推幅度 (最准)
    if zt_price > 0:
        span = zt_price / last_close - 1.0
        if 0.04 <= span <= 0.32:   # 5%(ST)~30%(北证) 合理区间
            ratio = (now_v - last_close) / last_close / span
            return max(0.0, min(1.0, ratio))
    # 2. 兜底: 按绝对涨幅/涨幅档 (主板 10 / 创业科创 20 / 北证 30)
    zaf = (now_v - last_close) / last_close * 100 if last_close > 0 else 0.0
    cap = 10.0 if zaf <= 10.5 else (20.0 if zaf <= 21.0 else 30.0)
    return max(0.0, min(1.0, zaf / cap))


def _get_stock_codes() -> list[str]:
    """全场个股代码 (缓存, 排除非个股)。"""
    global _CODES_CACHE, _CODES_TS
    now = time.time()
    if _CODES_CACHE is None or now - _CODES_TS > cfg.CODES_CACHE_TTL:
        meta = fetch_all_codes()
        _CODES_CACHE = [c['code'] for c in meta if c.get('code_type') == 'stock']
        _CODES_TS = now
        logger.info('代码表加载: {} 只股票', len(_CODES_CACHE))
    return _CODES_CACHE


def scan_universe() -> pd.DataFrame:
    """全场 tick: get_pricevol 批量 → DataFrame[code, Now, LastClose, Volume, pct]。pct 单位 %。"""
    codes = _get_stock_codes()
    t0 = time.time()
    data = safe_call(tq.get_pricevol, stock_list=codes)
    if not data:
        logger.warning('get_pricevol 返回空')
        return pd.DataFrame(columns=['code', 'Now', 'LastClose', 'Volume', 'pct'])
    rows = []
    for code, item in data.items():
        if not item:
            continue
        now_p = _to_float(item.get('Now'))
        lc = _to_float(item.get('LastClose'))
        vol = _to_float(item.get('Volume'))
        pct = (now_p - lc) / lc * 100 if lc > 0 else 0.0
        rows.append({'code': code, 'Now': now_p, 'LastClose': lc, 'Volume': vol, 'pct': pct})
    df = pd.DataFrame(rows)
    logger.info('全场 tick: {} 只, {:.2f}s', len(df), time.time() - t0)
    return df


def drill_stocks(codes: list[str], budget_sec: float | None = None) -> dict:
    """逐股 more_info+snapshot → {code: {aggregated fields}}, 带 budget 兜底。

    Args:
        codes: 已预筛的候选股 (调用方负责 lean 化, 见 radar_main: 每板 Top-N)
        budget_sec: 聚合预算 (默认 STOCK_DRILL_BUDGET_SEC, 超时 break + 部分降级)
    """
    budget_sec = cfg.STOCK_DRILL_BUDGET_SEC if budget_sec is None else budget_sec
    out: dict = {}
    n_empty = 0
    t0 = time.time()
    for code in codes:
        if time.time() - t0 > budget_sec:
            logger.warning('钻取超 {:.0f}s 预算, 已钻 {}/{}, 部分降级',
                           budget_sec, len(out), len(codes))
            break
        try:
            mi = safe_call(tq.get_more_info, stock_code=code, field_list=[]) or {}
            sn = safe_call(tq.get_market_snapshot, stock_code=code, field_list=[]) or {}
        except Exception as e:  # noqa: BLE001  safe_call 3 次重试后仍失败
            logger.debug('drill {} 异常: {}', code, e)
            continue
        if not mi or not sn:
            n_empty += 1
            continue
        his_high = _to_float(mi.get('HisHigh'))
        now_v = _to_float(sn.get('Now'))
        out[code] = {
            'ZAF': _to_float(mi.get('ZAF')),
            'FCAmo': _to_float(mi.get('FCAmo')),
            'fLianB': _to_float(mi.get('fLianB')),
            'fHSL': _to_float(mi.get('fHSL')),
            'Zjl': _to_float(mi.get('Zjl')),          # 主买净额 (全档位主动性口径; 探针保留)
            'Zjl_HB': _to_float(mi.get('Zjl_HB')),    # 主力净流入 (超大单+大单口径; 个股榜用)
            'FzAmo': _to_float(mi.get('FzAmo')),
            'BCancel': _to_float(mi.get('BCancel')),
            'SCancel': _to_float(mi.get('SCancel')),
            'ZTPrice': _to_float(mi.get('ZTPrice')),   # 涨停价 (静态日级; 派生 limit_pct)
            'HisHigh': his_high,
            'Now': now_v,
            'Max': _to_float(sn.get('Max')),
            'LastClose': _to_float(sn.get('LastClose')),
            'Volume': _to_float(sn.get('Volume')),
            'LastZTHzNum': _to_float(mi.get('LastZTHzNum')),
            'pos_ratio': now_v / his_high if his_high > 0 else 1.0,
            'limit_pct': _limit_pct(now_v, _to_float(sn.get('LastClose')),
                                    _to_float(mi.get('ZTPrice'))),
        }
    logger.info('钻取: {} 候选 → {} 只有数据 (空 {}), {:.1f}s',
                len(codes), len(out), n_empty, time.time() - t0)
    return out


if __name__ == '__main__':
    # 自检: python testv10.2/ticker.py  (scan_universe + 小样本钻取)
    from lib.tq_client import init, close  # noqa: E402
    import mapping_store  # noqa: E402

    init()
    try:
        df = scan_universe()
        print(f'scan_universe: {len(df)} 只, pct 头部:\n'
              f'{df.sort_values("pct", ascending=False).head(5)[["code","pct","Volume"]].to_string(index=False)}')
        ms = mapping_store.MappingStore(cfg.MAPPING_PARQUET)
        b = ms.boards(cfg.MONITOR_LEVELS)[0]
        members = list(ms.stocks_of(b['code']))[:8]
        drilled = drill_stocks(members)
        print(f'\ndrill_stocks {b["code"]}({b["name"]}) 前 8 成分股:')
        for c, d in drilled.items():
            print(f'  [{c}] {ms.stock_name(c):<8} ZAF={d["ZAF"]:>6.2f} FCAmo={d["FCAmo"]:>8.0f} '
                  f'fLianB={d["fLianB"]:>5.2f} pos={d["pos_ratio"]:.2f}')
    finally:
        close()
