# -*- coding: utf-8 -*-
"""Hikyuu 访问层与迁移后 fetcher 的契约测试。

本机 Hikyuu 本地库已就绪（G:/QTrading/StockData/stock.db）时，这些测试用真实
本地数据验证迁移后的契约（列名 / 单位 / 量级）——零网络。未安装 hikyuu 或
DB 不存在的环境（CI / 离线）自动 skip，不构成硬依赖，与 --demo 离线原则一致。

测真实数据故断言为量级/结构而非精确值（数据随重导变化）。
"""
import os

import pandas as pd
import pytest

hku = pytest.importorskip("hikyuu")          # 未装 hikyuu → 跳过本文件全部
from config import HIKYUU_DB_PATH
from data import (fetch_daily_data, fetch_benchmark_daily, fetch_dividend,
                  fetch_bond_yield_history, fetch_financial_abstract,
                  fetch_cashflow_detail, fetch_stock_list, hku_stock,
                  hku_total_shares, hku_industry_name, hku_weight_dividends,
                  hku_finance_records, hku_bond_yield_df, hku_pb_series)
from data.hikyuu_backend import _hku, fetch_kdata_df, hku_stock as _hku_stock
import config as C

pytestmark = pytest.mark.skipif(
    not os.path.exists(HIKYUU_DB_PATH),
    reason=f"Hikyuu 本地库 {HIKYUU_DB_PATH} 不存在（先跑 scripts/run_hikyuu_import.py）",
)

SYMBOL = "000001"          # 平安银行
MAOTAI = "600519"          # 贵州茅台


# ------------------------------------------------------------------
# backend 直接函数（纯 hku，确定性）
# ------------------------------------------------------------------
def test_hku_stock_valid():
    st = hku_stock(SYMBOL)
    assert st is not None and st.valid
    assert str(st.code) == SYMBOL


def test_hku_stock_bj_prefix():
    """北交所代码应解析成功（不抛错）；不在库则返 None（不验证具体标的）。"""
    # bj 段 920xxx；解析逻辑不抛即过
    st = hku_stock("920099")
    assert st is None or st is not None


def test_fetch_kdata_df_columns_and_close():
    df = fetch_kdata_df(SYMBOL, "20240101", "20241231", index=False, recover="FORWARD")
    assert not df.empty
    assert set(["日期", "开盘", "收盘", "最高", "最低"]).issubset(df.columns)
    assert df["收盘"].iloc[-1] > 0
    # 平安银行 2024 收盘 ~10-13 元
    assert 5 < df["收盘"].iloc[-1] < 25
    assert df["日期"].is_monotonic_increasing


def test_fetch_kdata_df_index_no_recover():
    df = fetch_kdata_df("000300", "20240101", "20241231", index=True, recover="NO_RECOVER")
    assert not df.empty
    assert set(["日期", "收盘"]).issubset(df.columns)
    # 沪深300 2024 ~3000-4500
    assert 1500 < df["收盘"].iloc[-1] < 6000


def test_hku_total_shares_unit_shares():
    """总股本单位为「股」（weight.total_count 万股 ×1e4）。"""
    st = hku_stock(SYMBOL)
    ts = hku_total_shares(st)
    assert ts is not None and ts > 0
    # 平安银行 ~1.94e10 股（194 亿股）
    assert 1e10 < ts < 3e10


def test_hku_industry_name_or_none():
    """行业板块未导入时返 None（不抛）；导入后返 str。"""
    st = hku_stock(SYMBOL)
    ind = hku_industry_name(st)
    assert ind is None or isinstance(ind, str)


def test_hku_weight_dividends_paixi_per10():
    """分红 bonus=每10股红利（元/10股），对齐 akshare「派息」列口径。"""
    st = hku_stock(SYMBOL)
    rows = hku_weight_dividends(st)
    assert len(rows) > 0
    df = pd.DataFrame(rows)
    assert "公告日期" in df.columns and "派息" in df.columns
    assert (df["派息"] > 0).all()
    # 平安银行近年每10股派息 ~2-6 元
    assert df["派息"].max() < 20


def test_hku_finance_records_units():
    """财报金额字段单位为元/%，总股本为股。"""
    F = C.HKYUU_FINANCE_FIELDS
    st = hku_stock(SYMBOL)
    fnames = [F["归母净利润"], F["总股本"], F["每股净资产"], F["ROE_加权"], F["资产负债率"]]
    df = hku_finance_records(st, fnames)
    assert not df.empty
    assert "报告期" in df.columns
    # 取最近年报（12 月）
    df["月"] = df["报告期"].dt.month
    annual = df[df["月"] == 12]
    assert not annual.empty
    last = annual.iloc[-1]
    assert 1e9 < last[F["归母净利润"]] < 1e12           # 净利润 ~数十~数百亿 元
    assert 1e9 < last[F["总股本"]] < 5e10               # 总股本 ~股
    assert 0 < last[F["每股净资产"]] < 200             # 元/股
    assert 0 < last[F["ROE_加权"]] < 100               # %
    assert 0 < last[F["资产负债率"]] < 100             # %


def test_hku_bond_yield_decimal_unit():
    """zh_bond10 value/1e6 → 小数（非 /1e4）。"""
    df = hku_bond_yield_df()
    assert df is not None and not df.empty
    assert set(["日期", "国债收益率"]).issubset(df.columns)
    # 10Y 国债小数 ~0.01-0.05（1%-5%）；/1e4 会得 1-5（错）
    assert df["国债收益率"].max() < 0.06
    assert df["国债收益率"].min() > 0


def test_hku_pb_series():
    df = hku_pb_series(SYMBOL, "20240101", "20241231")
    assert not df.empty
    assert set(["日期", "市净率PB"]).issubset(df.columns)
    v = df["市净率PB"].dropna()
    assert len(v) > 0
    # 平安银行 PB ~0.3-0.8
    assert v.iloc[-1] < 3


def test_hku_stock_list():
    from data.hikyuu_backend import hku_stock_list
    df = hku_stock_list()
    assert df is not None and len(df) > 1000
    assert set(["代码", "名称"]).issubset(df.columns)
    assert df["代码"].astype(str).str.match(r"^\d{6}$").all()


# ------------------------------------------------------------------
# 迁移后 fetcher（纯 hku 路径，hku 可用时不触 akshare）
# ------------------------------------------------------------------
def test_fetch_daily_data_contract():
    df = fetch_daily_data(SYMBOL, "20240101", "20241231")
    assert not df.empty
    assert "日期" in df.columns and "收盘" in df.columns
    assert df["收盘"].iloc[-1] > 0


def test_fetch_benchmark_daily_contract():
    df = fetch_benchmark_daily("000300", "20240101", "20241231")
    assert not df.empty
    assert list(df.columns) == ["日期", "收盘"] or set(df.columns) == {"日期", "收盘"}


def test_fetch_dividend_contract():
    df = fetch_dividend(SYMBOL)
    assert not df.empty
    assert "公告日期" in df.columns and "派息" in df.columns


def test_fetch_bond_yield_history_decimal():
    df = fetch_bond_yield_history("20241231", force_refresh=True)
    assert df is not None and not df.empty
    assert df["国债收益率"].iloc[-1] < 0.06


def test_fetch_financial_abstract_contract():
    df = fetch_financial_abstract(SYMBOL)
    assert not df.empty
    for c in ["报告期", "加权净资产收益率(%)", "资产负债率(%)",
              "经营活动产生的现金流量净额", "归属于上市公司股东的净利润",
              "归属母公司股东权益", "总股本"]:
        assert c in df.columns, f"缺列 {c}"


def test_fetch_cashflow_detail_contract():
    df = fetch_cashflow_detail(SYMBOL)
    assert not df.empty
    assert "报告期" in df.columns
    assert "购建固定资产" in "".join(df.columns)   # find_col_in 子串匹配
    assert "折旧与摊销" in df.columns


def test_fetch_stock_list_contract():
    df = fetch_stock_list(force_refresh=True)
    assert df is not None and len(df) > 1000
    assert set(["代码", "名称"]).issubset(df.columns)


# ------------------------------------------------------------------
# 降级：hku 不可用时 backend 函数返 None/空（不抛）
# ------------------------------------------------------------------
def test_fallback_when_hku_unavailable(monkeypatch):
    """模拟 hku 未装：_hku() 返 None → backend 函数优雅返 None/空，不抛。"""
    import data.hikyuu_backend as hb
    monkeypatch.setattr(hb, "_hku", lambda: None)
    assert hb.fetch_kdata_df(SYMBOL, "20240101", "20241231").empty
    assert hb.hku_stock(SYMBOL) is None
    assert hb.hku_total_shares(None) is None
    # hku_bond_yield_df 直读 sqlite，不依赖 _hku → 仍可用
    bd = hb.hku_bond_yield_df()
    assert bd is None or not bd.empty
