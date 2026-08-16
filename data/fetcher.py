# -*- coding: utf-8 -*-
"""
数据获取层 -- 从 AkShare 获取各类 A 股数据。
每个 fetch_* 函数独立封装，包含重试和网络异常处理。

AkShare 版本: 实测 1.17.85（代码兼容至 1.18.x；接口漂移时各函数会降级）
已验证可用的函数:
  - stock_zh_a_hist(symbol, period, start_date, end_date, adjust, timeout)
  - stock_zh_a_daily(symbol, start_date, end_date, adjust)
  - stock_financial_abstract(symbol)
  - stock_financial_report_sina(stock, symbol)  -- symbol 是报表类型，stock 是代码
  - stock_zh_a_spot_em()
  - bond_china_yield(start_date, end_date)
  - stock_history_dividend_detail(symbol, indicator, date)
  - stock_dividend_cninfo(symbol)
"""
import difflib
import time

import akshare as ak
import pandas as pd

from config import (STOCK_CODE, START_DATE, END_DATE,
                    STOCK_LIST_TTL_HOURS, STOCK_INDICATOR_TTL_HOURS)
from utils import try_fetch, find_col_in, disk_cache, clear_cache


# stock_financial_report_sina 的 symbol 参数表示报表类型
_CASHFLOW_REPORT_TYPE = "现金流量表"

# 交易所 xls 接口偶发返回非 Excel 内容（错误页），try_fetch 将其视为确定性错误
# (ValueError) 不重试；此处补一层短重试 —— 此类失败是间歇性的，重试通常即恢复。
_EXCHANGE_FETCH_ATTEMPTS = 3
_EXCHANGE_RETRY_WAIT = 1.5


def _prefix_symbol(symbol: str) -> str:
    """给股票代码加交易所前缀: 000001 -> sz000001, 600000 -> sh600000"""
    s = str(symbol).zfill(6)
    if s.startswith("6"):
        return "sh" + s
    elif s.startswith("0") or s.startswith("3"):
        return "sz" + s
    return s


def fetch_daily_data(symbol: str = STOCK_CODE,
                     start_date: str | None = None,
                     end_date: str | None = None) -> pd.DataFrame:
    """
    获取日频交易数据（前复权）。
    字段：日期 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 成交额
    尝试多个 AkShare 接口作为 fallback。
    start_date / end_date 默认回退到 config.START_DATE / END_DATE（今天）。
    """
    start = start_date or START_DATE
    end = end_date or END_DATE
    print(f"\n[INFO] 正在获取 {symbol} 的日频交易数据（{start} ~ {end}）...")

    # 策略顺序：新浪优先（多数网络环境可用），东方财富次之
    strategies = [
        # stock_zh_a_daily: 新浪端点，symbol 需带交易所前缀，日期格式 YYYYMMDD
        ("stock_zh_a_daily", {
            "symbol": _prefix_symbol(symbol),
            "start_date": start, "end_date": end,
        }),
        # stock_zh_a_hist: 东方财富端点（部分网络环境不可达）
        ("stock_zh_a_hist", {
            "symbol": symbol, "period": "daily",
            "start_date": start, "end_date": end, "adjust": "qfq",
        }),
        ("stock_zh_a_hist", {
            "symbol": symbol, "period": "daily",
            "start_date": start, "end_date": end,
        }),
    ]

    df = None
    for func_name, kwargs in strategies:
        fn = getattr(ak, func_name, None)
        if fn is None:
            print(f"  [!] {func_name} 不可用，跳过")
            continue
        df = try_fetch(fn, **kwargs)
        if df is not None and not df.empty:
            print(f"  [OK] 使用 {func_name} 成功获取数据")
            break

    if df is None or df.empty:
        print("  [X] 未能获取日频数据，后续步骤将受限。")
        return pd.DataFrame()

    df = _normalize_daily_df(df)
    print(f"  [OK] 获取到 {len(df)} 条日频记录，最新收盘价: {df['收盘'].iloc[-1]:.2f}")
    return df


def _normalize_daily_df(df: pd.DataFrame) -> pd.DataFrame:
    """统一日频数据列名。"""
    col_map = {}
    for candidate in ["日期", "date", "Datetime"]:
        if candidate in df.columns:
            col_map[candidate] = "日期"
    for candidate in ["收盘", "close", "Close"]:
        if candidate in df.columns:
            col_map[candidate] = "收盘"
    for candidate in ["开盘", "open", "Open"]:
        if candidate in df.columns:
            col_map[candidate] = "开盘"
    for candidate in ["最高", "high", "High"]:
        if candidate in df.columns:
            col_map[candidate] = "最高"
    for candidate in ["最低", "low", "Low"]:
        if candidate in df.columns:
            col_map[candidate] = "最低"

    df = df.rename(columns=col_map)
    if "日期" in df.columns:
        df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").reset_index(drop=True)
    return df


def _total_shares_from_daily(daily_df: pd.DataFrame) -> float | None:
    """从日频数据的 outstanding_share 列获取总股本。
    新浪接口返回的 daily 数据含此字段，优先使用。"""
    for col in ["outstanding_share", "总股本", "total_share", "total_shares"]:
        if col in daily_df.columns:
            val = pd.to_numeric(daily_df[col], errors="coerce").dropna()
            if len(val) > 0:
                return float(val.iloc[-1])
    return None


def _extract_financial_indicator(df, indicator_patterns: list) -> pd.Series | None:
    """
    在财务摘要 DataFrame 中查找匹配指定模式的指标行，返回该行的 Series。
    df 格式: 列为 [选项, 指标, 日期1, 日期2, ...]，每行是一个指标。
    匹配策略: 按 patterns 顺序，找到"指标"列值包含任一 pattern 的行。
    返回 Series(index=列名, value=值) 或 None。
    """
    if "指标" not in df.columns:
        return None
    for pattern in indicator_patterns:
        mask = df["指标"].astype(str).str.contains(pattern, na=False)
        rows = df[mask]
        if not rows.empty:
            return rows.iloc[0]
    return None


def _transform_financial_abstract(df: pd.DataFrame) -> pd.DataFrame:
    """
    将 AkShare 返回的宽格式财务摘要（指标为行，日期为列）
    转换为长格式（每行一个日期，列为关键财务指标）。

    原始格式:
        选项    指标              20251231    20241231
        盈利能力  净资产收益率(ROE)    12.5        11.8
        ...

    目标格式:
        报告期    加权净资产收益率(%)  资产负债率(%)  ...
        2025-12-31    12.5           91.2        ...
    """
    indicator_map = {
        # 键名保持与 demo_data / 分析模块一致，值是匹配 AkShare 原始指标名的 pattern 列表
        "加权净资产收益率(%)": ["加权净资产收益率", "净资产收益率(ROE)", "净资产收益率"],
        "资产负债率(%)": ["资产负债率", "负债率"],
        "经营活动产生的现金流量净额": ["经营现金流量净额", "经营活动产生的现金流量净额", "经营活动现金流量净额", "经营活动现金流"],
        "归属于上市公司股东的净利润": ["归母净利润", "归属于上市公司股东的净利润", "归属母公司股东的净利润", "净利润"],
        "归属母公司股东权益": ["归属母公司股东权益", "所有者权益合计", "股东权益合计"],
        "总股本": ["总股本", "股份总数", "股本"],
    }

    # 获取所有日期列（第 3 列之后通常是日期字符串）
    date_cols = [c for c in df.columns if c not in ["选项", "指标"]
                 and (isinstance(c, str) and len(c) == 8 and c.isdigit())]

    if not date_cols:
        return pd.DataFrame()

    rows = []
    for date_str in date_cols:
        try:
            dt = pd.to_datetime(date_str, format="%Y%m%d")
        except (ValueError, TypeError):
            continue

        row = {"报告期": dt}

        for target_col, patterns in indicator_map.items():
            ind_series = _extract_financial_indicator(df, patterns)
            if ind_series is not None and date_str in ind_series.index:
                val = ind_series[date_str]
                try:
                    row[target_col] = float(val)
                except (ValueError, TypeError):
                    row[target_col] = None
            else:
                row[target_col] = None

        rows.append(row)

    result = pd.DataFrame(rows).sort_values("报告期").reset_index(drop=True)
    return result


def fetch_financial_abstract(symbol: str = STOCK_CODE) -> pd.DataFrame:
    """
    获取财务摘要数据（含 ROE / 资产负债率 / 净利润 / 经营现金流）。
    返回长格式 DataFrame，每行一个报告期。
    """
    print(f"\n[INFO] 正在获取 {symbol} 的财务摘要数据...")
    raw = try_fetch(ak.stock_financial_abstract, symbol=symbol)
    if raw is None or raw.empty:
        print("  [X] 未能获取财务摘要数据。")
        return pd.DataFrame()

    df = _transform_financial_abstract(raw)
    if df.empty:
        print("  [X] 财务摘要数据转换失败。")
        return pd.DataFrame()

    print(f"  [OK] 获取到 {len(df)} 条财务记录（长格式），字段: {list(df.columns)}")
    return df


def fetch_cashflow_detail(symbol: str = STOCK_CODE) -> pd.DataFrame:
    """
    获取现金流量表详细数据（用于计算自由现金流中的资本性支出）。
    使用 stock_financial_report_sina，传入现金流量表类型。
    """
    print(f"\n[INFO] 正在获取 {symbol} 的现金流量表数据...")

    df = try_fetch(
        ak.stock_financial_report_sina,
        stock=_prefix_symbol(symbol),
        symbol=_CASHFLOW_REPORT_TYPE,
    )
    if df is not None and not df.empty:
        print(f"  [OK] 现金流量表数据: {len(df)} 条")
        return df

    print("  [X] 未能获取现金流量表数据，将用经营现金流近似。")
    return pd.DataFrame()


def fetch_dividend(symbol: str = STOCK_CODE) -> pd.DataFrame:
    """获取历史分红数据（用于计算股息率）。"""
    print(f"\n[INFO] 正在获取 {symbol} 的分红数据...")

    # 尝试 stock_history_dividend_detail（需要指定 indicator）
    # indicator 通常为 "分红" 或 "送转"
    for indicator in ["分红", "送转"]:
        df = try_fetch(
            ak.stock_history_dividend_detail,
            symbol=symbol, indicator=indicator, date="",
        )
        if df is not None and not df.empty:
            print(f"  [OK] 分红数据（{indicator}）: {len(df)} 条")
            return df

    # 尝试 stock_dividend_cninfo
    df = try_fetch(ak.stock_dividend_cninfo, symbol=symbol)
    if df is not None and not df.empty:
        print(f"  [OK] 分红数据（cninfo）: {len(df)} 条")
        return df

    print("  [X] 未能获取分红数据，将用其他方式估算股息率。")
    return pd.DataFrame()


def fetch_market_overview() -> pd.DataFrame | None:
    """
    获取全市场 A 股实时数据（用于计算市盈率中位数）。
    策略 1: stock_zh_a_spot_em（东方财富，含市盈率-动态列）
    策略 2: stock_zh_a_spot（新浪，无市盈率列但可获取部分数据）
    """
    print(f"\n[INFO] 正在获取全市场 A 股实时数据...")

    strategies = [
        "stock_zh_a_spot_em",   # 东方财富，含 PE 列
        "stock_zh_a_spot",      # 新浪，无 PE 列
    ]

    for func_name in strategies:
        fn = getattr(ak, func_name, None)
        if fn is None:
            print(f"  [!] {func_name} 不可用，跳过")
            continue
        df = try_fetch(fn)
        if df is not None and not df.empty:
            has_pe = any("市盈率" in str(c) or "PE" in str(c).upper() for c in df.columns)
            print(f"  [OK] {func_name}: {len(df)} 只股票" + ("（含 PE）" if has_pe else "（无 PE）"))
            return df

    print("  [X] 未能获取全市场数据。")
    return None


def fetch_bond_yield_10y(end_date: str | None = None) -> float | None:
    """
    获取中国 10 年期国债收益率。
    使用 bond_china_yield，返回最新收益率（小数形式，如 0.023 表示 2.3%）。
    end_date 默认取 config.END_DATE（今天）。
    """
    print(f"\n[INFO] 正在获取 10 年期国债收益率...")

    end = end_date or END_DATE
    df = try_fetch(
        ak.bond_china_yield,
        start_date="20200101", end_date=end,
    )
    if df is not None and not df.empty:
        target_col = find_col_in(["10", "十年"], df)
        if target_col is not None:
            df_sorted = df.copy()
            date_col = df_sorted.columns[0]
            df_sorted[date_col] = pd.to_datetime(df_sorted[date_col], errors="coerce")
            df_sorted = df_sorted.sort_values(date_col)
            val = df_sorted[target_col].dropna().iloc[-1]
            val_pct = float(val) / 100 if val > 1 else float(val)
            print(f"  [OK] 10 年期国债收益率: {val_pct * 100:.2f}%（来源: bond_china_yield）")
            return val_pct

    print("  [!] 未能从 AkShare 获取实时国债收益率，使用近 5 年典型值 ≈ 2.3%")
    return 0.023


def fetch_stock_indicator(symbol: str = STOCK_CODE, force_refresh: bool = False) -> pd.DataFrame | None:
    """
    获取个股历史估值指标（PE / PB），用于计算该股自身的 PE/PB 历史分位数
    （真实分位，优于全市场 ERP 的模拟分位）。

    带本地磁盘缓存（STOCK_INDICATOR_TTL_HOURS，默认 12h，按 symbol 分文件）：
    缓存有效直接读盘，过期/缺失/force_refresh 才联网。

    优先 stock_a_indicator_lg（乐咕乐股，含 pe_ttm/pb/dv_ttm）；
    akshare 1.17.85 缺该函数时，回退到 stock_zh_valuation_baidu
    （百度股市通），分别拉 PE(TTM) 与 PB 的历史序列再按日期对齐。
    取不到则返回 None，调用方自动回退到仅用市场 ERP 估算情绪分位。
    """
    return disk_cache(
        f"indicator_{symbol}.pkl", STOCK_INDICATOR_TTL_HOURS,
        lambda: _fetch_stock_indicator_live(symbol), force_refresh=force_refresh,
    )


def _fetch_stock_indicator_live(symbol: str) -> pd.DataFrame | None:
    """实时获取个股历史估值指标（不走缓存）。"""
    print(f"\n[INFO] 正在获取 {symbol} 的历史估值指标（PE/PB）...")

    # 优先：乐咕乐股（一次拉全 pe/pb/股息率）
    fn_lg = getattr(ak, "stock_a_indicator_lg", None)
    if fn_lg is not None:
        df = try_fetch(fn_lg, symbol=symbol)
        if df is not None and not df.empty:
            out = _normalize_indicator_lg(df)
            if out is not None:
                return out

    # 回退：百度股市通（分指标拉 PE(TTM) / PB，按日期对齐）
    fn_bd = getattr(ak, "stock_zh_valuation_baidu", None)
    if fn_bd is not None:
        out = _fetch_baidu_valuation(fn_bd, symbol)
        if out is not None:
            return out

    print("  [X] 未能获取个股估值指标，将仅用市场 ERP 估算情绪分位。")
    return None


def _normalize_indicator_lg(df: pd.DataFrame) -> pd.DataFrame | None:
    """归一 stock_a_indicator_lg 返回的列名（优先 TTM 口径）。"""
    date_col = find_col_in(["trade_date", "日期", "date"], df)
    pe_col = find_col_in(["pe_ttm", "pe", "市盈率", "PE"], df)
    pb_col = find_col_in(["pb", "市净率", "PB"], df)
    out = pd.DataFrame()
    if date_col:
        out["日期"] = pd.to_datetime(df[date_col], errors="coerce")
    if pe_col:
        out["市盈率PE"] = pd.to_numeric(df[pe_col], errors="coerce")
    if pb_col:
        out["市净率PB"] = pd.to_numeric(df[pb_col], errors="coerce")
    keep = [c for c in ["市盈率PE", "市净率PB"] if c in out.columns]
    if not keep or "日期" not in out.columns:
        return None
    out = out.dropna(subset=keep).sort_values("日期").reset_index(drop=True)
    print(f"  [OK] 个股估值指标（乐咕乐股）: {len(out)} 条，"
          f"最新 PE={out['市盈率PE'].iloc[-1]:.2f}"
          + (f", PB={out['市净率PB'].iloc[-1]:.3f}" if "市净率PB" in out.columns else ""))
    return out


def _fetch_baidu_valuation(fn, symbol: str) -> pd.DataFrame | None:
    """用百度股市通分别拉 PE(TTM)/PB 历史序列，按日期对齐为宽表。"""
    out = pd.DataFrame()
    for col_name, indicator in [("市盈率PE", "市盈率(TTM)"), ("市净率PB", "市净率")]:
        df = try_fetch(fn, symbol=symbol, indicator=indicator, period="全部")
        if df is None or df.empty or "date" not in df.columns or "value" not in df.columns:
            print(f"  [X] 百度 {indicator} 获取失败，跳过")
            continue
        df = (df[["date", "value"]]
              .rename(columns={"date": "日期", "value": col_name}))
        df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
        df[col_name] = pd.to_numeric(df[col_name], errors="coerce")
        df = (df.dropna(subset=["日期", col_name])
                .sort_values("日期")
                .drop_duplicates(subset=["日期"]))
        out = df if out.empty else out.merge(df, on="日期", how="outer")
        print(f"  [OK] 百度 {indicator}: {len(df)} 期")

    keep = [c for c in ["市盈率PE", "市净率PB"] if c in out.columns]
    if out.empty or not keep or "日期" not in out.columns:
        return None
    out = out.sort_values("日期").reset_index(drop=True)
    pe_last = out["市盈率PE"].dropna() if "市盈率PE" in out.columns else pd.Series(dtype=float)
    pb_last = out["市净率PB"].dropna() if "市净率PB" in out.columns else pd.Series(dtype=float)
    pe_str = f"PE={pe_last.iloc[-1]:.2f}" if len(pe_last) > 0 else "PE=N/A"
    pb_str = f", PB={pb_last.iloc[-1]:.3f}" if len(pb_last) > 0 else ""
    print(f"  [OK] 个股估值指标（百度）: {len(out)} 期，最新 {pe_str}{pb_str}")
    return out


def fetch_stock_list(force_refresh: bool = False) -> pd.DataFrame | None:
    """
    获取全 A 股 代码-名称 映射，用于按名称模糊搜索定位股票。
    带本地磁盘缓存（STOCK_LIST_TTL_HOURS，默认 24h）：缓存有效直接读盘，
    过期/缺失/force_refresh 才联网拉取。失败结果（None）不落盘。

    数据源策略见 _fetch_stock_list_live：逐交易所独立获取并合并，
    单个交易所瞬时失败不影响其他；新浪全 A 股兜底补深市。
    """
    return disk_cache(
        "stock_list.pkl", STOCK_LIST_TTL_HOURS,
        _fetch_stock_list_live, force_refresh=force_refresh,
    )


def _fetch_stock_list_live() -> pd.DataFrame | None:
    """
    实时获取全 A 股 代码-名称 映射（不走缓存）。
    逐交易所独立获取并合并（深交所 / 上交所主板 / 科创板 / 北交所），
    单个交易所瞬时失败不影响其他 —— 避免 stock_info_a_code_name 聚合函数
    "全有或全无"（任一所返回非 Excel 即整体抛异常）的缺陷。
    最后以 stock_zh_a_spot_em（东财实时行情）兜底。返回 DataFrame[代码,名称]，不可用返回 None。
    """
    print("\n[INFO] 正在获取 A 股代码-名称列表（逐交易所独立获取）...")

    # (标签, akshare 函数名, 关键字参数)
    sources = [
        ("深交所",     "stock_info_sz_name_code", {"symbol": "A股列表"}),
        ("上交所主板", "stock_info_sh_name_code", {"symbol": "主板A股"}),
        ("科创板",     "stock_info_sh_name_code", {"symbol": "科创板"}),
        ("北交所",     "stock_info_bj_name_code", {}),
    ]

    frames: list[pd.DataFrame] = []
    for label, func_name, kwargs in sources:
        fn = getattr(ak, func_name, None)
        if fn is None:
            print(f"  [!] {func_name} 不可用，跳过 {label}")
            continue
        # 交易所 xls 接口间歇性返回非 Excel → try_fetch 不重试；此处补短重试
        out = None
        for attempt in range(_EXCHANGE_FETCH_ATTEMPTS):
            df = try_fetch(fn, **kwargs)
            out = _pick_code_name(df)
            if out is not None and not out.empty:
                break
            if attempt < _EXCHANGE_FETCH_ATTEMPTS - 1:
                time.sleep(_EXCHANGE_RETRY_WAIT)
        if out is not None and not out.empty:
            tag = f"（第 {attempt + 1} 次尝试）" if attempt > 0 else ""
            print(f"  [OK] {label}: {len(out)} 只{tag}")
            frames.append(out)
        else:
            print(f"  [X] {label}: 获取失败或无可识别列，跳过")

    merged = (pd.concat(frames, ignore_index=True)
              .drop_duplicates(subset=["代码"]).reset_index(drop=True)) if frames else pd.DataFrame()

    # 官网接口若总数偏低（通常深交所 xls 接口挂掉、缺深市 ~2900 只），
    # 用新浪全 A 股补齐 —— 新浪不依赖深交所官网，含深市股票。
    if len(merged) < 4000:
        print(f"  [INFO] 官网接口仅 {len(merged)} 只，尝试新浪全 A 股补齐...")
        fn_sina = getattr(ak, "stock_zh_a_spot", None)
        if fn_sina is not None:
            df = try_fetch(fn_sina)
            sina = _pick_code_name(df)
            if sina is not None and not sina.empty:
                print(f"  [OK] 新浪补齐: {len(sina)} 只")
                merged = (pd.concat([merged, sina], ignore_index=True)
                          .drop_duplicates(subset=["代码"]).reset_index(drop=True))

    if not merged.empty:
        print(f"[OK] 合计获取到 {len(merged)} 只股票")
        return merged

    # 最后兜底：东财实时行情（含代码/名称列）
    fn2 = getattr(ak, "stock_zh_a_spot_em", None)
    if fn2 is not None:
        df = try_fetch(fn2)
        out = _pick_code_name(df)
        if out is not None and not out.empty:
            print(f"  [OK] 获取到 {len(out)} 只股票（spot_em 兜底）")
            return out

    print("  [X] 未能获取股票列表，名称搜索不可用。")
    return None


def _pick_code_name(df: pd.DataFrame) -> pd.DataFrame | None:
    """从 DataFrame 中识别代码/名称列并归一为 [代码, 名称]。
    名称候选含"简称"以覆盖深交所"A股简称"、上交所/北交所"证券简称"；
    代码用正则提取 6 位数字，兼容新浪 spot 带交易所前缀的代码（sz000001 / bj920000）。"""
    if df is None or df.empty:
        return None
    code_col = find_col_in(["code", "代码", "symbol"], df)
    name_col = find_col_in(["name", "名称", "简称"], df)
    if not code_col or not name_col:
        return None
    return pd.DataFrame({
        "代码": df[code_col].astype(str).str.extract(r'(\d{6})', expand=False),
        "名称": df[name_col].astype(str).str.strip(),
    }).dropna().reset_index(drop=True)


def _match_score(q: str, code: str, name: str) -> float:
    """单只股票与查询串的匹配分数（0 表示不匹配）。"""
    if q == code:
        return 100.0
    if q == name:
        return 98.0
    if code.startswith(q):
        return 92.0
    if name.startswith(q):
        return 88.0
    if q in name:
        return 82.0
    if q in code:
        return 80.0
    ratio = difflib.SequenceMatcher(None, q, name).ratio()
    if ratio > 0.45:                       # 模糊相似（错字/简写）
        return ratio * 70.0
    return 0.0


def search_stocks(query: str, stock_list: pd.DataFrame | None,
                  limit: int = 10) -> list:
    """
    模糊搜索 A 股：按代码或名称匹配，返回 [(代码, 名称, 分数)] 按分数降序。

    支持输入代码（如 000001）、完整名称（平安银行）、名称片段（平安/茅台）、
    含错字近似（贵州茅苔→贵州茅台）。代码优先直接命中，名称片段/模糊兜底。
    """
    if stock_list is None or stock_list.empty or not query:
        return []
    q = str(query).strip()
    if not q:
        return []

    scored = []
    codes = stock_list["代码"].astype(str).tolist()
    names = stock_list["名称"].astype(str).tolist()
    for i, code in enumerate(codes):
        s = _match_score(q, code, names[i])
        if s > 0:
            scored.append((code, names[i], s))
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:limit]