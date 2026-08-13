# -*- coding: utf-8 -*-
"""
第二步：估值锚定 — 自由现金流折现模型（DCF）

基期 FCF = 过去 5 年平均自由现金流
自由现金流 = 经营性现金流净额 − 资本性支出

三情景参数（来自 config.SCENARIOS）：
  保守 / 中性 / 乐观

输出：每股内在价值（保守 / 中性 / 乐观）
"""
import numpy as np
import pandas as pd

from config import FIN_START, FIN_END, SCENARIOS
from utils import sep, find_col_in


def dcf_valuation(
    symbol: str,
    fin_abstract: pd.DataFrame,
    cashflow_df: pd.DataFrame,
    daily_df: pd.DataFrame,
) -> dict:
    """执行 DCF 三情景估值，返回包含估值结果的字典。"""
    sep("第二步：估值锚定 — 自由现金流折现模型（DCF）")

    years = list(range(FIN_START, FIN_END + 1))

    # ── 提取经营性现金流 ──
    ocf_values = {}
    capex_values = {}

    if fin_abstract is not None and not fin_abstract.empty:
        date_col = find_col_in(["报告日期", "报告期", "report"], fin_abstract)
        ocf_col  = find_col_in(["经营活动产生的现金流量净额", "经营活动现金"], fin_abstract)
        if date_col and ocf_col:
            fin_abstract[date_col] = pd.to_datetime(fin_abstract[date_col], errors="coerce")
            fin_abstract["年份"] = fin_abstract[date_col].dt.year
            for year in years:
                year_data = fin_abstract[fin_abstract["年份"] == year]
                if not year_data.empty:
                    row = year_data.sort_values(date_col).iloc[-1]
                    try:
                        ocf_values[year] = float(row[ocf_col])
                    except (ValueError, TypeError):
                        pass

    # ── 提取资本性支出 ──
    if cashflow_df is not None and not cashflow_df.empty:
        date_col_cf = find_col_in(["报告期", "报告日期", "report"], cashflow_df)
        capex_col   = find_col_in(["购建固定资产", "资本性支出", "购建长期资产"], cashflow_df)
        if date_col_cf and capex_col:
            cashflow_df[date_col_cf] = pd.to_datetime(cashflow_df[date_col_cf], errors="coerce")
            cashflow_df["年份"] = cashflow_df[date_col_cf].dt.year
            for year in years:
                year_data = cashflow_df[cashflow_df["年份"] == year]
                if not year_data.empty:
                    row = year_data.sort_values(date_col_cf).iloc[-1]
                    try:
                        capex_values[year] = float(row[capex_col])
                    except (ValueError, TypeError):
                        pass

    # ── 计算 FCF ──
    print(f"\n  📊 各年现金流数据（单位：亿元）\n")
    fcf_values = {}
    print(f"  {'年份':>6s}  {'经营现金流':>12s}  {'资本性支出':>12s}  {'自由现金流':>12s}")
    print(f"  {'─' * 52}")

    for year in years:
        ocf = ocf_values.get(year, 0)
        capex = capex_values.get(year, 0)
        if year not in capex_values:
            capex = abs(ocf) * 0.20  # 典型水平近似
        fcf = ocf - capex
        fcf_values[year] = fcf
        print(f"  {year:>6d}  {ocf / 1e8:>12.1f}  {capex / 1e8:>12.1f}  {fcf / 1e8:>12.1f}")

    # ── 基期 FCF ──
    fcf_list = [v for v in fcf_values.values() if v is not None and v > 0]
    if not fcf_list:
        print("\n  ✗ 无法计算有效 FCF，跳过 DCF 估值。")
        return {"valuations": None, "base_fcf": None, "total_shares": None,
                "conservative": None, "neutral": None, "optimistic": None}

    base_fcf = np.mean(fcf_list)
    print(f"\n  📌 基期 FCF（5 年平均）: {base_fcf / 1e8:.1f} 亿元")

    # ── 总股本 ──
    total_shares = _get_total_shares(symbol, fin_abstract, daily_df)
    print(f"  📌 总股本: {total_shares / 1e8:.2f} 亿股")

    # ── 三情景 DCF ──
    print(f"\n  📊 DCF 三情景估值结果\n")
    print(f"  {'情景':>20s}  {'增长率':>8s}  {'永续增长':>8s}  {'WACC':>8s}  {'内在价值':>10s}")
    print(f"  {'─' * 66}")

    valuations = {}
    for scenario_name, params in SCENARIOS.items():
        g, perp_g, wacc = params["growth"], params["perpetual"], params["wacc"]

        pv_sum = sum(
            base_fcf * ((1 + g) ** t) / ((1 + wacc) ** t)
            for t in range(1, 6)
        )

        terminal_fcf = base_fcf * ((1 + g) ** 5) * (1 + perp_g)
        terminal_value = terminal_fcf / (wacc - perp_g)
        pv_terminal = terminal_value / ((1 + wacc) ** 5)

        intrinsic_value = (pv_sum + pv_terminal) / total_shares

        valuations[scenario_name] = {
            "intrinsic_value": intrinsic_value,
            "enterprise_value": pv_sum + pv_terminal,
            "pv_operations": pv_sum,
            "pv_terminal": pv_terminal,
            "terminal_value": terminal_value,
        }

        print(f"  {scenario_name:>20s}  {g:>7.0%}  {perp_g:>7.0%}  {wacc:>7.0%}"
              f"  {intrinsic_value:>9.2f} 元")

    conservative = valuations["保守 (Conservative)"]["intrinsic_value"]
    neutral      = valuations["中性 (Neutral)"]["intrinsic_value"]
    optimistic   = valuations["乐观 (Optimistic)"]["intrinsic_value"]

    print(f"\n  ── 每股内在价值估值区间 ──")
    print(f"  🔴 保守估值: {conservative:.2f} 元")
    print(f"  🟡 中性估值: {neutral:.2f} 元")
    print(f"  🟢 乐观估值: {optimistic:.2f} 元")
    print(f"  📏 估值区间: [{conservative:.2f}, {optimistic:.2f}] 元")

    return {
        "valuations": valuations,
        "base_fcf": base_fcf,
        "total_shares": total_shares,
        "conservative": conservative,
        "neutral": neutral,
        "optimistic": optimistic,
    }


def _get_total_shares(symbol: str, fin_abstract: pd.DataFrame,
                      daily_df: pd.DataFrame) -> float:
    """获取总股本，优先从财务数据，失败则用估算值。"""
    if fin_abstract is not None and not fin_abstract.empty:
        share_col = find_col_in(["总股本", "股份总数", "total_share"], fin_abstract)
        if share_col:
            try:
                last_row = fin_abstract.sort_values(fin_abstract.columns[0]).iloc[-1]
                total = float(last_row[share_col])
                if total > 0:
                    return total
            except Exception:
                pass

    # 平安银行总股本约 197.56 亿股
    total = 197.56e8
    print(f"  ⚠ 使用估算总股本: {total / 1e8:.0f} 亿股")
    return total
