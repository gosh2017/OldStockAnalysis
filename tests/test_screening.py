# -*- coding: utf-8 -*-
"""
基本面筛选测试：阈值与覆盖年数逻辑。
通过 monkeypatch 改 step1 模块内绑定的阈值常量（从 config import 时按值绑定），
验证 pass/fail 路径与"全部达标 + 覆盖年数"规则。
"""
from analysis import fundamental_screening
import analysis.step1_fundamental as s1


def test_demo_screening_fails(ctx, fin_abstract, daily_df, dividend_df):
    """demo ROE ~11–13% < 15%，股息率 ~1.1–1.3% < 2% → 未通过（记录预期行为）。"""
    res = fundamental_screening(ctx.symbol, fin_abstract, daily_df, dividend_df)
    assert res["screened"] is False
    assert res["roe_pass"] is False
    assert res["div_pass"] is False


def test_screening_passes_with_relaxed_thresholds(ctx, fin_abstract, daily_df,
                                                   dividend_df, monkeypatch):
    """放宽阈值至 ROE>10%、股息率>1% → demo 应通过（验证 pass 路径与阈值生效）。"""
    monkeypatch.setattr(s1, "ROE_THRESHOLD", 10.0)
    monkeypatch.setattr(s1, "DIV_THRESHOLD", 1.0)
    res = fundamental_screening(ctx.symbol, fin_abstract, daily_df, dividend_df)
    assert res["roe_pass"] is True
    assert res["div_pass"] is True
    assert res["screened"] is True


def test_coverage_gate(ctx, fin_abstract, daily_df, dividend_df, monkeypatch):
    """要求 ≥6 年但 demo 仅 5 年 → 覆盖不足 → 未通过（即便阈值放宽）。"""
    monkeypatch.setattr(s1, "ROE_THRESHOLD", 10.0)
    monkeypatch.setattr(s1, "DIV_THRESHOLD", 1.0)
    monkeypatch.setattr(s1, "MIN_COVERAGE_YEARS", 6)
    res = fundamental_screening(ctx.symbol, fin_abstract, daily_df, dividend_df)
    assert res["screened"] is False


def test_single_bad_year_fails(ctx, fin_abstract, daily_df, dividend_df, monkeypatch):
    """要求全部达标：即便放宽阈值到 10%，把 ROE 阈值抬到 13% 会出现不达标年份 → 失败。"""
    monkeypatch.setattr(s1, "ROE_THRESHOLD", 13.0)   # demo 2021 ROE 11.12 < 13
    monkeypatch.setattr(s1, "DIV_THRESHOLD", 1.0)
    res = fundamental_screening(ctx.symbol, fin_abstract, daily_df, dividend_df)
    assert res["roe_pass"] is False
    assert res["screened"] is False
