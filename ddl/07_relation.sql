-- ============================================================
-- 脚本路径: K:\QuestDB_test\ddl\07_relation.sql
-- 用途: 6 张关系图谱表 (板块元数据 + 个股三级分类 + 概念/地域/风格/指数映射)
-- 数据源: tqcenter get_sector_list + get_stock_list_in_sector + 财经分类接口
-- 时间戳: update_time (关系更新时刻)
-- 去重: DEDUP UPSERT KEYS(update_time, code/sector_code/...)
-- ============================================================


-- ------------------------------------------------------------
-- 表名: qd_sector_meta
-- 用途: 板块元数据 (板块代码 → 名称/类型)
-- 数据源: tqcenter get_sector_list
-- 时间戳: update_time
-- 字段映射:
--   sector_code  ← 板块代码
--   sector_name  ← 板块名称
--   sector_type  ← 板块类型 (industry/concept/region/style)
--   update_time  ← datetime.now()
--   stock_count  ← 板块内股票数量
--   description  ← 板块描述
-- 去重: (update_time, sector_code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_sector_meta (
    sector_code   VARCHAR,
    sector_name   VARCHAR,
    sector_type   VARCHAR,
    update_time   TIMESTAMP,
    stock_count   INT,
    description   VARCHAR
) TIMESTAMP(update_time) PARTITION BY YEAR
DEDUP UPSERT KEYS(update_time, sector_code);


-- ------------------------------------------------------------
-- 表名: qd_stock_industry
-- 用途: 个股申万三级分类 (industry_l1 → l2 → l3)
-- 数据源: tqcenter get_more_info + 申万行业分类
-- 时间戳: update_time
-- 字段映射:
--   code         ← 股票代码
--   update_time  ← datetime.now()
--   industry_l1  ← 一级行业
--   industry_l2  ← 二级行业
--   industry_l3  ← 三级行业
-- 去重: (update_time, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_stock_industry (
    code         VARCHAR,
    update_time  TIMESTAMP,
    industry_l1  VARCHAR,
    industry_l2  VARCHAR,
    industry_l3  VARCHAR
) TIMESTAMP(update_time) PARTITION BY YEAR
DEDUP UPSERT KEYS(update_time, code);


-- ------------------------------------------------------------
-- 表名: qd_map_concept_stock
-- 用途: 概念板块 → 个股映射 (多对多)
-- 数据源: tqcenter get_stock_list_in_sector (concept)
-- 时间戳: update_time
-- 字段映射:
--   concept_name ← 概念板块名称
--   code         ← 股票代码
--   update_time  ← datetime.now()
--   weight       ← 个股在板块中的权重 (%)
-- 去重: (update_time, concept_name, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_map_concept_stock (
    concept_name  VARCHAR,
    code          VARCHAR,
    update_time   TIMESTAMP,
    weight        DOUBLE
) TIMESTAMP(update_time) PARTITION BY YEAR
DEDUP UPSERT KEYS(update_time, concept_name, code);


-- ------------------------------------------------------------
-- 表名: qd_map_region_stock
-- 用途: 地域板块 → 个股映射
-- 数据源: tqcenter get_stock_list_in_sector (region)
-- 时间戳: update_time
-- 字段映射:
--   region       ← 地域名称 (省份/城市)
--   code         ← 股票代码
--   update_time  ← datetime.now()
-- 去重: (update_time, region, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_map_region_stock (
    region        VARCHAR,
    code          VARCHAR,
    update_time   TIMESTAMP
) TIMESTAMP(update_time) PARTITION BY YEAR
DEDUP UPSERT KEYS(update_time, region, code);


-- ------------------------------------------------------------
-- 表名: qd_map_style_stock
-- 用途: 风格板块 → 个股映射 (如大盘/小盘/价值/成长)
-- 数据源: tqcenter get_stock_list_in_sector (style)
-- 时间戳: update_time
-- 字段映射:
--   style        ← 风格名称
--   code         ← 股票代码
--   update_time  ← datetime.now()
-- 去重: (update_time, style, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_map_style_stock (
    style         VARCHAR,
    code          VARCHAR,
    update_time   TIMESTAMP
) TIMESTAMP(update_time) PARTITION BY YEAR
DEDUP UPSERT KEYS(update_time, style, code);


-- ------------------------------------------------------------
-- 表名: qd_map_index_stock
-- 用途: 指数 → 成份股映射 (含权重)
-- 数据源: tqcenter get_stock_list_in_sector (index)
-- 时间戳: update_time
-- 字段映射:
--   index_code   ← 指数代码
--   code         ← 成份股代码
--   update_time  ← datetime.now()
--   weight       ← 成份股权重 (%)
-- 去重: (update_time, index_code, code)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qd_map_index_stock (
    index_code   VARCHAR,
    code         VARCHAR,
    update_time  TIMESTAMP,
    weight       DOUBLE
) TIMESTAMP(update_time) PARTITION BY YEAR
DEDUP UPSERT KEYS(update_time, index_code, code);
