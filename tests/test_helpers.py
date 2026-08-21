# -*- coding: utf-8 -*-
"""
通用工具函数测试（item C2/C3）：
  - pick_annual_row：年报优先（月份==12）选取、季报回退、空/不可解析守卫
  - estimate_dividend_yield：行业分红率去硬编码（bucket payout_ratio）、
    隐含市值口径（market_pe）按桶差异化、向后兼容
"""
import pandas as pd

from utils import pick_annual_row, estimate_dividend_yield


# -- pick_annual_row（item C3）--------------------------------------------

def test_pick_annual_row_prefers_december():
    """同年有季报(09-30)与年报(12-31) → 取 12-31 行，is_annual=True。"""
    df = pd.DataFrame({
        "报告期": ["2025-09-30", "2025-12-31"],
        "净利润": [100, 120],
    })
    row, is_annual = pick_annual_row(df, "报告期")
    assert is_annual is True
    assert pd.Timestamp(row["报告期"]).month == 12
    assert float(row["净利润"]) == 120.0


def test_pick_annual_row_picks_max_date_when_no_december():
    """该年仅季报（无 12 月行）→ 取年内最大日期行，is_annual=False（透明标注）。"""
    df = pd.DataFrame({
        "报告期": ["2025-03-31", "2025-06-30", "2025-09-30"],
        "净利润": [10, 20, 30],
    })
    row, is_annual = pick_annual_row(df, "报告期")
    assert is_annual is False
    assert pd.Timestamp(row["报告期"]) == pd.Timestamp("2025-09-30")
    assert float(row["净利润"]) == 30.0


def test_pick_annual_row_multiple_december_takes_latest():
    """同年多笔 12 月行（异常但需稳健）→ 取其中日期最大者，is_annual=True。"""
    df = pd.DataFrame({
        "报告期": ["2025-12-15", "2025-12-31"],
        "净利润": [110, 130],
    })
    row, is_annual = pick_annual_row(df, "报告期")
    assert is_annual is True
    assert pd.Timestamp(row["报告期"]) == pd.Timestamp("2025-12-31")


def test_pick_annual_row_empty_returns_none():
    """空 DataFrame → (None, False)。"""
    row, is_annual = pick_annual_row(pd.DataFrame(), "报告期")
    assert row is None and is_annual is False


def test_pick_annual_row_none_or_missing_date_col():
    """year_df 为 None / date_col 为空 → (None, False)。"""
    row, is_annual = pick_annual_row(None, "报告期")
    assert row is None and is_annual is False
    df = pd.DataFrame({"净利润": [10]})
    row, is_annual = pick_annual_row(df, "")
    assert row is None and is_annual is False


def test_pick_annual_row_unparseable_dates_returns_none():
    """date_col 全为不可解析值（None/NaN）→ dropna 后为空 → (None, False)。"""
    df = pd.DataFrame({"报告期": [None, None], "净利润": [10, 20]})
    row, is_annual = pick_annual_row(df, "报告期")
    assert row is None and is_annual is False


# -- estimate_dividend_yield：行业分红率去硬编码（item C2）-----------------

def _row_with(roe=None, np_val=None):
    """构造含 ROE / 净利润列的行 Series（缺失项不建列）。"""
    data = {}
    if roe is not None:
        data["加权净资产收益率"] = roe
    if np_val is not None:
        data["归属于上市公司股东的净利润"] = np_val
    data["归属母公司股东权益"] = 1000.0
    return pd.Series(data)


def test_estimated_roe_payout_differentiated_by_bucket():
    """方案2：ROE×行业分红率。成长桶 0.15、消费桶 0.40 → 消费股息率高于成长
    （item C2：去硬编码 0.30，差异化反映行业再投资/分红结构）。"""
    row = _row_with(roe=15.0)        # ROE=15%
    # 成长桶
    val_g, src_g = estimate_dividend_yield(
        2025, row, "归属母公司股东权益", None, pd.DataFrame(),
        "加权净资产收益率", "归属于上市公司股东的净利润", bucket="成长")
    # 消费桶
    val_c, src_c = estimate_dividend_yield(
        2025, row, "归属母公司股东权益", None, pd.DataFrame(),
        "加权净资产收益率", "归属于上市公司股东的净利润", bucket="消费")
    assert src_g == "estimated_roe" and src_c == "estimated_roe"
    # 15% × 0.15 = 2.25；15% × 0.40 = 6.0
    assert abs(val_g - 2.25) < 1e-9
    assert abs(val_c - 6.0) < 1e-9
    assert val_c > val_g


def test_estimated_np_uses_market_pe_implied_mv():
    """方案3：隐含市值 = 净利润 × 市场PE。market_pe=30 → 股息率 = 分红率/PE×100。
    其他桶 payout 0.30、PE=30 → 0.30/30×100 = 1.0%（item C2：原 np×6 系统性偏高）。"""
    row = _row_with(np_val=100.0)    # 无 ROE → 跳过方案2，进方案3
    val, src = estimate_dividend_yield(
        2025, row, None, None, pd.DataFrame(),
        None, "归属于上市公司股东的净利润", bucket="其他", market_pe=30.0)
    assert src == "estimated_np"
    assert abs(val - 1.0) < 1e-9


def test_estimated_np_market_pe_none_falls_back_to_20():
    """market_pe 缺失 → 回退 20（向后兼容）。其他桶 → 0.30/20×100 = 1.5%。"""
    row = _row_with(np_val=100.0)
    val, src = estimate_dividend_yield(
        2025, row, None, None, pd.DataFrame(),
        None, "归属于上市公司股东的净利润", bucket="其他", market_pe=None)
    assert src == "estimated_np"
    assert abs(val - 1.5) < 1e-9


def test_estimated_np_payout_differentiated_by_bucket():
    """方案3 同样按桶差异化：成长桶(0.15, PE=30) → 0.5%；消费桶(0.40, PE=30) → 1.33%。"""
    row = _row_with(np_val=100.0)
    val_g, _ = estimate_dividend_yield(
        2025, row, None, None, pd.DataFrame(),
        None, "归属于上市公司股东的净利润", bucket="成长", market_pe=30.0)
    val_c, _ = estimate_dividend_yield(
        2025, row, None, None, pd.DataFrame(),
        None, "归属于上市公司股东的净利润", bucket="消费", market_pe=30.0)
    assert abs(val_g - 0.5) < 1e-9          # 0.15/30×100
    assert abs(val_c - 4.0 / 3) < 1e-9       # 0.40/30×100 ≈ 1.333
    assert val_c > val_g


def test_estimate_dividend_yield_backward_compatible_defaults():
    """不传 bucket/market_pe → '其他'桶(0.30) + PE 回退 20（向后兼容旧调用）。"""
    row = _row_with(np_val=100.0)
    val, src = estimate_dividend_yield(
        2025, row, None, None, pd.DataFrame(),
        None, "归属于上市公司股东的净利润")
    assert src == "estimated_np"
    assert abs(val - 1.5) < 1e-9            # 0.30/20×100
