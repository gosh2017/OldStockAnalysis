# -*- coding: utf-8 -*-
"""
pytest 根级配置：确保项目根目录可被测试导入，并提供基于 demo 数据的共享 fixtures。
"""
import os
import sys

# 将项目根目录加入 sys.path，使 `from analysis import ...` 等可用
sys.path.insert(0, os.path.dirname(__file__))

import pytest
from config import StockContext
from data import generate_all_demo_data


@pytest.fixture(scope="session")
def ctx():
    return StockContext(symbol="000001", name="平安银行", demo=True)


@pytest.fixture(scope="session")
def demo_data(ctx):
    return generate_all_demo_data(ctx)


@pytest.fixture
def daily_df(demo_data):
    """每个测试取副本，避免被原地修改的分析函数污染共享数据。"""
    return demo_data["daily_df"].copy()


@pytest.fixture
def fin_abstract(demo_data):
    return demo_data["fin_abstract"].copy()


@pytest.fixture
def cashflow_df(demo_data):
    return demo_data["cashflow_df"].copy()


@pytest.fixture
def dividend_df(demo_data):
    return demo_data["dividend_df"].copy()


@pytest.fixture
def stock_indicator(demo_data):
    return demo_data["stock_indicator"].copy()


@pytest.fixture
def market_df(demo_data):
    return demo_data["market_df"].copy()


@pytest.fixture
def bond_yield(demo_data):
    return demo_data["bond_yield"]


@pytest.fixture(scope="session")
def industry_info(demo_data):
    """行业归属与总股本（demo 口径，对应实盘 fetch_industry_info）。
    session 级：dict 不可变语义，无需逐测试 copy。"""
    return demo_data["industry_info"]


@pytest.fixture(scope="session")
def bucket(industry_info):
    """当前 demo 标的（000001）的行业桶——银行。"""
    return industry_info["bucket"]
