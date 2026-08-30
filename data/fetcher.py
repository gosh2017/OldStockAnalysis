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
                    STOCK_LIST_TTL_HOURS, STOCK_INDICATOR_TTL_HOURS,
                    MARKET_PE_TTL_HOURS, BOND_HISTORY_TTL_HOURS, MARKET_PE_BOARD,
                    INDUSTRY_INFO_TTL_HOURS, SW_TO_BUCKET,
                    HIKYUU_INDUSTRY_TO_BUCKET, HIKYUU_INDUSTRY_CATEGORY,
                    INDUSTRY_KEYWORDS, STOCK_SCREENING_TTL_HOURS,
                    HKYUU_FINANCE_FIELDS)
from utils import try_fetch, find_col_in, disk_cache, clear_cache
from data.hikyuu_backend import (
    fetch_kdata_df as _hku_kdata_df,        # hku 日线 → 中文列名 DataFrame
    hku_sm, hku_stock, hku_is_a_share, hku_last_close,
    hku_total_count_wan, hku_total_shares, hku_industry_name,
    hku_stock_list, hku_weight_dividends, hku_finance_records,
    hku_bond_yield_df, hku_pb_series,
)


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

    优先 Hikyuu 本地库（Query.FORWARD 前复权，对齐 akshare qfq；查询零 HTTP）；
    hku 未装/未导入/标的不在库/退市新股 → 降级 AkShare（新浪/东财 fallback）。
    start_date / end_date 默认回退到 config.START_DATE / END_DATE（今天）。

    注意：Hikyuu FORWARD 与 akshare qfq 同为前复权但实现略异，日线数值可能有
    厘级差异（影响回测净值/前向收益的精确复现）；严格复现可由 fallback 走 akshare。
    """
    start = start_date or START_DATE
    end = end_date or END_DATE
    print(f"\n[INFO] 正在获取 {symbol} 的日频交易数据（{start} ~ {end}）...")

    df = _hku_kdata_df(symbol, start, end, index=False, recover="FORWARD")
    if df is not None and not df.empty:
        print(f"  [OK] 使用 Hikyuu 本地库获取日频数据（{len(df)} 期）")
        df = _normalize_daily_df(df)
        print(f"  [OK] 最新收盘价: {df['收盘'].iloc[-1]:.2f}")
        return df

    print("  [INFO] Hikyuu 不可用，降级 AkShare（新浪/东财）…")
    return _fetch_daily_data_ak(symbol, start, end)


def _fetch_daily_data_ak(symbol: str, start: str, end: str) -> pd.DataFrame:
    """AkShare 日频 fallback：新浪优先（多数网络环境可用），东方财富次之。"""
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
    """从日频数据的总股本列获取总股本。
    优先明确命名的总股本列；outstanding_share（新浪口径常为流通股本）仅末位兜底，
    避免对含限售股的标的低估总股本。"""
    for col in ["总股本", "total_share", "total_shares", "outstanding_share"]:
        if col in daily_df.columns:
            val = pd.to_numeric(daily_df[col], errors="coerce").dropna()
            if len(val) > 0:
                return float(val.iloc[-1])
    return None


def fetch_benchmark_daily(symbol: str = "000300",
                          start_date: str | None = None,
                          end_date: str | None = None) -> pd.DataFrame:
    """
    获取基准指数日频数据（默认沪深 300），用于回测基准曲线。

    返回与 fetch_daily_data 同构的 [日期, 收盘] 表（仅保留两列，指数无 OHLCV
    需求）。优先 Hikyuu 本地库（指数不复权 NO_RECOVER，代码 000300→sh000300、
    999999/000001→sh000001、399001→sz399001）；hku 不可用 → 降级 AkShare
    （stock_zh_index_daily 新浪 / index_zh_a_hist 东财）。失败返回空 DataFrame。

    离线回测走 data.demo_data.generate_benchmark_daily（确定性模拟基准）。
    """
    start = start_date or START_DATE
    end = end_date or END_DATE
    print(f"\n[INFO] 正在获取基准指数 {symbol} 日线（{start} ~ {end}）...")

    df = _hku_kdata_df(symbol, start, end, index=True, recover="NO_RECOVER")
    if df is not None and not df.empty:
        df = _normalize_daily_df(df)
        keep = [c for c in ["日期", "收盘"] if c in df.columns]
        df = df[keep].sort_values("日期").reset_index(drop=True) if keep else df
        if not df.empty:
            print(f"  [OK] 使用 Hikyuu 本地库获取基准日线（{len(df)} 期，"
                  f"最新: {df['收盘'].iloc[-1]:.2f}）")
        return df

    print("  [INFO] Hikyuu 不可用，降级 AkShare（新浪/东财指数）…")
    return _fetch_benchmark_daily_ak(symbol, start, end)


def _fetch_benchmark_daily_ak(symbol: str, start: str, end: str) -> pd.DataFrame:
    """AkShare 基准指数 fallback：新浪优先，东财次之。"""
    # stock_zh_index_daily 需带交易所前缀：6 开头 → sh，0/3 开头 → sz
    prefixed = _prefix_symbol(symbol) if not str(symbol).startswith(("sh", "sz")) else symbol

    strategies = [
        ("stock_zh_index_daily", {"symbol": prefixed}),
        ("index_zh_a_hist", {"symbol": symbol, "period": "daily",
                             "start_date": start, "end_date": end}),
    ]

    df = None
    for func_name, kwargs in strategies:
        fn = getattr(ak, func_name, None)
        if fn is None:
            print(f"  [!] {func_name} 不可用，跳过")
            continue
        df = try_fetch(fn, **kwargs)
        if df is not None and not df.empty:
            print(f"  [OK] 使用 {func_name} 成功获取基准数据")
            break

    if df is None or df.empty:
        print("  [X] 未能获取基准指数日线，回测基准曲线将缺失。")
        return pd.DataFrame()

    df = _normalize_daily_df(df)
    # 仅保留日期/收盘（指数无需 OHLCV，且部分接口列名不一）
    keep = [c for c in ["日期", "收盘"] if c in df.columns]
    df = df[keep].sort_values("日期").reset_index(drop=True) if keep else df
    if not df.empty:
        print(f"  [OK] 基准日线 {len(df)} 期，最新: {df['收盘'].iloc[-1]:.2f}")
    return df


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

    优先 Hikyuu HistoryFinance（三表 581 字段，本地零 HTTP）；hku 不可用 →
    降级 AkShare stock_financial_abstract（经 _transform_financial_abstract 归一）。
    单位：OCF/净利/权益=元、ROE/资产负债率=%(0-100)、总股本=股（探针核实，无需缩放）。
    """
    print(f"\n[INFO] 正在获取 {symbol} 的财务摘要数据...")
    df = _fetch_financial_abstract_hku(symbol)
    if df is not None and not df.empty:
        print(f"  [OK] 使用 Hikyuu 本地库获取财务摘要（{len(df)} 期），"
              f"字段: {list(df.columns)}")
        return df

    print("  [INFO] Hikyuu 不可用，降级 AkShare…")
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


def _fetch_financial_abstract_hku(symbol: str) -> pd.DataFrame:
    """Hikyuu HistoryFinance → 财务摘要长格式（契约列名，元/股/% 不缩放）。"""
    st = hku_stock(symbol)
    if st is None or not getattr(st, "valid", True):
        return pd.DataFrame()
    F = HKYUU_FINANCE_FIELDS
    fnames = [F["归母净利润"], F["经营现金流"], F["归母权益"],
              F["总股本"], F["ROE_加权"], F["资产负债率"]]
    df = hku_finance_records(st, fnames)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        F["归母净利润"]: "归属于上市公司股东的净利润",
        F["经营现金流"]: "经营活动产生的现金流量净额",
        F["归母权益"]:   "归属母公司股东权益",
        F["总股本"]:     "总股本",
        F["ROE_加权"]:   "加权净资产收益率(%)",
        F["资产负债率"]: "资产负债率(%)",
    })
    cols = ["报告期", "加权净资产收益率(%)", "资产负债率(%)",
            "经营活动产生的现金流量净额", "归属于上市公司股东的净利润",
            "归属母公司股东权益", "总股本"]
    return df[[c for c in cols if c in df.columns]]


def fetch_cashflow_detail(symbol: str = STOCK_CODE) -> pd.DataFrame:
    """
    获取现金流量表详细数据（用于计算自由现金流中的资本性支出 capex 与 D&A）。

    优先 Hikyuu HistoryFinance（capex id114 / 折旧 id136 + 摊销 id137 合并为
    「折旧与摊销」列，元）；hku 不可用 → 降级 AkShare stock_financial_report_sina
    （现金流量表）。输出列名复用 akshare 原名 → 下游 step2 find_col_in 零改。
    """
    print(f"\n[INFO] 正在获取 {symbol} 的现金流量表数据...")
    df = _fetch_cashflow_detail_hku(symbol)
    if df is not None and not df.empty:
        print(f"  [OK] 使用 Hikyuu 本地库获取现金流量表（{len(df)} 期）")
        return df

    print("  [INFO] Hikyuu 不可用，降级 AkShare（sina 现金流量表）…")
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


def _fetch_cashflow_detail_hku(symbol: str) -> pd.DataFrame:
    """Hikyuu HistoryFinance → [报告期, 购建固定资产…, 折旧与摊销]（元）。

    折旧与摊销 = |折旧| + |摊销|（合并列，对齐 step2 find_col_in(['折旧与摊销',…])）；
    capex 取原值（step2 用 abs() 兼容负号口径）。
    """
    st = hku_stock(symbol)
    if st is None or not getattr(st, "valid", True):
        return pd.DataFrame()
    F = HKYUU_FINANCE_FIELDS
    df = hku_finance_records(st, [F["capex"], F["折旧"], F["摊销"]])
    if df is None or df.empty:
        return pd.DataFrame()
    out = pd.DataFrame()
    out["报告期"] = df["报告期"]
    out["购建固定资产、无形资产和其他长期资产支付的现金"] = pd.to_numeric(
        df[F["capex"]], errors="coerce")
    dep = pd.to_numeric(df[F["折旧"]], errors="coerce").fillna(0.0).abs()
    amo = pd.to_numeric(df[F["摊销"]], errors="coerce").fillna(0.0).abs()
    out["折旧与摊销"] = (dep + amo).replace(0.0, pd.NA)
    return out.dropna(subset=["报告期"]).reset_index(drop=True)


def fetch_dividend(symbol: str = STOCK_CODE) -> pd.DataFrame:
    """获取历史分红数据（用于计算股息率）。

    优先 Hikyuu get_weight（bonus=每10股红利，与 akshare stock_history_dividend_detail
    「派息」列口径一致 → 下游 _normalize_div_per_share 对「派息」列 /10 正确）；
    hku 不可用 → 降级 AkShare（stock_history_dividend_detail / stock_dividend_cninfo）。

    语义：hku weight.datetime 为权息日≈除权日（晚于分红方案公告 ~1-2 月）；
    estimate_dividend_yield 仅按年份匹配（公告年份==year+1），权息日仍在次年 →
    匹配成立（股息率估算本就近似，可接受）。
    """
    print(f"\n[INFO] 正在获取 {symbol} 的分红数据...")
    df = _fetch_dividend_hku(symbol)
    if df is not None and not df.empty:
        print(f"  [OK] 使用 Hikyuu 本地库获取分红数据（{len(df)} 期）")
        return df

    print("  [INFO] Hikyuu 不可用，降级 AkShare…")
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


def _fetch_dividend_hku(symbol: str) -> pd.DataFrame:
    """Hikyuu get_weight → [公告日期, 送股, 转增, 派息, 配股]（派息=每10股元）。"""
    st = hku_stock(symbol)
    if st is None or not getattr(st, "valid", True):
        return pd.DataFrame()
    rows = hku_weight_dividends(st)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


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


def fetch_bond_yield_history(end_date: str | None = None,
                              force_refresh: bool = False) -> pd.DataFrame | None:
    """
    获取 10 年期国债收益率历史序列，用于市场情绪 ERP 的历史分位计算
    ——与市场历史 PE 对齐后得到真实历史 ERP 序列，而非合成 mock 分布。
    返回 [日期, 国债收益率]（小数制，如 0.023 表 2.3%），按日期升序；失败 None。

    优先 Hikyuu 本地库 zh_bond10 表（1990-12-19 起，远早于 akshare 2020 起 →
    ERP 国债真实覆盖大增；value/1e6 归一小数）；hku 不可用/表空 → 降级 AkShare
    bond_china_yield（2020 至今）。带本地磁盘缓存（BOND_HISTORY_TTL_HOURS）。
    限定：本地库数据新鲜度 = 最近一次导入（zh_bond10 一次性依赖 akshare 导入）；
    需最新值时重跑 scripts/run_hikyuu_import.py 或由 fallback 走 akshare。
    """
    return disk_cache(
        "bond_yield_history.pkl", BOND_HISTORY_TTL_HOURS,
        lambda: _fetch_bond_yield_history(end_date),
        force_refresh=force_refresh,
    )


def _fetch_bond_yield_history(end_date: str | None) -> pd.DataFrame | None:
    """取国债历史序列（不走缓存）：Hikyuu zh_bond10 优先，AkShare fallback。"""
    # 1) Hikyuu 本地库 zh_bond10（直读 SQLite，零 HTTP）
    df = hku_bond_yield_df()
    if df is not None and not df.empty:
        if end_date:
            cutoff = pd.to_datetime(end_date, format="%Y%m%d", errors="coerce")
            if cutoff is not pd.NaT:
                df = df[df["日期"] <= cutoff].reset_index(drop=True)
        if not df.empty:
            print(f"  [OK] 使用 Hikyuu zh_bond10 国债历史（{len(df)} 期，"
                  f"最新 {df['国债收益率'].iloc[-1] * 100:.2f}%）")
            return df
    # 2) 降级 AkShare
    print("  [INFO] Hikyuu 不可用，降级 AkShare（bond_china_yield）…")
    return _fetch_bond_yield_history_live(end_date)


def _fetch_bond_yield_history_live(end_date: str | None) -> pd.DataFrame | None:
    """AkShare 10 年期国债收益率历史序列 fallback（不走缓存）。"""
    print("\n[INFO] 正在获取 10 年期国债收益率历史序列...")
    end = end_date or END_DATE
    df = try_fetch(ak.bond_china_yield, start_date="20200101", end_date=end)
    if df is None or df.empty:
        return None
    target_col = find_col_in(["10", "十年"], df)
    if target_col is None:
        return None
    date_col = df.columns[0]
    out = pd.DataFrame()
    out["日期"] = pd.to_datetime(df[date_col], errors="coerce")
    raw = pd.to_numeric(df[target_col], errors="coerce")
    # 列值可能是 2.3（百分比）或 0.023（小数），统一为小数
    out["国债收益率"] = raw.where(raw <= 1, raw / 100)
    out = (out.dropna()
             .sort_values("日期")
             .drop_duplicates(subset=["日期"])
             .reset_index(drop=True))
    if out.empty:
        return None
    print(f"  [OK] 国债收益率历史: {len(out)} 期，"
          f"最新 {out['国债收益率'].iloc[-1] * 100:.2f}%")
    return out


def fetch_bond_yield_10y(end_date: str | None = None) -> float | None:
    """
    获取最新 10 年期国债收益率（小数形式，如 0.023 表示 2.3%）。
    复用 fetch_bond_yield_history 末值（命中磁盘缓存，避免重复拉取）。
    end_date 默认取 config.END_DATE（今天）。
    """
    hist = fetch_bond_yield_history(end_date=end_date)
    if hist is not None and not hist.empty:
        val = float(hist["国债收益率"].iloc[-1])
        print(f"  [OK] 10 年期国债收益率: {val * 100:.2f}%（来源: bond_china_yield）")
        return val
    print("  [!] 未能从 AkShare 获取实时国债收益率，使用近 5 年典型值 ≈ 2.3%")
    return 0.023


def fetch_market_pe_history(market: str = MARKET_PE_BOARD,
                            force_refresh: bool = False) -> pd.DataFrame | None:
    """
    获取市场历史市盈率序列（乐咕乐股），用于市场情绪 ERP 历史分位。

    一次调用即得"当前市场 PE"（末值）+"历史序列"，取代不可靠的 spot 快照
    （东财 spot_em 持续断连、新浪 spot 无 PE 列）与合成 mock 分布。

    带本地磁盘缓存（MARKET_PE_TTL_HOURS）。优先 stock_market_pe_lg（主板），
    回退 stock_index_pe_lg（沪深300 指数）。返回 [日期, 市盈率]（pe>0，
    按日期升序）；失败 None（调用方回退合成分布）。
    """
    return disk_cache(
        "market_pe_history.pkl", MARKET_PE_TTL_HOURS,
        lambda: _fetch_market_pe_history_live(market),
        force_refresh=force_refresh,
    )


def _fetch_market_pe_history_live(market: str) -> pd.DataFrame | None:
    """实时获取市场历史 PE 序列（不走缓存）。"""
    print(f"\n[INFO] 正在获取市场历史 PE（乐咕·{market}）...")
    fn = getattr(ak, "stock_market_pe_lg", None)
    if fn is not None:
        out = _normalize_market_pe(try_fetch(fn, symbol=market))
        if out is not None:
            return out
    # 回退：沪深300 指数 PE（同源，覆盖面广）
    fn_idx = getattr(ak, "stock_index_pe_lg", None)
    if fn_idx is not None:
        print(f"  [INFO] 主板 PE 不可用，回退沪深300 指数 PE...")
        out = _normalize_market_pe(try_fetch(fn_idx, symbol="沪深300"))
        if out is not None:
            return out
    print("  [X] 未能获取市场历史 PE，情绪分位将回退合成分布。")
    return None


def _normalize_market_pe(df: pd.DataFrame) -> pd.DataFrame | None:
    """把乐咕返回归一为 [日期, 市盈率]（akshare 1.17.85 源码确认列为 [date, close, pe]）。"""
    if df is None or df.empty:
        return None
    date_col = find_col_in(["date", "日期", "trade_date"], df)
    pe_col = find_col_in(["pe", "市盈率", "PE"], df)
    if not date_col or not pe_col:
        return None
    out = pd.DataFrame()
    out["日期"] = pd.to_datetime(df[date_col], errors="coerce")
    out["市盈率"] = pd.to_numeric(df[pe_col], errors="coerce")
    out = out.dropna()
    out = out[(out["市盈率"] > 0) & (out["市盈率"] < 500)]
    if out.empty:
        return None
    out = (out.sort_values("日期")
             .drop_duplicates(subset=["日期"])
             .reset_index(drop=True))
    print(f"  [OK] 市场历史 PE: {len(out)} 期，最新 {out['市盈率'].iloc[-1]:.2f}")
    return out


def fetch_stock_indicator(symbol: str = STOCK_CODE, force_refresh: bool = False) -> pd.DataFrame | None:
    """
    获取个股历史估值指标（PE / PB），用于计算该股自身的 PE/PB 历史分位数
    （真实分位，优于全市场 ERP 的模拟分位）。

    带本地磁盘缓存（STOCK_INDICATOR_TTL_HOURS，默认 12h，按 symbol 分文件）：
    缓存有效直接读盘，过期/缺失/force_refresh 才联网。

    **PB 优先 Hikyuu 自算**（FINANCE 每股净资产，本地零 HTTP）；**PE 仍走 AkShare**
    （PE_TTM 自算暂缓——用户决策）。合并口径：PE 取 ak、PB 优先 hku（hku 缺失补
    ak 的 PB）。hku 不可用→ak 全量；ak 不可用→仅 hku PB（PE 缺失，step3 的
    `pe>0` 过滤与 step2 PE 锚回退已处理 NaN）。

    PE 来源：优先 stock_a_indicator_lg（乐咕乐股），缺函数时回退
    stock_zh_valuation_baidu（百度股市通）。
    """
    return disk_cache(
        f"indicator_{symbol}.pkl", STOCK_INDICATOR_TTL_HOURS,
        lambda: _fetch_stock_indicator_live(symbol), force_refresh=force_refresh,
    )


def _fetch_stock_indicator_live(symbol: str) -> pd.DataFrame | None:
    """个股历史估值指标（不走缓存）：PB 走 Hikyuu 自算，PE 走 AkShare 合并。"""
    print(f"\n[INFO] 正在获取 {symbol} 的历史估值指标（PE/PB）...")

    ak_out = _fetch_indicator_ak(symbol)          # [日期, 市盈率PE, 市净率PB]（akshare）
    hku_pb = hku_pb_series(symbol, START_DATE, END_DATE)   # [日期, 市净率PB]（hikyuu 自算）

    if ak_out is None:
        if hku_pb is not None and not hku_pb.empty:
            print(f"  [OK] 仅 Hikyuu 自算 PB（{len(hku_pb)} 期；AkShare 不可用，PE 缺失）")
            return hku_pb
        print("  [X] 未能获取个股估值指标，将仅用市场 ERP 估算情绪分位。")
        return None

    # PB 优先 hku（按日期覆盖 ak 的 PB），PE 取 ak
    if hku_pb is not None and not hku_pb.empty and "市净率PB" in ak_out.columns:
        ak_out = ak_out.copy()
        hku_pb = hku_pb.copy()
        ak_out["市净率PB"] = ak_out["日期"].map(
            dict(zip(hku_pb["日期"], hku_pb["市净率PB"]))
        ).where(lambda s: s.notna(), ak_out["市净率PB"])
        n_hku = int(pd.to_numeric(ak_out["市净率PB"], errors="coerce").notna().sum())
        print(f"  [OK] PB 由 Hikyuu 自算（{n_hku} 期），PE 由 AkShare 提供")
    else:
        print("  [INFO] Hikyuu PB 不可用，PB/PE 均用 AkShare")

    pe_last = ak_out["市盈率PE"].dropna() if "市盈率PE" in ak_out.columns else pd.Series(dtype=float)
    pb_last = ak_out["市净率PB"].dropna() if "市净率PB" in ak_out.columns else pd.Series(dtype=float)
    pe_str = f"PE={pe_last.iloc[-1]:.2f}" if len(pe_last) > 0 else "PE=N/A"
    pb_str = f", PB={pb_last.iloc[-1]:.3f}" if len(pb_last) > 0 else ""
    print(f"  [OK] 个股估值指标: {len(ak_out)} 期，最新 {pe_str}{pb_str}")
    return ak_out


def _fetch_indicator_ak(symbol: str) -> pd.DataFrame | None:
    """AkShare 个股历史估值指标 fallback：乐咕乐股优先，百度股市通次之。"""
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


def map_to_industry_bucket(industry: str | None) -> str:
    """行业名 → 6 桶之一；None / 未知 → "其他"。纯函数，无副作用。

    两条路径共用：单股 fetch_industry_info（申万一级行业名，仍走 akshare）
    与批量筛选 _fetch_stock_screening_data_hikyuu（Hikyuu 板块名）。故先查
    申万表 SW_TO_BUCKET（单股路径零回归），未命中再查 Hikyuu 次表
    HIKYUU_INDUSTRY_TO_BUCKET，再未命中走有序关键词兜底
    INDUSTRY_KEYWORDS（子串匹配，覆盖东财 ~496 细粒度名的长尾），仍无命中
    → "其他"。关键词顺序是关键（见 config 注释），精确表优先 → 零回归。
    """
    if not industry:
        return "其他"
    name = str(industry).strip()
    if not name:
        return "其他"
    if name in SW_TO_BUCKET:
        return SW_TO_BUCKET[name]
    if name in HIKYUU_INDUSTRY_TO_BUCKET:
        return HIKYUU_INDUSTRY_TO_BUCKET[name]
    # 关键词兜底：按 INDUSTRY_KEYWORDS 顺序子串匹配，命中即返回对应桶
    for kw, bucket in INDUSTRY_KEYWORDS:
        if kw in name:
            return bucket
    return "其他"


def fetch_industry_info(symbol: str = STOCK_CODE, force_refresh: bool = False) -> dict:
    """
    获取个股的行业归属与总股本，用于行业分桶（INDUSTRY_PROFILES 差异化参数）。

    带本地磁盘缓存（INDUSTRY_INFO_TTL_HOURS，默认 720h≈月级，按 symbol 分文件）：
    缓存有效直接读盘，过期/缺失/force_refresh 才联网。失败结果不落盘。

    数据源：ak.stock_individual_info_em(symbol)，返回 item/value 两列，
    取 "行业" 与 "总股本" 两项，并经 map_to_industry_bucket 映射到桶；
    总股本单位为"股"（与 dcf_valuation 的 total_shares 口径一致）。
    失败（接口漂移 / 网络异常 / 列结构异常）返回 {"industry": None, "bucket": "其他",
    "total_shares": None, "source": "fallback"} —— 不抛、不缓存失败态，
    调用方（dcf_valuation / scoring）对 None 均有兜底。
    """
    return disk_cache(
        f"industry_{symbol}.pkl", INDUSTRY_INFO_TTL_HOURS,
        lambda: _fetch_industry_info_live(symbol), force_refresh=force_refresh,
    )


def _fetch_industry_info_live(symbol: str) -> dict:
    """获取行业归属与总股本（不走缓存）：Hikyuu 优先，AkShare fallback。

    三级降级：
      1) Hikyuu 行业板块已导入（需 python scripts/import_hikyuu_industry_blocks.py）
         → hku_industry_name + hku_total_shares，source='hku'（零 HTTP）。
      2) Hikyuu 行业缺失（板块未导入 / 该股不在板块）→ 降级 AkShare
         stock_individual_info_em（取行业 + 总股本），source='em'。
      3) AkShare 也不可用 → 保留 Hikyuu 总股本（weight，可靠）+ 行业 None（桶「其他」），
         source='hku'。总股本单位为「股」。
    """
    print(f"\n[INFO] 正在获取 {symbol} 的行业归属与总股本...")
    st = hku_stock(symbol)
    valid = st is not None and getattr(st, "valid", True)
    hku_ind = hku_industry_name(st) if valid else None
    hku_ts = hku_total_shares(st) if valid else None

    if hku_ind is not None:
        ts_str = f"{hku_ts:.2e}" if hku_ts else "未知"
        print(f"  [OK] Hikyuu 行业={hku_ind} → 桶={map_to_industry_bucket(hku_ind)}，"
              f"总股本={ts_str}")
        return {"industry": hku_ind, "bucket": map_to_industry_bucket(hku_ind),
                "total_shares": hku_ts, "source": "hku"}

    print("  [INFO] Hikyuu 行业板块不可用（未导入），降级 AkShare（stock_individual_info_em）…")
    ak_out = _fetch_industry_info_live_ak(symbol)
    if ak_out.get("industry") is not None:
        return ak_out

    # AkShare 也不可用 → 保留 Hikyuu 总股本（行业缺失 → 桶「其他」）
    if hku_ts is not None:
        print(f"  [!] AkShare 行业也不可用，保留 Hikyuu 总股本={hku_ts:.2e}（行业缺失）")
        return {"industry": None, "bucket": "其他", "total_shares": hku_ts,
                "source": "hku"}
    return ak_out


def _fetch_industry_info_live_ak(symbol: str) -> dict:
    """AkShare 行业归属与总股本 fallback（stock_individual_info_em）。"""
    fallback = {"industry": None, "bucket": "其他",
                "total_shares": None, "source": "fallback"}

    fn = getattr(ak, "stock_individual_info_em", None)
    if fn is None:
        print("  [X] ak.stock_individual_info_em 不可用，行业信息回退。")
        return fallback

    df = try_fetch(fn, symbol=symbol)
    if df is None or df.empty:
        print("  [X] 未能获取行业信息，回退。")
        return fallback

    # stock_individual_info_em 返回 item / value 两列
    item_col = find_col_in(["item", "项目"], df)
    value_col = find_col_in(["value", "值"], df)
    if not item_col or not value_col:
        print("  [X] 行业信息返回列结构异常，回退。")
        return fallback

    info = dict(zip(df[item_col].astype(str), df[value_col]))

    # 行业：优先精确项名 "行业"，其次模糊含"行业"
    industry = None
    if info.get("行业") not in (None, "", "—", "-", "nan"):
        industry = str(info["行业"]).strip()
    if industry is None:
        for k, v in info.items():
            if "行业" in k and v not in (None, "", "—", "-", "nan"):
                industry = str(v).strip()
                break

    # 总股本：优先精确项名 "总股本"，其次模糊含"总股本"
    total_shares = None
    if info.get("总股本") is not None:
        total_shares = _parse_shares(info["总股本"])
    if total_shares is None:
        for k, v in info.items():
            if "总股本" in k:
                total_shares = _parse_shares(v)
                if total_shares is not None:
                    break

    bucket = map_to_industry_bucket(industry)
    result = {"industry": industry, "bucket": bucket,
              "total_shares": total_shares, "source": "em"}
    ind_str = industry if industry else "未知"
    ts_str = f"{total_shares:.2e}" if total_shares else "未知"
    print(f"  [OK] 行业={ind_str} → 桶={bucket}，总股本={ts_str}")
    return result


def _parse_shares(raw) -> float | None:
    """把 stock_individual_info_em 的总股本值解析为 float（股数）。"""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if s in ("", "—", "-", "nan", "None"):
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


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
    """获取全 A 股 代码-名称 映射（不走缓存）：Hikyuu 优先，AkShare fallback。"""
    df = _fetch_stock_list_hku()
    if df is not None and not df.empty:
        print(f"[OK] 使用 Hikyuu 本地库获取 A 股清单（{len(df)} 只）")
        return df
    print("  [INFO] Hikyuu 不可用，降级 AkShare（逐交易所独立获取）…")
    return _fetch_stock_list_live_ak()


def _fetch_stock_list_hku() -> pd.DataFrame | None:
    """Hikyuu 本地库 sm 迭代 → [代码, 名称]（零 HTTP）。"""
    print("\n[INFO] 正在获取 A 股代码-名称列表（Hikyuu 本地库 sm 迭代）...")
    return hku_stock_list()


def _fetch_stock_list_live_ak() -> pd.DataFrame | None:
    """
    AkShare 全 A 股 代码-名称 映射 fallback（不走缓存）。
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


def fetch_stock_screening_data(force_refresh: bool = False,
                               on_progress=None) -> pd.DataFrame | None:
    """
    获取全 A 股批量筛选表 [代码, 名称, 总市值, 行业, 桶]，供仪表盘按
    市值区间 + 行业筛选。带本地磁盘缓存（STOCK_SCREENING_TTL_HOURS，默认 24h）：
    缓存有效直接读盘，过期/缺失/force_refresh 才联网。失败结果（None/空）不落盘。

    数据源：Hikyuu 本地库（一次性 pytdx 导入后查询全走本地，不再依赖实时 HTTP）。
    口径：总市值 = 总股本(万股) × 1e4 × 最近收盘(元)；行业 = Hikyuu 行业板块名
    （stock.get_belong_to_block_list(category=HIKYUU_INDUSTRY_CATEGORY)）经
    map_to_industry_bucket 分桶（先申万次表 SW_TO_BUCKET，未命中 Hikyuu 表）。
    on_progress(done, total, desc) 回报逐只取数进度（缺省 None 即不报），
    与 run_batch / run_backtest 同款回调。

    降级：hikyuu 未装 / 本地库未导入（sm 空 / load_hikyuu 抛 HKUException）→
    返回 None（app 层 st.error 提示切 Demo 或先跑导入）；总股本或收盘取不到
    的标的总市值置 NaN（市值筛排除），行业取不到置 None / 桶 "其他"。
    旧 akshare 链路（_fetch_stock_screening_data_live + _fetch_sw_industry_map）
    保留为休眠代码备手动切换；本函数不再调用它们（用户明确"别用 akshare 了"）。
    """
    return disk_cache(
        "stock_screening.pkl", STOCK_SCREENING_TTL_HOURS,
        lambda: _fetch_stock_screening_data_hikyuu(on_progress),
        force_refresh=force_refresh,
    )


def _fetch_stock_screening_data_hikyuu(on_progress=None) -> pd.DataFrame | None:
    """用 Hikyuu 本地库构建全 A 股批量筛选表（不走缓存，不走实时 HTTP）。

    替代旧 akshare 实时链路（stock_zh_a_spot_em + sw_index_first_info +
    index_component_sw 31 次循环），根因：东财/乐咕/申万 HTTP 端点频繁连不上。
    Hikyuu 一次性 pytdx 导入后查询全走本地（HDF5 kdata + SQLite 板块/股本）。

    经 backend._hku() 共享 load（finance+weight，进程级单次），与其他 hku fetcher
    一致；股本 total_count 为万股 → 总市值 = 万股 × 1e4 × 收盘。
    """
    if on_progress is not None:
        try:
            on_progress(0, 0, "初始化 Hikyuu 本地库…")
        except Exception:
            pass

    # _hku() 失败（未装/未导入/sm 空）→ None，app 层 st.error 提示切 Demo 或先跑导入。
    sm = hku_sm()
    if sm is None:
        print("  [X] Hikyuu 本地库未初始化或未导入数据（sm 空 / load_hikyuu 抛异常）。")
        print("      请先跑数据导入：python scripts/run_hikyuu_import.py")
        return None

    # 沪深京 A 股枚举：直接迭代 sm 按 hku_is_a_share 过滤（预定义板块 get_block('A',…)
    # 仅 3193 只漏创业板/科创板，迭代得 ~5400 只含北交所；一次性，缓存 24h）。
    stocks = [s for s in sm if hku_is_a_share(s)]
    print(f"  [OK] 迭代 sm 过滤得沪深京 A 股 {len(stocks)} 只")
    if not stocks:
        print("  [X] 枚举到 0 只 A 股（本地库未导入？），批量筛选不可用。")
        return None

    total = len(stocks)
    rows = []
    for i, s in enumerate(stocks):
        if s is None or (hasattr(s, "is_null") and s.is_null()) or not s.valid:
            continue
        code = str(s.code)
        if len(code) != 6 or not code.isdigit():
            continue
        close = hku_last_close(s)
        tc = hku_total_count_wan(s)                    # 万股
        cap = tc * 1e4 * close if (tc and close and tc > 0 and close > 0) else float("nan")
        industry = hku_industry_name(s)
        rows.append({
            "代码": code, "名称": str(s.name), "总市值": cap,
            "行业": industry, "桶": map_to_industry_bucket(industry),
        })
        if on_progress is not None and (i % 200 == 0 or i == total - 1):
            try:
                on_progress(i + 1, total, f"Hikyuu 取数 {i + 1}/{total}")
            except Exception:
                pass

    if not rows:
        print("  [X] 取数后 0 行（全部标的无效？），批量筛选不可用。")
        return None
    df = pd.DataFrame(rows, columns=["代码", "名称", "总市值", "行业", "桶"])
    have_cap = int(df["总市值"].notna().sum())
    have_ind = int(df["行业"].notna().sum())
    # ---- 行业兜底链（市值/代码/名称始终只走 Hikyuu 本地）----
    # Hikyuu 本地行业板块由 scripts/import_hikyuu_industry_blocks.py 从东财导入
    # （496 个）；该导入失败/部分成功时本地仅十几个板块（实测 19），行业列大量空、
    # 且【无「银行」】——此时若用新浪行业兜底，银行/证券/保险被新浪合并成整个
    # 「金融行业」→ 桶误判为「非银金融」（银行打分口径整体错）。故兜底优先级：
    #   1) 申万一级（31 行业，银行/非银分列，覆盖沪深全 A ~5200 只）；
    #   2) 申万也不可用 → 新浪（至少行业列非空，比全 None 好）；
    #   3) 申万覆盖后残留的缺口（主要是北交所，申万不覆盖；新浪金融无银行粒度）
    #      仍留空 → 桶「其他」，宁缺勿错。
    # 本地行业板块完整后（覆盖 ≥ 98%）此链自动不触发。
    have_ind = _fill_industry_from_sw(df, have_ind, on_progress)
    if have_ind < len(df) * 0.95:
        have_ind = _fill_industry_from_sina(df, have_ind, on_progress)
    print(f"[OK] 批量筛选表(Hikyuu): {len(df)} 只，{have_ind} 只有行业归属，"
          f"总市值有效 {have_cap} 只")
    return df


def _fill_industry_from_sw(df, have_ind, on_progress=None) -> int:
    """申万一级行业补齐 df 的行业/桶 空缺（就地改 df），返回补齐后覆盖数。

    仅在本地行业覆盖率 < 98% 时尝试。失败（端点不可达 / 映射为空）不改 df。
    """
    if have_ind >= len(df) * 0.98:
        return have_ind
    cov = have_ind * 100 // max(len(df), 1)
    print(f"  [INFO] Hikyuu 本地行业板块仅覆盖 {have_ind}/{len(df)}（{cov}%）"
          f"{'（未导入）' if have_ind == 0 else '（不完整）'}，"
          f"降级 申万一级行业 补齐…")
    ind_map = _fetch_sw_industry_map(on_progress)
    if not ind_map:
        print("  [!] 申万一级行业拉取失败，转 新浪行业板块 兜底。")
        return have_ind
    return _apply_industry_map(df, ind_map, "申万")


def _fill_industry_from_sina(df, have_ind, on_progress=None) -> int:
    """新浪行业补齐 df 的行业/桶 空缺（就地改 df），返回补齐后覆盖数。"""
    cov = have_ind * 100 // max(len(df), 1)
    print(f"  [INFO] 行业仍覆盖 {have_ind}/{len(df)}（{cov}%），"
          f"用 新浪行业板块 补齐…")
    ind_map = _fetch_sina_industry_map(on_progress)
    if not ind_map:
        print("  [!] 新浪行业板块也拉取失败，行业列维持现状（部分缺失）；"
              "市值/代码/名称仍全走 Hikyuu 本地，不受影响。")
        return have_ind
    return _apply_industry_map(df, ind_map, "新浪")


def _apply_industry_map(df, ind_map, tag) -> int:
    """把 代码→行业名 映射填进 df 的行业空列并重算桶，返回新覆盖数。"""
    df["行业"] = df["行业"].fillna(df["代码"].map(ind_map))
    df["桶"] = df["行业"].apply(map_to_industry_bucket)
    have_ind = int(df["行业"].notna().sum())
    print(f"  [OK] {tag}补齐后行业覆盖 {have_ind}/{len(df)}")
    return have_ind


def _fetch_stock_screening_data_live(on_progress=None) -> pd.DataFrame | None:
    """实时获取全 A 股批量筛选表（不走缓存）。"""
    print("\n[INFO] 正在获取批量筛选表（总市值 + 申万一级行业）...")

    # --- 1. 总市值：东财实时行情（含代码 / 名称 / 总市值，单位元）---
    fn_spot = getattr(ak, "stock_zh_a_spot_em", None)
    if fn_spot is None:
        print("  [X] ak.stock_zh_a_spot_em 不可用，批量筛选不可用。")
        return None
    # spot_em 单次拉取全市场（~5000 只）可能耗时数十秒且含重试退避，期间无回调；
    # 先回报一句进度文案，让仪表盘进度条不致卡在通用提示上被误以为"假死"。
    if on_progress is not None:
        try:
            on_progress(0, 0, "获取全市场实时行情（东财 spot_em，约 5000 只）…")
        except Exception:
            pass
    spot = try_fetch(fn_spot)
    if spot is None or spot.empty:
        print("  [X] 未能获取全市场实时行情，批量筛选不可用。")
        return None

    code_col = find_col_in(["代码", "code"], spot)
    name_col = find_col_in(["名称", "简称", "name"], spot)
    cap_col = find_col_in(["总市值"], spot)
    if not code_col or not name_col or not cap_col:
        print("  [X] spot_em 列结构异常（缺代码/名称/总市值），批量筛选不可用。")
        return None

    df = pd.DataFrame({
        "代码": spot[code_col].astype(str).str.extract(r'(\d{6})', expand=False),
        "名称": spot[name_col].astype(str).str.strip(),
        "总市值": pd.to_numeric(spot[cap_col], errors="coerce"),
    })
    df = df.dropna(subset=["代码"]).reset_index(drop=True)
    print(f"  [OK] 总市值: {len(df)} 只（spot_em）")

    # --- 2. 行业归属：申万一级成份股循环构建 代码→行业名 映射 ---
    industry_map = _fetch_sw_industry_map(on_progress)
    if industry_map:
        df["行业"] = df["代码"].map(industry_map)
        have_ind = int(df["行业"].notna().sum())
        print(f"  [OK] 行业归属覆盖 {have_ind}/{len(df)} 只")
    else:
        df["行业"] = None
        print("  [!] 行业归属拉取失败，仅支持市值区间筛选（行业列置空）。")
    df["桶"] = df["行业"].apply(map_to_industry_bucket)

    print(f"[OK] 批量筛选表: {len(df)} 只，"
          f"{int(df['行业'].notna().sum())} 只有行业归属")
    return df


def _fetch_sw_industry_map(on_progress=None) -> dict:
    """构建 代码 → 申万一级行业名 映射。

    数据源：sw_index_first_info 取 31 个一级行业代码+名称，index_component_sw
    逐行业拉成份股（沪深全 A ~5200 只，**含「银行」42 只 / 「非银金融」79 只**）。
    任一环节失败返回 {}（调用方按市值区间降级筛选）。
    on_progress(done, total, desc) 回报循环进度。

    两个坑（均已处理，勿回退）：
      1. **行业代码须去 ".SI" 后缀**（801780.SI → 801780）。成份端点只认裸码，
         带后缀返空 → akshare 在列选择处抛 KeyError。这是本函数长期失效的根因。
      2. index_component_sw 间歇性因连接池耗尽返回残缺响应（同样以 KeyError 形
         式出现），try_fetch 视为确定性错误不重试 → 此处直接调用 + 手动重试 3 次。
    """
    fn_first = getattr(ak, "sw_index_first_info", None)
    if fn_first is None:
        print("  [X] ak.sw_index_first_info 不可用，行业映射跳过。")
        return {}
    first_df = try_fetch(fn_first)
    if first_df is None or first_df.empty:
        print("  [X] 申万一级行业分类获取失败，行业映射跳过。")
        return {}
    code_col = find_col_in(["行业代码", "代码"], first_df)
    name_col = find_col_in(["行业名称", "名称"], first_df)
    if not code_col or not name_col:
        print("  [X] sw_index_first_info 列结构异常，行业映射跳过。")
        return {}

    # ⚠ sw_index_first_info 返回的代码带 ".SI" 后缀（如 801780.SI），但成份端点
    # swsresearch.com/.../component_stocks/ 只认裸码（801780）——带后缀时返回
    # results=[]，akshare 随后在列选择处抛 KeyError（"证券代码 ... not in index"）。
    # 这是本函数历史上"申万连拉易阻断"的真因，重试救不了（确定性空响应）；
    # 截掉后缀后 31 个行业 42/79/104… 只全部正常返回（含「银行」42 只）。
    industries = [
        (ind_code.split(".", 1)[0].strip(), ind_name.strip())
        for ind_code, ind_name in zip(
            first_df[code_col].astype(str).str.strip(),
            first_df[name_col].astype(str).str.strip(),
        )
        if ind_code.split(".", 1)[0].strip()
    ]
    fn_comp = getattr(ak, "index_component_sw", None)
    if fn_comp is None:
        print("  [X] ak.index_component_sw 不可用，行业映射跳过。")
        return {}

    total = len(industries)
    mapping: dict = {}
    for i, (ind_code, ind_name) in enumerate(industries):
        comp = None
        # index_component_sw 间歇性因连接池耗尽返回残缺响应（KeyError: not in
        # index），try_fetch 将其视为确定性错误不重试。此处直接调用 + 手动重试。
        for _attempt in range(3):
            if _attempt > 0:
                time.sleep(2)  # 前次失败后稍等再试
            try:
                comp = fn_comp(symbol=ind_code)
            except (KeyError, ValueError, AttributeError) as e:
                # 虽为"确定性"异常类型，但此 API 的间歇性残缺响应即以 KeyError
                # 形式出现（pandas 列选择失败）——重试通常即恢复。
                comp = None
            except Exception:
                comp = None
            if comp is not None and not comp.empty:
                break
            # 重试前小憩（抗连接池耗尽）
            if _attempt < 2:
                time.sleep(1.5)
        n = 0
        if comp is not None and not comp.empty:
            sc = find_col_in(["证券代码", "代码", "stockcode"], comp)
            if sc:
                codes = (comp[sc].astype(str)
                         .str.extract(r'(\d{6})', expand=False).dropna())
                for c in codes:
                    mapping[c] = ind_name
                n = len(codes)
        if n:
            print(f"  [OK] {ind_name}（{ind_code}）: {n} 只")
        else:
            print(f"  [!] {ind_name}（{ind_code}）: 成份股获取失败，跳过")
        if on_progress is not None:
            try:
                on_progress(i + 1, total, f"行业成份 {i + 1}/{total}")
            except Exception:
                pass

    print(f"  [OK] 行业映射覆盖 {len(mapping)} 只股票")
    return mapping


def _fetch_sina_industry_map(on_progress=None) -> dict:
    """构建 代码 → 新浪行业名 映射。数据源：stock_sector_spot(indicator="新浪行业")
    取 ~49 个行业 label，stock_sector_detail(sector=label) 逐行业拉成份股。任一环节
    失败返回 {}（调用方维持现有行业列）。on_progress(done, total, desc) 回报进度。

    兜底链中排在申万之后（申万见 _fetch_sw_industry_map）：新浪行业端点稳定
    （memory: 新浪 spot 稳定），但分类粗——**无「银行」粒度**，银行/证券/保险被
    合并成整个「金融行业」（→ 桶「非银金融」，银行打分口径错）。故仅作最后一层
    残留补齐；正常路径下银行股的行业应由申万一级（银行 / 非银金融 分列）或
    Hikyuu 东财板块（import_hikyuu_industry_blocks.py）给出。
    """
    fn_spot = getattr(ak, "stock_sector_spot", None)
    if fn_spot is None:
        print("  [X] ak.stock_sector_spot 不可用，新浪行业映射跳过。")
        return {}
    spot = try_fetch(fn_spot, indicator="新浪行业")
    if spot is None or spot.empty:
        print("  [X] 新浪行业板块列表获取失败，行业映射跳过。")
        return {}
    if "label" not in spot.columns or "板块" not in spot.columns:
        print("  [X] 新浪行业列表列结构异常，行业映射跳过。")
        return {}
    fn_detail = getattr(ak, "stock_sector_detail", None)
    if fn_detail is None:
        print("  [X] ak.stock_sector_detail 不可用，新浪行业映射跳过。")
        return {}

    labels = spot["label"].astype(str).tolist()
    names = spot["板块"].astype(str).tolist()
    total = len(labels)
    mapping: dict = {}
    for i, (lab, name) in enumerate(zip(labels, names)):
        try:
            det = fn_detail(sector=lab)
        except Exception:
            det = None
        n = 0
        if det is not None and not det.empty and "code" in det.columns:
            codes = (det["code"].astype(str)
                     .str.extract(r'(\d{6})', expand=False).dropna())
            for c in codes:
                mapping[c] = name
            n = len(codes)
        if n:
            print(f"  [OK] {name}: {n} 只")
        if on_progress is not None:
            try:
                on_progress(i + 1, total, f"新浪行业 {i + 1}/{total}")
            except Exception:
                pass
        time.sleep(0.3)  # 礼貌延时，抗连续拉取被限
    print(f"  [OK] 新浪行业映射覆盖 {len(mapping)} 只股票")
    return mapping