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
numpy 兜底）。

历史分位基准：优先用真实历史序列——市场历史 PE（乐咕 stock_market_pe_lg）
+ 国债历史（bond_china_yield），按日期对齐得历史 ERP；缺失时回退合成分布
generate_historical_erp()。个股自身 PE/PB 历史分位由 stock_indicator 补充。
"""
import numpy as np
import pandas as pd

from config import INDIVIDUAL_PERCENTILE_WINDOW_YEARS
from utils import sep, find_col_in, generate_historical_erp, percentile_of_score


def _historical_erp_series(market_pe_history: pd.DataFrame | None,
                           bond_yield_history: pd.DataFrame | None,
                           bond_yield: float) -> tuple[list | None, bool]:
    """
    从市场历史 PE + 国债历史构造历史 ERP 序列（1/pe − bond，按日期对齐）。

    国债历史按市场 PE 的日期 reindex + ffill 对齐（国债收益率慢变，日前向
    填充合理）；早于国债起点的 PE 日期用标量 bond_yield 兜底。无国债历史时
    整列用标量 bond_yield。返回 (erp 列表, used_real_bond)：
      - erp 列表已 dropna、过滤 (0,1) 区间外异常，长度≥2；构造失败为 None
        （调用方回退合成分布）。
      - used_real_bond：是否实际用到了真实国债历史序列（False=整列标量兜底，
        调用方据此标 erp_source="real_partial"）。
    """
    if market_pe_history is None or market_pe_history.empty:
        return None, False
    pe = market_pe_history.copy()
    pe["日期"] = pd.to_datetime(pe["日期"], errors="coerce")
    pe["市盈率"] = pd.to_numeric(pe["市盈率"], errors="coerce")
    pe = pe.dropna(subset=["日期", "市盈率"])
    pe = pe[(pe["市盈率"] > 0) & (pe["市盈率"] < 500)]
    if pe.empty:
        return None, False
    pe = (pe.sort_values("日期")
            .drop_duplicates(subset=["日期"])
            .set_index("日期"))

    bond_aligned = None
    used_real_bond = False
    if bond_yield_history is not None and not bond_yield_history.empty:
        bd = bond_yield_history.copy()
        bd["日期"] = pd.to_datetime(bd["日期"], errors="coerce")
        bd["国债收益率"] = pd.to_numeric(bd["国债收益率"], errors="coerce")
        bd = bd.dropna(subset=["日期", "国债收益率"])
        bd = (bd.sort_values("日期")
               .drop_duplicates(subset=["日期"])
               .set_index("日期")["国债收益率"])
        if not bd.empty:
            bond_aligned = bd.reindex(pe.index).ffill()      # 对齐到 PE 日期
            bond_aligned = bond_aligned.fillna(bond_yield)    # 早于国债起点用标量
            used_real_bond = True

    if bond_aligned is None:
        bond_aligned = pd.Series(bond_yield, index=pe.index)

    erp = (1 / pe["市盈率"]) - bond_aligned
    erp = erp.replace([np.inf, -np.inf], np.nan).dropna()
    erp = erp[(erp > 0) & (erp < 1)]                          # 过滤异常 ERP
    out = erp.tolist()
    return (out if len(out) >= 2 else None, used_real_bond)


def _recent_window(series: pd.Series, date_series: pd.Series,
                   window_years: int) -> tuple[pd.Series, bool]:
    """
    按报告日期取最近 window_years 年的子序列（item C1）。

    估值中枢长期漂移时，全历史分位会失真（如银行 PE 从 20→5）。改用近 N 年
    滚动窗口与市场 ERP 口径统一。当前值仍取末值（调用方负责取末值）。

    series 与 date_series 可能因前置 dropna 长度不一致、索引不对齐，此处按
    共同索引对齐后一并 dropna，保证日期与数值一一对应。

    返回 (windowed_series, used_full_history)：
      - windowed_series：窗口内数值序列；窗口内不足 2 期（或无可用日期）时
        回退全历史非空序列。
      - used_full_history：是否回退了全历史（调用方据此打印 [!] 提醒）。
    """
    full = pd.to_numeric(series, errors="coerce").dropna()
    df = pd.DataFrame({"v": series, "d": date_series})
    df["d"] = pd.to_datetime(df["d"], errors="coerce")
    df = df.dropna(subset=["v", "d"])
    if len(df) >= 2:
        latest = df["d"].max()
        cutoff = latest - pd.DateOffset(years=window_years)
        windowed = df[df["d"] >= cutoff]["v"]
        if len(windowed) >= 2:
            return windowed, False
    return full, True


def market_sentiment(market_df: pd.DataFrame | None,
                     bond_yield: float | None,
                     stock_indicator: pd.DataFrame | None = None,
                     market_pe_history: pd.DataFrame | None = None,
                     bond_yield_history: pd.DataFrame | None = None) -> dict:
    """
    计算股债性价比（ERP）及历史分位数，输出市场情绪判断。

    历史分位优先用真实序列：market_pe_history（乐咕市场历史 PE）+
    bond_yield_history（国债历史）按日期对齐得历史 ERP，当前 ERP 与之比分位
    （见 _historical_erp_series）；二者缺失时回退合成分布 generate_historical_erp()。

    当前市场 PE 取值优先级：market_pe_history 末值 > market_df 快照中位数 > 默认 20。
    可选 stock_indicator 计算个股自身 PE/PB 历史分位，并暴露 current_pe/current_pb
    供评分层 PB-ROE 锚使用（item 8）。erp_source 标历史分位来源可信度
    （real/real_partial/synthetic，item 10）。
    返回 pe_median/bond_yield/equity_risk_premium/percentile/sentiment/
    pe_percentile/pb_percentile/market_pe_source/current_pe/current_pb/erp_source。
    """
    sep("第三步：市场情绪辅助 — 股债性价比分析")

    # -- 当前市场 PE + 来源 --
    pe_median = None
    market_pe_source = None
    if market_pe_history is not None and not market_pe_history.empty:
        pe_s = pd.to_numeric(market_pe_history["市盈率"], errors="coerce")
        pe_s = pe_s[(pe_s > 0) & (pe_s < 500)].dropna()
        if len(pe_s) > 0:
            pe_median = float(pe_s.iloc[-1])           # 真实历史末值
            market_pe_source = "history"
            print(f"\n  [DATA] 市场历史 PE（乐咕）末值: {pe_median:.2f}（共 {len(pe_s)} 期）")
    if pe_median is None and market_df is not None and not market_df.empty:
        pe_col = find_col_in(["市盈率", "PE", "pe"], market_df)
        if pe_col:
            pe_series = pd.to_numeric(market_df[pe_col], errors="coerce")
            pe_series = pe_series[(pe_series > 0) & (pe_series < 500)]
            if len(pe_series) > 0:
                pe_median = float(pe_series.median())
                market_pe_source = "snapshot"
                print(f"\n  [DATA] 全市场 A 股 PE 中位数: {pe_median:.2f}")

    if pe_median is None:
        print(f"\n  [!] 未能获取市场 PE 数据，使用近 5 年中位数 ≈ 20")
        pe_median = 20.0
        market_pe_source = "default"

    # -- 10 年期国债收益率（当前）--
    if bond_yield is None:
        bond_yield = 0.025
        print(f"  [!] 使用默认 10 年期国债收益率: {bond_yield * 100:.1f}%")
    else:
        print(f"  [DATA] 10 年期国债收益率: {bond_yield * 100:.2f}%")

    # -- 股债性价比 --
    equity_risk_premium = (1 / pe_median) - bond_yield
    print(f"\n  [DATA] 股债性价比（ERP）: {equity_risk_premium * 100:.2f}%")
    print(f"     = (1 / {pe_median:.1f}) - {bond_yield * 100:.2f}% = {equity_risk_premium * 100:.2f}%")

    # -- 历史分位数（优先真实历史 ERP 序列，缺失回退合成分布）--
    # 高分位 = 当前 ERP 处于历史高位 = 股票相对便宜（低估）
    # erp_source 标注分位来源可信度：
    #   real          = 真实 PE + 真实国债历史序列
    #   real_partial  = 仅 PE 真实、国债整列标量兜底（可信度降低，[WARN] 不静默）
    #   synthetic     = 无真实 PE 历史序列 → 合成分布兜底（[WARN] 不静默）
    historical_erp, used_real_bond = _historical_erp_series(
        market_pe_history, bond_yield_history, bond_yield)
    if historical_erp is None:
        historical_erp = generate_historical_erp()
        erp_source = "synthetic"
        print(f"  [WARN] 无真实历史序列，降级合成分布估算分位（{len(historical_erp)} 期）")
    elif used_real_bond:
        erp_source = "real"
        print(f"  [INFO] 历史分位基于真实序列（{len(historical_erp)} 期 ERP）")
    else:
        erp_source = "real_partial"
        print(f"  [WARN] 历史分位仅 PE 真实、国债用标量兜底，可信度降低（{len(historical_erp)} 期 ERP）")
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
    print(f"  [CHART] 当前股债性价比处于历史的 {percentile:.1f}% 分位数")
    print(f"  [TAG]  市场情绪判断: {color} {sentiment}")

    # -- 个股自身 PE/PB 历史分位（真实分位）--
    # 分位含义：高 = 当前估值处于历史高位 = 偏贵；低 = 偏便宜。
    # current_pe/current_pb 暴露当前值供评分层 PB-ROE 锚使用（item 8）。
    # item C1：分位改用近 N 年滚动窗口（与市场 ERP 口径统一，避免估值中枢
    # 长期漂移使全历史分位失真）；窗口不足 2 期回退全历史。
    pe_percentile = None
    pb_percentile = None
    current_pe = None
    current_pb = None
    if stock_indicator is not None and not stock_indicator.empty:
        date_col_si = find_col_in(["日期", "date", "trade_date"], stock_indicator)
        print(f"\n  -- 个股自身估值分位（{len(stock_indicator)} 期历史，"
              f"滚动 {INDIVIDUAL_PERCENTILE_WINDOW_YEARS} 年窗口）--")
        if "市盈率PE" in stock_indicator.columns:
            pe_s = pd.to_numeric(stock_indicator["市盈率PE"], errors="coerce")
            pe_s = pe_s[(pe_s > 0) & (pe_s < 1000)].dropna()
            if len(pe_s) > 1:
                current_pe = float(pe_s.iloc[-1])
                if date_col_si and date_col_si in stock_indicator.columns:
                    pe_win, used_full = _recent_window(
                        pe_s, stock_indicator[date_col_si], INDIVIDUAL_PERCENTILE_WINDOW_YEARS)
                else:
                    pe_win, used_full = pe_s, True
                pe_percentile = percentile_of_score(pe_win.tolist(), current_pe)
                if used_full:
                    print(f"  [!] 个股 PE 分位窗口不足，回退全历史")
                print(f"  [DATA] 当前 PE={current_pe:.2f}，处于自身近 "
                      f"{INDIVIDUAL_PERCENTILE_WINDOW_YEARS} 年 {pe_percentile:.1f}% 分位"
                      f"（{ '偏贵' if pe_percentile > 60 else ('便宜' if pe_percentile < 40 else '合理')}）")
        if "市净率PB" in stock_indicator.columns:
            pb_s = pd.to_numeric(stock_indicator["市净率PB"], errors="coerce")
            pb_s = pb_s[(pb_s > 0) & (pb_s < 100)].dropna()
            if len(pb_s) > 1:
                current_pb = float(pb_s.iloc[-1])
                if date_col_si and date_col_si in stock_indicator.columns:
                    pb_win, used_full = _recent_window(
                        pb_s, stock_indicator[date_col_si], INDIVIDUAL_PERCENTILE_WINDOW_YEARS)
                else:
                    pb_win, used_full = pb_s, True
                pb_percentile = percentile_of_score(pb_win.tolist(), current_pb)
                if used_full:
                    print(f"  [!] 个股 PB 分位窗口不足，回退全历史")
                print(f"  [DATA] 当前 PB={current_pb:.3f}，处于自身近 "
                      f"{INDIVIDUAL_PERCENTILE_WINDOW_YEARS} 年 {pb_percentile:.1f}% 分位"
                      f"（{ '偏贵' if pb_percentile > 60 else ('便宜' if pb_percentile < 40 else '合理')}）")

    return {
        "pe_median": pe_median,
        "bond_yield": bond_yield,
        "equity_risk_premium": equity_risk_premium,
        "percentile": percentile,
        "sentiment": sentiment,
        "pe_percentile": pe_percentile,
        "pb_percentile": pb_percentile,
        "market_pe_source": market_pe_source,
        "current_pe": current_pe,
        "current_pb": current_pb,
        "erp_source": erp_source,
    }
