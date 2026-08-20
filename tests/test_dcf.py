# -*- coding: utf-8 -*-
"""
DCF 估值测试（成熟期「老登股」口径 + 行业画像）：
  结构正确性、三情景单调（破产清算<保守<中性）、FCF 用 70% capex、
  公式复核（用 scenario_params 复算中性应 = neutral_raw）、
  合理估值上限锚定（min(中性DCF, PE中位×EPS)）、破产清算 D&A 口径、
  D&A 缺失回退+钳位、行业画像决定 WACC/永续/EPS 算法/总股本来源、
  显性增长率由净利 CAGR 推导、基期 FCF 加权含负值、总股本无兜底 None 守卫。
"""
import numpy as np

import analysis.step2_dcf as dcf_mod
from analysis import dcf_valuation
from analysis.step2_dcf import derive_explicit_growth
from config import INDUSTRY_PROFILES

SCN_ORDER = ["保守 (Conservative)", "中性 (Neutral)", "破产清算 (Liquidation)"]


# -- 结构与公式（"其他"桶零回归路径）------------------------------------

def test_dcf_structure(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """不传 industry_info → "其他"桶（零回归路径），结构字段齐全。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    assert dcf["valuations"] is not None
    assert dcf["base_fcf"] > 0
    assert dcf["total_shares"] > 0
    for s in SCN_ORDER:
        assert s in dcf["valuations"]
        assert dcf["valuations"][s]["intrinsic_value"] > 0
    # 既有字段
    assert "fair_value_ceiling" in dcf
    assert "base_fcf_liquidation" in dcf
    assert dcf["da_available"] is True   # demo cashflow 含 D&A 列
    # item A1：demo capex 齐全 → 无年份走兜底 → capex_estimated=False
    assert dcf["capex_estimated"] is False
    # P2 新增字段（"其他"桶口径）
    assert dcf["bucket"] == "其他"
    assert dcf["eps_method"] == "normalized"
    assert dcf["has_negative_fcf"] is False        # demo OCF 恒正
    assert "scenario_params" in dcf
    assert dcf["explicit_growth"] is not None      # demo 5 年净利 CAGR 可算


def test_dcf_monotonic_scenarios(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """破产清算 < 保守 < 中性（D&A 口径下严格成立）。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    assert dcf["liquidation"] < dcf["conservative"]
    assert dcf["conservative"] < dcf["neutral_raw"]   # 原始中性 DCF（未封顶）


def test_dcf_formula_recheck(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """复核公式：用 scenario_params 复算中性情景，应与 neutral_raw 一致。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    base, shares = dcf["base_fcf"], dcf["total_shares"]
    # 用 dcf 内随桶构造的参数（含 CAGR 推导的显性增长），而非全局 SCENARIOS
    p = dcf["scenario_params"]["中性 (Neutral)"]
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


# -- 合理估值上限锚定 ---------------------------------------------------

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
    """item A5：D&A 不可得时按 FCF×0.5 估算清算口径（不再回退归母净利润），
    自然保证 liquidation <= conservative（不依赖事后钳位）。"""
    cf_no_da = cashflow_df.drop(
        columns=[c for c in cashflow_df.columns if "折旧" in str(c) or "摊销" in str(c)])
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cf_no_da, daily_df,
                        stock_indicator=stock_indicator)
    assert dcf["da_available"] is False
    # FCF×0.5 口径：清算基期 = 基期 FCF × 0.5
    assert abs(dcf["base_fcf_liquidation"] - dcf["base_fcf"] * 0.5) < 1e-6
    # 自然 ≤ 保守（0.5×FCF < FCF，保守情景用 base_fcf 且 0 增长，无需钳位）
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


# -- P2 新增：行业画像 / 总股本 / 加权 FCF / 席勒 EPS ---------------------

def test_wacc_matches_industry_profile(ctx, fin_abstract, cashflow_df, daily_df,
                                       stock_indicator, bucket):
    """三情景 WACC == 行业画像 wacc（item 1：DCF 参数行业化）。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator,
                        industry_info={"bucket": bucket})
    expected_wacc = INDUSTRY_PROFILES[bucket]["wacc"]
    for name, p in dcf["scenario_params"].items():
        assert abs(p["wacc"] - expected_wacc) < 1e-9
    assert dcf["bucket"] == bucket


def test_neutral_growth_from_cagr(ctx, fin_abstract, cashflow_df, daily_df,
                                  stock_indicator, bucket):
    """中性显性增长由净利 CAGR 推导（非 None）；保守/破产清算 growth=0；
    永续取行业画像 perpetual（item 1）。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator,
                        industry_info={"bucket": bucket})
    sp = dcf["scenario_params"]
    expected_perp = INDUSTRY_PROFILES[bucket]["perpetual"]
    # 保守 / 破产清算：恒 0 增长 0 永续
    for name in ("保守 (Conservative)", "破产清算 (Liquidation)"):
        assert abs(sp[name]["growth"]) < 1e-9
        assert abs(sp[name]["perpetual"]) < 1e-9
    # 中性：显性增长 = CAGR 推导值（demo 5 年净利可算 → 非 None）；永续 = 行业画像
    assert dcf["explicit_growth"] is not None
    assert abs(sp["中性 (Neutral)"]["growth"] - dcf["explicit_growth"]) < 1e-9
    assert abs(sp["中性 (Neutral)"]["perpetual"] - expected_perp) < 1e-9


def test_total_shares_none_guard(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """总股本三源全失败 → None（不兜底）；valuations None；base_fcf 仍可算（item 2）。"""
    # 去掉 fin_abstract 的总股本列 + 不传 industry_info → 三源全失
    fa = fin_abstract.drop(columns=[c for c in fin_abstract.columns if "总股本" in str(c)])
    dcf = dcf_valuation(ctx.symbol, fa, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    assert dcf["total_shares"] is None
    assert dcf["valuations"] is None
    assert dcf["bucket"] == "其他"
    # base_fcf 不依赖总股本，仍可算
    assert dcf["base_fcf"] is not None and dcf["base_fcf"] > 0


def test_industry_info_shares_source(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """industry_info.total_shares 优先于 fin_abstract（item 2：行业信息第 1 源 f84）。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator,
                        industry_info={"bucket": "其他", "total_shares": 1e10})
    # 1e10 != fin_abstract 的 197.56e8(=1.9756e10)，证明取自 industry_info
    assert abs(dcf["total_shares"] - 1e10) < 1e-3
    assert abs(dcf["total_shares"] - 197.56e8) > 1e-3


def test_negative_fcf_signal(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """某年 OCF 置负 → has_negative_fcf True，base_fcf 加权含负值仍有限且更低（item 3）。"""
    fa = fin_abstract.copy()
    # 把 2023 年 OCF 置为大负值
    mask = fa["报告期"].dt.year == 2023
    fa.loc[mask, "经营活动产生的现金流量净额"] = -500e8

    dcf_neg = dcf_valuation(ctx.symbol, fa, cashflow_df, daily_df,
                            stock_indicator=stock_indicator)
    dcf_orig = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                             stock_indicator=stock_indicator)
    assert dcf_neg["has_negative_fcf"] is True
    assert dcf_orig["has_negative_fcf"] is False
    # 加权均值含负值后仍有限且更低
    assert np.isfinite(dcf_neg["base_fcf"])
    assert dcf_neg["base_fcf"] < dcf_orig["base_fcf"]


def test_shiller_eps_for_cyclic(ctx, fin_abstract, cashflow_df, daily_df, stock_indicator):
    """周期桶 eps_method=shiller（item 4）；current_eps 平滑后仍为正。"""
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator,
                        industry_info={"bucket": "周期"})
    assert dcf["eps_method"] == "shiller"
    assert dcf["bucket"] == "周期"
    assert dcf["current_eps"] is not None and dcf["current_eps"] > 0


# -- item A1：capex 行业化兜底 ------------------------------------------

def _drop_capex(cf):
    """去掉资本性支出列，模拟 capex 全缺失。"""
    return cf.drop(columns=[c for c in cf.columns
                            if "购建" in str(c) or "固定资产" in str(c)])


def _ocf_weighted_mean(fin_abstract):
    """从财务摘要提取各年 OCF，按 step2_dcf 同款权重算加权均值（原始元）。"""
    fa = fin_abstract.copy()
    fa["年份"] = fa["报告期"].dt.year
    ocf_vals = []
    for y in range(2021, 2026):
        row = fa[fa["年份"] == y].sort_values("报告期").iloc[-1]
        ocf_vals.append(float(row["经营活动产生的现金流量净额"]))
    w = np.linspace(0.5, 1.0, len(ocf_vals))
    return float(np.average(np.array(ocf_vals), weights=w))


def test_capex_fallback_uses_industry_ratio(ctx, fin_abstract, cashflow_df, daily_df,
                                            stock_indicator):
    """item A1：capex 缺失年份按行业 capex_ratio 兜底（周期桶 0.45），
    capex_estimated=True，且 base_fcf = OCF×(1 − 0.45×0.7)。"""
    cf_no_capex = _drop_capex(cashflow_df)
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cf_no_capex, daily_df,
                        stock_indicator=stock_indicator,
                        industry_info={"bucket": "周期"})
    assert dcf["capex_estimated"] is True
    # 周期桶 0.45：capex = OCF×0.45 → fcf = OCF − OCF×0.45×0.7 = OCF×0.685
    ocf_wmean = _ocf_weighted_mean(fin_abstract)
    expected_base = ocf_wmean * (1 - INDUSTRY_PROFILES["周期"]["capex_ratio"] * 0.7)
    assert abs(dcf["base_fcf"] - expected_base) < 1.0       # 1 元容差


def test_capex_ratio_differentiates_fcf(ctx, fin_abstract, cashflow_df, daily_df,
                                        stock_indicator):
    """item A1：行业差异化使重资产周期桶 FCF 低于轻资产消费桶（修正固定 0.20 的虚高/虚低）。"""
    dcf_cycle = dcf_valuation(ctx.symbol, fin_abstract, _drop_capex(cashflow_df), daily_df,
                              stock_indicator=stock_indicator,
                              industry_info={"bucket": "周期"})    # 0.45
    dcf_cons  = dcf_valuation(ctx.symbol, fin_abstract, _drop_capex(cashflow_df), daily_df,
                              stock_indicator=stock_indicator,
                              industry_info={"bucket": "消费"})    # 0.15
    # 0.45 扣得多 → 周期 base_fcf < 消费 base_fcf（同样 OCF 下）
    assert dcf_cycle["base_fcf"] < dcf_cons["base_fcf"]


# -- item A2：显性增长率最小二乘 CAGR -----------------------------------

def test_derive_explicit_growth_least_squares():
    """item A2：[10,8,12,14,16] 最小二乘 CAGR 落在 clip 内且方向合理（正增长）。
    两点法 CAGR≈12.5%、最小二乘≈16.2%，均超 clip 上限 0.12 → 裁剪到 0.12。"""
    vals = {2021: 10.0, 2022: 8.0, 2023: 12.0, 2024: 14.0, 2025: 16.0}
    g = derive_explicit_growth(vals)
    assert g is not None
    lo, hi = INDUSTRY_PROFILES["其他"]["growth_clip"]   # == DCF_GROWTH_CAGR_CLIP
    assert lo <= g <= hi            # 落在 clip 内
    assert g > 0                    # 方向合理：序列整体上行 → 正增长


def test_derive_explicit_growth_declining_negative():
    """item A2：下行序列 → 负增长方向，clip 到下限 -5%。"""
    vals = {2021: 16.0, 2022: 14.0, 2023: 12.0, 2024: 10.0, 2025: 8.0}
    g = derive_explicit_growth(vals)
    assert g is not None
    lo, hi = INDUSTRY_PROFILES["其他"]["growth_clip"]
    assert lo <= g <= hi
    assert g < 0                    # 方向合理：序列整体下行 → 负增长


def test_derive_explicit_growth_insufficient_returns_none():
    """item A2：不足 3 个数据点 → None（回退行业永续）。"""
    assert derive_explicit_growth({2021: 10.0, 2022: 12.0}) is None
    assert derive_explicit_growth({}) is None
    assert derive_explicit_growth({2021: 10.0}) is None


def test_derive_explicit_growth_nonpositive_returns_none():
    """item A2：任一净利润 ≤ 0（log 无定义）→ None（含原首末非正回退语义）。"""
    # 首点为 0
    assert derive_explicit_growth({2021: 0.0, 2022: 12.0, 2023: 14.0}) is None
    # 末点为负
    assert derive_explicit_growth({2021: 10.0, 2022: 12.0, 2023: -5.0}) is None
    # 中间为负（原两点法不触发，最小二乘因 log 无定义而回退）
    assert derive_explicit_growth({2021: 10.0, 2022: -8.0, 2023: 14.0, 2024: 16.0}) is None


# -- item A4：wacc≤永续 防御性 guard ------------------------------------

def test_wacc_le_perp_guard_skips_scenario(ctx, fin_abstract, cashflow_df, daily_df,
                                           stock_indicator, monkeypatch):
    """item A4：wacc≤永续的情景被 guard 跳过，不产生负/无穷内在价值、不抛异常。
    post-loop 安全兜底把 None 转为非负有限值，供下游 investment_advice（无 None 处理）使用。"""
    def divergent_scenarios(_bucket):
        return {
            "保守 (Conservative)":  {"growth": 0.000, "perpetual": 0.000, "wacc": 0.095},
            "中性 (Neutral)":        {"growth": 0.30, "perpetual": 0.30, "wacc": 0.095},  # perp>wacc
            "破产清算 (Liquidation)": {"growth": 0.000, "perpetual": 0.000, "wacc": 0.095, "liquidation": True},
        }
    monkeypatch.setattr(dcf_mod, "scenarios_for", divergent_scenarios)
    # 不抛异常
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    iv_n = dcf["valuations"]["中性 (Neutral)"]["intrinsic_value"]
    # 核心：guard 后不产生负数/无穷（None 或非负有限值）
    assert iv_n is None or (np.isfinite(iv_n) and iv_n >= 0)
    assert not (iv_n is not None and (iv_n < 0 or np.isinf(iv_n) or np.isnan(iv_n)))
    # guard 触发证据：中性被跳过后 post-loop 兜底 = 保守值（正常中性 > 保守）
    assert abs(dcf["neutral_raw"] - dcf["conservative"]) < 1e-9
    # 未触发 guard 的情景正常计算为正
    assert dcf["valuations"]["保守 (Conservative)"]["intrinsic_value"] > 0
    assert dcf["valuations"]["破产清算 (Liquidation)"]["intrinsic_value"] > 0
    # 阶梯仍单调
    assert dcf["liquidation"] <= dcf["conservative"]
