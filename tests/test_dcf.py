# -*- coding: utf-8 -*-
"""
DCF 估值测试（成熟期「老登股」口径）：
  结构正确性、三情景单调（破产清算<保守<中性）、FCF 用 70% capex、
  公式复核（用 base_fcf/total_shares 复算中性应 = neutral_raw）、
  合理估值上限锚定（min(中性DCF, PE中位×EPS)）、破产清算 D&A 口径、
  D&A 缺失回退+钳位、WACC 固定 9.5%、显性增长率=永续增长率。
"""
from analysis import dcf_valuation
from config import SCENARIOS

SCN_ORDER = ["保守 (Conservative)", "中性 (Neutral)", "破产清算 (Liquidation)"]


def test_dcf_structure(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    assert dcf["valuations"] is not None
    assert dcf["base_fcf"] > 0
    assert dcf["total_shares"] > 0
    for s in SCN_ORDER:
        assert s in dcf["valuations"]
        assert dcf["valuations"][s]["intrinsic_value"] > 0
    # 新增字段
    assert "fair_value_ceiling" in dcf
    assert "base_fcf_liquidation" in dcf
    assert dcf["da_available"] is True   # demo cashflow 含 D&A 列


def test_dcf_monotonic_scenarios(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """破产清算 < 保守 < 中性（D&A 口径下严格成立）。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    assert dcf["liquidation"] < dcf["conservative"]
    assert dcf["conservative"] < dcf["neutral_raw"]   # 原始中性 DCF（未封顶）


def test_dcf_formula_recheck(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """复核公式：用 base_fcf / total_shares 复算中性情景，应与 neutral_raw 一致。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    base, shares = dcf["base_fcf"], dcf["total_shares"]
    p = SCENARIOS["中性 (Neutral)"]
    g, perp, wacc = p["growth"], p["perpetual"], p["wacc"]
    pv_sum = sum(base * (1 + g) ** t / (1 + wacc) ** t for t in range(1, 6))
    terminal_value = base * (1 + g) ** 5 * (1 + perp) / (wacc - perp)
    pv_terminal = terminal_value / (1 + wacc) ** 5
    expected = (pv_sum + pv_terminal) / shares
    assert abs(expected - dcf["neutral_raw"]) < 1e-6


def test_dcf_fcf_uses_70pct_capex(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """FCF = OCF − CAPEX×0.7：base_fcf 应 < OCF 均值（扣减 70% capex）。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    ocf_mean = fin_abstract["经营活动产生的现金流量净额"].astype(float).mean()
    assert dcf["base_fcf"] < ocf_mean


def test_liquidation_uses_da(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """破产清算 base = FCF − D&A，应 < base_fcf（D&A>0）。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    assert dcf["da_available"] is True
    assert dcf["base_fcf_liquidation"] < dcf["base_fcf"]


def test_fair_value_ceiling_capped(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """合理估值上限 = min(中性DCF, PE中位×EPS)。demo 中 DCF 中性 < PE锚定，故上限=中性。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    assert dcf["fair_value_ceiling"] is not None
    assert dcf["pe_median_5y"] is not None
    assert dcf["current_eps"] is not None
    pe_anchor = dcf["pe_median_5y"] * dcf["current_eps"]
    assert abs(dcf["fair_value_ceiling"] - min(dcf["neutral_raw"], pe_anchor)) < 1e-6
    # demo: DCF 为约束（neutral_raw < pe_anchor）
    assert abs(dcf["fair_value_ceiling"] - dcf["neutral_raw"]) < 1e-6
    # dcf["neutral"]（封顶值，下游使用）== ceiling
    assert abs(dcf["neutral"] - dcf["fair_value_ceiling"]) < 1e-6


def test_fair_value_ceiling_binds_when_pe_low(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """PE 锚定 < 中性DCF 时，上限 = PE锚定（cap 生效）。"""
    si = stock_indicator.copy()
    si["市盈率PE"] = 0.5   # 极低 PE → pe_anchor = 0.5 × EPS 极小
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=si)
    assert dcf["pe_anchor_value"] is not None
    assert dcf["pe_anchor_value"] < dcf["neutral_raw"]
    assert abs(dcf["fair_value_ceiling"] - dcf["pe_anchor_value"]) < 1e-6
    assert dcf["fair_value_ceiling"] < dcf["neutral_raw"]


def test_liquidation_fallback_no_da(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """D&A 不可得时回退归母净利润，且钳位保证 liquidation <= conservative。"""
    cf_no_da = cashflow_df.drop(
        columns=[c for c in cashflow_df.columns if "折旧" in str(c) or "摊销" in str(c)])
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cf_no_da, daily_df,
                        stock_indicator=stock_indicator)
    assert dcf["da_available"] is False
    # 回退净利润（银行净利润 >> FCF 会超保守），钳位保证 ≤
    assert dcf["liquidation"] <= dcf["conservative"]


def test_ladder_never_exceeds_ceiling(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """低 PE 股（银行/周期）PE 锚定会压低天花板至 DCF 之下；此时保守/清算让位于
    上限，保证 破产清算 ≤ 保守 ≤ 合理估值上限（避免「底值 > 上限」把建议搞反）。"""
    # 适中低 PE：使 pe_anchor 落在清算与保守之间，触发上限生效且阶梯不塌缩
    si = stock_indicator.copy()
    si["市盈率PE"] = 1.3
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=si)
    assert dcf["fair_value_ceiling"] < dcf["neutral_raw"]          # 锚定生效
    assert dcf["liquidation"] <= dcf["conservative"] + 1e-9
    assert dcf["conservative"] <= dcf["fair_value_ceiling"] + 1e-9
    assert dcf["liquidation"] <= dcf["fair_value_ceiling"] + 1e-9

    # 极低 PE：上限远低于 DCF，三档被钳到天花板
    si["市盈率PE"] = 0.5
    dcf2 = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df, stock_indicator=si)
    assert dcf2["conservative"] <= dcf2["fair_value_ceiling"] + 1e-9
    assert dcf2["liquidation"] <= dcf2["fair_value_ceiling"] + 1e-9


def test_wacc_fixed(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """三情景 WACC 均为 0.095。"""
    for name, p in SCENARIOS.items():
        assert abs(p["wacc"] - 0.095) < 1e-9


def test_growth_equals_perpetual(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """显性增长率 = 永续增长率（成熟股稳态口径）。"""
    for name, p in SCENARIOS.items():
        assert abs(p["growth"] - p["perpetual"]) < 1e-9
