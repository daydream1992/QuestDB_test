"""策略注册器

脚本路径: K:\QuestDB_test\\strategy\\registry.py
用途: 策略热插拔注册, 支持从 strategies.yaml 加载开关, 动态导入 plugins/ 目录
依赖: pyyaml, loguru
配置: config/strategies.yaml 的 strategies 段 (enabled 开关)
说明:
  - 装饰器 @StrategyRegistry.register 注册策略类到全局表
  - get_all 仅返回 enabled=True 的策略
  - load_config 从 yaml 读取开关, 运行时动态启停
  - load_plugins 动态导入 plugins/ 目录下所有 p*.py (下划线开头跳过)
"""

import importlib
import os
import pathlib

from loguru import logger


class StrategyRegistry:
    _strategies = {}  # name → class

    @classmethod
    def register(cls, strategy_class):
        cls._strategies[strategy_class.name] = strategy_class
        logger.debug('注册策略: {} ({})', strategy_class.name,
                     getattr(strategy_class, 'version', '?'))
        return strategy_class

    @classmethod
    def get_all(cls):
        return {n: c for n, c in cls._strategies.items() if c.enabled}

    @classmethod
    def get(cls, name):
        return cls._strategies.get(name)

    @classmethod
    def enable(cls, name):
        if name in cls._strategies:
            cls._strategies[name].enabled = True

    @classmethod
    def disable(cls, name):
        if name in cls._strategies:
            cls._strategies[name].enabled = False

    @classmethod
    def load_config(cls, yaml_path):
        """从 strategies.yaml 加载开关"""
        import yaml
        if not os.path.exists(yaml_path):
            logger.warning('策略配置不存在: {}', yaml_path)
            return
        with open(yaml_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
        for name, settings in cfg.get('strategies', {}).items():
            if settings.get('enabled', True):
                cls.enable(name)
            else:
                cls.disable(name)
        logger.info('策略配置加载完成: 启用={}, 已注册={}',
                    len(cls.get_all()), len(cls._strategies))

    @classmethod
    def load_plugins(cls, plugins_dir):
        """动态导入 plugins/ 目录下所有 pXX_*.py"""
        if not os.path.isdir(plugins_dir):
            logger.warning('插件目录不存在: {}', plugins_dir)
            return
        for p in sorted(pathlib.Path(plugins_dir).glob('p*.py')):
            if p.name.startswith('_'):
                continue
            mod_name = f'strategy.plugins.{p.stem}'
            try:
                importlib.import_module(mod_name)
                logger.debug('加载插件: {}', mod_name)
            except Exception as e:
                logger.error('加载插件失败 {}: {}', mod_name, e)
