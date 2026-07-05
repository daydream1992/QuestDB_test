# `docs/INVENTORY_README.md` — 维护已迁移

2026-07-05 整理批次 5 已将 `docs/data_inventory.json` + `docs/未使用字段清单.md` 迁至 `_deprecated/inventory/`。

## 新位置

```
_deprecated/inventory/data_inventory.json     (178 KB)
_deprecated/inventory/未使用字段清单.md        (50 KB)
```

## 为什么放 _deprecated/

这两个文件是 `scripts/data_inventory*.py` 的**生成产物**，不应作为主项目代码被版本控制。
但因为是人工校对过的中文资产清单（含业务语义/权威来源标注），丢弃可惜——
放进 `_deprecated/inventory/` 作为"盘点快照"。

## 重新生成

```bash
python scripts/data_inventory.py            # → _deprecated/inventory/data_inventory.json
python scripts/data_inventory_spec.py       # 给骨架补"说明书权威来源"
python scripts/data_inventory_semantics.py  # 给骨架补"业务语义"
python scripts/data_inventory_rules.py      # 给骨架补"规则/约束"
python scripts/data_inventory_target.py     # 给骨架补"适用对象"
python scripts/data_inventory_unused.py     # → _deprecated/inventory/未使用字段清单.md
```

未来如需移到更显眼的位置（如 `data/inventory/`），只需改这 7 个 scripts 里的 `os.path.join` 常量。

## 取回方式

```bash
cp _deprecated/inventory/data_inventory.json     docs/data_inventory.json
cp _deprecated/inventory/未使用字段清单.md        docs/未使用字段清单.md
```

并把 `scripts/data_inventory*.py` 里的路径改回 `docs/`。