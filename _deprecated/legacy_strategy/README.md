# legacy_strategy/

已废弃的 strategy 层代码归档区。**禁止业务代码 import**（CLAUDE.md §5 引用约束）。

## auction_engine.py

- **原路径**: `strategy/auction_engine.py`
- **归档日期**: 2026-07-05
- **内容**: 竞价规则树标签 (`label_row`) + 资金强度相对化 (`calc_zjl_ratio`) + 线性拟合 (`fit_trend`)
- **移植自**: DB数据库_v2 竞价监控/engine.py v2 规则树

### 废弃原因

1. **依赖资源已删除**: docstring 写明 `trap_cnt 来自 qd_pianpao_daily (k4_pianpao 盘后产出)`，
   但 `ddl/15_pianpao.sql` 已删除，`qd_pianpao_daily` 表不存在；`k4_pianpao` 也未实现
   （CLAUDE.md §12.1 中 k4 是大盘情绪预留位，非 pianpao）。
2. **职责转移**: pianpao（骗炮检测）由外部 `DB数据库_v2` 每天产出，Q 不重复算。
3. **零引用**: 项目内 0 个 `import auction_engine`，三个函数 (`calc_zjl_ratio`/`label_row`/`fit_trend`) 0 外部调用。
4. `_deprecated/session_handover_2026-07-03.md` 已记录"规则树是死代码"。

### 取回方式

```bash
cp _deprecated/legacy_strategy/auction_engine.py strategy/auction_engine.py
```

需同时恢复 `ddl/15_pianpao.sql`（可从 git history `git log --all -- ddl/15_pianpao.sql` 查）。
