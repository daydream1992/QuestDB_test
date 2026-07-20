# QuestDB 启动 & 灾难恢复手册

> **盘中第一优先级**：数据库必须 3 分钟内恢复可用。
> 写于 2026-07-07，经历一次 `tables.d` 被覆盖后数据完好恢复。

---

## 一、正常启动（30 秒）

### 方式 A：双击 bat（推荐）

```
K:\QuestDB_test\start_questdb.bat
```

内容就是 `cd /d K:\QuestDB\bin && questdb.exe`。

### 方式 B：Explorer 直接双击

```
K:\QuestDB\bin\questdb.exe
```

### 确认启动成功

```cmd
tasklist | findstr java
:: 应看到 1 个 java.exe 进程
```

端口侦听（启动后 3-5 秒就绪）：
- PG 协议 `127.0.0.1:8812`
- HTTP Console `http://127.0.0.1:9000`

---

## 二、关键路径（记住它）

| 项目 | 值 |
|---|---|
| **安装路径** | `K:\QuestDB\bin\questdb.exe` |
| **数据目录** | `K:\QuestDB\bin\qdbroot\db\` |
| **项目 DDL** | `K:\QuestDB_test\ddl\NN_*.sql` |
| **启动脚本** | `K:\QuestDB_test\start_questdb.bat` |

**`K:\QuestDB_test\qdbroot\` 只是配置备份，不含数据。** 数据在安装目录的 `qdbroot/db/` 里。

---

## 三、禁止操作

| 操作 | 后果 |
|---|---|
| Git Bash 里 `./questdb.exe` | signal 11 segfault，进程崩 |
| `start "" questdb.exe` | 启动不完整，端口没听上 |
| `questdb.exe -d 其他目录` | 另起空实例，覆盖 `tables.d` 注册文件 |
| 删 `db/` 目录下的 `~NNN/` 分区目录 | 永久丢数据 |

前 3 条只是起不来或注册丢失，数据还在。第 4 条是真的毁灭性操作。

---

## 四、灾难恢复（tables.d 被覆盖）

### 现象

QuestDB 能启动，PG 端口能连，但 `tables()` 查不到任何 `qd_*` 表。
数据目录 `db/` 下的 `qd_stock_snapshot~302/` 等分区目录都在，但 `tables.d.0` 是空的。

### 恢复步骤（已验证，约 1 分钟）

直接双击 `K:\QuestDB_test\recover_questdb.bat`，一键完成：
1. 停已有 QuestDB
2. 启动 QuestDB（自动等待 10 秒）
3. 从 `ddl/*.sql` 重建 40 张表注册
4. 确认结果

### 恢复脚本做了什么

`recover_questdb.bat` 调 `scripts/recover_tables.py`，后者：
1. 从 `ddl/01_daily.sql` ~ `ddl/23_positions_v2.sql` 自动解析 CREATE TABLE 语句
2. 逐条执行 DDL（`CREATE TABLE IF NOT EXISTS`）
3. QuestDB 自动复用已有的分区目录
4. 数据行数立即可查

**DDL 即唯一来源**：任何表结构改动只需改 `ddl/` 下的 SQL 文件，
`recover_tables.py` 下次运行自动同步，无需手动维护硬编码字典。

---

## 五、DuckDB 双写备份（parquet）

从 2026-07-07 起，高频表写入 QuestDB 的同时会**自动**在 `D:\dbshujubeifen\` 留一份 parquet 备份。

### 机制

只改 `lib/qdb.py` 一个文件，调用方零感知。

```
executemany_batch(QuestDB写入) 
   ↓ 成功后自动
_append_to_parquet_buffer(内存缓存行数)
   ↓ 攒够 5 万行 或 进程关闭前
_flush_parquet_buffer(按天合并写 parquet)
```

### 覆盖的 6 张高频表

| 表 | 写入间隔 | 日均行数 | 单日 parquet 大小 |
|---|---|---|---|
| qd_pricevol | 10s | ~250 万 | ~150 MB |
| qd_kline_1m | 60s | ~500 万 | ~480 MB |
| qd_kline_5m | 5min | ~100 万 | ~96 MB |
| qd_stock_snapshot | 3s | ~200 万 | ~240 MB |
| qd_money_flow | ~60s | ~50 万 | ~15 MB |
| qd_big_order | ~60s | ~30 万 | ~10 MB |
| **合计** | | **~1130 万** | **~1 GB/天** |

### 文件结构

```
D:\dbshujubeifen\
├── qd_pricevol\
│   └── 2026-07-07.parquet    ← 按天合并，每天只一个文件
├── qd_kline_1m\
│   └── 2026-07-07.parquet
└── ...
```

### DuckDB 直接读

```sql
SELECT snapshot_time, code, Now, Volume 
FROM 'D:\dbshujubeifen\qd_pricevol\2026-07-07.parquet'
WHERE code = '000001.SH';
```

### 故障恢复（QuestDB 挂了）

```python
import pandas as pd
df = pd.read_parquet(r'D:\dbshujubeifen\qd_pricevol\2026-07-07.parquet')
# 或者用 DuckDB 直接 SQL 查询
```

### 设计要点

- **不阻断主流程**：任何异常只打 `logger.warning`，不影响 QuestDB 写入
- **至少丢一次**：写 QuestDB 成功后才进缓存，缓存进程崩溃可能丢一批（最多 5 万行）
- **按天合并**：当日多次写入合并到一个文件，避免碎片
- **年增量**：约 250 GB，`D:` 盘留足空间即可

### 强制落盘（供进程关闭前调用）

```python
from lib.qdb import force_flush_backup
force_flush_backup()
```

---

## 六、盘中心跳检查

以下 Python 脚本可以在盘中定时运行，确认数据库活着：

```python
from lib.qdb import connect, query_df

con = connect()
# 查最新信号时间
row = query_df(con, "SELECT max(signal_time) as last_signal FROM qd_signals")
if row['last_signal'][0]:
    print(f'OK 最后信号: {row["last_signal"][0]}')
else:
    print('WARN qd_signals 为空')
con.close()
```

也可以用 HTTP 接口快速检查：
```
curl http://127.0.0.1:9000/exec?query=SELECT+COUNT(*)+FROM+qd_signals
```

---

## 六、备份策略

| 频率 | 内容 | 命令 |
|---|---|---|
| 每次入库后 | `db/` 全量 | `xcopy /E /I db db.snapshot.YYYYMMDD` |
| 改 DDL 前 | `db/` 全量 | 同上 |
| 磁盘空间告警 | 清理超过 7 天的 `.backup` | `rmdir /S /Q db.backup.OLD` |

---

## 七、tl;dr（3 句话）

1. **双击** `start_questdb.bat` 启动
2. **绝对不要**用 `-d` 参数覆盖数据目录
3. **tables.d 坏了** → 停库 → 备份 → 启动 → `python scripts/recover_tables.py`
