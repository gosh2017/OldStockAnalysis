# -*- coding: utf-8 -*-
"""
基本面筛选测试：阈值、覆盖年数与中位数口径。
通过 monkeypatch 改 step1 模块内绑定的阈值常量（从 config import 时按值绑定），
验证 pass/fail 路径与"中位数达标 + ≥N 年达标 + 覆盖 ≥M 年"规则。

中位数口径（item 7）的核心改进：允许个别异常年而不误杀稳健蓝筹，
故新增 test_median_passes_with_one_bad_year（一年差仍通过）与
test_median_fails_below_threshold（中位数低于阈值仍失败）。
为避免 demo 随机种子的脆弱性，中位数测试直接用 _set_roe_by_year 写入确定 ROE。
"""
import pandas as pd

from analysis import fundamental_screening
import analysis.step1_fundamental as s1
from utils import estimate_dividend_yield, find_col_in


def _set_roe_by_year(fin_abstract, values: dict):
    """直接设置 ROE 列为确定值，按 报告期 年份映射。
    values: {year: roe_pct}。返回新 df（不污染 fixture）。
    用于中位数口径测试，避免依赖 demo 随机种子的具体取值。"""
    fa = fin_abstract.copy()
    fa["报告期"] = pd.to_datetime(fa["报告期"])
    fa["加权净资产收益率(%)"] = fa["报告期"].dt.year.map(values)
    return fa


# -- 既有行为：阈值与覆盖年数（中位数口径下仍成立）-----------------------

def test_demo_screening_fails(ctx, fin_abstract, daily_df, dividend_df):
    """demo ROE ~11–13%（中位 ~12.1）< 15%，股息率 ~1.1–1.3%（中位 ~1.2）< 2% → 未通过。"""
    res = fundamental_screening(ctx.symbol, fin_abstract, daily_df, dividend_df)
    assert res["screened"] is False
    assert res["roe_pass"] is False
    assert res["div_pass"] is False


def test_screening_passes_with_relaxed_thresholds(ctx, fin_abstract, daily_df,
                                                   dividend_df, monkeypatch):
    """放宽阈值至 ROE>10%、股息率>1% → demo 中位 12.1>10 且 5/5 年达标，
    中位 1.2>1 且 5/5 年达标 → 通过（验证 pass 路径与阈值生效）。"""
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


# -- item 7：中位数口径（允许个别异常年）---------------------------------

def test_median_passes_with_one_bad_year(ctx, fin_abstract, daily_df, dividend_df, monkeypatch):
    """中位数口径：ROE=[10,13,13,13,13]（一年差），阈值 12 → 中位 13>12 且 4 年达标（≥3）→ 通过。
    旧"全部达标"口径下此例会因 2021 的 10<12 失败——这正是中数规则要修正的误杀。"""
    fa = _set_roe_by_year(fin_abstract, {2021: 10, 2022: 13, 2023: 13, 2024: 13, 2025: 13})
    monkeypatch.setattr(s1, "ROE_THRESHOLD", 12.0)
    monkeypatch.setattr(s1, "DIV_THRESHOLD", 0.0)
    res = fundamental_screening(ctx.symbol, fa, daily_df, dividend_df)
    assert res["roe_pass"] is True
    assert res["screened"] is True


def test_median_fails_below_threshold(ctx, fin_abstract, daily_df, dividend_df, monkeypatch):
    """中位数不达标：ROE=[11,11,12,12,13] 中位 12，阈值 13 → 12<13 → 失败
    （即便有 1 年达标，中位数低于阈值仍不通过）。"""
    fa = _set_roe_by_year(fin_abstract, {2021: 11, 2022: 11, 2023: 12, 2024: 12, 2025: 13})
    monkeypatch.setattr(s1, "ROE_THRESHOLD", 13.0)
    monkeypatch.setattr(s1, "DIV_THRESHOLD", 0.0)
    res = fundamental_screening(ctx.symbol, fa, daily_df, dividend_df)
    assert res["roe_pass"] is False
    assert res["screened"] is False


# -- item 9：股息来源标注 ------------------------------------------------

def test_dividend_source_column(ctx, fin_abstract, daily_df, dividend_df):
    """结果表新增"分红来源"列；demo 有分红记录 + 年末价 → 方案1 real 路径生效。"""
    res = fundamental_screening(ctx.symbol, fin_abstract, daily_df, dividend_df)
    tbl = res["table"]
    assert "分红来源" in tbl.columns
    # 列名不含"股息"子串，避免 scoring 的 col("股息率") 误匹配
    assert "股息" not in "分红来源"
    # demo dividend_df 含"派息"列、daily_df 含年末收盘价 → 5 年全部走 real
    # （.all() 而非 .any()：全部为 real 才有资格支撑 div_pass 判定）
    assert (tbl["分红来源"] == "real").all()


def test_estimated_dividends_dont_pass_screen(ctx, fin_abstract, daily_df, monkeypatch):
    """回归：无分红记录时股息率走 estimated_roe/np 估算路径，**不参与** div_pass 判定。

    用"消费"桶（payout_ratio 0.40）把估算股息率推到 ~4.8%，远超 DIV_THRESHOLD=2%——
    旧逻辑（排除 missing 即计入）会因此通过筛选，使从未分红的次新股被误判为高股息。
    修复后估算值仅展示于表中，div_pass 恒为 False（无 real 数据 = 股息率数据不足）。
    """
    monkeypatch.setattr(s1, "DIV_THRESHOLD", 2.0)
    # 清空分红记录 → 方案1 全失，全部落入估算路径
    res = fundamental_screening(ctx.symbol, fin_abstract, daily_df,
                                 pd.DataFrame(), bucket="消费", market_pe=25.0)
    tbl = res["table"]
    # 表里仍有估算值（展示不丢）
    est = tbl.loc[tbl["分红来源"].isin(["estimated_roe", "estimated_np"]), "股息率(%)"]
    assert not est.empty
    assert est.median() > 2.0          # 估算值确实远超阈值——旧逻辑会误通过
    # 但筛选不认估算
    assert res["div_pass"] is False
    assert res["screened"] is False


def test_estimate_dividend_yield_returns_tuple(ctx, fin_abstract, daily_df, dividend_df):
    """estimate_dividend_yield 返回 (value, source) 二元组，四路径 source 正确。"""
    fa = fin_abstract
    row = fa.sort_values("报告期").iloc[-1]   # 2025 年行
    roe_col = find_col_in(["加权净资产收益率"], fa)
    np_col = find_col_in(["归属于上市公司股东的净利润"], fa)
    equity_col = find_col_in(["归属母公司股东权益"], fa)
    assert roe_col and np_col and equity_col   # demo 列名均可识别
    year = 2025

    # 方案1 real：有分红记录 + 年末价
    val, src = estimate_dividend_yield(year, row, equity_col, dividend_df, daily_df, roe_col, np_col)
    assert src == "real"
    assert val > 0

    # 方案2 estimated_roe：无分红记录，有 roe_col + equity_col
    val2, src2 = estimate_dividend_yield(year, row, equity_col, None, daily_df, roe_col, np_col)
    assert src2 == "estimated_roe"
    assert val2 >= 0

    # 方案3 estimated_np：无分红、无 roe_col，有 np_col
    val3, src3 = estimate_dividend_yield(year, row, None, None, daily_df, None, np_col)
    assert src3 == "estimated_np"
    assert val3 > 0

    # 方案4 missing：全失
    val4, src4 = estimate_dividend_yield(year, row, None, None, daily_df, None, None)
    assert src4 == "missing"
    assert val4 == 0.0
