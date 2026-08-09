"""testv10.2 meso 雷达: monitor 板块扫描 + 6 探照灯打分

脚本路径: K:/QuestDB_test/testv10.2/meso_radar.py
用途: 每轮扫 578 monitor 板块 (概念+三级), 取 ZAF/ZTGPNum/fLianB/振幅 + 涨速(prev/current),
      6 探照灯求并集入池, calc_sector_score 排序。状态机 (board_pool) 的数据源。
依赖: lib/tq_client (safe_call), mapping_store (板块宇宙)
数据源: tqcenter get_more_info + get_market_snapshot (实测 578 板 ~5s)
克隆: compute/k6 calc_linkage_score 思路 (不导入, 独立实现)
说明: 本模块只取数+打分; init/close 由调用方 (radar_main) 管。涨速需 prev_zaf (首轮=0)。
"""

import bootstrap
bootstrap.ensure_paths()

import time  # noqa: E402

from loguru import logger  # noqa: E402

from lib.tq_client import safe_call  # noqa: E402
from tqcenter import tq  # noqa: E402
import settings as cfg  # noqa: E402

# === 6 探照灯 Top N (并集入池) ===
TOP_GAIN = 15    # 纯涨幅 (抓主升浪头部)
TOP_ZT = 15      # 涨停数 (抓攻击强度梯队)
TOP_VEL = 15     # 1分钟涨速 (抓起涨/低位极速反弹, 核心)
TOP_LIANB = 15   # 1分钟量比 (抓资金刚介入异动)
TOP_AMP = 10     # 板块振幅 (抓跌停撬平盘大长腿)
TOP_DROP = 10    # 跌幅 (找错杀低吸池)


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _norm(v: float, lo: float, hi: float) -> float:
    """线性归一到 [0,1], 越界截断。"""
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def calc_sector_score(zaf: float, velocity: float, flianb: float, ztgpnum: float) -> float:
    """板块动能综合分 (0-100)。权重: 涨速=涨幅 > 涨停 > 量比 (涨速高权重匹配反转定位)。"""
    return 100 * (0.30 * _norm(zaf, -3, 8)
                  + 0.30 * _norm(velocity, -1, 4)
                  + 0.25 * _norm(ztgpnum, 0, 10)
                  + 0.15 * _norm(flianb, 0, 4))


class MesoRadar:
    """中观板块雷达 (每轮 scan 一次, 累积 prev_zaf 算涨速)。"""

    def __init__(self, mapping_store, monitor_levels: set[str]):
        self.ms = mapping_store
        self.levels = monitor_levels
        self.prev_zaf: dict[str, float] = {}   # 上一轮板 ZAF, 算涨速

    def scan(self) -> list[dict]:
        """扫全 monitor 板块 → 打分 + 6 探照灯标记。返回 list[dict] (含 searchlights set)。"""
        boards = self.ms.boards(self.levels)
        t0 = time.time()
        rows: list[dict] = []
        n_empty = 0
        n_skip = 0
        for b in boards:
            # 聚合预算: COM 慢时 break, 剩余板本轮降级 (下轮自动恢复), 保 60s 轮次
            if time.time() - t0 > cfg.MESO_SCAN_BUDGET:
                n_skip += 1
                continue
            code = b['code']
            try:
                mi = safe_call(tq.get_more_info, stock_code=code, field_list=[],
                               timeout=cfg.MOREINFO_TIMEOUT) or {}
                sn = safe_call(tq.get_market_snapshot, stock_code=code, field_list=[],
                               timeout=cfg.MOREINFO_TIMEOUT) or {}
            except Exception:  # noqa: BLE001  红线#3: 单板失败不崩整轮
                logger.debug('meso scan {} 失败, 跳过', code)
                continue
            if not mi or not sn:
                n_empty += 1
                continue
            zaf = _f(mi.get('ZAF'))
            zt = _f(mi.get('ZTGPNum'))
            flianb = _f(mi.get('fLianB'))
            prev = self.prev_zaf.get(code)
            velocity = (zaf - prev) if prev is not None else 0.0
            lc = _f(sn.get('LastClose'))
            amp = (_f(sn.get('Max')) - _f(sn.get('Min'))) / lc * 100 if lc > 0 else 0.0
            rows.append({'code': code, 'name': b['name'], 'level': b['level'],
                         'ZAF': zaf, 'ZTGPNum': zt, 'fLianB': flianb,
                         'velocity': velocity, 'amplitude': amp,
                         'score': calc_sector_score(zaf, velocity, flianb, zt),
                         'searchlights': set()})
        # 更新 prev_zaf 供下轮涨速
        self.prev_zaf = {r['code']: r['ZAF'] for r in rows}
        self._tag_searchlights(rows)
        n_hit = sum(1 for r in rows if r['searchlights'])
        logger.info('meso 扫描: {} 板 / 命中探照灯 {} 板 / 空数据 {} 跳过 / 预算降级 {} 板, {:.1f}s',
                    len(rows), n_hit, n_empty, n_skip, time.time() - t0)
        return rows

    @staticmethod
    def _tag_searchlights(rows: list[dict]) -> None:
        """6 探照灯各取 TopN, 命中者写入 searchlights set (求并集)。

        动态阈值 (floor): 先按 dim 降序, 取 dim ≥ floor 的 (绝对阈值滤平淡日噪音);
        无 floor 的灯 (gain/lianb) 仍固定 TopN。缓冲带 TopN+3: 排名 N+1~N+3 与第 N
        差距极小时保留, 防边缘误裁。纯内存排序, 0 COM 开销。"""
        def top(key: str, n: int, reverse: bool = True, filt=None,
                floor: float | None = None, le: bool = False) -> list[dict]:
            """按 key 排序取 TopN+3。floor 绝对阈值: 默认取 >= floor (下界);
            le=True 时取 <= floor (上界, 用于跌幅/低吸这类越小越强的维度)。"""
            cands = [r for r in rows if (filt is None or filt(r))]
            if floor is not None:
                cands = [r for r in cands if (r[key] <= floor if le else r[key] >= floor)]
            cands.sort(key=lambda r: r[key], reverse=reverse)
            return cands[:n + 3]   # TopN+3 缓冲带 (防排名边缘误裁)

        lights = {
            'gain':  top('ZAF', TOP_GAIN, floor=2.0),       # 板块涨幅≥2%
            'zt':    top('ZTGPNum', TOP_ZT, floor=3.0),     # 板块内≥3只涨停
            'vel':   top('velocity', TOP_VEL, filt=lambda r: r['velocity'] > 0,
                         floor=0.5),                        # 涨速≥0.5%
            'lianb': top('fLianB', TOP_LIANB, floor=1.5),   # 量比≥1.5
            'amp':   top('amplitude', TOP_AMP, floor=3.0),  # 振幅≥3%
            'drop':  top('ZAF', TOP_DROP, reverse=False, filt=lambda r: r['ZAF'] < 0,
                         floor=-2.0, le=True),              # 跌幅≥2% (ZAF ≤ -2, 上界)
        }
        for lname, winners in lights.items():
            for r in winners:
                r['searchlights'].add(lname)


def pool_boards(rows: list[dict], hard_cap: int | None = None) -> list[dict]:
    """命中任一探照灯的板块求并集, 按 score 降序 (供 board_pool 状态机入池)。

    Args:
        rows: meso_radar.scan() 返回值
        hard_cap: 可选硬上限 (按 score 升序裁最低分), 状态机层做更准; 这里默认不裁。
    """
    hit = [r for r in rows if r['searchlights']]
    hit.sort(key=lambda r: r['score'], reverse=True)
    if hard_cap and len(hit) > hard_cap:
        hit = hit[:hard_cap]
    return hit


if __name__ == '__main__':
    # 自检: python testv10.2/meso_radar.py  (盘前排名无离散, 验结构+计时)
    import bootstrap
    bootstrap.ensure_paths()
    from lib.tq_client import init, close  # noqa: E402
    import mapping_store  # noqa: E402
    import settings as cfg  # noqa: E402

    ms = mapping_store.MappingStore(cfg.MAPPING_PARQUET)
    radar = MesoRadar(ms, cfg.MONITOR_LEVELS)
    init()
    try:
        rows = radar.scan()
    finally:
        close()

    pool = pool_boards(rows)
    print(f'\n=== 扫描 {len(rows)} 板, 命中并集 {len(pool)} 板 ===')
    # 各探照灯命中数
    from collections import Counter
    lc = Counter()
    for r in rows:
        lc.update(r['searchlights'])
    print(f'探照灯命中: {dict(lc)}')
    print(f'\n=== 池 Top 15 (按 score) ===')
    print(f'{"code":<12} {"name":<10} {"ZAF":>6} {"vel":>6} {"zt":>4} {"liab":>5} {"amp":>5} {"score":>6} lights')
    for r in pool[:15]:
        print(f'{r["code"]:<12} {r["name"][:8]:<10} {r["ZAF"]:>6.2f} {r["velocity"]:>6.2f} '
              f'{r["ZTGPNum"]:>4.0f} {r["fLianB"]:>5.2f} {r["amplitude"]:>5.2f} {r["score"]:>6.1f} '
              f'{",".join(sorted(r["searchlights"]))}')
