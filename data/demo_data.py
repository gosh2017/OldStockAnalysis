# -*- coding: utf-8 -*-
"""
Demo / Mock 数据生成器 — 无需联网即可运行完整分析流程。

用于：
  - 在无网络或 AkShare 接口异常时验证分析逻辑
  - 单元测试与快速演示
  - CI 环境下的端到端验证

所有数据基于平安银行（000001）的典型财务特征生成，
并非真实数据，仅用于功能验证。
"""
import numpy as np
import pandas as pd

from config import (
    STOCK_CODE, START_DATE, END_DATE,
    FIN_START, FIN_END,
)


def generate_daily_data(
    symbol: str = STOCK_CODE,
    start: str = START_DATE,
    end: str = END_DATE,
    seed: int = 42,
) -> pd.DataFrame:
    """
    生成日频交易数据（前复权）。
    用几何布朗运动模拟股价，基价 ~14 元（平安银行近年区间）。
    """
    np.random.seed(seed)
    dates = pd.bdate_range(start=start, end=end, freq="B")

    n = len(dates)
    base_price = 14.0
    annual_vol = 0.25
    daily_vol = annual_vol / np.sqrt(250)
    drift = 0.04 / 250

    # 几何布朗运动
    log_returns = np.random.normal(drift, daily_vol, n)
    price = base_price * np.exp(np.cumsum(log_returns))
    price = np.clip(price, 6, 35)

    close = np.round(price, 2)
    # 开盘/最高/最低围绕收盘随机波动
    open_p = close + np.round(np.random.uniform(-0.15, 0.15, n), 2)
    high_p = np.maximum(close, open_p) + np.round(np.random.uniform(0, 0.2, n), 2)
    low_p = np.minimum(close, open_p) - np.round(np.random.uniform(0, 0.2, n), 2)
    low_p = np.maximum(low_p, 1)
    volume = np.random.randint(5e6, 5e7, n)
    amount = close * volume * np.random.uniform(0.95, 1.05, n)

    df = pd.DataFrame({
        "日期": dates,
        "开盘": open_p,
        "收盘": close,
        "最高": high_p,
        "最低": low_p,
        "成交量": volume,
        "成交额": np.round(amount, 0),
    })
    return df


def generate_financial_abstract(
    symbol: str = STOCK_CODE,
    start_year: int = FIN_START,
    end_year: int = FIN_END,
    seed: int = 43,
) -> pd.DataFrame:
    """
    生成财务摘要数据。
    模拟平安银行 2021-2025 年核心指标：
      ROE ~11-13%（银行股偏低但稳健）
      资产负债率 ~90-93%（银行典型）
      经营现金流 ~1200-1600 亿
      净利润 ~1100-1300 亿
      归母权益 ~18000-21000 亿
      总股本 ~197.56 亿股
    """
    np.random.seed(seed)
    years = list(range(start_year, end_year + 1))
    rows = []

    for year in years:
        t = (year - start_year)
        roe = 11.5 + t * 0.3 + np.random.uniform(-0.5, 0.5)
        debt_ratio = 91 + t * 0.2 + np.random.uniform(-0.3, 0.3)
        net_profit = 1150 + t * 20 + np.random.uniform(-30, 30)
        # OCF scaled for realistic DCF: ~180亿 vs actual ~1300亿
        # DCF per-share val = ~15-20元 (matching simulated price)
        ocf = 180 + t * 12 + np.random.uniform(-8, 8)
        equity = 18500 + t * 500 + np.random.uniform(-200, 200)
        total_shares = 197.56e8

        rows.append({
            "报告期": pd.Timestamp(f"{year}-12-31"),
            "加权净资产收益率(%)": round(roe, 2),
            "资产负债率(%)": round(debt_ratio, 2),
            # OCF scaled for realistic DCF per-share valuation (~15-20 yuan)
            "经营活动产生的现金流量净额": int(ocf * 1e8),
            "归属于上市公司股东的净利润": int(net_profit * 1e8),
            "归属母公司股东权益": int(equity * 1e8),
            "总股本": int(total_shares),
        })

    return pd.DataFrame(rows)


def generate_cashflow_detail(
    symbol: str = STOCK_CODE,
    start_year: int = FIN_START,
    end_year: int = FIN_END,
    seed: int = 44,
) -> pd.DataFrame:
    """
    生成现金流量表数据（仅需"购建固定资产"用于 DCF 的资本性支出）。
    """
    np.random.seed(seed)
    years = list(range(start_year, end_year + 1))
    rows = []
    for year in years:
        capex = 50 + np.random.uniform(-10, 10)
        rows.append({
            "报告期": pd.Timestamp(f"{year}-12-31"),
            "购建固定资产、无形资产和其他长期资产支付的现金": int(capex * 1e8),
        })
    return pd.DataFrame(rows)


def generate_dividend(
    symbol: str = STOCK_CODE,
    start_year: int = FIN_START,
    end_year: int = FIN_END,
    seed: int = 45,
) -> pd.DataFrame:
    """
    生成分红记录数据，列名匹配 stock_history_dividend_detail 的输出。
    平安银行近年每股分红约 0.40-0.50 元（派息列直接存每股金额）。
    """
    np.random.seed(seed)
    years = list(range(start_year, end_year + 1))
    rows = []
    for year in years:
        div_per_share = 0.40 + np.random.uniform(-0.03, 0.07)
        month = np.random.randint(6, 9)
        day = np.random.randint(5, 25)
        rows.append({
            "公告日期": pd.Timestamp(f"{year + 1}-{month:02d}-{day:02d}"),
            "送股": 0,
            "转增": 0,
            "派息": round(div_per_share, 4),
            "配股": 0,
            "除权除息日": pd.Timestamp(f"{year + 1}-{month:02d}-{day + 5:02d}"),
            "除权除息基准日": pd.Timestamp(f"{year + 1}-{month:02d}-{day + 4:02d}"),
            "权益分派进度": "实施",
        })
    return pd.DataFrame(rows)


def generate_market_overview(seed: int = 46) -> pd.DataFrame:
    """
    生成全市场快照数据，仅需"市盈率-动态"列用于 PE 中位数计算。
    模拟 ~5000 只股票，PE 分布均值 ~25，中位数 ~18。
    """
    np.random.seed(seed)
    n = 4800
    # 对数正态分布模拟 PE
    pe_raw = np.random.lognormal(mean=2.5, sigma=0.8, size=n)
    pe_raw = np.clip(pe_raw, 0.5, 500)
    pe = np.round(pe_raw, 2)

    codes = [f"{i:06d}" for i in range(100000, 100000 + n)]
    names = [f"股票{i}" for i in range(n)]
    prices = np.round(np.random.uniform(3, 80, n), 2)

    return pd.DataFrame({
        "代码": codes,
        "名称": names,
        "最新价": prices,
        "市盈率-动态": pe,
    })


def generate_bond_yield_10y() -> float:
    """
    返回近似的 10 年期国债收益率（2025 年 ~2.3%）。
    """
    return 0.023


def generate_all_demo_data() -> dict:
    """
    一次性生成所有分析步骤所需的模拟数据。
    返回的字典键与主流程中的 fetch_* 函数返回类型一致。
    """
    return {
        "daily_df": generate_daily_data(),
        "fin_abstract": generate_financial_abstract(),
        "cashflow_df": generate_cashflow_detail(),
        "dividend_df": generate_dividend(),
        "market_df": generate_market_overview(),
        "bond_yield": generate_bond_yield_10y(),
    }