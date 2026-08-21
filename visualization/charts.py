# -*- coding: utf-8 -*-
"""
可视化 — 绘制估值走势图：
  上图：股价历史走势 + 估值线（破产清算 / 保守 / 合理估值上限）
  下图：相对合理估值上限的安全边际（溢价 / 折价）

标的名称/代码/输出目录均来自调用方传入的 StockContext，
不再依赖 config 全局常量，从而支持多标的与 --out-dir。
"""
import os
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.colors import LinearSegmentedColormap

try:
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# 单色顺序型色阶（蓝色 light→dark）：内在价值由低到高，CVD 安全。
# 浅色域用黑字、深色域用白字，保证每格数值可读（冗余编码，非颜色唯一）。
_VAL_SEQ_CMAP = LinearSegmentedColormap.from_list(
    "val_seq",
    ["#eaf1fb", "#bbd3f4", "#6fa3e6", "#2a72d0", "#0d47a1"],
)


def plot_valuation_chart(
    daily_df: pd.DataFrame,
    valuations: dict,
    sentiment_result: dict,
    dcf_result: dict,
    ctx,
) -> None:
    """绘制并保存估值走势图（matplotlib 静态图 + plotly 交互图，可选）。"""
    if daily_df.empty or valuations is None:
        print("\n[!] 数据不足，跳过图表绘制。")
        return

    os.makedirs(ctx.chart_dir, exist_ok=True)
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    liquidation  = valuations["破产清算 (Liquidation)"]["intrinsic_value"]
    conservative = valuations["保守 (Conservative)"]["intrinsic_value"]
    ceiling      = dcf_result.get("fair_value_ceiling") or valuations["中性 (Neutral)"]["intrinsic_value"]

    _plot_matplotlib(daily_df, liquidation, conservative, ceiling, sentiment_result, ctx)

    if HAS_PLOTLY:
        _plot_interactive(daily_df, liquidation, conservative, ceiling, sentiment_result, ctx)


def _plot_matplotlib(daily_df, liquidation, conservative, ceiling, sentiment_result, ctx):
    """matplotlib 静态双面板图。"""
    dates  = daily_df["日期"]
    prices = daily_df["收盘"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10),
                                   gridspec_kw={"height_ratios": [3, 1]},
                                   sharex=True)

    # 上图
    ax1.plot(dates, prices, color="#1a73e8", linewidth=1.5, label="收盘价（前复权）")
    ax1.axhline(liquidation, color="#616161", linestyle="-.", linewidth=1.2,
                label=f"破产清算 {liquidation:.2f} 元")
    ax1.axhline(conservative, color="#d32f2f", linestyle="--", linewidth=1.2,
                label=f"保守估值 {conservative:.2f} 元")
    ax1.axhline(ceiling, color="#2e7d32", linestyle="-", linewidth=1.5,
                label=f"合理估值上限 {ceiling:.2f} 元")
    ax1.fill_between(dates, conservative, ceiling, alpha=0.08, color="#1a73e8")

    latest_price = prices.iloc[-1]
    latest_date  = dates.iloc[-1]
    ax1.scatter([latest_date], [latest_price], color="#1a73e8", s=80, zorder=5)
    ax1.annotate(f"{latest_price:.2f}", xy=(latest_date, latest_price),
                 xytext=(10, 10), textcoords="offset points",
                 fontsize=11, fontweight="bold", color="#1a73e8")

    ax1.set_ylabel("股价（元）", fontsize=12)
    ax1.set_title(
        f"{ctx.name}（{ctx.symbol}）估值走势图\n"
        f"股价 vs 破产清算/保守/合理估值上限 | 市场情绪: "
        f"{sentiment_result.get('sentiment', 'N/A')} "
        f"（{sentiment_result.get('percentile', 0):.0f}% 分位数）",
        fontsize=13, fontweight="bold",
    )
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(True, alpha=0.3)

    # 下图
    margin = (prices - ceiling) / ceiling * 100
    ax2.fill_between(dates, margin, 0, where=(margin >= 0),
                     color="#d32f2f", alpha=0.4, label="溢价（高估）")
    ax2.fill_between(dates, margin, 0, where=(margin < 0),
                     color="#2e7d32", alpha=0.4, label="折价（低估）")
    ax2.axhline(0, color="black", linewidth=0.8)
    ax2.set_ylabel("相对合理估值上限（%）", fontsize=12)
    ax2.legend(loc="upper right", fontsize=9)
    ax2.grid(True, alpha=0.3)

    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    plt.xticks(rotation=45)

    plt.tight_layout()
    chart_path = f"{ctx.chart_dir}/valuation_{ctx.symbol}.png"
    plt.savefig(chart_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\n  [OK] 图表已保存: {chart_path}")


def _plot_interactive(daily_df, liquidation, conservative, ceiling, sentiment_result, ctx):
    """plotly 交互式图表（plotly 未安装时不会调用）。"""
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=daily_df["日期"], y=daily_df["收盘"],
        mode="lines", name="收盘价",
        line=dict(color="#1a73e8", width=1.5),
    ))
    # 画线不带内置标注，再统一沿 x 错位放标签（避免多线标注挤在同一角重叠）
    lines = [
        (liquidation,  f"清算 {liquidation:.2f}",  "#616161"),
        (conservative, f"保守 {conservative:.2f}", "#d32f2f"),
        (ceiling,      f"上限 {ceiling:.2f}",      "#2e7d32"),
    ]
    for v, _, color in lines:
        fig.add_hline(y=v, line_color=color, line_width=1.4)
    # 标签沿 x 轴错位：数值接近/相等时并排而非堆叠
    xs = daily_df["日期"]
    xmin, xmax = xs.iloc[0], xs.iloc[-1]
    span = (xmax - xmin)
    n = len(lines)
    for i, (v, text, color) in enumerate(lines):
        xpos = xmin + span * (i + 0.5) / n
        fig.add_annotation(x=xpos, y=v, text=text, showarrow=False,
                           font=dict(color=color, size=11),
                           bgcolor="rgba(255,255,255,0.78)", borderpad=2)

    fig.update_layout(
        title=f"{ctx.name}（{ctx.symbol}）估值走势",
        xaxis_title="日期",
        yaxis_title="股价（元）",
        template="plotly_white",
        height=500,
    )

    plotly_path = f"{ctx.chart_dir}/valuation_{ctx.symbol}.html"
    fig.write_html(plotly_path)
    print(f"  [OK] 交互式图表已保存: {plotly_path}")


def plot_sensitivity_heatmap(sensitivity: dict, ctx, price: float | None = None) -> str | None:
    """
    绘制 DCF 敏感性热力图（增长率 × WACC → 每股内在价值）。

    单色顺序型色阶（蓝，light→dark = 低→高内在价值）；每格标注数值；
    若提供当前股价 price，在 colorbar 上标出其位置，并尽量叠一条红色
    等值线标"内在价值 = 现价"的买/卖分界（线左下侧内在价值 > 现价 = 买入区）。
    现价落在网格值域 [vmin, vmax] 外时等值线无法绘制，改由 colorbar 端点
    标注 + 标题说明（全面高估 / 全面低估）传达现价的相对位置。
    """
    grid = sensitivity.get("grid") if sensitivity else None
    if grid is None or grid.empty:
        print("\n[!] 敏感性数据不足，跳过热力图。")
        return None

    os.makedirs(ctx.chart_dir, exist_ok=True)
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    data = grid.values.astype(float)
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return None
    vmin, vmax = float(np.nanmin(finite)), float(np.nanmax(finite))

    fig, ax = plt.subplots(figsize=(9, 6))
    im = ax.imshow(data, cmap=_VAL_SEQ_CMAP, vmin=vmin, vmax=vmax, aspect="auto")

    # 每格标注数值；浅色用黑字、深色用白字
    span = vmax - vmin or 1.0
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            v = data[i, j]
            if not np.isfinite(v):
                continue
            txt_color = "white" if (v - vmin) / span > 0.6 else "#1a1a1a"
            ax.text(j, i, f"{v:.1f}", ha="center", va="center",
                    fontsize=6.5, color=txt_color)

    # 等值线标出"内在价值 = 现价"的买/卖分界。
    # matplotlib contour 无法在数据值域 [vmin, vmax] 之外画等值线，故仅当
    # 现价落在网格值域内时画线；超界时改由 colorbar 端点标注 + 标题说明传达。
    price_in_range = bool(price and price > 0 and vmin < price < vmax)
    if price_in_range:
        cs = ax.contour(data, levels=[price], colors="#d32f2f",
                        linewidths=1.8, linestyles="--")
        ax.clabel(cs, fmt=f"现价 {price:.2f}", fontsize=8, colors="#d32f2f")

    ax.set_xticks(range(len(grid.columns)))
    ax.set_xticklabels(grid.columns)
    ax.set_yticks(range(len(grid.index)))
    ax.set_yticklabels(grid.index)
    ax.set_xlabel("折现率 WACC", fontsize=11)
    ax.set_ylabel("永续增长率", fontsize=11)
    title = f"{ctx.name}（{ctx.symbol}）DCF 敏感性 — 每股内在价值（元）"
    if price and price > 0:
        if price_in_range:
            title += f"\n现价 {price:.2f} 元 · 红线左下侧（内在价值 > 现价）= 买入区"
        elif price >= vmax:
            title += (f"\n现价 {price:.2f} 元 ≥ 网格上限 {vmax:.2f} · "
                     f"全参数下内在价值 < 现价 → 全面高估")
        else:  # price <= vmin
            title += (f"\n现价 {price:.2f} 元 ≤ 网格下限 {vmin:.2f} · "
                     f"全参数下内在价值 > 现价 → 全面低估")
    ax.set_title(title, fontsize=12, fontweight="bold")
    cbar = fig.colorbar(im, ax=ax, label="每股内在价值（元）")
    # colorbar 始终标出现价：范围内画在真值处，超界时夹到端点并注明方向，
    # 确保现价无论是否落在网格值域内都能在图上看到其相对位置。
    if price and price > 0:
        clamped = min(max(price, vmin), vmax)
        cbar.ax.axhline(clamped, color="#d32f2f", lw=2.0, ls="--")
        if price >= vmax:
            va, note = "top", f"现价\n{price:.2f}"
        elif price <= vmin:
            va, note = "bottom", f"现价\n{price:.2f}"
        else:
            va, note = "center", f"现价 {price:.2f}"
        cbar.ax.text(0.5, clamped, note,
                     transform=cbar.ax.get_yaxis_transform(),
                     ha="center", va=va, fontsize=8, color="#d32f2f",
                     bbox=dict(facecolor="white", edgecolor="#d32f2f",
                               boxstyle="round,pad=0.2", alpha=0.9))
    ax.invert_yaxis()  # 低增长率在上方，符合表格阅读习惯

    fig.tight_layout()
    path = f"{ctx.chart_dir}/sensitivity_{ctx.symbol}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\n  [OK] 敏感性热力图已保存: {path}")
    return path
