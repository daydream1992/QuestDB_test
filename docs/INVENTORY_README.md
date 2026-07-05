# `docs/data_inventory.json` + `docs/未使用字段清单.md`

这两个文件是 `scripts/data_inventory*.py` 系列脚本的**生成产物**，不是手写文档。

## 生成链路

```
scripts/data_inventory.py           → docs/data_inventory.json     (骨架)
scripts/data_inventory_spec.py      → 给骨架补"说明书权威来源"
scripts/data_inventory_semantics.py → 给骨架补"业务语义"
scripts/data_inventory_rules.py     → 给骨架补"规则/约束"
scripts/data_inventory_target.py    → 给骨架补"适用对象"
scripts/data_inventory_unused.py    → docs/未使用字段清单.md      (派生)
```

## 重新生成

```bash
python scripts/data_inventory.py
python scripts/data_inventory_spec.py
python scripts/data_inventory_semantics.py
python scripts/data_inventory_rules.py
python scripts/data_inventory_target.py
python scripts/data_inventory_unused.py
```

## 路径约束

这 7 个脚本硬编码了 `docs/data_inventory.json` 与 `docs/未使用字段清单.md` 的相对路径，
**不能移动到别处**——除非同步修改 scripts 里的 `os.path.join`。

未来如需移动，建议改用环境变量 `INVENTORY_DIR` 或 config 配置文件。

## 当前 status

2026-07-05：保留在原位，不移动。这两个文件体积合计 230 KB，是盘点产物而非代码，
**可以考虑从版本控制中排除**（加入 `.gitignore`），让它们在本地重新生成——
但目前仍入库以备跨机器同步。