"""testv10.2 板块-个股映射读取器 (MappingStore)

脚本路径: K:/QuestDB_test/testv10.2/mapping_store.py
用途: 启动时加载 sector_mapping.parquet, 建板块↔个股正反索引 + 名称/级别元数据, 内存常驻。
依赖: pandas, loguru (纯读, **不依赖 tq, 不依赖 lib.relation_graph** —— v10.2 用自己的 Parquet 自洽)
数据源: settings.MAPPING_PARQUET (refresh_mapping.py 产出的长表)
设计: 两进程 (雷达/tick) 启动各加载一次; 文件不存在则索引为空 (调用方自行降级)。
"""

import os

import pandas as pd
from loguru import logger


class MappingStore:
    """板块↔个股映射的内存只读索引 (启动加载一次, 常驻)。"""

    def __init__(self, parquet_path: str):
        self.parquet_path = parquet_path
        self.forward: dict[str, set[str]] = {}       # 板块代码 -> {个股代码}
        self.reverse: dict[str, set[str]] = {}       # 个股代码 -> {板块代码}
        self.board_meta: dict[str, dict] = {}        # 板块代码 -> {板块名称, 行业级别}
        self._stock_name: dict[str, str] = {}        # 个股代码 -> 个股名称 (首次出现)
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.parquet_path):
            logger.warning('MappingStore: {} 不存在, 索引为空 (先跑 refresh_mapping.py 生成)',
                           self.parquet_path)
            return
        df = pd.read_parquet(self.parquet_path)
        # 代码列强制 str (防个别全数字代码被推断为数值, 影响 groupby/set)
        df['板块代码'] = df['板块代码'].astype(str)
        df['个股代码'] = df['个股代码'].astype(str)
        df = df.dropna(subset=['板块代码', '个股代码'])
        self.forward = df.groupby('板块代码')['个股代码'].apply(set).to_dict()
        self.reverse = df.groupby('个股代码')['板块代码'].apply(set).to_dict()
        meta = df[['板块代码', '板块名称', '行业级别']].drop_duplicates(subset=['板块代码'])
        self.board_meta = meta.set_index('板块代码').to_dict('index')
        self._stock_name = (df.drop_duplicates(subset=['个股代码'])
                            .set_index('个股代码')['个股名称'].to_dict())
        logger.info('MappingStore 加载: {} 关系行 / {} 板块 / {} 个股',
                    len(df), len(self.forward), len(self.reverse))

    # --- 公开查询 ---
    def stocks_of(self, board_code: str) -> set[str]:
        """板块 -> 成分股代码集 (空集表示无此板块)。"""
        return self.forward.get(board_code, set())

    def boards_of(self, stock_code: str) -> set[str]:
        """个股 -> 所属板块代码集。"""
        return self.reverse.get(stock_code, set())

    def boards(self, levels: set[str] | None = None) -> list[dict]:
        """板块列表 [{code, name, level}]; levels 过滤行业级别 (如 MONITOR_LEVELS ≈398)。"""
        out: list[dict] = []
        for code, meta in self.board_meta.items():
            lv = meta.get('行业级别', '')
            if levels is not None and lv not in levels:
                continue
            out.append({'code': code, 'name': meta.get('板块名称', code), 'level': lv})
        return out

    def board_name(self, code: str) -> str:
        return self.board_meta.get(code, {}).get('板块名称', code)

    def stock_name(self, code: str) -> str:
        """个股代码 -> 中文名 (缺失回退代码本身)。"""
        return self._stock_name.get(code, code)

    def stats(self) -> dict:
        return {'boards': len(self.forward), 'stocks': len(self.reverse),
                'base_dir': self.parquet_path}


if __name__ == '__main__':
    # 自检: python testv10.2/mapping_store.py
    import time

    import bootstrap
    bootstrap.ensure_paths()
    import settings as cfg  # noqa: E402

    t0 = time.time()
    ms = MappingStore(cfg.MAPPING_PARQUET)
    dt = (time.time() - t0) * 1000
    print('=== MappingStore stats ===')
    print(ms.stats())
    print(f'读取+建索引耗时: {dt:.0f} ms')
    monitor = ms.boards(cfg.MONITOR_LEVELS)
    print(f'监控级别 {cfg.MONITOR_LEVELS}: {len(monitor)} 板块')
    if ms.forward:
        bk = next(iter(ms.forward))
        print(f'\n抽样 板块[{bk}] {ms.board_name(bk)} -> {len(ms.stocks_of(bk))} 只, 例: {list(ms.stocks_of(bk))[:5]}')
        sc = next(iter(ms.reverse))
        bs = ms.boards_of(sc)
        print(f'抽样 个股[{sc}] {ms.stock_name(sc)} -> 命中 {len(bs)} 板块: {list(bs)[:5]}')
