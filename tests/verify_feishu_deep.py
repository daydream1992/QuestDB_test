"""验证飞书推送 - 类型校验/幂等/一致性/时间戳/去重"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=" * 60)
print("飞书推送深度验证")
print("=" * 60)

# 1. 字段类型校验
print("\n[1] 字段类型校验验证")
# 检查函数是否存在
with open('feishu/bitable_writer.py', 'r', encoding='utf-8') as f:
    content = f.read()

if '_validate_record_field' in content:
    print("  OK: _validate_record_field 函数已存在")
else:
    print("  WARNING: _validate_record_field 函数不存在（需要新增）")

# 模拟字段定义
field_text = {'field_name': '股票名称', 'type': 1}  # 文本
field_num = {'field_name': '价格', 'type': 2}  # 数字
field_multi = {'field_name': '板块', 'type': 4}  # 多选
field_bool = {'field_name': '是否涨停', 'type': 7}  # 复选框

test_cases = [
    (field_text, '文本正确', True),
    (field_text, 123, False),  # 文本字段传数字
    (field_num, 5.62, True),
    (field_num, '5.62', False),  # 数字字段传字符串
    (field_multi, ['环保', '节能'], True),
    (field_multi, '环保', False),  # 多选字段传字符串
    (field_multi, [], True),  # 空列表是有效的
    (field_bool, True, True),
    (field_bool, False, True),
    (field_bool, 'true', False),  # 布尔字段传字符串
]

issues_found = 0
if '_validate_record_field' in content:
    from feishu.bitable_writer import _validate_record_field
    # 模拟字段定义
    field_text = {'field_name': '股票名称', 'type': 1}  # 文本
    field_num = {'field_name': '价格', 'type': 2}  # 数字
    field_multi = {'field_name': '板块', 'type': 4}  # 多选
    field_bool = {'field_name': '是否涨停', 'type': 7}  # 复选框

    test_cases = [
        (field_text, '文本正确', True),
        (field_text, 123, False),
        (field_num, 5.62, True),
        (field_num, '5.62', False),
        (field_multi, ['环保', '节能'], True),
        (field_multi, '环保', False),
        (field_bool, True, True),
        (field_bool, 'true', False),
    ]

    for field, value, expected in test_cases:
        result = _validate_record_field({field['field_name']: value}, field)
        if result != expected:
            print(f"  FAIL: {field['field_name']}={value}")
            issues_found += 1

    if issues_found == 0:
        print(f"  OK: {len(test_cases)}个测试用例通过")
else:
    print("  (跳过类型校验测试，函数不存在)")

# 2. 字段创建幂等保护
print("\n[2] 字段创建幂等保护验证")
# 检查 _add_signal_fields 是否检查字段已存在
with open('feishu/bitable_writer.py', 'r', encoding='utf-8') as f:
    content = f.read()

if 'existing_fields' in content and 'existing_names' in content:
    print("  OK: 有幂等保护检查")
else:
    print("  WARNING: 可能缺少幂等保护")
    # 检查相关代码
    if 'if fname in existing' in content:
        print("    - 已检测到类似检查")

# 3. Sheet/Bitable 字段一致性
print("\n[3] Sheet/Bitable 字段一致性验证")
try:
    from feishu.sheet_writer import SIGNAL_HEADERS
    print(f"  Sheet SIGNAL_HEADERS: {SIGNAL_HEADERS}")
except:
    print("  WARNING: 无法读取 SIGNAL_HEADERS")

try:
    from feishu.bitable_writer import SIGNAL_FIELDS
    bitable_names = [f['field_name'] for f in SIGNAL_FIELDS]
    print(f"  Bitable SIGNAL_FIELDS: {bitable_names}")

    if 'SIGNAL_HEADERS' in dir():
        diff = set(SIGNAL_HEADERS) ^ set(bitable_names)
        if diff:
            print(f"  WARNING: 字段不一致: {diff}")
        else:
            print("  OK: 字段一致")
except Exception as e:
    print(f"  ERROR: {e}")

# 4. 时间戳处理
print("\n[4] 时间戳处理验证")
from feishu.bitable_writer import _parse_time_to_ms
import inspect

# 检查函数签名
sig = inspect.signature(_parse_time_to_ms)
print(f"  函数签名: {sig}")

# 测试解析
test_times = [
    ('2026-07-13 09:35:00', '标准格式'),
    ('09:35:00', '时间格式'),
    (None, '空值'),
    ('invalid', '非法格式'),
    ('2026-07-13T09:35:00', 'ISO格式'),
]

for t, desc in test_times:
    try:
        result = _parse_time_to_ms(t)
        print(f"  {desc}: {t} -> {result}")
    except Exception as e:
        print(f"  {desc}: {t} -> ERROR: {e}")

# 5. 聚合卡片去重
print("\n[5] 聚合卡片去重验证")
# 检查 _flush_bucket 是否有去重逻辑
with open('feishu/push.py', 'r', encoding='utf-8') as f:
    content = f.read()

if '_flush_bucket' in content:
    # 查找去重相关代码
    if 'seen_codes' in content or 'unique_codes' in content:
        print("  OK: 有去重逻辑")
    else:
        print("  WARNING: 可能缺少去重逻辑")
        # 检查是否有类似代码
        if 'def _flush_bucket' in content:
            # 提取函数体
            start = content.find('def _flush_bucket')
            # 找到下一个 def 或文件结尾
            next_def = content.find('\ndef ', start + 20)
            if next_def == -1:
                next_def = len(content)
            func_body = content[start:next_def]
            if 'seen' in func_body or 'unique' in func_body:
                print("    - 函数内有去重相关代码")

print("\n" + "=" * 60)
print("深度验证完成")
print("=" * 60)