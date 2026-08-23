# -*- coding: utf-8 -*-
"""
回测可视化 — 净值曲线 / 水下回撤图 / 各等级前向收益柱状图。

与 visualization/charts.py 同口径：matplotlib 优先（Agg 后端，中文标题/标签），
plotly 软导入出交互版（未装则静默跳过）；--no-chart 由调用方（main）控制是否调用本模块。
输出到 ctx.chart_dir 下的 backtest_*.{png,html}。

三张图分别回答：
  - plot_equity_curve            ：策略 vs 基准净值——"按建议操作能否跑赢买入持有"。
  - plot_drawdown                ：水下图——策略的历史回撤与回撤期长度。
  - plot_grade_forward_returns   ：各等级平均前向收益——"A/B 是否跑赢 D"的单调性证据。
"""
import os

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


def _style(ctx):
    """统一 matplotlib 字体与输出目录（与 charts.py 同口径）。"""
    os.makedirs(ctx.chart_dir, exist_ok=True)
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def plot_equity_curve(result, ctx) -> None:
    """策略 vs 基准净值曲线（matplotlib 静态 + plotly 交互，可选）。"""
    eq = result.equity_curve
    if eq is None or eq.empty:
        print("\n[!] 净值曲线为空，跳过净值图。")
        return
    _style(ctx)

    fig, ax = plt.subplots(figsize=(13, 6))
    ax.plot(eq.index, eq.values, color="#1a73e8", linewidth=1.6, label="策略净值")
    bench = result.benchmark_curve
    has_bench = bench is not None and not bench.empty
    if has_bench:
        # 对齐到策略起点归一（基准曲线应已归一，此处再保险）
        b = bench.reindex(eq.index, method="ffill")
        b0 = b.iloc[0] if not b.empty and b.iloc[0] > 0 else 1.0
        ax.plot(b.index, (b / b0).values, color="#999999", linewidth=1.2,
                linestyle="--", label=f"基准（{getattr(ctx, 'symbol', '000300')}）")
    ax.axhline(1.0, color="black", linewidth=0.6, alpha=0.4)
    ax.set_title(f"{ctx.name} · 回测净值曲线（策略 vs 基准）", fontsize=13, fontweight="bold")
    ax.set_ylabel("累计净值（起点=1.0）", fontsize=11)
    ax.set_xlabel("日期", fontsize=11)
    ax.legend(loc="upper left", fontsize=10)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = f"{ctx.chart_dir}/backtest_equity_{ctx.symbol}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\n  [OK] 回测净值图已保存: {path}")

    if HAS_PLOTLY:
        figp = go.Figure()
        figp.add_trace(go.Scatter(x=eq.index, y=eq.values, mode="lines",
                                  name="策略净值", line=dict(color="#1a73e8", width=1.6)))
        if has_bench:
            b = bench.reindex(eq.index, method="ffill")
            b0 = b.iloc[0] if not b.empty and b.iloc[0] > 0 else 1.0
            figp.add_trace(go.Scatter(x=b.index, y=(b / b0).values, mode="lines",
                                      name="基准", line=dict(color="#999999", width=1.2, dash="dash")))
        figp.update_layout(title=f"{ctx.name} · 回测净值曲线", xaxis_title="日期",
                           yaxis_title="累计净值（起点=1.0）", template="plotly_white", height=450)
        hpath = f"{ctx.chart_dir}/backtest_equity_{ctx.symbol}.html"
        figp.write_html(hpath)
        print(f"  [OK] 交互式净值图已保存: {hpath}")


def plot_drawdown(result, ctx) -> None:
    """水下图（回撤序列）：cummax 净值回撤比例，填色到 0 线。"""
    eq = result.equity_curve
    if eq is None or eq.empty:
        print("\n[!] 净值曲线为空，跳过回撤图。")
        return
    _style(ctx)

    cummax = eq.cummax()
    drawdown = (eq - cummax) / cummax.replace(0, np.nan)  # 负值序列

    fig, ax = plt.subplots(figsize=(13, 4.5))
    ax.fill_between(drawdown.index, drawdown.values * 100, 0,
                    color="#d32f2f", alpha=0.45, label="回撤（%）")
    ax.axhline(0, color="black", linewidth=0.6)
    mdd = result.metrics.get("max_drawdown") if result.metrics else None
    title = f"{ctx.name} · 回测水下图（最大回撤）"
    if mdd is not None:
        title += f"  最大回撤 ≈ {mdd * 100:.1f}%"
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.set_ylabel("回撤（%）", fontsize=11)
    ax.set_xlabel("日期", fontsize=11)
    ax.legend(loc="lower left", fontsize=10)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = f"{ctx.chart_dir}/backtest_drawdown_{ctx.symbol}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [OK] 回撤图已保存: {path}")


def plot_grade_forward_returns(result, ctx) -> None:
    """各等级（A/B/C/D）平均前向收益柱状图——验证等级单调性证据。

    高等级平均前向收益高于低等级 → 信号有效（单调）。无数据的等级置 0 且标注"无样本"。
    """
    gfr = result.grade_forward_returns or {}
    if not gfr:
        print("\n[!] 无等级前向收益数据，跳过柱状图。")
        return
    _style(ctx)

    grades = ["A", "B", "C", "D"]
    means = []
    samples = []
    for g in grades:
        seq = gfr.get(g, [])
        samples.append(len(seq))
        means.append(float(np.mean(seq)) * 100 if seq else 0.0)

    # 误差棒：grade_signal 的 bootstrap CI 上下半宽（不对称，分数转百分），无则 0
    gs = getattr(result, "grade_signal", None) or {}
    ci_by = gs.get("ci_by_grade", {})
    err_minus, err_plus = [], []
    for g, m, n in zip(grades, means, samples):
        lo, hi = ci_by.get(g, (None, None))
        if lo is not None and hi is not None and n > 0:
            err_minus.append(max(m - lo * 100, 0.0))
            err_plus.append(max(hi * 100 - m, 0.0))
        else:
            err_minus.append(0.0)
            err_plus.append(0.0)
    yerr = [err_minus, err_plus]  # 2×N：[下偏, 上偏]

    fig, ax = plt.subplots(figsize=(8, 5.5))
    colors = ["#1a73e8", "#34a853", "#fbbc04", "#ea4335"]
    bars = ax.bar(grades, means, color=colors, edgecolor="black", linewidth=0.5,
                  width=0.6, yerr=yerr, capsize=3,
                  error_kw=dict(lw=1.2, ecolor="#555"))
    for bar, m, n in zip(bars, means, samples):
        label = f"{m:+.2f}%" if n > 0 else "无样本"
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                label, ha="center",
                va="bottom" if m >= 0 else "top",
                fontsize=10, fontweight="bold")
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_title(f"{ctx.name} · 各等级平均前向收益（信号单调性验证）",
                 fontsize=13, fontweight="bold")
    ax.set_ylabel("平均前向收益（%）", fontsize=11)
    ax.set_xlabel("综合评分等级", fontsize=11)
    ax.grid(True, alpha=0.3, axis="y")
    # 留出顶部标注空间
    ymax = max(means + [0]) if means else 0
    ymin = min(means + [0]) if means else 0
    ax.set_ylim(min(ymin - 2, -1), max(ymax + 2, 1))
    fig.tight_layout()
    path = f"{ctx.chart_dir}/backtest_grade_returns_{ctx.symbol}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [OK] 等级前向收益图已保存: {path}")
