# -*- coding: utf-8 -*-
"""
数据获取层 -- 从 AkShare 获取各类 A 股数据。
每个 fetch_* 函数独立封装，包含重试和网络异常处理。

AkShare 版本: 1.18.87
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
import akshare as ak
import pandas as pd

from config import STOCK_CODE, START_DATE, END_DATE
from utils import try_fetch, find_col_in


# stock_financial_report_sina 的 symbol 参数表示报表类型
_CASHFLOW_REPORT_TYPE = "现金流量表"


def _prefix_symbol(symbol: str) -> str:
    """给股票代码加交易所前缀: 000001 -> sz000001, 600000 -> sh600000"""
    s = str(symbol).zfill(6)
    if s.startswith("6"):
        return "sh" + s
    elif s.startswith("0") or s.startswith("3"):
        return "sz" + s
    return s


def fetch_daily_data(symbol: str = STOCK_CODE) -> pd.DataFrame:
    """
    获取日频交易数据（前复权）。
    字段：日期 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 成交额
    尝试多个 AkShare 接口作为 fallback。
    """
    print(f"\n[INFO] 正在获取 {symbol} 的日频交易数据（{START_DATE} ~ {END_DATE}）...")

    strategies = [
        ("stock_zh_a_hist", {
            "symbol": symbol, "period": "daily",
            "start_date": START_DATE, "end_date": END_DATE, "adjust": "qfq",
        }),
        ("stock_zh_a_hist", {
            "symbol": symbol, "period": "daily",
            "start_date": START_DATE, "end_date": END_DATE,
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
    """
    print(f"\n[INFO] 正在获取全市场 A 股实时数据...")
    df = try_fetch(ak.stock_zh_a_spot_em)
    if df is not None and not df.empty:
        print(f"  [OK] 全市场数据: {len(df)} 只股票")
        return df
    print("  [X] 未能获取全市场数据。")
    return None


def fetch_bond_yield_10y() -> float | None:
    """
    获取中国 10 年期国债收益率。
    使用 bond_china_yield，返回最新收益率（小数形式，如 0.023 表示 2.3%）。
    """
    print(f"\n[INFO] 正在获取 10 年期国债收益率...")

    df = try_fetch(
        ak.bond_china_yield,
        start_date="20200101", end_date="20260813",
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