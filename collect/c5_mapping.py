"""c5: 板块-个股关系图谱加载

脚本路径: K:\QuestDB_test\\collect\\c5_mapping.py
用途: 从 JSON 加载 6 维关系图谱 (行业/概念/地域/风格/指数 + 个股三级分类), 同步入库
数据源: K:\\QuestDB_test\\data\\market_data\\市场数据\\*.json
入库表 (6 张):
  - qd_sector_meta         板块元数据
  - qd_stock_industry      个股申万三级分类
  - qd_map_concept_stock   概念-个股
  - qd_map_region_stock    地域-个股
  - qd_map_style_stock     风格-个股
  - qd_map_index_stock     指数-成份股
频率: 盘前 1 次/天
字段映射: 由 lib/relation_graph.py 的 sync_to_db 统一处理

说明:
  - JSON 文件名带时间戳, lib.relation_graph.load_from_json 用 glob 取最新
  - 行业板块-个股无独立表 (通过 qd_stock_industry 三级分类体现)
  - 不依赖 tqcenter (纯文件读取)
"""

import os
import sys

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect  # noqa: E402
from lib.relation_graph import load_from_json, sync_to_db, DEFAULT_JSON_DIR  # noqa: E402

_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'c5_mapping_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')


def run(con=None, json_dir=None):
    """加载关系图谱 JSON 并同步到 QuestDB

    Args:
        con:       psycopg2 连接, None 则自建
        json_dir:  JSON 目录, None 用默认 (K:\\QuestDB_test\\data\\market_data\\市场数据)

    Returns:
        dict: 各表写入行数
    """
    own_con = con is None
    if own_con:
        con = connect()

    json_dir = json_dir or DEFAULT_JSON_DIR
    try:
        logger.info('开始加载关系图谱 JSON, dir={}', json_dir)
        if not os.path.exists(json_dir):
            logger.error('JSON 目录不存在: {}', json_dir)
            return {}

        # 1. 加载到内存
        load_from_json(json_dir)

        # 2. 同步入库
        counts = sync_to_db(con)
        logger.info('关系图谱同步完成: {}', counts)
        return counts
    finally:
        if own_con:
            con.close()


def main():
    run()


if __name__ == '__main__':
    main()
