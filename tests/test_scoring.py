# -*- coding: utf-8 -*-
"""
综合评分测试：等级边界、类内缺失重归一化、估值随价格单调。
"""
from analysis import (
    fundamental_screening, dcf_valuation, market_sentiment, investment_advice,
    compute_score,
)
from analysis.scoring import _grade


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
