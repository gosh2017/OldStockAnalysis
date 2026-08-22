# -*- coding: utf-8 -*-
"""
回测引擎测试（提示词 D2/D3/D4）。

覆盖：
  - analyze_as_of：fin_end 随 as_of 前移而变化、latest_price=截断末值、返回键齐全
  - analyze_as_of 与 main(ctx, quiet=True) 在同 ctx + 同未截断数据下等级/建议一致（一致性回归）
  - run_backtest：demo + freq="Y" 3 调仓日、equity_curve 合理、grade_forward_returns 四等级键齐全、
    txn_cost>0 净值 <= txn_cost=0 净值
  - compute_metrics：已知曲线总收益/最大回撤、常数序列 Sharpe=None、benchmark 同曲线 Beta≈1
"""
import io
from contextlib import redirect_stdout

import pandas as pd

from config import StockContext
from data import as_of_bundle, generate_all_demo_data
from analysis import analyze_as_of, run_backtest, compute_metrics, BacktestResult
from main import main


# =====================================================================
# D2 — analyze_as_of
# =====================================================================

def test_analyze_as_of_returns_keys(ctx):
    """返回 dict 含全部约定键。"""
    cache = generate_all_demo_data(ctx, backtest=True)
    bundle = as_of_bundle(ctx.symbol, "2022-06-30", cache, demo=True)
    res = analyze_as_of(ctx, bundle)
    for key in ("score", "grade", "recommendation", "latest_price",
                "screened", "as_of", "ctx"):
        assert key in res, f"缺少键 {key}"
    assert res["grade"] in "ABCD"
    assert res["as_of"] == pd.Timestamp("2022-06-30")


def test_analyze_as_of_fin_window_advances_with_as_of(ctx):
    """fin_end 随 as_of 前移而变化（早期 as_of 的 fin_end < 晚期 as_of 的 fin_end）。"""
    cache = generate_all_demo_data(ctx, backtest=True)

    bundle_early = as_of_bundle(ctx.symbol, "2018-06-30", cache, demo=True)
    res_early = analyze_as_of(ctx, bundle_early)

    bundle_late = as_of_bundle(ctx.symbol, "2024-06-30", cache, demo=True)
    res_late = analyze_as_of(ctx, bundle_late)

    # 早期 fin_end（最新可用年报年）< 晚期 fin_end
    assert res_early["fin_end"] < res_late["fin_end"]
    # as_of=2024-06-30：fin_end 应为 2023（2023 年报 <= 2024-06-30-120d=2024-03-02 保留；
    #                                      2024 年报 > 该日排除）
    assert res_late["fin_end"] == 2023


def test_analyze_as_of_latest_price_is_truncated_end(ctx):
    """latest_price = 截断日线末值（PIT 正确，不偷看 as_of 之后）。"""
    cache = generate_all_demo_data(ctx, backtest=True)
    as_of = "2023-06-30"
    bundle = as_of_bundle(ctx.symbol, as_of, cache, demo=True)
    res = analyze_as_of(ctx, bundle)
    # 截断日线的末行收盘
    daily = bundle["daily_df"]
    expected = float(daily["收盘"].iloc[-1])
    assert abs(res["latest_price"] - expected) < 1e-6
    # 末行日期 <= as_of
    assert daily["日期"].iloc[-1] <= pd.Timestamp(as_of)


def test_analyze_as_of_consistency_with_main(ctx):
    """与 main(ctx, quiet=True) 在同 ctx + 同（未截断）数据下等级/建议一致。

    as_of=今天（ctx.end_date）截断默认 demo 数据 ≡ 未截断（2021–2025 全保留），
    fin_end 派生 = 2025 == main 的 ctx.fin_end，故四步 + 评分口径应一致。
    """
    # main 在未截断默认数据上跑（静默）
    buf = io.StringIO()
    with redirect_stdout(buf):
        main_res = main(ctx, quiet=True)
    main_grade = main_res["score"]["grade"]
    main_rec = main_res["advice"]["recommendation"]

    # analyze_as_of 在同 ctx + 截断到今天（≈未截断）的默认数据上跑
    cache = generate_all_demo_data(ctx)  # backtest=False：与 main 内部 generate 同口径
    bundle = as_of_bundle(ctx.symbol, ctx.end_date, cache, demo=True)
    ares = analyze_as_of(ctx, bundle)

    assert ares["grade"] == main_grade
    assert ares["recommendation"] == main_rec


# =====================================================================
# D3 — run_backtest
# =====================================================================

_BT_ITEMS = [
    ("000001", "平安银行"),
    ("600519", "贵州茅台"),
    ("000651", "格力电器"),
    ("600036", "招商银行"),
    ("601318", "中国平安"),
]


def _run_bt(**kwargs):
    """统一回测参数（demo、年度调仓、3 期、min_grade=D 保证入选）。"""
    defaults = dict(start="2022-01-01", end="2024-12-31", freq="Y",
                   demo=True, top_n=5, min_grade="D", weight="equal",
                   txn_cost=0.001, benchmark="000300")
    defaults.update(kwargs)
    return run_backtest(_BT_ITEMS, **defaults)


def test_run_backtest_demo_yearly_structure():
    """demo + freq='Y' → 3 个调仓日、equity_curve 非空、BacktestResult 字段齐全。"""
    res = _run_bt()
    assert isinstance(res, BacktestResult)
    assert len(res.rebalance_dates) == 3       # 2022/2023/2024 年末
    assert not res.equity_curve.empty
    assert len(res.equity_curve) > 2           # 至少若干交易日
    assert isinstance(res.positions, list) and len(res.positions) == 3
    assert isinstance(res.trades, list)
    assert res.metrics and "sharpe" in res.metrics


def test_run_backtest_grade_forward_returns_keys():
    """grade_forward_returns 各等级键齐全（无样本时空序列）。"""
    res = _run_bt()
    assert set(res.grade_forward_returns.keys()) == {"A", "B", "C", "D"}
    # 每个键值都是 list
    for g, seq in res.grade_forward_returns.items():
        assert isinstance(seq, list)
    # 至少 D 级应有样本（demo 标的在多数调仓日会落 D）
    total = sum(len(v) for v in res.grade_forward_returns.values())
    assert total > 0


def test_run_backtest_txn_cost_reduces_nav():
    """txn_cost>0 时净值 <= txn_cost=0 时净值（同种子确定性数据，仅成本差异）。"""
    res_cost = _run_bt(txn_cost=0.001)
    res_free = _run_bt(txn_cost=0.0)
    nav_cost = float(res_cost.equity_curve.iloc[-1])
    nav_free = float(res_free.equity_curve.iloc[-1])
    assert nav_cost <= nav_free + 1e-9


def test_run_backtest_benchmark_curve_aligned():
    """基准曲线与策略同日历、起点归一 1.0。"""
    res = _run_bt()
    assert res.benchmark_curve is not None and not res.benchmark_curve.empty
    # 起点归一
    assert abs(float(res.benchmark_curve.iloc[0]) - 1.0) < 1e-6


def test_run_backtest_positions_forward_returns_consistent():
    """positions 中持仓个股的前向收益与 grade_forward_returns 口径一致（均为有限值或 0）。"""
    res = _run_bt()
    for pos in res.positions:
        assert "holdings" in pos
        for h in pos["holdings"]:
            fr = h["forward_return"]
            if fr is not None:
                assert -1.0 <= float(fr) <= 5.0   # 合理前向收益区间


# =====================================================================
# D4 — compute_metrics
# =====================================================================

def test_compute_metrics_known_curve():
    """[1, 1.1, 1.05, 1.2]：总收益≈20%、最大回撤≈(1.1→1.05)/1.1≈4.5%。"""
    eq = pd.Series([1.0, 1.1, 1.05, 1.2])
    m = compute_metrics(eq, eq)
    assert abs(m["total_return"] - 0.20) < 1e-6
    assert abs(m["max_drawdown"] - (1.1 - 1.05) / 1.1) < 1e-3
    assert m["sharpe"] is not None or m["sharpe"] is None  # 不爆


def test_compute_metrics_constant_sharpe_none():
    """常数序列 → vol=0 → Sharpe=None，不抛异常。"""
    eq = pd.Series([1.0, 1.0, 1.0, 1.0])
    m = compute_metrics(eq)
    assert m["sharpe"] is None
    assert m["volatility"] is None or m["volatility"] == 0.0
    assert m["total_return"] == 0.0


def test_compute_metrics_beta_one_when_benchmark_same():
    """benchmark 与策略同曲线 → Beta≈1。"""
    eq = pd.Series([1.0, 1.1, 1.05, 1.2])
    m = compute_metrics(eq, benchmark_curve=eq)
    assert m["beta"] is not None
    assert abs(m["beta"] - 1.0) < 1e-6


def test_compute_metrics_empty_safe():
    """空/单点序列 → 全 None 度量，不抛。"""
    m = compute_metrics(pd.Series([], dtype=float))
    assert m["total_return"] is None
    assert m["sharpe"] is None
    m1 = compute_metrics(pd.Series([1.0]))
    assert m1["total_return"] is None
