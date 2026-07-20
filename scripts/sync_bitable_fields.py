"""飞书多维表格字段同步脚本

用法: python scripts/sync_bitable_fields.py

功能:
  - 自动检测今日表是否缺少字段
  - 自动添加缺失字段: 是否涨停(复选框)、评分档位(公式)
"""

import os
import sys

# 添加项目根目录到 path
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ_ROOT, 'config', '.env'))

from feishu import _bitable, auto_daily_table, _auth_mod


def sync_fields():
    """同步飞书多维表格字段"""
    token = os.getenv('LARK_BITABLE_TOKEN')
    if not token:
        print('[错误] 未配置 LARK_BITABLE_TOKEN')
        return False

    print(f'多维表格 Token: {token[:20]}...')

    # 获取今日表
    tid = auto_daily_table(token)
    if not tid:
        print('[错误] 无法获取今日表')
        return False
    print(f'今日表 ID: {tid}')

    # 列出当前字段
    fields = _bitable._list_fields(token, tid)
    current_names = {f['field_name'] for f in fields}
    print(f'当前字段数: {len(fields)}')

    # 需要添加的字段定义
    # type=7: 复选框, type=20: 公式
    needed_fields = [
        {'field_name': '是否涨停', 'type': 7, 'property': {'symbol': '✅'}},
        {'field_name': '评分档位', 'type': 20, 'property': {
            'formula': 'IF(评分>=80,"优",IF(评分>=60,"良",IF(评分>=40,"中","差")))'
        }},
    ]

    success = True
    for field_def in needed_fields:
        name = field_def['field_name']
        ftype = field_def['type']

        if name in current_names:
            print(f'  [跳过] {name} 已存在 (type={ftype})')
            continue

        print(f'  [添加] {name} (type={ftype})...')
        try:
            result = _bitable._create_field(token, tid, field_def)
            if result:
                print(f'         成功! field_id={result}')
            else:
                print(f'         失败')
                success = False
        except Exception as e:
            print(f'         异常: {e}')
            success = False

    # 验证
    if success:
        print('\n验证字段...')
        fields2 = _bitable._list_fields(token, tid)
        print(f'最终字段数: {len(fields2)}')
        for f in fields2:
            print(f'  - {f["field_name"]} (type={f["type"]})')

    return success


if __name__ == '__main__':
    print('=' * 50)
    print('飞书多维表格字段同步')
    print('=' * 50)
    success = sync_fields()
    print()
    if success:
        print('[完成] 字段同步成功!')
    else:
        print('[失败] 请检查错误信息')
        sys.exit(1)
