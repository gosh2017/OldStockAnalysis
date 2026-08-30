# -*- coding: utf-8 -*-
"""Hikyuu 本地库统一访问层。

将项目的**历史/静态数据**查询从 AkShare 实时 HTTP 切到 Hikyuu 本地库
（一次性 pytdx 导入 → HDF5 kdata + SQLite stock.db）。实时数据仍走 AkShare，
不在本层。设计见 code_plan/hikyuu-data-migration.md。

核心职责：
  - 惰性单次 ``load_hikyuu(load_history_finance=True, load_weight=True,
    start_spot=False)``，进程级缓存（``_HKU``），失败返 None；各 fetcher 共享，
    避免重复 load（实测 ~0.5s）。
  - symbol→Stock 解析（含 **bj** 北交所前缀，补 _prefix_symbol 的漏项）。
  - KData→DataFrame（中文列名，对齐 _normalize_daily_df 契约）。
  - 财报 HistoryFinance 字段按名读取（避免 id off-by-one：DB id N 在数组下标 N-1）。
  - weight（分红 bonus / 总股本 total_count）、国债 zh_bond10 直读、PB 自算。

未安装 hikyuu / 本地库未导入（sm 空 / load_hikyuu 抛异常）时，各函数返回
None / 空 DataFrame，由调用方降级到 akshare fallback。--demo 路径完全不触
本层（main 的 if ctx.demo 分支用 generate_all_demo_data）。

单位口径（000001 平安银行 2025Q3 探针核实，见 scripts/probe_hikyuu_finance.py）：
  - HistoryFinance 金额字段（归母净利/净利润/OCF/capex/归母权益）均为「元」；
    总股本字段为「股」；每股净资产为「元/股」；ROE/资产负债率为 0-100 %。
  - weight.total_count 为「万股」→ 转股须 ×1e4（hku_total_shares 已做）。
  - weight.bonus 为「每 10 股红利」元（与 akshare「派息」列口径一致）。
  - zh_bond10.value 为「小数×1e6」（18140 ≈ 1.814%），→ 小数须 /1_000_000。
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from config import (HIKYUU_DB_PATH, HIKYUU_INDUSTRY_CATEGORY,
                     HKYUU_FINANCE_FIELDS)

# 进程级缓存：惰性 load 后的 hikyuu 模块（含 sm/Query/Datetime）。None = 未装/未导入。
_HKU = None
# 字段名 → 数组下标（get_history_finance_field_index 结果，0 基），跨股共享、惰性缓存。
_FIELD_INDEX: dict[str, int] = {}


def _hku():
    """惰性 load_hikyuu（finance+weight）。失败返 None。进程内只 load 一次。

    load_history_finance=True 才会把 HistoryFinance 载入（get_history_finance /
    FINANCE 指标可用）；load_weight=True 才有 get_weight 的 total_count/bonus。
    start_spot=False 跳过实时 spot（历史数据不需要，省一次联网）。
    """
    global _HKU
    if _HKU is not None:
        return _HKU
    try:
        import hikyuu as hku
    except Exception:
        return None
    try:
        hku.load_hikyuu(load_history_finance=True, load_weight=True,
                        start_spot=False)
    except Exception:
        # 本地库未导入（no such table）等 → 返 None，调用方降级 akshare。
        return None
    if not len(hku.sm):
        return None
    _HKU = hku
    return _HKU


def hku_sm():
    """返回 hku.sm（StockManager）；hkyuu 不可用 / 未导入 → None。

    供需直接迭代 sm 的调用方（批量筛选枚举）使用；单股路径请用 hku_stock。
    """
    hku = _hku()
    return hku.sm if hku is not None else None


def _field_index(hku, name: str) -> int | None:
    """字段名 → HistoryFinance values 数组的 0 基下标；未知字段返 None（缓存）。"""
    if name in _FIELD_INDEX:
        return _FIELD_INDEX[name]
    try:
        ix = hku.sm.get_history_finance_field_index(name)
    except Exception:
        ix = None
    if ix is not None:
        _FIELD_INDEX[name] = int(ix)
    return int(ix) if ix is not None else None


# ------------------------------------------------------------------
# symbol 解析
# ------------------------------------------------------------------
def hku_stock(symbol):
    """6 位代码 → hku.Stock（sh/sz/bj 前缀）。无效/不在库 → None 或 null stock。

    SH: 60/68；SZ: 00/30；BJ: 43/83/87/92（北交所，补 _prefix_symbol 漏项）。
    """
    hku = _hku()
    if hku is None:
        return None
    s = str(symbol).zfill(6)
    if s.startswith("6"):
        prefix = "sh"
    elif s.startswith(("0", "3")):
        prefix = "sz"
    elif s.startswith(("43", "83", "87", "92")):
        prefix = "bj"
    else:
        prefix = "sz"
    try:
        return hku.sm[f"{prefix}{s}"]
    except Exception:
        return None


def hku_index_stock(symbol):
    """指数代码 → Stock。000300→sh000300；000001/999999(上证综指)→sh000001；
    399001→sz399001。传入 6 位裸码按 0→sh、3→sz 加前缀（指数段：000xxx=沪、399xxx=深）。

    注意：999999 不是真实 Hikyuu 指数代码——上证综指在 Hikyuu 为 sh000001，
    故 999999 统一映射到 sh000001。
    """
    hku = _hku()
    if hku is None:
        return None
    s = str(symbol).zfill(6)
    if s == "999999":            # 上证综指别称 → sh000001
        s = "000001"
    prefix = "sh" if s.startswith("0") else ("sz" if s.startswith("3") else "sh")
    try:
        return hku.sm[f"{prefix}{s}"]
    except Exception:
        return None


# ------------------------------------------------------------------
# KData → DataFrame
# ------------------------------------------------------------------
def kdata_to_df(kdata) -> pd.DataFrame:
    """KData → DataFrame（中文列名 日期/开盘/收盘/最高/最低/成交量/成交额）。

    对齐 fetcher._normalize_daily_df 的列契约（下游 main/step1/step2/backtest/
    charts 消费）。空 KData → 空 df。
    """
    if kdata is None or not len(kdata):
        return pd.DataFrame()
    rows = []
    for r in kdata:
        rows.append({
            "日期": str(r.datetime),
            "开盘": float(r.open),
            "收盘": float(r.close),
            "最高": float(r.high),
            "最低": float(r.low),
            "成交量": float(r.volume),
            "成交额": float(r.amount),
        })
    df = pd.DataFrame(rows)
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    return df.sort_values("日期").reset_index(drop=True)


def fetch_kdata_df(symbol, start: str, end: str, *,
                   index: bool = False, recover: str = "FORWARD") -> pd.DataFrame:
    """日线 [start, end]（YYYYMMDD）→ DataFrame（中文列名）。

    index=True 用 hku_index_stock；recover 选 "NO_RECOVER"(指数)/"FORWARD"(个股前复权)。
    失败（hku 不可用 / 标的不在库 / KData 空）→ 空 DataFrame。
    """
    hku = _hku()
    if hku is None:
        return pd.DataFrame()
    st = hku_index_stock(symbol) if index else hku_stock(symbol)
    if st is None or not getattr(st, "valid", True):
        return pd.DataFrame()
    rt = (hku.Query.NO_RECOVER if recover == "NO_RECOVER"
          else getattr(hku.Query, "FORWARD", hku.Query.NO_RECOVER))
    try:
        q = hku.Query(hku.Datetime(f"{start}0000"), hku.Datetime(f"{end}0000"),
                       ktype=hku.Query.DAY, recover_type=rt)
        return kdata_to_df(st.get_kdata(q))
    except Exception:
        return pd.DataFrame()


# ------------------------------------------------------------------
# 股票枚举 / 收盘 / 总股本 / 行业（批量筛选与单股 industry_info 共用）
# ------------------------------------------------------------------
def hku_is_a_share(stock) -> bool:
    """粗筛沪深京 A 股：market∈{SH,SZ,BJ} 且 code 6 位、前缀属 A 股段。

    SH: 60(主板)/68(科创板)；SZ: 00(主板/中小板)/30(创业板)；BJ: 43/83/87/92。
    迭代 sm 的主枚举口径——预定义板块 get_block('A',…) 不全（'沪深' 漏创业板/科创板）。
    """
    try:
        mkt = str(stock.market).upper()
        code = str(stock.code)
    except Exception:
        return False
    if len(code) != 6 or not code.isdigit():
        return False
    if mkt == "SH":
        return code[:2] in ("60", "68")
    if mkt == "SZ":
        return code[:2] in ("00", "30")
    if mkt == "BJ":
        return code[:2] in ("43", "83", "87", "92")
    return False


def hku_last_close(stock) -> float | None:
    """stock 最近收盘价（元，Query(-1) 末根）；取不到返 None。"""
    hku = _hku()
    if hku is None or stock is None:
        return None
    try:
        kd = stock.get_kdata(hku.Query(-1))
        return float(kd[-1].close) if len(kd) else None
    except Exception:
        return None


def hku_total_count_wan(stock) -> float | None:
    """stock.get_weight()[-1].total_count（总股本，**万股**，原始）；取不到返 None。"""
    if stock is None:
        return None
    try:
        wl = stock.get_weight()
        if not len(wl):
            return None
        return float(wl[-1].total_count)
    except Exception:
        return None


def hku_total_shares(stock) -> float | None:
    """总股本（**股**）：weight.total_count(万股) × 1e4。取不到返 None。

    与 fetch_industry_info 契约一致（dcf._get_total_shares 直接 float() 当股用）。
    """
    tc = hku_total_count_wan(stock)
    return tc * 1e4 if (tc and tc > 0) else None


def hku_industry_name(stock) -> str | None:
    """stock 所属行业板块名（category=HIKYUU_INDUSTRY_CATEGORY 过滤，排除概念/地域/指数）。"""
    if stock is None:
        return None
    try:
        bl = stock.get_belong_to_block_list(category=HIKYUU_INDUSTRY_CATEGORY)
        if not bl:
            return None
        return str(bl[0].name)
    except Exception:
        return None


# ------------------------------------------------------------------
# 股票清单
# ------------------------------------------------------------------
def hku_stock_list() -> pd.DataFrame | None:
    """迭代 sm 按 hku_is_a_share 过滤 → [代码, 名称]（沪深京全 A 股 ~5400 只）。

    复用筛选已验证的枚举口径（迭代 sm 而非 get_block('A',…)，后者仅 3193 漏创业板/
    科创板）。6 位纯数字代码、去重。hku 不可用 / sm 空 / 枚举 0 只 → None。
    """
    hku = _hku()
    if hku is None:
        return None
    sm = hku.sm
    if not len(sm):
        return None
    rows = []
    for s in sm:
        if not hku_is_a_share(s):
            continue
        try:
            code = str(s.code)
            name = str(s.name)
        except Exception:
            continue
        if len(code) == 6 and code.isdigit():
            rows.append({"代码": code, "名称": name})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    return df.drop_duplicates(subset=["代码"]).reset_index(drop=True)


# ------------------------------------------------------------------
# 分红 / 权息
# ------------------------------------------------------------------
def hku_weight_dividends(stock) -> list[dict]:
    """get_weight() → 现金分红记录列表，每条 {公告日期, 送股, 转增, 派息, 配股}。

    ``bonus`` 为**每 10 股红利**（元/10股），与 akshare stock_history_dividend_detail
    「派息」列口径一致 → 下游 _normalize_div_per_share 对「派息」列 /10 正确。
    权息日 weight.datetime ≈ 除权日（晚于分红方案公告 ~1-2 月）；estimate_dividend_yield
    仅按年份匹配（公告年份 == year+1），权息日仍在次年 → 匹配成立。

    仅保留 bonus>0 的记录（送转/配股不计股息率）；空 weight 返空列表。
    """
    if stock is None:
        return []
    try:
        wl = stock.get_weight()
    except Exception:
        return []
    if not len(wl):
        return []
    rows = []
    for w in wl:
        try:
            bonus = float(w.bonus)
        except Exception:
            bonus = 0.0
        if not bonus or bonus <= 0:
            continue
        rows.append({
            "公告日期": str(w.datetime),
            "送股": float(w.count_as_gift),
            "转增": float(w.increasement),
            "派息": bonus,                       # 每 10 股红利（元/10股）
            "配股": 0.0,
        })
    if not rows:
        return rows
    df = pd.DataFrame(rows)
    df["公告日期"] = pd.to_datetime(df["公告日期"], errors="coerce")
    df = (df.dropna(subset=["公告日期"])
            .sort_values("公告日期")
            .reset_index(drop=True))
    return df.to_dict("records")


# ------------------------------------------------------------------
# 财报 HistoryFinance
# ------------------------------------------------------------------
def hku_finance_records(stock, field_names) -> pd.DataFrame:
    """get_history_finance() → 长格式 DataFrame，每行一个报告期。

    field_names: 需读取的 HistoryFinance 字段名列表（HKYUU_FINANCE_FIELDS 的值）。
    输出列：``报告期``(datetime) + 各 field_name 列（值原样，单位元/股/% 不缩放）。
    HistoryFinance 含季报（Q1/中报/Q3/年报）；下游 pick_annual_row 选年报优先。
    失败 / 无记录 / 字段全未知 → 空 DataFrame。
    """
    hku = _hku()
    if hku is None or stock is None:
        return pd.DataFrame()
    # 预解析各字段下标（未知字段记 None，跳过）
    idx_map: dict[str, int | None] = {}
    for nm in field_names:
        idx_map[nm] = _field_index(hku, nm)
    known = {nm: ix for nm, ix in idx_map.items() if ix is not None}
    if not known:
        return pd.DataFrame()
    try:
        hf = stock.get_history_finance()
    except Exception:
        return pd.DataFrame()
    if not hf:
        return pd.DataFrame()
    rows = []
    for rec in hf:
        try:
            report_date = str(rec[0])
            values = rec[2]
        except Exception:
            continue
        row = {"报告期": report_date}
        for nm, ix in known.items():
            try:
                row[nm] = float(values[ix])
            except Exception:
                row[nm] = None
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["报告期"] = pd.to_datetime(df["报告期"], errors="coerce")
    df = (df.dropna(subset=["报告期"])
            .sort_values("报告期")
            .reset_index(drop=True))
    return df


# ------------------------------------------------------------------
# 国债 zh_bond10（直读 SQLite，运行时零 HTTP）
# ------------------------------------------------------------------
def hku_bond_yield_df() -> pd.DataFrame | None:
    """直读 stock.db 的 zh_bond10 → [日期, 国债收益率]（小数，如 0.018），升序去重。

    ``value`` 为「小数×1e6」（末值 18140 ≈ 1.814%）→ /1_000_000 归一小数。
    1990-12-19 起（vs akshare bond_china_yield 2020 起）→ ERP 国债真实覆盖大增。
    表空 / DB 不存在 → None（调用方降级 akshare）。
    """
    if not HIKYUU_DB_PATH:
        return None
    try:
        conn = sqlite3.connect(HIKYUU_DB_PATH)
    except Exception:
        return None
    try:
        df = pd.read_sql("SELECT date, value FROM zh_bond10 ORDER BY date", conn)
    except Exception:
        return None
    finally:
        conn.close()
    if df is None or df.empty:
        return None
    out = pd.DataFrame()
    out["日期"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d",
                                 errors="coerce")
    out["国债收益率"] = pd.to_numeric(df["value"], errors="coerce") / 1_000_000.0
    out = (out.dropna()
             .sort_values("日期")
             .drop_duplicates(subset=["日期"])
             .reset_index(drop=True))
    return out if not out.empty else None


# ------------------------------------------------------------------
# PB 自算（每股净资产按交易日 PIT 对齐）
# ------------------------------------------------------------------
def hku_pb_series(symbol, start: str, end: str) -> pd.DataFrame:
    """逐交易日 PB = 收盘 / 每股净资产 → [日期, 市净率PB]。

    ``FINANCE("每股净资产")(kdata)`` 指标按 KData 日期对齐——天然 PIT（截至 d
    最近的报告期取值）。收盘取前复权（Query.FORWARD，与日线 fetch_daily_data 同基）。

    限定（文档标注）：前复权收盘与原始报告期每股净资产在送股/转增/现金分红节点
    存在口径错配（前复权价被下调、净资产为报告期原值）→ 个别标的 PB 历史序列
    可能有偏；当前 PB（末值）用最新真实收盘 / 最新净资产，无偏。PE_TTM 自算
    暂缓（用户决策），PE 列仍走 akshare。
    """
    hku = _hku()
    if hku is None:
        return pd.DataFrame()
    st = hku_stock(symbol)
    if st is None or not getattr(st, "valid", True):
        return pd.DataFrame()
    try:
        rt = getattr(hku.Query, "FORWARD", hku.Query.NO_RECOVER)
        q = hku.Query(hku.Datetime(f"{start}0000"), hku.Datetime(f"{end}0000"),
                       ktype=hku.Query.DAY, recover_type=rt)
        kd = st.get_kdata(q)
    except Exception:
        return pd.DataFrame()
    if not len(kd):
        return pd.DataFrame()
    try:
        nav_ind = hku.FINANCE(HKYUU_FINANCE_FIELDS["每股净资产"])(kd)
    except Exception:
        return pd.DataFrame()
    rows = []
    for i, r in enumerate(kd):
        try:
            nav = float(nav_ind[i])
        except Exception:
            nav = float("nan")
        close = float(r.close)
        pb = (close / nav) if (nav and nav > 0 and close > 0) else float("nan")
        rows.append({"日期": pd.to_datetime(str(r.datetime)), "市净率PB": pb})
    return pd.DataFrame(rows)
