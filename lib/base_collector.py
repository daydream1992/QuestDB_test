"""base_collector: 采集器基类

脚本路径: K:\\QuestDB_test\\lib\\base_collector.py
用途: 抽取 cN 模块公共模式 (con 生命周期 / 日志 / 异常处理)
用法: 子类继承 BaseCollector, 实现 _collect(con, **kwargs) 方法

示例:
    class C3Collector(BaseCollector):
        name = 'c3_more_info'
        def _collect(self, con, codes, mode='daily'):
            ...
"""

import time
import logging
from abc import ABC, abstractmethod

from lib.qdb import connect

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """采集器基类, 封装 con 生命周期 + 起止日志 + 异常处理"""

    name: str = ''

    @abstractmethod
    def _collect(self, con, **kwargs):
        """子类实现: 采集+写入, 返回行数或 dict"""
        ...

    def run(self, con=None, **kwargs):
        """统一入口: 管理 con 生命周期, 记录起止日志"""
        own = con is None
        if own:
            con = connect()
        t0 = time.time()
        logger.info('▶ %s 开始', self.name)
        try:
            result = self._collect(con, **kwargs)
            elapsed = time.time() - t0
            logger.info('✓ %s 完成 (%.1fs)', self.name, elapsed)
            return result
        except Exception:
            elapsed = time.time() - t0
            logger.exception('✗ %s 失败 (%.1fs)', self.name, elapsed)
            raise
        finally:
            if own:
                try:
                    con.close()
                except Exception:
                    pass
