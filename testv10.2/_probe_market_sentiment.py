"""testv10.2 大盘情绪监控 - API 探针 v2 (对齐 8 维度纯实时快照版参考资料)

参考资料接口分类:
  get_market_snapshot  实时快照 (单只 指数/板块/个股)
  get_pricevol         实时批量 (全A LastClose/Now/Volume) — 已由 scan_universe 验证, 本探针不重验
  get_more_info        实时扩展 (个股 封单/连板/封成比)
  废弃: get_scjy_value_by_date / get_bkjy_value_by_date (日级, 不用)

只验新参考资料带来的未知点 (已知能用的不重复验):
  P1[BLOCK] 999999.SH UpHome/DownHome  — 维度1 全A涨跌家数 (指数自带, 与df计数两路源)
  P2       get_stock_list(market='10') + 板块snapshot Outside/Inside — 维度2/8 涨跌停家数源
  P3[BLOCK] 涨停候选 get_more_info EverZTCount/LastZTHzNum/FCAmo/FCb — 维度4/5 (FCb封成比是新未知)
  P4       880863 昨日涨停指数 snapshot — 维度6 持续性 (新源, 替代废弃的SC系列)
  P5       5宽基 snapshot + Zangsu + 000688.SH 指数vs个股判别 — 维度7 指数风向

BLOCK 门: P1/P3 失败 → 停, 找用户对齐 (核心源无数据则方案要改)。
"""
import bootstrap
bootstrap.ensure_paths()

from lib.tq_client import safe_call, init, close  # noqa: E402
from tqcenter import tq  # noqa: E402

import ticker as tk  # noqa: E402  复用 scan_universe 找涨停候选


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


print('=' * 64)
print('大盘情绪 API 探针 v2 (8 维度纯实时快照版)')
print('=' * 64)

init()
try:
    # ---------- P1[BLOCK] 999999.SH 上证指数 UpHome/DownHome (维度1) ----------
    print('\n[P1·BLOCK] 999999.SH snapshot — 全A涨跌家数 (UpHome/DownHome)')
    try:
        s = safe_call(tq.get_market_snapshot, stock_code='999999.SH',
                      field_list=['Now', 'LastClose', 'UpHome', 'DownHome', 'Amount']) or {}
        uph = s.get('UpHome'); dnh = s.get('DownHome')
        now = _f(s.get('Now')); lc = _f(s.get('LastClose'))
        chg = (now - lc) / lc * 100 if lc > 0 else None
        print(f'  Now={s.get("Now")} LastClose={s.get("LastClose")} 涨幅={chg:.2f}%'
              if chg is not None else f'  Now/LC 异常: {s}')
        print(f'  UpHome(上涨家数)={uph}  DownHome(下跌家数)={dnh}')
        ok1 = uph not in (None, '', '0') and dnh not in (None, '', '0')
        print(f'  → {"✅ 维度1有源" if ok1 else "⚠️ UpHome/DownHome 空 → 维度1退化为df计数"}')
    except Exception as e:  # noqa: BLE001
        print(f'  ❌ 失败: {e}')

    # ---------- P2 板块指数列表 + Outside/Inside (维度2/8) ----------
    print('\n[P2] get_stock_list(market=10) 板块指数 + Outside/Inside')
    for mkt, label in [('10', '所有板块指数'), ('11', '行业板块'), ('12', '概念板块')]:
        try:
            lst = safe_call(tq.get_stock_list, market=mkt, list_type=0) or []
            print(f'  market={mkt}({label}): {len(lst)} 条; 样例 {lst[:2]}')
        except Exception as e:  # noqa: BLE001
            print(f'  market={mkt} 失败: {e}')
    try:
        bl = safe_call(tq.get_stock_list, market='10', list_type=0) or []
        if bl:
            # 找一个非空的板块验 Outside/Inside
            for bc in bl[:8]:
                sn = safe_call(tq.get_market_snapshot, stock_code=bc,
                               field_list=['Now', 'LastClose', 'Outside', 'Inside',
                                           'UpHome', 'DownHome', 'Amount', 'Zangsu']) or {}
                if sn.get('Outside') not in (None, '') or sn.get('UpHome') not in (None, ''):
                    now = _f(sn.get('Now')); lc = _f(sn.get('LastClose'))
                    chg = (now - lc) / lc * 100 if lc > 0 else None
                    print(f'  板块样例 {bc}: 涨幅={chg:.2f}%'
                          f' Outside(涨停家数)={sn.get("Outside")} Inside(跌停家数)={sn.get("Inside")}'
                          f' UpHome={sn.get("UpHome")} DownHome={sn.get("DownHome")}'
                          f' Zangsu={sn.get("Zangsu")}')
                    break
    except Exception as e:  # noqa: BLE001
        print(f'  板块snapshot失败: {e}')

    # ---------- P3[BLOCK] 涨停候选 get_more_info (维度4连板/维度5封板) ----------
    print('\n[P3·BLOCK] 涨停候选 get_more_info — EverZTCount/LastZTHzNum/FCAmo/FCb')
    df = tk.scan_universe()
    cand = df[df['pct'] >= 9.5].sort_values('pct', ascending=False)
    print(f'  scan_universe {len(df)} 只 → pct≥9.5 候选 {len(cand)} 只')
    if len(cand) == 0:
        print('  ⚠️ 当前无涨停候选 (盘后/弱势?) — 用全场最高涨幅股替验字段存在性')
        cand = df.sort_values('pct', ascending=False).head(3)
    else:
        cand = cand.head(3)
    fc_ok = lb_ok = fcb_seen = False
    for code in cand['code'].tolist():
        try:
            mi = safe_call(tq.get_more_info, stock_code=code, field_list=[]) or {}
        except Exception as e:  # noqa: BLE001
            print(f'  {code} more_info 失败: {e}')
            continue
        relevant = {k: mi.get(k) for k in
                    ['EverZTCount', 'LastStartZT', 'LastZTHzNum', 'FCAmo', 'FCb', 'ZAF']}
        print(f'  {code}: {relevant}')
        if mi.get('EverZTCount') not in (None, ''):
            lb_ok = True
        if mi.get('FCAmo') not in (None, ''):
            fc_ok = True
        if mi.get('FCb') is not None:
            fcb_seen = True
    print(f'  → EverZTCount有值: {"✅" if lb_ok else "❌"}  '
          f'FCAmo有值: {"✅" if fc_ok else "❌"}  '
          f'FCb(封成比)字段存在: {"✅" if fcb_seen else "❌ 维度5封成比降级(仅FCAmo封单额)"}')

    # ---------- P2.5 880001.SH 大A指数 Outside/Inside (维度2 全市场涨跌停家数 单点源) ----------
    print('\n[P2.5] 880001.SH 大A指数 Outside/Inside — 维度2 全A涨跌停家数(单点, 避免遍历重复)')
    try:
        s = safe_call(tq.get_market_snapshot, stock_code='880001.SH',
                      field_list=['Now', 'LastClose', 'Outside', 'Inside',
                                  'UpHome', 'DownHome', 'Amount']) or {}
        print(f'  Now={s.get("Now")} LastClose={s.get("LastClose")} '
              f'Outside(涨停家数)={s.get("Outside")} Inside(跌停家数)={s.get("Inside")} '
              f'UpHome={s.get("UpHome")} DownHome={s.get("DownHome")}')
        print(f'  → {"✅ 维度2单点源可用" if s.get("Outside") not in (None, "") else "⚠️ 880001 Outside 空"}')
    except Exception as e:  # noqa: BLE001
        print(f'  ❌ 失败: {e}')

    # ---------- P4 880863.SH 昨日涨停指数 (维度6 持续性) ----------
    print('\n[P4] 880863.SH 昨日涨停指数 snapshot — 维度6 持续性')
    try:
        s = safe_call(tq.get_market_snapshot, stock_code='880863.SH',
                      field_list=['Now', 'LastClose', 'Outside', 'Inside']) or {}
        now = _f(s.get('Now')); lc = _f(s.get('LastClose'))
        chg = (now - lc) / lc * 100 if lc > 0 else None
        print(f'  Now={s.get("Now")} LastClose={s.get("LastClose")} 涨幅={chg:.2f}%'
              if chg is not None else f'  数据: {s}')
        print(f'  Outside(昨日涨停股中今日涨停数)={s.get("Outside")} '
              f'Inside(反包跌停数)={s.get("Inside")}')
        print(f'  → {"✅ 维度6有源" if chg is not None else "⚠️ 880863 无数据, 维度6降级/弃"}')
    except Exception as e:  # noqa: BLE001
        print(f'  ❌ 失败: {e}')

    # ---------- P5 5 宽基 + Zangsu + 000688 指数vs个股 (维度7) ----------
    print('\n[P5] 5 宽基 snapshot + Zangsu + 000688.SH 指数/个股判别')
    for name, code in [('上证指数', '999999.SH'), ('深证成指', '399001.SZ'),
                       ('创业板指', '399006.SZ'), ('科创50?', '000688.SH'),
                       ('沪深300', '000300.SH')]:
        try:
            s = safe_call(tq.get_market_snapshot, stock_code=code,
                          field_list=['Now', 'LastClose', 'Open', 'Max', 'Min',
                                      'Amount', 'Zangsu', 'UpHome', 'DownHome']) or {}
            now = _f(s.get('Now')); lc = _f(s.get('LastClose'))
            chg = (now - lc) / lc * 100 if lc > 0 else None
            verdict = ''
            if code == '000688.SH' and now > 0:
                verdict = ' → 像指数(>100)' if now > 100 else ' → 像个股(股价数元, 非科创50指数!)'
            print(f'  {name}({code}): Now={s.get("Now")} 涨幅={chg:.2f}%'
                  f' Zangsu={s.get("Zangsu")} Amount={s.get("Amount")}{verdict}')
        except Exception as e:  # noqa: BLE001
            print(f'  {name}({code}) 失败: {e}')

finally:
    close()
print('\n' + '=' * 64)
print('探针结束 — P1/P3 是 BLOCK 门, 失败须对齐')
