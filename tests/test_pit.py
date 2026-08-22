# -*- coding: utf-8 -*-
"""
时点数据截断层（data/pit.py）测试（提示词 D1）。

覆盖：
  - truncate_to_date：按日期列截断到 <= as_of、inclusive 边界、空 df 安全
  - filter_reports_by_pub_lag：报告期 + 披露滞后过滤（2024 年报在 as_of=2024-06-30 被排除）
  - as_of_bundle：组合截断，daily/fin/dividend/indicator 各按口径截断、industry_info 透传
"""
import numpy as np
import pandas as pd

from data.pit import truncate_to_date, filter_reports_by_pub_lag, as_of_bundle


def _daily_2020_2025() -> pd.DataFrame:
    """跨 2020–2025 的月频日线。"""
    dates = pd.date_range("2020-01-31", "2025-12-31", freq="ME")
    return pd.DataFrame({"日期": dates, "收盘": np.linspace(10.0, 15.0, len(dates))})


def _fin_2020_2025() -> pd.DataFrame:
    """跨 2020–2025 的年报财务摘要（报告期 = 各年 12-31）。"""
    years = range(2020, 2026)
    return pd.DataFrame({
        "报告期": [pd.Timestamp(f"{y}-12-31") for y in years],
        "加权净资产收益率(%)": [12.0] * len(years),
    })


def _div_2020_2025() -> pd.DataFrame:
    """跨 2020–2025 的分红记录（公告日期次年 6–8 月）。"""
    years = range(2020, 2026)
    return pd.DataFrame({
        "公告日期": [pd.Timestamp(f"{y + 1}-07-15") for y in years],
        "派息": [4.0] * len(years),
    })


def test_truncate_to_date_basic():
    """按日期列截断到 <= as_of；模糊匹配列名。"""
    df = _daily_2020_2025()
    out = truncate_to_date(df, "日期", "2024-06-30")
    assert out["日期"].max() <= pd.Timestamp("2024-06-30")
    assert out["日期"].min() >= pd.Timestamp("2020-01-31")
    # 行数 < 原表（剔除了 2024-07 及以后的月）
    assert len(out) < len(df)


def test_truncate_to_date_inclusive_boundary():
    """inclusive=True 保留 == as_of 的行。"""
    df = pd.DataFrame({"日期": pd.to_datetime(["2024-06-29", "2024-06-30", "2024-07-01"]),
                       "收盘": [1.0, 2.0, 3.0]})
    out = truncate_to_date(df, "日期", "2024-06-30")
    assert len(out) == 2  # 06-29 + 06-30
    out_excl = truncate_to_date(df, "日期", "2024-06-30", inclusive=False)
    assert len(out_excl) == 1  # 仅 06-29


def test_truncate_to_date_empty_and_none_safe():
    """空 df / None 安全返回空 df。"""
    assert truncate_to_date(pd.DataFrame(), "日期", "2024-06-30").empty
    assert truncate_to_date(None, "日期", "2024-06-30").empty


def test_truncate_to_date_fuzzy_column_match():
    """列名提示模糊匹配（传 'date' 也能命中 '日期' 列的兜底）。"""
    df = pd.DataFrame({"报告期": pd.to_datetime(["2024-01-31", "2024-06-30", "2024-12-31"]),
                       "v": [1, 2, 3]})
    out = truncate_to_date(df, "报告期", "2024-06-30")
    assert out["报告期"].max() <= pd.Timestamp("2024-06-30")
    assert len(out) == 2


def test_filter_reports_by_pub_lag_excludes_undisclosed_annual():
    """as_of=2024-06-30：2024 年报（报告期 2024-12-31 > 2024-06-30 − 120d=2024-03-02）
    被排除；2023 年报（2023-12-31 <= 2024-03-02）保留。"""
    fin = _fin_2020_2025()
    out = filter_reports_by_pub_lag(fin, "2024-06-30")
    years = pd.to_datetime(out["报告期"]).dt.year.tolist()
    assert 2024 not in years      # 2024-12-31 尚未披露 → 排除
    assert 2025 not in years      # 2025-12-31 同理排除
    assert 2023 in years         # 2023 年报已披露 → 保留
    assert 2020 in years          # 历史年报保留


def test_filter_reports_by_pub_lag_empty_safe():
    """空/None fin 安全返回空 df。"""
    assert filter_reports_by_pub_lag(pd.DataFrame(), "2024-06-30").empty
    assert filter_reports_by_pub_lag(None, "2024-06-30").empty


def test_filter_reports_by_pub_lag_lag_days_param():
    """lag_days 放宽到 30：as_of=2024-06-30 − 30d=2024-05-31，仍排除 2024 年报。"""
    fin = _fin_2020_2025()
    out = filter_reports_by_pub_lag(fin, "2024-06-30", lag_days=30)
    years = pd.to_datetime(out["报告期"]).dt.year.tolist()
    assert 2024 not in years  # 2024-12-31 > 2024-05-31


def test_as_of_bundle_truncates_all_series():
    """as_of_bundle：daily/fin/dividend/indicator 各按口径截断；industry_info 透传。"""
    cache = {
        "daily_df": _daily_2020_2025(),
        "fin_abstract": _fin_2020_2025(),
        "cashflow_df": _fin_2020_2025().rename(columns={"加权净资产收益率(%)": "折旧与摊销"}),
        "dividend_df": _div_2020_2025(),
        "stock_indicator": _daily_2020_2025().rename(columns={"收盘": "市盈率PE"}),
        "market_pe_history": _daily_2020_2025().rename(columns={"收盘": "市盈率"}),
        "bond_yield_history": pd.DataFrame({
            "日期": pd.date_range("2020-01-31", "2025-12-31", freq="ME"),
            "国债收益率": [0.025] * 72,
        }),
        "bond_yield": 0.025,
        "market_df": None,
        "industry_info": {"industry": "银行", "bucket": "银行",
                          "total_shares": 197.56e8, "source": "demo"},
    }
    as_of = "2024-06-30"
    bundle = as_of_bundle("000001", as_of, cache, demo=True)

    # daily 截断到 <= as_of
    assert bundle["daily_df"]["日期"].max() <= pd.Timestamp(as_of)
    # fin 按披露滞后过滤：不含 2024 年报
    fin_years = pd.to_datetime(bundle["fin_abstract"]["报告期"]).dt.year.tolist()
    assert 2024 not in fin_years
    assert 2023 in fin_years
    # dividend 公告日期 <= as_of（2024-07-15 的分红被排除）
    assert bundle["dividend_df"]["公告日期"].max() <= pd.Timestamp(as_of)
    # stock_indicator / market_pe_history 截断
    assert bundle["stock_indicator"]["日期"].max() <= pd.Timestamp(as_of)
    assert bundle["market_pe_history"]["日期"].max() <= pd.Timestamp(as_of)
    # industry_info 透传不截断
    assert bundle["industry_info"]["bucket"] == "银行"
    # bond_yield 取截断后国债历史末值
    assert bundle["bond_yield"] == 0.025
    # as_of 标记
    assert bundle["_as_of"] == pd.Timestamp(as_of)
    assert bundle["_demo"] is True


def test_as_of_bundle_bond_yield_from_truncated_history():
    """bond_yield 取截断后国债历史末值（截断口径，PIT 正确）。"""
    bh = pd.DataFrame({
        "日期": pd.to_datetime(["2023-12-31", "2024-03-31", "2024-12-31"]),
        "国债收益率": [0.028, 0.026, 0.020],
    })
    cache = {"bond_yield_history": bh, "bond_yield": 0.025}
    bundle = as_of_bundle("000001", "2024-06-30", cache, demo=True)
    # 截断到 <= 2024-06-30 → 末值 = 2024-03-31 的 0.026（非 2024-12-31 的 0.020）
    assert abs(bundle["bond_yield"] - 0.026) < 1e-9


def test_as_of_bundle_missing_history_falls_back_to_scalar():
    """国债历史缺失 → bond_yield 回退预取标量。"""
    cache = {"bond_yield_history": pd.DataFrame(), "bond_yield": 0.025}
    bundle = as_of_bundle("000001", "2024-06-30", cache, demo=True)
    assert bundle["bond_yield"] == 0.025
