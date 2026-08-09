"""testv10.2 板块池状态机 (board_pool)

脚本路径: K:/QuestDB_test/testv10.2/board_pool.py
用途: 维护板块生命周期状态 NEW/HOT/WARN/DEAD + 3 轮宽限期 + 硬上限, 平滑轮动 (v10.2 核心价值)。
      消费 meso_radar.pool_boards() 的并集池, 输出每轮新入池 (NEW) 板块供起涨信号。
依赖: 无 (纯数据结构; dataclass + dict), 不依赖 tq/lib
设计: 每轮 update() 一次; DEAD 必须 pop (防内存泄漏, 红线#4); 硬上限按 score 升序裁。
参考: happy-wishing-tarjan 蓝本 C.1/C.2, v10.1 pools.py dataclass 风格。
"""

from dataclasses import dataclass, field

from loguru import logger


@dataclass
class BoardState:
    code: str
    name: str
    state: str = 'NEW'                       # NEW / HOT / WARN / DEAD
    miss_count: int = 0                      # 连续不在并集池的轮数
    score: float = 0.0
    score_prev1: float = 0.0                 # 上轮 score (★加速用)
    score_prev2: float = 0.0                 # 上上轮 score (近3轮变化)
    zt_num: int = 0
    zt_num_prev: int = 0                     # 上轮涨停数 (E1 主线首个涨停)
    peak_zt_num: int = 0                     # 今日涨停数峰值 (退潮"涨停X→Y")
    velocity: float = 0.0
    zaf: float = 0.0                         # 板块涨幅% (入选理由展示)
    flianb: float = 0.0                      # 量比 (入选理由展示)
    peak_score: float = 0.0                  # 池内峰值动能分 (退潮判定: 只推强板退潮)
    rounds_in: int = 0                       # 连续在池轮数 (确认信号, 滤开盘换手噪音)
    rank: int = 0                            # 当前轮排名 (HOT/NEW 内按分降序; 0=非活跃)
    peak_rank: int = 999                     # 今日最好排名 (999=未上过榜)
    rounds_at_top1: int = 0                  # 累计在#1的轮数 (退潮"曾连续N轮第1")
    first_seen_round: int = 0
    last_seen_round: int = 0
    searchlights: set[str] = field(default_factory=set)


class BoardPool:
    """板块生命周期状态机 (NEW→HOT→WARN→DEAD, 带宽限期)。"""

    def __init__(self, grace: int = 3, hard_cap: int = 30, min_per_light: int = 2):
        self.boards: dict[str, BoardState] = {}
        self.grace = grace
        self.hard_cap = hard_cap
        self.min_per_light = min_per_light

    def update(self, pool_rows: list[dict], round_idx: int) -> list[str]:
        """纳入本轮探照灯并集池, 更新状态机。

        Args:
            pool_rows: meso_radar.pool_boards() 输出 (dict 含 code/name/score/ZTGPNum/velocity/searchlights)
            round_idx: 当前轮次号
        Returns:
            新入池 (NEW) 板块 code 列表 (供 🟢 起涨信号)
        """
        in_pool: set[str] = {r['code'] for r in pool_rows}
        scored: dict[str, dict] = {r['code']: r for r in pool_rows}
        new_entries: list[str] = []

        # 1. 老板块: 在池→HOT, 不在池→miss++/WARN or DEAD
        for code, st in self.boards.items():
            if code in in_pool:
                st.miss_count = 0
                st.state = 'HOT'
                st.last_seen_round = round_idx
                st.rounds_in += 1
                r = scored[code]
                st.score_prev2 = st.score_prev1
                st.score_prev1 = st.score
                st.score = r.get('score', st.score)
                st.zt_num_prev = st.zt_num
                st.zt_num = r.get('ZTGPNum', st.zt_num)
                st.peak_zt_num = max(st.peak_zt_num, st.zt_num)
                st.velocity = r.get('velocity', st.velocity)
                st.zaf = r.get('ZAF', st.zaf)
                st.flianb = r.get('fLianB', st.flianb)
                st.peak_score = max(st.peak_score, st.score)
                if r.get('searchlights'):
                    st.searchlights = r['searchlights']
            else:
                st.miss_count += 1
                st.state = 'WARN' if st.miss_count < self.grace else 'DEAD'
                st.rounds_in = 0

        # 2. 新面孔: 入池 NEW
        for code in in_pool:
            if code not in self.boards:
                r = scored[code]
                self.boards[code] = BoardState(
                    code=code, name=r.get('name', code), state='NEW', miss_count=0,
                    score=r.get('score', 0.0), zt_num=r.get('ZTGPNum', 0),
                    peak_zt_num=r.get('ZTGPNum', 0),
                    velocity=r.get('velocity', 0.0), zaf=r.get('ZAF', 0.0),
                    flianb=r.get('fLianB', 0.0), peak_score=r.get('score', 0.0),
                    rounds_in=1,
                    searchlights=set(r.get('searchlights', set())),
                    first_seen_round=round_idx, last_seen_round=round_idx)
                new_entries.append(code)

        # 3. DEAD 必须 pop (红线#4: 防内存泄漏)
        for c in [c for c, s in self.boards.items() if s.state == 'DEAD']:
            self.boards.pop(c, None)

        # 4. 硬上限: 保证每探照灯 TopK 留存 + WARN 不裁 (见 _enforce_cap)
        if len(self.boards) > self.hard_cap:
            self._enforce_cap()

        # 5. 排名派生 (HOT/NEW 按分降序; 更新 peak_rank / rounds_at_top1, 信号语义用)
        self._update_ranks()

        return new_entries

    def _update_ranks(self) -> None:
        """HOT/NEW 板按 score 降序排名; 更新 rank/peak_rank/rounds_at_top1。非活跃板 rank=0。"""
        active = sorted((s for s in self.boards.values() if s.state in ('HOT', 'NEW')),
                        key=lambda s: s.score, reverse=True)
        for i, s in enumerate(active, 1):
            s.rank = i
            if i < s.peak_rank:
                s.peak_rank = i
            if i == 1:
                s.rounds_at_top1 += 1
        for s in self.boards.values():
            if s.state not in ('HOT', 'NEW'):
                s.rank = 0

    def _enforce_cap(self) -> None:
        """裁到 hard_cap: 每探照灯保证 TopK (min_per_light) 留存 (防 score-cap 踢掉
        错杀/反转低分板), WARN 板 grace 期内不裁 (让状态机先观察企稳), 其余按分升序裁。
        状态优先级淘汰: NEW > HOT > WARN (新热点优先保留, HOT 次之, WARN 可牺牲)。"""
        light_codes: dict[str, list[str]] = {}
        for c, s in self.boards.items():
            for l in s.searchlights:
                light_codes.setdefault(l, []).append(c)
        guaranteed: set[str] = set()
        for codes in light_codes.values():
            for c in sorted(codes, key=lambda k: self.boards[k].score,
                            reverse=True)[:self.min_per_light]:
                guaranteed.add(c)
        candidates = [c for c, s in self.boards.items()
                      if s.state != 'WARN' and c not in guaranteed]
        # 状态优先级: HOT(0) 最后裁 > NEW(1) > 其他(2); 同状态按分升序先裁低分
        _PRIO = {'HOT': 0, 'NEW': 1}
        candidates.sort(key=lambda k: (_PRIO.get(self.boards[k].state, 2),
                                       self.boards[k].score))
        overflow = len(self.boards) - self.hard_cap
        for c in candidates[:overflow]:
            self.boards.pop(c, None)
        if len(self.boards) > self.hard_cap:
            n_warn = sum(1 for s in self.boards.values() if s.state == 'WARN')
            logger.warning('hard_cap 未满裁: guaranteed+WARN 超额, 当前 {} (warn {})',
                           len(self.boards), n_warn)

    def adjust_cap(self, n_hit: int) -> int:
        """动态容量: 按探照灯命中数调整 hard_cap (平淡日收紧, 活跃日放宽)。

        命中≤30 → 25 (平淡日少钻取); ≤50 → 30 (正常日); 否则 35 (活跃日)。
        返回调整后的容量。只放大不缩小 (池内已确认的板不被中途踢出, 保状态机稳定)。
        """
        new_cap = 25 if n_hit <= 30 else (30 if n_hit <= 50 else 35)
        if new_cap > self.hard_cap:
            self.hard_cap = new_cap
        return self.hard_cap

    def hot_codes(self) -> list[str]:
        """HOT + NEW 板块 (成分股钻取目标, 按分降序)。"""
        return [c for c in sorted(self.boards, key=lambda k: self.boards[k].score, reverse=True)
                if self.boards[c].state in ('HOT', 'NEW')]

    def warn_codes(self) -> list[str]:
        """WARN 板块 (宽限观察期, 仍拉取看是否企稳)。"""
        return [c for c, s in self.boards.items() if s.state == 'WARN']

    def snapshot(self) -> list[BoardState]:
        """当前池快照 (按 score 降序), 供看板/舰队映射。"""
        return [self.boards[c] for c in sorted(self.boards, key=lambda k: self.boards[k].score, reverse=True)]

    def stats(self) -> dict:
        from collections import Counter
        cnt = Counter(s.state for s in self.boards.values())
        return {'total': len(self.boards), **dict(cnt)}


if __name__ == '__main__':
    # 单元测试: mock 多轮并集池, 验状态机转移 (盘前无多轮真数据, 用合成数据)
    def _row(code: str, score: float, zt: int = 0, lights: set | None = None) -> dict:
        return {'code': code, 'name': f'b{code}', 'score': score, 'ZTGPNum': zt,
                'velocity': 0.0, 'searchlights': lights or {'gain'}}

    pool = BoardPool(grace=3, hard_cap=30)

    # R0: A/B/C 入池 → 全 NEW
    pool.update([_row('A', 50), _row('B', 40), _row('C', 30)], 0)
    assert set(pool.hot_codes()) == {'A', 'B', 'C'}, pool.hot_codes()
    assert all(pool.boards[c].state == 'NEW' for c in 'ABC')
    print(f'R0: {pool.stats()}  new=[A,B,C]')

    # R1: A/B 在池, C 跌出 → A/B→HOT, C→WARN(miss=1)
    pool.update([_row('A', 52), _row('B', 42)], 1)
    assert pool.boards['A'].state == 'HOT' and pool.boards['B'].state == 'HOT'
    assert pool.boards['C'].state == 'WARN' and pool.boards['C'].miss_count == 1
    print(f'R1: {pool.stats()}  A/B->HOT, C->WARN(1)')

    # R2: 仅 A → B WARN(1), C WARN(2)
    pool.update([_row('A', 55)], 2)
    assert pool.boards['B'].miss_count == 1 and pool.boards['C'].miss_count == 2
    print(f'R2: {pool.stats()}  B->WARN(1), C->WARN(2)')

    # R3: 仅 A → C miss=3→DEAD→pop; B miss=2 仍 WARN
    pool.update([_row('A', 58)], 3)
    assert 'C' not in pool.boards, 'C 连续 miss 3 轮必须 DEAD pop'
    assert pool.boards['B'].state == 'WARN' and pool.boards['B'].miss_count == 2
    assert pool.boards['A'].state == 'HOT'
    print(f'R3: {pool.stats()}  C->DEAD popped, B 仍 WARN(2), A HOT')

    # R4: A + D(新), B 跌出 → B miss=3→DEAD→pop; D=NEW (起涨信号)
    new = pool.update([_row('A', 60), _row('D', 45)], 4)
    assert 'B' not in pool.boards, 'B 连续 miss 3 轮必须 DEAD pop'
    assert new == ['D'], new
    assert pool.boards['D'].state == 'NEW'
    print(f'R4: {pool.stats()}  B->DEAD popped, new={new} (D 起涨信号)')

    # R5: 硬上限 30: 塞 35 个板, 裁到 30 (留分最高 30, 状态优先级 HOT>NEW)
    big = [_row(f'X{i}', 100 - i) for i in range(35)]
    pool2 = BoardPool(grace=3, hard_cap=30)
    pool2.update(big, 0)
    assert len(pool2.boards) == 30, len(pool2.boards)
    assert pool2.boards["X0"].score == 100 and "X34" not in pool2.boards
    # 状态优先级淘汰: 同 NEW 状态按 score 升序裁最低分 (X30..X34 应被裁, X5 高分保留)
    assert "X30" not in pool2.boards, 'NEW 中分最低 X30..X34 应被裁'
    assert "X5" in pool2.boards, '高分 NEW 保留'
    print(f'R5 hard_cap: 35 入 -> {len(pool2.boards)} (裁 X30..X34 低分, 留分最高 30)')

    # R6: 错杀 ('drop') 板即使最低分也留 (per-light quota 保证, 修原 score-cap bug)
    pool3 = BoardPool(grace=3, hard_cap=5)
    rows3 = [_row(f'G{i}', 80 - i, lights={'gain'}) for i in range(6)]
    rows3.append(_row('DROP', 1, lights={'drop'}))
    pool3.update(rows3, 0)
    assert 'DROP' in pool3.boards, 'drop 错杀板须由 per-light quota 保留'
    assert len(pool3.boards) <= 5
    print(f'R6 错杀保留: DROP(score=1) 存活, 池 {len(pool3.boards)} (per-light quota)')

    # R7: WARN 板 grace 期内不被 hard_cap 裁 (状态机先观察企稳)
    pool4 = BoardPool(grace=3, hard_cap=5)
    pool4.update([_row('W', 50), _row('A', 80), _row('B', 70)], 0)
    pool4.update([_row('A', 80), _row('B', 70)], 1)   # W 跌出 -> WARN(1)
    assert pool4.boards['W'].state == 'WARN'
    pool4.update([_row('A', 80), _row('B', 70)] + [_row(f'N{i}', 95 - i) for i in range(8)], 2)
    assert 'W' in pool4.boards, 'WARN 板 grace 期内不被 hard_cap 裁'
    print(f'R7 WARN 保留: W 板 grace 期内存活, 池 {len(pool4.boards)}')

    print('\nALL ASSERTIONS PASSED')
