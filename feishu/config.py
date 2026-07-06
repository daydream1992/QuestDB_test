"""feishu.config: 飞书模块配置加载

脚本路径: K:\\QuestDB_test\\feishu\\config.py
用途: 从 config/.env 读飞书应用凭据与目标资源 ID
依赖: os / dotenv
配置: LARK_APP_ID / LARK_APP_SECRET / LARK_SHEET_TOKEN / LARK_BITABLE_TOKEN /
      LARK_DOC_ID / LARK_FOLDER_TOKEN / LARK_WEBHOOK_URL / SIGNAL_COOLDOWN_SEC
入参: 模块级常量, 调用方直接 import
返回: dict 形式的全局配置
说明:
  - 默认值与 .env.example 保持一致
  - SIGNAL_COOLDOWN_SEC 默认 300 秒 (与 qd_signal_log 配合)
"""

import os
from dotenv import load_dotenv

# 加载 config/.env
_ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'config', '.env',
)
load_dotenv(_ENV_PATH)

# ── 飞书应用凭据 ──────────────────────────────────────────
APP_ID = os.getenv('LARK_APP_ID', '')
APP_SECRET = os.getenv('LARK_APP_SECRET', '')

# ── Webhook (零依赖推送, 保留) ─────────────────────────────
WEBHOOK_URL = os.getenv('LARK_WEBHOOK_URL', '')

# ── 默认目标资源 ID (可选, 未配则运行时自动创建) ───────────
DOC_ID = os.getenv('LARK_DOC_ID', '')           # 飞书文档 ID
SHEET_TOKEN = os.getenv('LARK_SHEET_TOKEN', '')  # 电子表格 token
SHEET_ID = os.getenv('LARK_SHEET_ID', '')        # 子 sheet ID
BITABLE_TOKEN = os.getenv('LARK_BITABLE_TOKEN', '')  # 多维表格 token
FOLDER_TOKEN = os.getenv('LARK_FOLDER_TOKEN', '')  # 飞书文件夹 token (必配, 否则自动创建文档在根目录)

# ── API 基地址 ─────────────────────────────────────────────
BASE_URL = 'https://open.feishu.cn/open-apis'

# ── 频控默认值 ─────────────────────────────────────────────
SIGNAL_COOLDOWN_SEC = 300   # 同 code+signal_type 5 分钟内只推一次
BATCH_FLUSH_SEC = 60        # 攒批写入间隔 (秒)

# ── Dry-Run 模式 (CLAUDE.md §9) ────────────────────────────
DRY_RUN = os.getenv('FEISHU_DRY_RUN', 'false').lower() in ('1', 'true', 'yes')


def has_app_credentials() -> bool:
    """是否已配置飞书应用凭据 (决定 API 通道是否可用)"""
    return bool(APP_ID and APP_SECRET)
