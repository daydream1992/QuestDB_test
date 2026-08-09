"""testv10.2 飞书表字段同步 (reconcile) — 配置 diff → 补缺字段

脚本路径: K:/QuestDB_test/testv10.2/reconcile_fields.py
用途: 防止 SENTIMENT_FIELDS 等字段配置改了, 当日已存在的飞书表不跟 (auto_named_table
      对已存在表直接返回, 不补字段 → append_records 静默剔除新字段, 写不进去)。
原理: 读配置字段集 (SENTIMENT_FIELDS/SLOT_FIELDS...) vs 飞书表实际字段,
      缺的用 create-field 补上, 多出的标 warning (不删, 防误删数据)。
跑法: python testv10.2/reconcile_fields.py [--dry-run]   # dry-run 只打印 diff
依赖: 复用 feishu/bitable_writer 的 _api (已有 auth), 不依赖 skill 脚本。
"""

import bootstrap
bootstrap.ensure_paths()

import argparse  # noqa: E402
import sys  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402
import sentiment_monitor as sm  # noqa: E402  复用 SENTIMENT_FIELDS/_slot_fields
import feishu.bitable_writer as bw  # noqa: E402  复用 _api/_create_field


def _table_fields(app_token: str, table_id: str) -> dict:
    """表现有字段: field_name -> {field_id, type}。"""
    data = bw._api('GET', f'/bitable/v1/apps/{app_token}/tables/{table_id}/fields')
    if not data:
        return {}
    return {f['field_name']: f for f in data.get('data', {}).get('items', [])}


def _config_fields() -> list:
    """配置字段集 (sentiment 主表 + 时段聚合表)。"""
    return sm.SENTIMENT_FIELDS


def reconcile(app_token: str, table_id: str, dry_run: bool) -> int:
    """对比配置 vs 表字段, 补缺。返回补的字段数。"""
    existing = _table_fields(app_token, table_id)
    if not existing:
        logger.error('无法获取表字段, 跳过')
        return 0
    existing_names = set(existing)
    conf_fields = _config_fields()
    conf_names = {f['field_name'] for f in conf_fields}
    missing = conf_names - existing_names
    extra = existing_names - conf_names
    if extra:
        logger.warning('表有多余字段(配置未含, 不删): {}', sorted(extra))
    if not missing:
        logger.info('字段全同步 ({} 个), 无需补', len(existing))
        return 0
    logger.info('缺 {} 个字段: {}', len(missing), sorted(missing))
    n = 0
    for fd in conf_fields:
        if fd['field_name'] not in missing:
            continue
        if dry_run:
            logger.info('[dry-run] 将补字段: {} (type {})', fd['field_name'], fd['type'])
            n += 1
            continue
        bw._create_field(app_token, table_id, fd)
        logger.info('已补字段: {} (type {})', fd['field_name'], fd['type'])
        n += 1
    return n


def main():
    p = argparse.ArgumentParser(description='飞书表字段同步 (配置 diff → 补缺)')
    p.add_argument('--dry-run', action='store_true', help='只打印 diff 不真补')
    p.add_argument('--table-id', default='tblK5bWF5fgoL3gC',
                   help='目标表 (默认 v10.2情绪 2026-08-09)')
    a = p.parse_args()
    if not cfg.SENTIMENT_BITABLE_APP_TOKEN:
        logger.error('无 app_token, 退出')
        sys.exit(1)
    n = reconcile(cfg.SENTIMENT_BITABLE_APP_TOKEN, a.table_id, a.dry_run)
    mode = 'dry-run' if a.dry_run else '实际'
    logger.info('字段同步 {} 完成, 补 {} 个', mode, n)


if __name__ == '__main__':
    main()
