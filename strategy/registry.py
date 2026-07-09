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
    _strategies = {}  # {scope: {name: class}}  默认 scope='default'

    @classmethod
    def register(cls, strategy_class=None, *, scope='default'):
        """注册策略类

        支持两种用法:
          @StrategyRegistry.register                        # 无参, scope='default'
          @StrategyRegistry.register(scope='cross_section') # 指定 scope

        Args:
            strategy_class: 装饰器直接用法传入的 class
            scope: 策略分组 (默认 'default', cross_section 等)
        """
        def _wrap(sc):
            if scope not in cls._strategies:
                cls._strategies[scope] = {}
            cls._strategies[scope][sc.name] = sc
            logger.debug('注册策略: {} ({}) scope={}', sc.name,
                         getattr(sc, 'version', '?'), scope)
            return sc

        if strategy_class is not None:
            # 装饰器无参直接调用: @StrategyRegistry.register
            return _wrap(strategy_class)
        # 装饰器带参调用: @StrategyRegistry.register(scope='...')
        return _wrap

    @classmethod
    def get_all(cls, scope=None):
        """获取已启用的策略

        Args:
            scope: None = 所有 scope, 'default' = 仅该 scope

        Returns:
            dict: {name: class}
        """
        result = {}
        for scp, strats in cls._strategies.items():
            if scope is not None and scp != scope:
                continue
            for n, c in strats.items():
                if c.enabled:
                    result[n] = c
        return result

    @classmethod
    def get_by_scope(cls, scope):
        """获取指定 scope 的已启用策略"""
        return cls.get_all(scope=scope)

    @classmethod
    def get(cls, name, scope='default'):
        return cls._strategies.get(scope, {}).get(name)

    @classmethod
    def enable(cls, name, scope='default'):
        if name in cls._strategies.get(scope, {}):
            cls._strategies[scope][name].enabled = True

    @classmethod
    def disable(cls, name, scope='default'):
        if name in cls._strategies.get(scope, {}):
            cls._strategies[scope][name].enabled = False

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
                    len(cls.get_all()), sum(len(v) for v in cls._strategies.values()))

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

    @classmethod
    def validate_required_fields(cls, ctx):
        """H1 护栏: 校验已启用策略的 required_fields 是否在 ctx 任意非空 df 列中

        缺失则 logger.error (字段名错配/大小写错/DDL 缺列), 不强制 disable
        (避免当轮某 df 为空时误杀; 缺字段策略本就会因取不到列而空返)。
        返回缺失列表 [(strategy_name, field), ...]。

        约定: required_fields 声明的是 ctx 各 DataFrame 的列名 (跨 df 大杂烩),
        只要出现在任意一个非空 df 的 columns 中即视为存在。
        """
        all_cols = set()
        for attr in ('pricevol_df', 'snapshot_focus_df', 'more_info_df',
                     'indicators_df', 'signals_df', 'sector_flow_df',
                     'resonance_df', 'money_flow_df', 'big_order_df',
                     'auction_df'):
            df = getattr(ctx, attr, None)
            if df is not None and not df.empty:
                try:
                    all_cols.update(df.columns)
                except Exception:
                    pass
        missing = []
        for name, strat_cls in cls.get_all(scope=None).items():
            if not strat_cls.enabled:
                continue
            try:
                need = strat_cls().required_fields() or []
            except Exception:
                need = []
            for f in need:
                if f not in all_cols:
                    missing.append((name, f))
                    logger.warning('字段护栏: 策略 "{}" required_fields 含 "{}" '
                                   '但 ctx 无此列 (检查 DDL/字段名/大小写)', name, f)
        return missing
