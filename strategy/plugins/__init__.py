"""策略插件包

脚本路径: K:\QuestDB_test\\strategy\\plugins\\__init__.py
用途: 策略插件目录包标识, 由 StrategyRegistry.load_plugins 动态导入 pXX_*.py
说明:
  - 每个插件文件用 @StrategyRegistry.register 装饰器注册
  - 继承 StrategyBase, 实现 evaluate(context) -> List[Decision]
  - 插件 name 必须与 config/strategies.yaml 的 key 一致
"""
