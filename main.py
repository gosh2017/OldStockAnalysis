# -*- coding: utf-8 -*-
"""
量化价值投资分析系统 — 主入口

执行流程：
  1. 获取日频交易数据
  2. 获取财务摘要 / 现金流量表 / 分红数据
  3. Step 1: 基本面筛选
  4. Step 2: DCF 估值
  5. 获取市场数据（全市场 PE + 国债收益率）
  6. Step 3: 市场情绪分析
  7. Step 4: 综合投资建议
  8. 绘制估值走势图
"""
import argparse
from datetime import datetime

from config import STOCK_CODE, STOCK_NAME
from utils import sep
from data import (
    fetch_daily_data,
    fetch_financial_abstract,
    fetch_cashflow_detail,
    fetch_dividend,
    fetch_market_overview,
    fetch_bond_yield_10y,
)
from analysis import (
    fundamental_screening,
    dcf_valuation,
    market_sentiment,
    investment_advice,
)
from visualization import plot_valuation_chart


def main(symbol: str = STOCK_CODE, stock_name: str = STOCK_NAME) -> None:
    print(f"\n{'━' * 70}")
    print(f"  量化价值投资分析系统")
    print(f"  标的: {stock_name}（{symbol}）")
    print(f"  分析日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'━' * 70}")

    # ── 1. 获取数据 ──
    daily_df     = fetch_daily_data(symbol)
    fin_abstract = fetch_financial_abstract(symbol)
    cashflow_df  = fetch_cashflow_detail(symbol)
    dividend_df  = fetch_dividend(symbol)

    # ── 2. Step 1：基本面筛选 ──
    screening = fundamental_screening(symbol, fin_abstract, daily_df, dividend_df)

    # ── 3. Step 2：DCF 估值 ──
    dcf_result = dcf_valuation(symbol, fin_abstract, cashflow_df, daily_df)

    # ── 4. 市场数据 ──
    market_df  = fetch_market_overview()
    bond_yield = fetch_bond_yield_10y()

    # ── 5. Step 3：市场情绪 ──
    sentiment = market_sentiment(market_df, bond_yield)

    # ── 6. Step 4：综合建议 ──
    advice = investment_advice(daily_df, dcf_result, sentiment, screening)

    # ── 7. 图表 ──
    if dcf_result.get("valuations"):
        plot_valuation_chart(daily_df, dcf_result["valuations"], sentiment, dcf_result)

    # ── 最终摘要 ──
    _print_summary(symbol, stock_name, advice)


def _print_summary(symbol: str, stock_name: str, advice: dict) -> None:
    """打印最终分析摘要表格。"""
    sep("分析摘要")

    latest_price = advice.get("latest_price")
    conservative = advice.get("conservative")
    neutral      = advice.get("neutral")
    optimistic   = advice.get("optimistic")
    screened     = advice.get("screened", False)
    sentiment    = advice.get("sentiment", "N/A")
    recommendation = advice.get("recommendation", "N/A")

    price_str = f"{latest_price:.2f}" if latest_price else "N/A"
    cons_str  = f"{conservative:.2f}" if conservative else "N/A"
    neu_str   = f"{neutral:.2f}" if neutral else "N/A"
    opt_str   = f"{optimistic:.2f}" if optimistic else "N/A"

    print(f"""
  ┌─────────────────────────────────────────────────────┐
  │  标的            │ {stock_name}（{symbol}）               │
  │  当前股价        │ {price_str:>8} 元          │
  │  ────────────────────────────────────────             │
  │  保守估值        │ {cons_str:>8} 元          │
  │  中性估值        │ {neu_str:>8} 元          │
  │  乐观估值        │ {opt_str:>8} 元          │
  │  ────────────────────────────────────────             │
  │  基本面筛选      │ {'通过' if screened else '未通过':>20}       │
  │  市场情绪        │ {str(sentiment):>20}     │
  │  ────────────────────────────────────────             │
  │  ★ 操作建议      │ {str(recommendation):>20}      │
  └─────────────────────────────────────────────────────┘
""")

    if recommendation:
        print("分析完成。")
    else:
        print("部分分析因数据获取失败未能完成。")
    print()


def _cli():
    parser = argparse.ArgumentParser(
        description="量化价值投资分析系统 — A 股长线价值投资分析"
    )
    parser.add_argument(
        "symbol", nargs="?", default=STOCK_CODE,
        help=f"股票代码（默认 {STOCK_CODE}）",
    )
    parser.add_argument(
        "-n", "--name", default=STOCK_NAME,
        help="股票名称（用于图表标题）",
    )
    args = parser.parse_args()
    main(symbol=args.symbol, stock_name=args.name)


if __name__ == "__main__":
    _cli()
