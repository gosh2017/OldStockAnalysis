# -*- coding: utf-8 -*-
"""
第三步：市场情绪辅助 - 股债性价比

指标：
  股债性价比 = 全市场市盈率倒数 - 10 年期国债收益率
            = (1 / PE_median) - r_bond

分位数判断（高分位 = 当前 ERP 处于历史高位 = 股票相对便宜 = 低估）：
  0%  - 20%：极度高估   （ERP 历史低位，股票相对昂贵）
  20% - 40%：高估
  40% - 60%：合理
  60% - 80%：低估
  80% - 100%：极度低估 （ERP 历史高位，股票相对便宜）

注：旧实现把"高分位"误标为"极度高估"，与 ERP 高=股票便宜 的经济学含义
相反，已在本次修正。分位数改用 utils.percentile_of_score（scipy 优先，
numpy 兜底），真实历史序列由个股 PE/PB 指标补充（见 step3 个股分位）。
"""
import numpy as np
import pandas as pd

from utils import sep, find_col_in, generate_historical_erp, percentile_of_score


def market_sentiment(market_df: pd.DataFrame | None,
                     bond_yield: float | None,
                     stock_indicator: pd.DataFrame | None = None) -> dict:
    """
    计算股债性价比及历史分位数，输出市场情绪判断。

    可选传入 stock_indicator（个股历史 PE/PB），计算该股自身的
    PE/PB 历史分位（真实分位，优于全市场 ERP 的模拟分位）。
    返回包含 pe_median / bond_yield / equity_risk_premium /
    percentile / sentiment / pe_percentile / pb_percentile 的字典。
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
    # 高分位 = 当前 ERP 处于历史高位 = 股票相对便宜（低估）
    historical_erp = generate_historical_erp()
    percentile = percentile_of_score(historical_erp, equity_risk_premium)
    percentile = max(0, min(100, percentile))

    # -- 判断（高分位 = 低估）--
    if percentile <= 20:
        sentiment, color = "极度高估", "[RED]"
    elif percentile <= 40:
        sentiment, color = "高估", "[ORG]"
    elif percentile <= 60:
        sentiment, color = "合理", "[YLW]"
    elif percentile <= 80:
        sentiment, color = "低估", "[GRN]"
    else:
        sentiment, color = "极度低估", "[GRN]"

    print(f"\n  -- 历史分位数分析 --")
    print(f"  [CHART] 当前股债性价比处于过去 5 年的 {percentile:.1f}% 分位数")
    print(f"  [TAG]  市场情绪判断: {color} {sentiment}")

    # -- 个股自身 PE/PB 历史分位（真实分位）--
    # 分位含义：高 = 当前估值处于历史高位 = 偏贵；低 = 偏便宜。
    pe_percentile = None
    pb_percentile = None
    if stock_indicator is not None and not stock_indicator.empty:
        print(f"\n  -- 个股自身估值分位（{len(stock_indicator)} 期历史）--")
        if "市盈率PE" in stock_indicator.columns:
            pe_s = pd.to_numeric(stock_indicator["市盈率PE"], errors="coerce").dropna()
            pe_s = pe_s[(pe_s > 0) & (pe_s < 1000)]
            if len(pe_s) > 1:
                cur_pe = float(pe_s.iloc[-1])
                pe_percentile = percentile_of_score(pe_s.tolist(), cur_pe)
                print(f"  [DATA] 当前 PE={cur_pe:.2f}，处于自身历史 {pe_percentile:.1f}% 分位"
                      f"（{ '偏贵' if pe_percentile > 60 else ('便宜' if pe_percentile < 40 else '合理')}）")
        if "市净率PB" in stock_indicator.columns:
            pb_s = pd.to_numeric(stock_indicator["市净率PB"], errors="coerce").dropna()
            pb_s = pb_s[(pb_s > 0) & (pb_s < 100)]
            if len(pb_s) > 1:
                cur_pb = float(pb_s.iloc[-1])
                pb_percentile = percentile_of_score(pb_s.tolist(), cur_pb)
                print(f"  [DATA] 当前 PB={cur_pb:.3f}，处于自身历史 {pb_percentile:.1f}% 分位"
                      f"（{ '偏贵' if pb_percentile > 60 else ('便宜' if pb_percentile < 40 else '合理')}）")

    return {
        "pe_median": pe_median,
        "bond_yield": bond_yield,
        "equity_risk_premium": equity_risk_premium,
        "percentile": percentile,
        "sentiment": sentiment,
        "pe_percentile": pe_percentile,
        "pb_percentile": pb_percentile,
    }
