# -*- coding: utf-8 -*-
"""
通用工具函数 — 被多个模块共享。
"""
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def sep(title: str = "") -> None:
    """打印分隔线"""
    print(f"\n{'═' * 70}")
    if title:
        print(f"  {title}")
        print(f"{'═' * 70}")


def try_fetch(fn, retries: int = 2, **kwargs) -> pd.DataFrame | None:
    """
    带重试的 AkShare 数据获取封装。
    AkShare 依赖第三方接口，网络波动时抛异常，自动重试 1~2 次。
    """
    for attempt in range(1 + retries):
        try:
            df = fn(**kwargs)
            if df is None:
                continue
            if isinstance(df, pd.DataFrame) and df.empty:
                continue
            return df
        except Exception as e:
            if attempt < retries:
                print(f"  ⚠ 第 {attempt + 1} 次请求失败: {e}，正在重试...")
            else:
                print(f"  ✗ 请求最终失败: {e}")
    return None


def find_col_in(candidates: list, df: pd.DataFrame) -> str | None:
    """在 DataFrame 列中查找包含候选关键词的列名（大小写不敏感）"""
    for c in candidates:
        for col in df.columns:
            if c in str(col):
                return col
    return None


def estimate_dividend_yield(
    year: int, row, equity_col: str,
    dividend_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    roe_col: str, np_col: str,
) -> float:
    """
    估算某年的股息率。
    方案 1：从分红记录中提取实际分红金额 / 年末股价。
    方案 2：用 ROE × 30%（A 股银行典型分红比例）近似。
    方案 3：用净利润 × 30% / 隐含市值 近似。
    """
    # 方案 1：分红记录
    if dividend_df is not None and not dividend_df.empty:
        try:
            div_data = dividend_df.copy()
            date_col = None
            for c in div_data.columns:
                if "公告日期" in str(c) or "除权" in str(c) or "日期" in str(c):
                    date_col = c
                    break
            if date_col:
                div_data[date_col] = pd.to_datetime(div_data[date_col], errors="coerce")
                div_data["年份"] = div_data[date_col].dt.year
                year_div = div_data[div_data["年份"] == year]
                if not year_div.empty:
                    eps_div_col = None
                    for c in div_data.columns:
                        if "每股" in str(c) and ("分红" in str(c) or "派" in str(c)
                                                    or "息" in str(c) or "红利" in str(c)):
                            eps_div_col = c
                            break
                    if eps_div_col:
                        div_per_share = float(year_div[eps_div_col].iloc[0])
                        if not daily_df.empty and "日期" in daily_df.columns:
                            year_end = daily_df[daily_df["日期"] <= pd.Timestamp(f"{year}-12-31")]
                            if not year_end.empty:
                                price = float(year_end.iloc[-1]["收盘"])
                                return div_per_share / price * 100
        except Exception:
            pass

    # 方案 2：ROE × 30% 近似
    try:
        if roe_col and equity_col:
            roe_val = float(row[roe_col])
            if roe_val > 100:
                roe_val = roe_val / 100
            return roe_val * 0.30 / 100 * 100
    except Exception:
        pass

    # 方案 3：净利润 × 30% / 隐含市值 近似
    try:
        if np_col:
            np_val = float(row[np_col])
            implied_mv = np_val * 6
            dividend = np_val * 0.30
            return dividend / implied_mv * 100
    except Exception:
        pass

    return 0.0


def generate_historical_erp() -> list:
    """
    基于近 5 年 A 股市场经验数据生成股债性价比（ERP）的历史模拟分布，
    用于估算当前值的分位数位置。
    """
    np.random.seed(42)
    erp_list = []
    for _ in range(250):  # 约 5 年交易日
        pe = np.random.normal(22, 4)
        pe = max(15, min(35, pe))
        bond_r = np.random.normal(0.028, 0.004)
        bond_r = max(0.02, min(0.04, bond_r))
        erp_list.append((1 / pe) - bond_r)
    return erp_list
