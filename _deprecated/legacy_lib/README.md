# legacy_lib/

已废弃的 lib 层代码归档区。**禁止业务代码 import**（CLAUDE.md §5 引用约束）。

## lark.py

- **原路径**: `lib/lark.py`
- **归档日期**: 2026-07-05
- **内容**: 飞书 webhook 推送 (`push_text`/`push_signal`/`push_decision`) + 5 分钟频控 (`qd_signal_log`)

### 废弃原因

1. **被 feishu 三通道完全替代**: `feishu/push.py` 提供同名函数 (`push_text`/`push_signal`/`push_decision`) + 文档/表格/多维表格三通道；lark 仅 webhook 单通道。
2. **零引用**: 项目内 0 个 `import lib.lark`；0 动态调用 (`import_module`)。
3. **历史使用者已迁移**: `compute/k3_sentiment.py:392-393` 原用 `lark.push_text`，现改用 `feishu.push_text`。

### 取回方式

```bash
cp _deprecated/legacy_lib/lark.py lib/lark.py
```

注意：取回前确认 `feishu/push.py` 是否已覆盖需求（通常已覆盖）。
