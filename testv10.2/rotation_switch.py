"""testv10.2 板块高低切检测 (rotation_switch) — 资金从A切向B → 推卡

脚本路径: K:/QuestDB_test/testv10.2/rotation_switch.py
用途: 交易者痛点1"大盘情绪高低切没有输出"。检测"资金从哪些板块撤出、
      流向哪些板块", 输出一句人话"资金: 科技→医药+电力"。
消费: meso_radar.scan() rows (板块 ZAF/ZTGPNum/score) + rotation 跨轮
      prev_rank/prev_score (方向数据) + board_pool 的 zt_num_prev/peak_zt_num。
逻辑 (三要素):
  流入侧: rank 上升≥ROTATION_RANK_DELTA 或 score 加速≥ROTATION_ACCEL,
          且涨停数上升 (资金在进)。
  流出侧: 曾入榜 (prev_rank≤5) 现跌出 或 涨停数回落≥2 (资金在撤)。
  组合: 流入 Top1 + 流出 Top1 → "资金: 流出板块→流入板块"。
落地: publisher.on_rotation_switch 卡 (lane0 机会桶 ≤1/min) + 变化门控
      (同方向 N 分钟内不重复推)。
红线: 纯内存 (零新采集), 失败不崩。
"""

import bootstrap
bootstrap.ensure_paths()

from datetime import datetime, timedelta  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402


class RotationSwitch:
    """板块高低切检测: 流入/流出侧 + 组合人话 → 推卡。"""

    def __init__(self, pub=None, dry_run: bool | None = None):
        self.pub = pub
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self.prev_rank: dict[str, int] = {}     # 跨轮: code -> 上轮 rank
        self.prev_zt: dict[str, int] = {}       # 跨轮: code -> 上轮涨停数
        self._last_push: datetime | None = None  # 变化门控: N 分钟推一次
        self._last_direction: tuple = ()         # 同方向去重

    def compute(self, rows: list[dict], now: datetime) -> dict | None:
        """检测高低切。返回 {out_boards, in_boards, direction} 或 None (无切换)。"""
        if not rows:
            return None
        # 当前排名 (全板块按 score)
        ranked = sorted(rows, key=lambda r: r.get('score', 0), reverse=True)
        cur_rank = {r['code']: i + 1 for i, r in enumerate(ranked)}
        cur_zt = {r['code']: int(r.get('ZTGPNum', 0)) for r in rows}

        # 流入侧: rank 上升≥2 且 涨停数上升 (资金在进, 放宽到 2 档)
        inflow = []
        for r in rows:
            c = r['code']
            pr = self.prev_rank.get(c)
            if pr is None:
                continue
            if pr - cur_rank[c] >= 2 and cur_zt.get(c, 0) > self.prev_zt.get(c, 0):
                inflow.append((r['name'], pr, cur_rank[c], cur_zt.get(c, 0)))
        # 流出侧: rank 跌≥2 或 涨停数回落≥2 (资金在撤)
        outflow = []
        for c, pr in self.prev_rank.items():
            cr = cur_rank.get(c, 999)
            if pr - cr <= -2:
                name = next((r['name'] for r in rows if r['code'] == c), c)
                outflow.append((name, pr, cr))
            elif cur_zt.get(c, 0) <= self.prev_zt.get(c, 0) - 2:
                name = next((r['name'] for r in rows if r['code'] == c), c)
                outflow.append((name, self.prev_zt.get(c, 0), cur_zt.get(c, 0)))

        # 更新跨轮
        self.prev_rank = cur_rank
        self.prev_zt = cur_zt

        if not inflow or not outflow:
            return None
        # 组合: 流入 = rank 升最多 (pr - cur 最大); 流出 = rank 跌最多 (pr - cur 最小)
        in_top = max(inflow, key=lambda x: x[1] - x[2])
        out_top = min(outflow, key=lambda x: x[1] - x[2])
        direction = (out_top[0], in_top[0])
        # 变化门控: 同方向 N 分钟内不重复
        if self._last_push and (now - self._last_push) < timedelta(
                seconds=cfg.SWITCH_COOLDOWN_SEC):
            if direction == self._last_direction:
                return None
        self._last_push = now
        self._last_direction = direction
        return {'out': out_top, 'in': in_top, 'direction': direction}

    def maybe_push(self, rows: list[dict], now: datetime) -> bool:
        """检测 + 推卡。失败不崩。"""
        if not self.pub:
            return False
        try:
            res = self.compute(rows, now)
            if not res:
                return False
            out, inn = res['out'], res['in']
            lines = [f'🔀 高低切 | {now.strftime("%H:%M")}', '',
                     f'资金: {out[0]} → {inn[0]}',
                     f'  撤: {out[0]} (rank {out[1]}→{out[2]})',
                     f'  进: {inn[0]} (rank {inn[1]}→{inn[2]}, 涨停{inn[3]})']
            if self.pub.on_rotation_switch(lines, now):
                logger.info('🔀 高低切: {}→{}', out[0], inn[0])
                return True
        except Exception:  # noqa: BLE001  失败不崩
            logger.exception('高低切检测异常, 跳过')
        return False


if __name__ == '__main__':
    # 自检: 合成两轮 rows 验高低切
    import publisher as pub_mod
    pub = pub_mod.Publisher(dry_run=True)
    sw = RotationSwitch(pub=pub)
    now = datetime.now()
    # 第1轮: 科技强 (建立 prev)
    r1 = [{'code': '880001.SH', 'name': '科技', 'level': '概念', 'score': 80, 'ZTGPNum': 8},
          {'code': '880002.SH', 'name': '医药', 'level': '概念', 'score': 50, 'ZTGPNum': 3},
          {'code': '880003.SH', 'name': '电力', 'level': '概念', 'score': 40, 'ZTGPNum': 2}]
    sw.compute(r1, now)
    # 第2轮: 医药/电力强, 科技弱 (触发高低切)
    r2 = [{'code': '880001.SH', 'name': '科技', 'level': '概念', 'score': 30, 'ZTGPNum': 2},
          {'code': '880002.SH', 'name': '医药', 'level': '概念', 'score': 80, 'ZTGPNum': 6},
          {'code': '880003.SH', 'name': '电力', 'level': '概念', 'score': 70, 'ZTGPNum': 4}]
    pushed = sw.maybe_push(r2, now)
    print(f'高低切自检: 推送={pushed}')
    print('ROTATION SWITCH SELF-TEST PASSED' if pushed else 'ROTATION SWITCH 未触发 (检查)')
