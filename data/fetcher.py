# -*- coding: utf-8 -*-
"""
数据获取层 — 从 AkShare 获取各类 A 股数据。
每个 fetch_* 函数独立封装，包含重试和网络异常处理。
"""
import akshare as ak
import pandas as pd

from config import STOCK_CODE, START_DATE, END_DATE
from utils import try_fetch, find_col_in


def fetch_daily_data(symbol: str = STOCK_CODE) -> pd.DataFrame:
    """
    获取日频交易数据（前复权）。
    字段：日期 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 成交额
    """
    print(f"\n[INFO] 正在获取 {symbol} 的日频交易数据（{START_DATE} ~ {END_DATE}）...")
    df = try_fetch(
        ak.stock_zh_a_hist,
        symbol=symbol,
        period="daily",
        start_date=START_DATE,
        end_date=END_DATE,
        adjust="qfq",
    )
    if df is None or df.empty:
        print("  [X] 未能获取日频数据，后续步骤将受限。")
        return pd.DataFrame()

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
    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").reset_index(drop=True)

    print(f"  [OK] 获取到 {len(df)} 条日频记录，最新收盘价: {df['收盘'].iloc[-1]:.2f}")
    return df


def fetch_financial_abstract(symbol: str = STOCK_CODE) -> pd.DataFrame:
    """
    获取财务摘要数据（含 ROE / 资产负债率 / 净利润 / 经营现金流）。
    """
    print(f"\n[INFO] 正在获取 {symbol} 的财务摘要数据...")
    df = try_fetch(ak.stock_financial_abstract, symbol=symbol)
    if df is None or df.empty:
        print("  [X] 未能获取财务摘要数据。")
        return pd.DataFrame()
    print(f"  [OK] 获取到 {len(df)} 条财务记录，字段: {list(df.columns)[:15]}")
    return df


def fetch_cashflow_detail(symbol: str = STOCK_CODE) -> pd.DataFrame:
    """
    获取现金流量表详细数据（用于计算自由现金流中的资本性支出）。
    优先尝试新浪接口，失败则回退到东方财富。
    """
    print(f"\n[INFO] 正在获取 {symbol} 的现金流量表数据...")
    df = try_fetch(
        ak.stock_financial_report_sina, symbol=symbol, indicator="现金流量表"
    )
    if df is not None and not df.empty:
        print(f"  [OK] 新浪现金流量表: {len(df)} 条")
        return df

    df = try_fetch(
        ak.stock_financial_report, symbol=symbol, indicator="现金流量表"
    )
    if df is not None and not df.empty:
        print(f"  [OK] 东方财富现金流量表: {len(df)} 条")
        return df

    print("  [X] 未能获取现金流量表数据，将用经营现金流近似。")
    return pd.DataFrame()


def fetch_dividend(symbol: str = STOCK_CODE) -> pd.DataFrame:
    """获取历史分红数据（用于计算股息率）。"""
    print(f"\n[INFO] 正在获取 {symbol} 的分红数据...")
    df = try_fetch(ak.stock_dividend_record, symbol=symbol)
    if df is not None and not df.empty:
        print(f"  [OK] 分红数据: {len(df)} 条")
        return df
    print("  [X] 未能获取分红数据，将用其他方式估算股息率。")
    return pd.DataFrame()


def fetch_market_overview() -> pd.DataFrame | None:
    """
    获取全市场 A 股实时数据（用于计算市盈率中位数）。
    优先东方财富接口，失败则回退到新浪。
    """
    print(f"\n[INFO] 正在获取全市场 A 股实时数据...")
    df = try_fetch(ak.stock_zh_a_spot_em)
    if df is None or df.empty:
        df = try_fetch(ak.stock_zh_a_spot)
    if df is not None and not df.empty:
        print(f"  [OK] 全市场数据: {len(df)} 只股票")
        return df
    print("  [X] 未能获取全市场数据。")
    return None


def fetch_bond_yield_10y() -> float | None:
    """
    获取中国 10 年期国债收益率。
    尝试多种 AkShare 接口，返回最新收益率（小数形式，如 0.0255 表示 2.55%）。
    """
    print(f"\n[INFO] 正在获取 10 年期国债收益率...")

    df = try_fetch(ak.rate_ts_bond)
    if df is not None and not df.empty:
        target_col = find_col_in(["10", "十年"], df)
        if target_col is not None:
            df_sorted = df.copy()
            date_col = df_sorted.columns[0]
            df_sorted[date_col] = pd.to_datetime(df_sorted[date_col])
            df_sorted = df_sorted.sort_values(date_col)
            val = df_sorted[target_col].dropna().iloc[-1]
            val_pct = float(val) / 100 if val > 1 else float(val)
            print(f"  [OK] 10 年期国债收益率: {val_pct * 100:.2f}%（来源: rate_ts_bond）")
            return val_pct

    df = try_fetch(ak.bond_china_yield)
    if df is not None and not df.empty:
        target_col = find_col_in(["10", "十年"], df)
        if target_col is not None:
            val = df[target_col].dropna().iloc[-1]
            val_pct = float(val) / 100 if val > 1 else float(val)
            print(f"  [OK] 10 年期国债收益率: {val_pct * 100:.2f}%")
            return val_pct

    print("  [!] 未能从 AkShare 获取实时国债收益率，使用近 5 年典型值 ≈ 2.5%")
    return 0.025
