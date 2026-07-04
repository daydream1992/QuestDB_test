"""飞书模块配置

从 config/.env 读取飞书应用凭据与目标资源 ID。
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
FOLDER_TOKEN = os.getenv('LARK_FOLDER_TOKEN', 'Le3af8zGMlOIDXde5QecCJ1lnZg')  # 默认文件夹

# ── API 基地址 ─────────────────────────────────────────────
BASE_URL = 'https://open.feishu.cn/open-apis'

# ── 频控默认值 ─────────────────────────────────────────────
SIGNAL_COOLDOWN_SEC = 300   # 同 code+signal_type 5 分钟内只推一次
BATCH_FLUSH_SEC = 60        # 攒批写入间隔 (秒)


def has_app_credentials() -> bool:
    """是否已配置飞书应用凭据 (决定 API 通道是否可用)"""
    return bool(APP_ID and APP_SECRET)
