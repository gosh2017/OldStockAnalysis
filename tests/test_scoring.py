# -*- coding: utf-8 -*-
"""
综合评分测试：等级边界、类内缺失重归一化、估值随价格单调。

P3b 新增（items 5/6/8）：行业 ROE 基准、金融桶跳过 OCF、非金融桶 debt 权重、
PB-ROE 独立锚。新增测试用合成 screening dict 直接断言子分，避免依赖 demo 随机种子。
"""
import pandas as pd

from analysis import (
    fundamental_screening, dcf_valuation, market_sentiment, investment_advice,
    compute_score,
)
from analysis.scoring import _grade, _quality_subscores, _valuation_subscores


def test_grade_bands():
    assert _grade(85.0) == "A"
    assert _grade(80.0) == "A"
    assert _grade(79.9) == "B"
    assert _grade(65.0) == "B"
    assert _grade(64.9) == "C"
    assert _grade(50.0) == "C"
    assert _grade(49.9) == "D"
    assert _grade(0.0) == "D"


def _run_pipeline(ctx, fin_abstract, cashflow_df, daily_df, dividend_df, stock_indicator):
    screening = fundamental_screening(ctx.symbol, fin_abstract, daily_df, dividend_df)
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    sentiment = market_sentiment(None, 0.023, stock_indicator)
    advice = investment_advice(daily_df, dcf, sentiment, screening)
    return screening, dcf, sentiment, advice


def test_score_in_range(ctx, fin_abstract, cashflow_df, daily_df, dividend_df, stock_indicator):
    screening, dcf, sentiment, advice = _run_pipeline(
        ctx, fin_abstract, cashflow_df, daily_df, dividend_df, stock_indicator)
    score = compute_score(screening, dcf, sentiment, advice, ctx)
    assert 0 <= score["score"] <= 100
    assert score["grade"] in "ABCD"
    assert score["valuation"] >= 0
    assert "subscores" in score


def test_renormalize_missing_individual(ctx, fin_abstract, cashflow_df, daily_df, dividend_df, stock_indicator):
    """无个股 PE/PB 分位时，情绪类应只用市场 ERP（80）且不报错。"""
    screening = fundamental_screening(ctx.symbol, fin_abstract, daily_df, dividend_df)
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    sentiment = {"percentile": 80.0, "pe_percentile": None,
                 "pb_percentile": None, "sentiment": "低估"}
    advice = {"latest_price": dcf["neutral"]}
    score = compute_score(screening, dcf, sentiment, advice, ctx)
    assert score["sentiment"] is not None
    assert abs(score["sentiment"] - 80.0) < 1e-6


def test_valuation_monotonic_in_price(ctx, fin_abstract, cashflow_df, daily_df, dividend_df, stock_indicator):
    """估值安全边际随价格单调：价格越低 → 估值分越高。"""
    screening = fundamental_screening(ctx.symbol, fin_abstract, daily_df, dividend_df)
    dcf = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                        stock_indicator=stock_indicator)
    sentiment = {"percentile": 50.0, "pe_percentile": None,
                 "pb_percentile": None, "sentiment": "合理"}
    cheap = compute_score(screening, dcf, sentiment,
                         {"latest_price": dcf["conservative"] * 0.5}, ctx)
    dear = compute_score(screening, dcf, sentiment,
                         {"latest_price": dcf["neutral"] * 1.5}, ctx)
    assert cheap["valuation"] > dear["valuation"]


def test_no_fcf_zeroes_valuation(ctx, fin_abstract, cashflow_df, daily_df, dividend_df):
    """DCF 无法估值（base_fcf=None）时估值类记 0。"""
    screening = fundamental_screening(ctx.symbol, fin_abstract, daily_df, dividend_df)
    dcf = {"base_fcf": None, "neutral": None, "conservative": None, "valuations": None}
    sentiment = {"percentile": 50.0, "pe_percentile": None,
                 "pb_percentile": None, "sentiment": "合理"}
    score = compute_score(screening, dcf, sentiment, {"latest_price": 10.0}, ctx)
    assert score["valuation"] == 0.0


# -- P3b 新增：行业化质量子分 + PB-ROE 独立锚（items 5, 6, 8）---------------

def _screening_with(roe=(12, 12, 12, 12, 12), div=(2, 2, 2, 2, 2),
                    debt=(60, 60, 60, 60, 60), ocf=(1.0, 1.0, 1.0, 1.0, 1.0)):
    """合成 screening 结果（确定性指标），直接断言子分，避免依赖 demo 随机种子。"""
    table = pd.DataFrame({
        "年份": [2021, 2022, 2023, 2024, 2025],
        "ROE(%)": list(roe),
        "股息率(%)": list(div),
        "资产负债率(%)": list(debt),
        "经营现金流/净利润": list(ocf),
    })
    return {"screened": True, "table": table, "roe_pass": True, "div_pass": True}


def test_industry_roe_benchmark():
    """item 5：同 ROE 水平，银行基准 11 < 成长基准 15 → 银行 ROE 子分更高。"""
    scr = _screening_with(roe=(12,) * 5)
    bank = _quality_subscores(scr, "银行")
    growth = _quality_subscores(scr, "成长")
    assert bank["roe"] == 100.0          # 12/11*100 → clip 100
    assert growth["roe"] < 100.0         # 12/15*100 = 80
    assert bank["roe"] > growth["roe"]


def test_ocf_skip_financial():
    """item 6：金融桶（银行）跳过 OCF 子分 → ocf_quality 恒 None；非金融桶会算。"""
    scr = _screening_with(ocf=(0.2,) * 5)     # 较差 OCF
    bank = _quality_subscores(scr, "银行")
    other = _quality_subscores(scr, "其他")
    assert bank["ocf_quality"] is None
    assert other["ocf_quality"] is not None


def test_debt_weight_nonfinancial():
    """item 6：非金融桶 debt 权重 0.20 生效 → 低负债质量分高于高负债。"""
    low = _screening_with(debt=(30,) * 5)
    high = _screening_with(debt=(90,) * 5)
    # dcf 无 bucket 键 → 默认"其他"（debt 权重 0.20）；base_fcf None → 估值 0，不影响质量
    low_s = compute_score(low, {"base_fcf": None}, {"percentile": 50.0},
                          {"latest_price": 10.0})
    high_s = compute_score(high, {"base_fcf": None}, {"percentile": 50.0},
                           {"latest_price": 10.0})
    assert low_s["quality"] > high_s["quality"]


def test_pb_roe_subscore():
    """item 8：实际 PB 低于公允 PB（ROE/15）→ pb_roe 高分。"""
    scr = _screening_with(roe=(15,) * 5)          # roe_mean=15 → implied_pb=1.0
    dcf = {"base_fcf": 1e9, "neutral": 20.0, "conservative": 15.0}
    sentiment = {"current_pb": 0.5}               # 实际 0.5 < 公允 1.0
    subs = _valuation_subscores(dcf, {"latest_price": 10.0}, sentiment, scr)
    assert subs["pb_roe"] is not None
    assert subs["pb_roe"] == 100.0                # (1.0-0.5)/0.5*50+50 = 100


def test_pb_roe_missing_when_no_pb():
    """item 8：current_pb 缺失 → pb_roe=None，margin 子分仍正常。"""
    scr = _screening_with(roe=(15,) * 5)
    dcf = {"base_fcf": 1e9, "neutral": 20.0, "conservative": 15.0}
    subs = _valuation_subscores(dcf, {"latest_price": 10.0}, {}, scr)
    assert subs["pb_roe"] is None
    assert subs["margin_neutral"] is not None


# -- P4 新增：完整性置信度（item 11）-----------------------------------------

def _screening_full_real():
    """合成全数据 screening：5 年 ROE 全有 + 分红来源全 real。"""
    table = pd.DataFrame({
        "年份": [2021, 2022, 2023, 2024, 2025],
        "ROE(%)": [12.0] * 5,
        "股息率(%)": [2.0] * 5,
        "资产负债率(%)": [60.0] * 5,
        "经营现金流/净利润": [1.0] * 5,
        "分红来源": ["real"] * 5,
    })
    return {"screened": True, "table": table, "roe_pass": True, "div_pass": True}


def test_completeness_tag(ctx, fin_abstract, cashflow_df, daily_df, dividend_df, stock_indicator):
    """item 11：demo 全数据 → 完整度"高"；DCF 失效 + erp synthetic → 下移。"""
    # 全数据路径：覆盖 100% + DCF 可估值 + 股息全 real + erp(real/synthetic)
    screening, dcf, sentiment, advice = _run_pipeline(
        ctx, fin_abstract, cashflow_df, daily_df, dividend_df, stock_indicator)
    full = compute_score(screening, dcf, sentiment, advice, ctx)
    assert full["completeness_tag"] == "高"
    assert 0 <= full["completeness"] <= 100

    # 降级路径：DCF 无 base_fcf + erp synthetic → 完整度显著下移
    scr = _screening_full_real()
    degraded = compute_score(
        scr, {"base_fcf": None, "bucket": "其他"},
        {"percentile": 50.0, "erp_source": "synthetic"},
        {"latest_price": 10.0})
    assert degraded["completeness"] < full["completeness"]
    assert degraded["completeness_tag"] != "高"


def test_completeness_signal_isolation():
    """item 11：四信号各自贡献正分；erp_source 缺失按 0 计不报错。"""
    scr = _screening_full_real()
    # erp_source 缺失（旧式 sentiment dict）→ erp 信号 0，其余仍计
    s_no_erp = compute_score(
        scr, {"base_fcf": 1e9, "neutral": 20.0, "bucket": "其他"},
        {"percentile": 50.0}, {"latest_price": 10.0})
    s_real_erp = compute_score(
        scr, {"base_fcf": 1e9, "neutral": 20.0, "bucket": "其他"},
        {"percentile": 50.0, "erp_source": "real"}, {"latest_price": 10.0})
    assert s_real_erp["completeness"] > s_no_erp["completeness"]
