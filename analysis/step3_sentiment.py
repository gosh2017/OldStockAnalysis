# -*- coding: utf-8 -*-
"""
第三步：市场情绪辅助 - 股债性价比

指标：
  股债性价比 = 全市场市盈率倒数 - 10 年期国债收益率
            = (1 / PE_median) - r_bond

分位数判断：
  0%  - 20%：极度低估
  20% - 40%：低估
  40% - 60%：合理
  60% - 80%：高估
  80% - 100%：极度高估
"""
import numpy as np
import pandas as pd

from utils import sep, find_col_in, generate_historical_erp


def market_sentiment(market_df: pd.DataFrame | None,
                     bond_yield: float | None) -> dict:
    """
    计算股债性价比及历史分位数，输出市场情绪判断。
    返回包含 pe_median / bond_yield / equity_risk_premium /
    percentile / sentiment 的字典。
    """
    sep("第三步：市场情绪辅助 — 股债性价比分析")

    # -- 全市场 PE 中位数 --
    pe_median = None
    if market_df is not None and not market_df.empty:
        pe_col = find_col_in(["市盈率", "PE", "pe"], market_df)
        if pe_col:
            pe_series = pd.to_numeric(market_df[pe_col], errors="coerce")
            pe_series = pe_series[(pe_series > 0) & (pe_series < 500)]
            if len(pe_series) > 0:
                pe_median = pe_series.median()
                print(f"\n  [DATA] 全市场 A 股 PE 中位数: {pe_median:.2f}")

    if pe_median is None:
        print(f"\n  [!] 未能获取全市场 PE 数据，使用近 5 年中位数 ≈ 20")
        pe_median = 20.0

    # -- 10 年期国债收益率 --
    if bond_yield is None:
        bond_yield = 0.025
        print(f"  [!] 使用默认 10 年期国债收益率: {bond_yield * 100:.1f}%")
    else:
        print(f"  [DATA] 10 年期国债收益率: {bond_yield * 100:.2f}%")

    # -- 股债性价比 --
    equity_risk_premium = (1 / pe_median) - bond_yield
    print(f"\n  [DATA] 股债性价比（ERP）: {equity_risk_premium * 100:.2f}%")
    print(f"     = (1 / {pe_median:.1f}) - {bond_yield * 100:.2f}% = {equity_risk_premium * 100:.2f}%")

    # -- 历史分位数 --
    historical_erp = generate_historical_erp()
    percentile = np.mean([1 for v in historical_erp if v <= equity_risk_premium]) * 100
    percentile = max(0, min(100, percentile))

    # -- 判断 --
    if percentile <= 20:
        sentiment, color = "极度低估", "[RED]"
    elif percentile <= 40:
        sentiment, color = "低估", "[ORG]"
    elif percentile <= 60:
        sentiment, color = "合理", "[YLW]"
    elif percentile <= 80:
        sentiment, color = "高估", "[ORG]"
    else:
        sentiment, color = "极度高估", "[RED]"

    print(f"\n  -- 历史分位数分析 --")
    print(f"  [CHART] 当前股债性价比处于过去 5 年的 {percentile:.1f}% 分位数")
    print(f"  [TAG]  市场情绪判断: {color} {sentiment}")

    return {
        "pe_median": pe_median,
        "bond_yield": bond_yield,
        "equity_risk_premium": equity_risk_premium,
        "percentile": percentile,
        "sentiment": sentiment,
    }
