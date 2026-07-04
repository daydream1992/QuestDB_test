# 飞书模块维护说明

## 一、前因

本项目 (QuestDB_test) 是一个 A 股量化交易系统，核心流程：
1. **采集** (collect/) — 从通达信拉行情/快照/K线/龙虎榜等
2. **计算** (compute/) — 算指标、信号、情绪
3. **策略** (strategy/) — 打板、炸板、突破、背离等策略插件
4. **执行** (runner/) — 盘中循环、日终收盘、集合竞价监控

系统原有一个 `lib/lark.py`，只做 Webhook 纯文本推送。信号只能推到群里看一眼就没了，无法回溯、无法分析。

## 二、为什么做这个模块

需求：信号除了推送到飞书群，还要**持久化到飞书文档和表格**，方便：
- 盘后回溯某天出了什么信号
- 按策略/类型筛选统计胜率
- 多维表格做仪表盘可视化

因此将飞书功能独立为 `4_feishu/` 模块，解耦原有 `lib/lark.py`。

## 三、系统架构定位

### 三层各司其职

```
QuestDB (主存储, 实时)
  │  全量信号，零延迟，做筛选的数据源
  │
  ├──→ 飞书群推送 (通道层)
  │     当前已关闭全量推送，等二次过滤层实现后按需开启
  │
  ├──→ 飞书Sheet (展示层, 有延迟)
  │     简单日志追加，人工翻看
  │
  └──→ 飞书Bitable (展示层, 有延迟)
        结构化存储，支持筛选/视图/仪表盘，人工分析
```

### 关键理解

| 层 | 角色 | 数据完整性 | 实时性 | 用途 |
|----|------|-----------|--------|------|
| QuestDB | 主存储 | 完整可靠 | 实时 | 程序自动化、二次筛选的数据源 |
| 飞书群 | 推送通道 | 经过频控/筛选 | 准实时 | 人即时看到重要信号 |
| 飞书表格 | 展示层 | 可能延迟/缺失 | 有延迟 | 人工回看、筛选、统计 |

- **飞书表格不是程序的数据源**，不要从飞书读数据做自动化
- **二次筛选走 QuestDB**：SQL查库 → 筛选逻辑 → 再推飞书群
- **飞书表格只给人看**：人手动筛选、建视图、做仪表盘
- 当前信号已经在数据库里（`qd_signals`、`qd_intraday_event` 等），**不需要额外录入一份**

### 二次筛选架构（后续实现）

```
QuestDB (全量信号)
   │
   ▼
二次筛选逻辑 (SQL)
   │  例如: 同策略1小时内≥3次 → 升级推送
   │       评分>80 + 主力流入 → 高优先级推送
   │       炸板信号 → 立即推 (已有)
   ▼
飞书群 (只推筛选后的, 减少噪音)
```

## 四、模块架构

```
4_feishu/
├── __init__.py          # 统一出口 + log_signals() 全链路入口
├── config.py            # 环境变量 (config/.env)
├── auth.py              # 飞书 API 认证 (tenant_access_token 自动刷新)
├── push.py              # 推送 (Webhook 优先 + API 降级, 含频控)
├── doc_writer.py        # 飞书文档写入 (创建/追加/日终报告)
├── sheet_writer.py      # 电子表格写入 (简单日志追加)
└── bitable_writer.py    # 多维表格写入 (结构化, 支持筛选/视图/仪表盘)
```

### 数据流

```
信号产生 (strategy/plugins/*)
  │
  ▼
log_signals(signals)           ← 调用方只需调这一个函数
  ├── push_signal()            → 飞书群卡片推送 (当前默认关闭, push=True时才推)
  ├── write_signal_batch()     → 电子表格追加行 (按日期自动建子sheet)
  └── write_signal_batch_bitable() → 多维表格追加记录 (按日期自动建表)
```

三路独立容错，任何一路失败不影响其他。

### 为什么目录名是 4_feishu (数字开头)

用户指定了落地路径。Python 不允许 `import 4_feishu`，所以用 `importlib`：
```python
import importlib
feishu = importlib.import_module('4_feishu')
```

### 文件存哪里 & 怎么找

应用创建的文件存在**机器人空间**，你无法像自己的文件夹一样浏览。解决方式：
- **每次创建新资源，自动推送链接到飞书群**（格式：`📎 新文档: xxx\nhttps://...`）
- 群消息里搜 `📎 新` 即可找到所有链接
- 每个资源自动设置「组织内链接可编辑」，打开即可编辑，无需申请权限
- 文件夹 token 已配置 (`FOLDER_TOKEN`)，新文件会归入 `量化数据` 文件夹

## 五、各模块关键逻辑

### config.py
- 从 `config/.env` 读取所有配置
- 关键变量：`APP_ID`, `APP_SECRET`, `WEBHOOK_URL`, `SHEET_TOKEN`, `BITABLE_TOKEN`, `FOLDER_TOKEN`

### auth.py
- 调用 `POST /auth/v3/tenant_access_token/internal` 获取 token
- 内存缓存，2h 有效，提前 5min 自动刷新
- 线程安全 (threading.Lock)

### push.py
- **双通道**：Webhook 优先（零依赖、快），不可用时降级到 API
- **频控**：同 `code+signal_type` 5分钟内只推一次，查 `qd_signal_log` 表
- 颜色映射含异动类型：surge_up/down(绿/红), limit_seal/break(绿/红), capital_in/out(绿/红)
- 从旧 `lib/lark.py` 迁移，逻辑完全保留

### doc_writer.py
- 基于 block API 写入（标题/段落拆分为 block）
- `create_daily_report()` 自动按日期命名，创建后自动设置权限+推送链接
- 时间字段：只有时分秒时自动补今日日期

### sheet_writer.py
- `auto_daily_sheet()` 按日期自动创建子 sheet + 写表头
- `ensure_headers()` range 格式必须指定结束列 (如 `A1:I1`)
- 未配 `SHEET_TOKEN` 时自动创建电子表格到指定文件夹

### bitable_writer.py
- 主键字段 (Primary Field) **不可删除**，只能改名
- 信号类型设为单选 (type=3)，带颜色标签
- `_add_signal_fields()` 逻辑：主键改名→删其余默认→加自定义字段
- 信号类型选项含异动：surge_up/down, limit_seal/break, capital_in/out
- 创建后自动设置权限+推送链接

### 信号/决策格式兼容

`_signal_to_row` / `_signal_to_record` 同时兼容两种格式：
- signal: `{signal_time, signal_type, signal_score, ...}`
- decision: `{decision_time, action, position_size, ...}`

`log_signals` 自动识别：有 `action` 字段 → 调 `push_decision`；否则调 `push_signal`。

## 六、信号字段定义

表格/多维表格/卡片共用统一字段：

| 字段 | Sheet | Bitable类型 | 说明 |
|------|-------|------------|------|
| 时间 | 文本 | 文本 | 只有时分秒时自动补日期 |
| 代码 | 文本 | 文本 | 如 002479.SZ |
| 股票名称 | 文本 | 文本 | 如 富春环保 |
| 策略 | 文本 | 文本 | 如 divergence_warn |
| 信号类型 | 文本 | 单选(带颜色) | buy/sell/warn/.../surge_up/limit_break/capital_in等 |
| 评分 | 文本 | 数字 | 0-100 |
| 价格 | 文本 | 数字 | |
| 成交量 | 文本 | 数字 | |
| 原因 | 文本 | 文本 | |

## 七、环境配置

`config/.env` 中需要：

```env
# Webhook (推送必需)
LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# 应用凭据 (文档/表格 API 必需)
LARK_APP_ID=cli_xxx
LARK_APP_SECRET=xxx

# 资源 ID (可选, 程序会自动创建)
LARK_SHEET_TOKEN=        # 电子表格 token
LARK_BITABLE_TOKEN=      # 多维表格 token
LARK_DOC_ID=             # 文档 ID
LARK_SHEET_ID=           # 子 sheet ID
LARK_FOLDER_TOKEN=       # 文件夹 token (默认已内置)
```

未配置 WEBHOOK → 推送跳过
未配置 APP_ID/SECRET → API 通道不可用，降级或跳过
未配置 SHEET_TOKEN/BITABLE_TOKEN → 自动创建

## 八、接入状态

| 调用方 | 调用方式 | 说明 |
|--------|---------|------|
| runner/intraday_loop.py | `log_signals(signals)` | 盘中策略决策，写Sheet+Bitable，推送默认关 |
| runner/auction_monitor.py | `log_signals(signals)` | 集合竞价信号，写Sheet+Bitable，推送默认关 |
| runner/daily_close.py | `push_text()` + `create_daily_report()` | 日终通知+策略报告文档 |
| runner/daily_init.py | `push_text()` | 初始化通知，非信号 |
| strategy/intraday_engine.py | `log_signals(signals, sheet=True, bitable=True)` | 盘中异动，写Sheet+Bitable，推送默认关 |
| compute/k3_sentiment.py | `push_text()` | 情绪变盘通知，非标准信号 |

**当前推送策略**：`log_signals` 默认 `push=False`，等二次过滤层实现后再按需开启。

## 九、已知坑 & 排查

| 问题 | 原因 | 解决 |
|------|------|------|
| 多维表格前几列是空的 | 创建时自带默认字段(文本/单选/日期/附件) | `_add_signal_fields()` 已处理：主键改名+删默认+加自定义 |
| 表头没写入 | range 格式 `A1` 不行 | 必须用 `A1:I1` 指定结束列 |
| No edit permission | 应用未授权表格 | 用 API 创建的表格天然有权限；手动创建的需分享给应用 |
| Field validation failed | bitable 字段创建 body 格式 | `field_name` 和 `type` 必须在顶层，不能嵌套在 `field` 下 |
| Primary Field cannot be deleted | bitable 主键字段不可删 | 只能改名+改类型 |
| FieldNameDuplicated | 改名时和已有字段重名 | 先删重复字段再改名 |
| 时间只有时分秒 | 信号数据只传了时间 | `_signal_to_row/record` 自动补今日日期 |
| 需要申请权限 | 应用创建的文件默认只有应用自己可访问 | `_set_public_permission()` 自动设为组织内可编辑 |
| 找不到文件 | 机器人空间的文件无法在云空间浏览 | 创建时自动推送链接到群，搜 `📎 新` 即可 |

## 十、飞书资源 URL

| 资源 | URL |
|------|-----|
| 电子表格 | https://bytedance.larkoffice.com/sheets/LRkFs5pFkh2J4ttrTmWcMRrrnxE |
| 多维表格 | https://bytedance.larkoffice.com/base/BwfdbFjHiaIyqjs9uIMc5lntnwg |
