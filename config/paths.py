"""config.paths: 项目路径常量"""
import os

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 市场数据 JSON 目录
MARKET_DATA_DIR = os.getenv('MARKET_DATA_DIR', os.path.join(_PROJ_ROOT, 'data', 'market_data'))

# 日志目录
LOGS_DIR = os.path.join(_PROJ_ROOT, 'logs')

# 市场数据子目录
MARKET_DATA_JSON_DIR = os.path.join(MARKET_DATA_DIR, '市场数据')
