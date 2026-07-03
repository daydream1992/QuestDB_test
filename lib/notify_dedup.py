"""通用推送频控 (Deduper)

key=(code, type), TTL 秒内同 key 不重复推送; critical 事件豁免。
移植自 DB数据库_v2 01实盘监控/notify.py (Deduper 3分钟去重 + critical 豁免)。
进程内单例 (intraday_loop daemon 常驻); 定期 cleanup 避免内存无限增长。
"""

import time

# 默认 TTL: 同 (code, type) 180 秒内只推一次
_DEFAULT_TTL = 180


class Deduper:
    def __init__(self, ttl: int = _DEFAULT_TTL):
        self.ttl = ttl
        self._seen: dict = {}  # key -> last_push_ts

    def allow(self, key, critical: bool = False) -> bool:
        """是否允许推送。critical=True 时豁免频控 (封板/炸板等关键事件绝不被去重)"""
        if critical:
            return True
        now = time.time()
        last = self._seen.get(key)
        if last is not None and now - last < self.ttl:
            return False
        self._seen[key] = now
        return True

    def cleanup(self, max_size: int = 10000) -> None:
        """超过 max_size 时清掉过期 key"""
        if len(self._seen) < max_size:
            return
        cutoff = time.time() - self.ttl
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}


# 模块级单例 (intraday_loop 进程内共享)
_deduper = Deduper(ttl=_DEFAULT_TTL)


def allow_push(code: str, event_type: str, critical: bool = False) -> bool:
    """便捷接口: (code, event_type) 在 TTL 内是否允许推送"""
    return _deduper.allow((code, event_type), critical)


def cleanup():
    _deduper.cleanup()
