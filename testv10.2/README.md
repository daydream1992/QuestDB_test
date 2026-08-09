# testv10.2 — A股盘中实时量化监控系统

> 独立新系统(与 v10.1/老 intraday_loop 无关),采集统一 → 计算并联 → 预警统一 → 5 时段调度。
> 数据源 tqcenter COM(需通达信客户端),输出飞书多维表 + 实时推送卡 + DuckDB 本地快照。
> **生产入口**: `python testv10.2/radar_main.py --push`(或根目录 `run_v10.2.bat --push`)。

## 一、架构总览

```
radar_main.py  单进程主循环 market_clock 5 时段调度 (auction/open/intraday/tail/close)
  │
  ├─ 采集层 data_provider.fetch_bundle(stage, df)
  │     ├─ ticker.scan_universe()       全A df (pricevol, pct)
  │     ├─ sentiment_fetcher            大盘情绪 raw (含日级留存候选)
  │     └─ fetch_auction_raw()          竞价: 板块Outside/OpenAmo + 一字候选
  │
  ├─ 计算层 (并联, per-module try 故障隔离, 零 tq 调用)
  │     ├─ sentiment_monitor   大盘情绪 8 维度 → v10.2情绪 (1min/行) + 时段聚合
  │     ├─ rotation ×2         概念/行业轮动 → 轮动表 (3min/行)
  │     ├─ auction_monitor     竞价榜 + 一字候选 → v10.2竞价
  │     ├─ open_monitor        subscribe_hq 方案A (开盘拉升)
  │     ├─ tail_monitor        尾盘炸板 → v10.2尾盘
  │     ├─ stock_ranking       池内个股榜 (含全市场Top20补钻) → v10.2个股榜
  │     ├─ blindspot_monitor   Top20 盲区补盲 (6s回调, 首封/炸板/回封计数)
  │     ├─ opportunity_engine  3正事件 (新主线/趋势确认/龙头封板)
  │     └─ ladder_tracker      涨停梯队事件落表 (概念/行业映射)
  │
  ├─ 预警层 alert_engine (L1跳水→L2板块→L3个股) + publisher 8卡 (≤2/min分桶)
  │
  └─ DuckDB 本地快照 duckdb_snapshot (sentiment/pool/drilled/events, 防飞书挂)
```

### 5 时段激活矩阵
| 时段 | sentiment | rotation | auction | open | tail | ranking | 机会/梯队 | alert |
|---|---|---|---|---|---|---|---|---|
| auction 9:15 | — | — | ✅ | — | — | — | — | — |
| open 9:30 | — | — | — | ✅订阅 | — | — | — | — |
| intraday 9:45 | ✅1min | ✅3min | — | — | — | ✅2min | ✅ | ✅ |
| tail 14:00 | ✅ | ✅ | — | — | ✅1min | ✅ | ✅ | ✅+blast |
| close 15:00 | ✅定格 | ✅定格 | — | — | — | — | — | ✅ |

## 二、模块清单 (24 业务文件)

| 文件 | 职责 |
|---|---|
| radar_main.py | 主循环: 采集578板→探照灯→池→钻取→分时段计算; 5时段调度 + 故障隔离 + 预检/心跳 |
| data_provider.py | 统一采集层, 按 stage 编排 bundle (故障隔离) |
| ticker.py | 全A预筛(scan_universe) + 个股钻取(drill_stocks, 单调用优化 + 超时重试) |
| meso_radar.py | 板块扫描 + 6探照灯(floor阈值) + 动能分 |
| board_pool.py | 板块状态机 NEW/HOT/WARN/DEAD + 动态容量(25-35) + 优先级淘汰 |
| mapping_store.py | 板块-个股映射索引 (parquet 只读) |
| sentiment_fetcher.py | 大盘情绪采集 (含日级留存候选, 防炸板跌出样本) |
| sentiment_monitor.py | 8维度情绪分 + 240行表 + 时段聚合 |
| rotation.py | 概念/行业轮动强度榜 + 切换事件 |
| auction_monitor.py | 竞价放量板块 + 一字候选 |
| open_monitor.py | 开盘拉升 (subscribe_hq 方案A: 回调只queue.put) |
| tail_monitor.py | 尾盘炸板 (FCAmo 曾封现开) |
| stock_ranking.py | 池内个股榜 (5因子: 涨停比例/封单/量比/主力/位置) |
| blindspot_monitor.py | Top20 盲区补盲: 6s回调 + 首封时间戳 + 封/炸/回计数 |
| opportunity_engine.py | 3正事件: 新主线/趋势确认/龙头封板(首封排序) |
| ladder_tracker.py | 涨停梯队表: 事件+概念/行业映射+计数 |
| alert_engine.py | 大盘跳水 L1→L2→L3 + 尾盘炸板 |
| publisher.py | 8卡推送 + 分桶令牌桶(机会/预警各≤1min) + 失败计数 |
| duckdb_snapshot.py | DuckDB 本地快照 (4表, 防飞书挂 + 盘后SQL) |
| refresh_mapping.py | 生成 sector_mapping.parquet (盘后跑) |
| refresh_snapshot.py | 探针快照 (调试用) |
| export_main_data.py | 导出 Excel (盘后检查) |
| reconcile_fields.py | 飞书表字段同步 (配置diff补缺) |
| settings.py | 全部配置 |

## 三、字段单位速查 (改计算必看, 错 1e4 量级)

| 字段 | 单位 | 说明 |
|---|---|---|
| **OpenAmo** | **元** | 竞价金额 (DYNAINFO(15)/10000 铁证) |
| FCAmo / OpenZTBuy / CJJEPre1 / CJJEPre3 / OpenAmoPre1 | **万元** | 封单/竞价买入/次日预判/昨开盘 |
| Zjl | 万元 | 主买净额 (全档位主动性, 非主力!) |
| **Zjl_HB** | 万元 | **主力净流入** (超大单+大单, 个股榜用这个) |
| HisHigh / HisLow | 元 | 52周高低 (静态日级) |
| EverZTCount | 天 | 连板高度 |
| ZAF | % | 涨幅 |
| 880001 Amount | 万元 | 全A成交额 |
| **涨停判定** | — | **FCAmo > 0** (非 Now>=ZTPrice) |
| 竞价昨量比 | 无量纲 | `(OpenAmo/1e4) ÷ OpenAmoPre1` |

## 四、生产跑法 + 自检

```bash
# 盘前检查
powershell -ExecutionPolicy Bypass -File prepare_v10.2.ps1
# 生产 (真推飞书, 9:10 前启动, 9:15 自动进竞价)
python testv10.2/radar_main.py --push
# 或统一入口
run_v10.2.bat --push
# dry-run 验证 (禁推)
python testv10.2/radar_main.py --force --rounds 1
# 进程守护 (盘中自动重启)
powershell -ExecutionPolicy Bypass -File watch_radar.ps1 --push --restart
```

自检: `python testv10.2/opportunity_engine.py` / `blindspot_monitor.py` / `ladder_tracker.py` / `duckdb_snapshot.py` / `board_pool.py` / `sentiment_monitor.py` 各模块自检。

## 五、扩展指南 (新增表/字段/事件)

**新增飞书表**: 新建模块 + `*_FIELDS` 列表 + `auto_named_table`(零改 bitable_writer)。参考 ladder_tracker.py。

**新增字段**: ①settings 加常量 ②模块 `_FIELDS` 加列 ③写表 flat dict 加 key。改表结构后跑 `reconcile_fields.py --push` 补到已存在表。

**新增推送卡**: ①publisher 加 `on_xxx` 方法(用 `self._dispatch(title, lines, now, lane)`, lane=0 机会/1 预警) ②调用方触发。

**新增探照灯/阈值**: settings + meso_radar `_tag_searchlights`。**注意 drop 灯用 `le=True`(ZAF≤floor)**。

## 六、运维

- **日志**: `logs/testv10.2_radar_YYYYMMDD.log`(50MB轮转/30天);心跳 `logs/heartbeats/radar_main.ts`
- **DuckDB 快照**: `logs/v10.2_radar.duckdb`(sentiment/board_pool/drilled/events, 盘后 SQL 分析)
- **盘后健康度**: 日志末行"共 N 轮"; publisher.send_fail/bucket_dropped 计数
- **蓝图文档**: `SIGNAL_DESIGN.md`(signal_extractor 体系)**已砍除**; `BOARDING_PLAN.md`(5闸)**未实现蓝图** — 别按它们找代码

## 七、待验证 (盘中)

详见 [VERIFY_CHECKLIST.md](VERIFY_CHECKLIST.md): 竞价门控修复后首次验证 / close 定格 / 全市场补钻 / 分桶优先级 / 涨停梯队/机会事件真数据。
