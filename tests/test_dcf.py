# -*- coding: utf-8 -*-
"""
DCF 估值测试：结构正确性、三情景单调、公式复核（用结果的 base_fcf/total_shares
重新计算中性情景，应与函数输出一致）。
"""
from analysis import dcf_valuation
from config import SCENARIOS

SCN_ORDER = ["保守 (Conservative)", "中性 (Neutral)", "乐观 (Optimistic)"]


def test_dcf_structure(ctx, fin_abstract, cashflow_df, daily_df):
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df)
    assert dcf["valuations"] is not None
    assert dcf["base_fcf"] > 0
    assert dcf["total_shares"] > 0
    for s in SCN_ORDER:
        assert s in dcf["valuations"]
        assert dcf["valuations"][s]["intrinsic_value"] > 0


def test_dcf_monotonic_scenarios(ctx, fin_abstract, cashflow_df, daily_df):
    """保守 < 中性 < 乐观（增长率/WACC 决定）。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df)
    assert dcf["conservative"] < dcf["neutral"] < dcf["optimistic"]


def test_dcf_formula_recheck(ctx, fin_abstract, cashflow_df, daily_df):
    """复核公式：用结果自带的 base_fcf / total_shares 复算中性情景，应一致。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df)
    base, shares = dcf["base_fcf"], dcf["total_shares"]
    p = SCENARIOS["中性 (Neutral)"]
    g, perp, wacc = p["growth"], p["perpetual"], p["wacc"]
    pv_sum = sum(base * (1 + g) ** t / (1 + wacc) ** t for t in range(1, 6))
    terminal_value = base * (1 + g) ** 5 * (1 + perp) / (wacc - perp)
    pv_terminal = terminal_value / (1 + wacc) ** 5
    expected = (pv_sum + pv_terminal) / shares
    assert abs(expected - dcf["neutral"]) < 1e-6
