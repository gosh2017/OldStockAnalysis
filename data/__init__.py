# -*- coding: utf-8 -*-
"""
量化价值投资分析系统 — 数据获取层
"""
from .fetcher import (
    fetch_daily_data,
    fetch_financial_abstract,
    fetch_cashflow_detail,
    fetch_dividend,
    fetch_market_overview,
    fetch_bond_yield_10y,
    fetch_bond_yield_history,
    fetch_market_pe_history,
    fetch_stock_indicator,
    fetch_stock_list,
    search_stocks,
)
from .demo_data import (
    generate_all_demo_data,
    generate_stock_indicator,
    generate_stock_list,
    generate_market_pe_history,
    generate_bond_yield_history,
)

__all__ = [
    "fetch_daily_data",
    "fetch_financial_abstract",
    "fetch_cashflow_detail",
    "fetch_dividend",
    "fetch_market_overview",
    "fetch_bond_yield_10y",
    "fetch_bond_yield_history",
    "fetch_market_pe_history",
    "fetch_stock_indicator",
    "fetch_stock_list",
    "search_stocks",
    "generate_all_demo_data",
    "generate_stock_indicator",
    "generate_stock_list",
    "generate_market_pe_history",
    "generate_bond_yield_history",
]
