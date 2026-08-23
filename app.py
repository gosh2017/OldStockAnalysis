# -*- coding: utf-8 -*-
"""
量化价值投资分析系统 — Streamlit Web 仪表盘

启动：
    streamlit run app.py

特性：
  - 侧边栏输入标的/年份/模式，点击「开始分析」执行四步分析 + 评分
  - 交互式估值走势图、DCF 敏感性热力图（plotly，鼠标悬停查看）
  - KPI 卡片、基本面/DCF 表、市场情绪与个股 PE/PB 分位、评分明细
  - 「批量排名」tab：多标的打分排序
  - 「历史回测」tab：调仓/选股/日频净值/换仓成本，业绩度量 + 各等级前向收益验证信号有效性
  - 支持 --demo 离线模式（无需网络）

复用 main.main(ctx, quiet=True) 的完整分析管线，本文件只负责交互与可视化呈现。
"""
import io
import json
import os
import re
from contextlib import redirect_stdout

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import (
    StockContext, END_DATE, CACHE_DIR,
    BACKTEST_REBALANCE_FREQ, BACKTEST_HOLD_PERIOD, BACKTEST_TOP_N,
    BACKTEST_MIN_GRADE, BACKTEST_WEIGHT, BACKTEST_TXN_COST,
    BACKTEST_BENCHMARK, BACKTEST_LOOKBACK_YEARS,
)
from data import fetch_stock_list, generate_stock_list, search_stocks
from analysis import run_backtest, BacktestResult
from main import main, run_batch, BATCH_DEMO_LIST

# -- 配色（与 matplotlib 图表 / HTML 报告保持一致）---------
COLORS = {
    "price": "#1a73e8", "liq": "#616161", "cons": "#d32f2f", "ceil": "#2e7d32",
}
GRADE_COLOR = {"A": "#2e7d32", "B": "#1565c0", "C": "#ef6c00", "D": "#c62828"}
SCENES = [
    ("破产清算 (Liquidation)", COLORS["liq"],  "dashdot"),
    ("保守 (Conservative)",   COLORS["cons"], "dash"),
]

st.set_page_config(page_title="量化价值投资分析", page_icon="📊", layout="wide")


def get_stock_list(demo: bool, force_refresh: bool = False):
    """获取代码-名称列表。
    demo 用内置清单；实盘走 fetch_stock_list（自带 24h 磁盘缓存）。
    force_refresh=True 时绕过磁盘缓存强制重拉（用于"刷新"按钮）。"""
    return generate_stock_list() if demo else fetch_stock_list(force_refresh=force_refresh)


# -- 分析执行（静默，复用管线）----------------------------
def run_analysis(symbol, name, demo, fin_start, fin_end):
    ctx = StockContext(symbol=symbol, name=name, demo=demo,
                       no_chart=True, fin_start=fin_start, fin_end=fin_end)
    buf = io.StringIO()
    with redirect_stdout(buf):
        return main(ctx, quiet=True)


def run_batch_silent(demo=True, items=None):
    buf = io.StringIO()
    with redirect_stdout(buf):
        return run_batch(items or BATCH_DEMO_LIST, demo=demo)


def run_backtest_silent(items, *, demo, start, end, **kwargs):
    """静默执行回测（复用 analysis.run_backtest），返回 BacktestResult。

    kwargs 透传 run_backtest 的 freq/hold_days/top_n/min_grade/weight/txn_cost/benchmark。
    demo 走 generate_all_demo_data(backtest=True) 全程无网；live 需联网预取。
    """
    buf = io.StringIO()
    with redirect_stdout(buf):
        return run_backtest(items, start=start, end=end, demo=demo, **kwargs)


def _parse_batch_text(text: str) -> list:
    """解析批量标的文本：每行 `代码,名称` 或仅代码（# 注释、空行跳过）。
    返回 [(code, name), ...]。复用 main._read_batch_file 的分割逻辑。"""
    items = []
    for line in str(text).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[,，\s]+", line, maxsplit=1)
        code = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else code
        if code:
            items.append((code, name))
    return items


def _batch_default_text() -> str:
    """批量标的输入框默认文本（与 BATCH_DEMO_LIST 同步）。"""
    return "\n".join(f"{c},{n}" for c, n in BATCH_DEMO_LIST)


# -- 仪表盘输入清单持久化 ---------------------------------
# 把「批量排名 / 历史回测」两个输入框的标的文本缓存到 .cache/，
# 下次启动 streamlit 时恢复上次的输入，免去重复录入。
# 语义与 utils/cache.py 一致：任何读写异常都静默降级，不阻断主流程。
_DASHBOARD_INPUTS_CACHE = os.path.join(CACHE_DIR, "dashboard_inputs.json")


def _load_dashboard_inputs() -> dict:
    """读取仪表盘输入清单缓存。返回 dict（可能为空）。"""
    try:
        if os.path.exists(_DASHBOARD_INPUTS_CACHE):
            with open(_DASHBOARD_INPUTS_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"[CACHE] 读取仪表盘输入缓存失败，回退默认：{e}")
    return {}


def _save_dashboard_inputs() -> None:
    """把当前 session_state 中的批量/回测标的文本落盘。失败静默降级。

    作为两个输入框的 on_change 回调，也供「➕ 添加 / ❌ 移除」后显式调用——
    这些路径直接改写 widget 的 session_state，不会触发 on_change，需手动保存。"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        payload = {
            "batch_symbols": st.session_state.get("batch_symbols", ""),
            "bt_symbols": st.session_state.get("bt_symbols", ""),
        }
        with open(_DASHBOARD_INPUTS_CACHE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[CACHE] 写入仪表盘输入缓存失败（不影响本次结果）：{e}")


def _active_input_lines(text: str) -> list:
    """返回文本中的有效行（非空、非注释），用于去重与「已添加标的」展示。"""
    return [ln.strip() for ln in str(text).splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _remove_nth_active_line(key: str, n: int) -> None:
    """从输入文本中删除第 n 个有效行（0 基），保留空行/注释行。"""
    raw = str(st.session_state.get(key, "")).splitlines()
    out, seen = [], 0
    for line in raw:
        s = line.strip()
        is_active = bool(s) and not s.startswith("#")
        if is_active and seen == n:
            seen += 1
            continue  # 跳过被删除的行
        if is_active:
            seen += 1
        out.append(line)
    st.session_state[key] = "\n".join(out)


# -- plotly 图表构建 ---------------------------------------
def valuation_figure(daily_df, dcf):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=daily_df["日期"], y=daily_df["收盘"], name="收盘价",
        line=dict(color=COLORS["price"], width=1.5),
    ))
    # 收集估值线：画线（不带内置标注，避免多线挤在同一角），再统一错位放标签
    lines = []
    for name, color, dash in SCENES:
        if dcf and dcf.get("valuations") and name in dcf["valuations"]:
            v = dcf["valuations"][name]["intrinsic_value"]
            fig.add_hline(y=v, line_dash=dash, line_color=color, line_width=1.5)
            lines.append((v, f"{name.split(' ')[0]} {v:.2f}", color))
    # 合理估值上限（min(中性DCF, PE中位×EPS)）单独画一条绿实线
    ceiling = dcf.get("fair_value_ceiling") if dcf else None
    if ceiling:
        fig.add_hline(y=ceiling, line_dash="solid", line_color=COLORS["ceil"],
                      line_width=1.5)
        lines.append((ceiling, f"合理上限 {ceiling:.2f}", COLORS["ceil"]))
    # 标签沿 x 轴错位放置：数值接近/相等时并排而非堆叠，避免重叠
    if daily_df is not None and not daily_df.empty and lines:
        xs = daily_df["日期"]
        xmin, xmax = xs.iloc[0], xs.iloc[-1]
        span = (xmax - xmin)
        n = len(lines)
        for i, (v, text, color) in enumerate(lines):
            xpos = xmin + span * (i + 0.5) / n
            fig.add_annotation(x=xpos, y=v, text=text, showarrow=False,
                               font=dict(color=color, size=11),
                               bgcolor="rgba(255,255,255,0.78)",
                               borderpad=2)
    # 最新价标注
    if not daily_df.empty:
        fig.add_trace(go.Scatter(
            x=[daily_df["日期"].iloc[-1]], y=[daily_df["收盘"].iloc[-1]],
            mode="markers+text", name="最新价",
            text=[f"{daily_df['收盘'].iloc[-1]:.2f}"],
            textposition="top center",
            marker=dict(color=COLORS["price"], size=10),
            showlegend=False,
        ))
    fig.update_layout(xaxis_title="日期", yaxis_title="股价（元）",
                      height=420, hovermode="x unified",
                      legend=dict(orientation="h", y=1.08),
                      margin=dict(t=40))
    return fig


def sensitivity_figure(sensitivity, price=None):
    grid = sensitivity.get("grid") if sensitivity else None
    if grid is None or grid.empty:
        return None
    z = grid.values
    finite = z[np.isfinite(z)]
    if finite.size == 0:
        return None
    vmin, vmax = float(np.nanmin(finite)), float(np.nanmax(finite))
    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        z=z, x=list(grid.columns), y=list(grid.index),
        colorscale=[[0, "#eaf1fb"], [0.5, "#6fa3e6"], [1, "#0d47a1"]],
        text=[[f"{v:.1f}" for v in row] for row in z],
        texttemplate="%{text}", textfont=dict(size=10),
        hovertemplate="永续 %{y} × WACC %{x}<br>内在价值 %{z:.2f} 元<extra></extra>",
        colorbar=dict(title="元/股"),
    ))
    # 现价分界：落在网格值域内才画等值线（plotly Contour 同样无法在值域外渲染），
    # 超界时改用图角标注传达现价与"全面高估/低估"的判定。
    price_in_range = bool(price and price > 0 and vmin < price < vmax)
    if price_in_range:
        fig.add_trace(go.Contour(
            z=z, x=list(grid.columns), y=list(grid.index),
            contours=dict(start=price, end=price, size=1),
            line=dict(color=COLORS["cons"], width=2.5, dash="dash"),
            showscale=False, name=f"现价 {price:.2f}",
        ))
    if price and price > 0:
        if price_in_range:
            note = f"现价 {price:.2f} 元 · 红线左下侧（内在价值 > 现价）= 买入区"
        elif price >= vmax:
            note = (f"现价 {price:.2f} 元 ≥ 网格上限 {vmax:.2f} · "
                    f"全参数下内在价值 < 现价 → 全面高估")
        else:  # price <= vmin
            note = (f"现价 {price:.2f} 元 ≤ 网格下限 {vmin:.2f} · "
                    f"全参数下内在价值 > 现价 → 全面低估")
        fig.add_annotation(
            text=note, xref="paper", yref="paper", x=1.0, y=1.0,
            xanchor="right", yanchor="top", showarrow=False,
            font=dict(color=COLORS["cons"], size=12),
            bgcolor="rgba(255,255,255,0.85)", bordercolor=COLORS["cons"],
            borderwidth=1, borderpad=4,
        )
    fig.update_layout(xaxis_title="折现率 WACC", yaxis_title="永续增长率",
                      height=440, margin=dict(t=20, r=20))
    return fig


def score_bar(score):
    # 评分类别配色（与估值线解耦：质量=绿、估值=橙、情绪=蓝）
    subs = [("质量", score.get("quality"), COLORS["ceil"]),
            ("估值", score.get("valuation"), "#f57c00"),
            ("情绪", score.get("sentiment"), COLORS["price"])]
    names, vals, cols = [], [], []
    for n, v, c in subs:
        if v is not None:
            names.append(n); vals.append(v); cols.append(c)
    fig = go.Figure(go.Bar(
        x=names, y=vals, marker_color=cols,
        text=[f"{v:.0f}" for v in vals], textposition="outside",
        width=0.5,
    ))
    fig.update_layout(yaxis=dict(range=[0, 100], title="得分"), height=260,
                      showlegend=False, margin=dict(t=10, b=10))
    return fig


# -- 回测图表（plotly 交互版，与 backtest_charts.py 同口径）--
def equity_figure(result):
    """策略 vs 基准净值曲线。"""
    eq = result.equity_curve
    fig = go.Figure()
    if eq is None or eq.empty:
        return fig
    fig.add_trace(go.Scatter(
        x=eq.index, y=eq.values, name="策略净值",
        line=dict(color=COLORS["price"], width=1.8),
        hovertemplate="%{x|%Y-%m-%d}<br>净值 %{y:.3f}<extra>策略</extra>",
    ))
    bench = result.benchmark_curve
    if bench is not None and not bench.empty:
        b = bench.reindex(eq.index, method="ffill")
        b0 = b.iloc[0] if not b.empty and b.iloc[0] > 0 else 1.0
        fig.add_trace(go.Scatter(
            x=b.index, y=(b / b0).values, name="基准（沪深300）",
            line=dict(color="#9aa0a6", width=1.3, dash="dash"),
            hovertemplate="%{x|%Y-%m-%d}<br>净值 %{y:.3f}<extra>基准</extra>",
        ))
    # 调仓日竖线
    for rd in result.rebalance_dates:
        fig.add_vline(x=rd, line_color="#bbb", line_width=0.6, line_dash="dot",
                      opacity=0.5)
    fig.update_layout(xaxis_title="日期", yaxis_title="累计净值（起点=1.0）",
                      height=440, hovermode="x unified",
                      legend=dict(orientation="h", y=1.08), margin=dict(t=40))
    return fig


def drawdown_figure(result):
    """水下回撤图（cummax 回撤比例，填色到 0）。"""
    eq = result.equity_curve
    fig = go.Figure()
    if eq is None or eq.empty:
        return fig
    cummax = eq.cummax()
    dd = ((eq - cummax) / cummax.replace(0, np.nan) * 100).fillna(0)
    fig.add_trace(go.Scatter(
        x=dd.index, y=dd.values, name="回撤", fill="tozeroy",
        line=dict(color=COLORS["cons"], width=1),
        fillcolor="rgba(211,47,47,0.30)",
        hovertemplate="%{x|%Y-%m-%d}<br>回撤 %{y:.2f}%<extra></extra>",
    ))
    mdd = (result.metrics or {}).get("max_drawdown")
    title = "回测水下图（最大回撤）"
    if mdd is not None:
        title += f"  ·  最大回撤 ≈ {mdd * 100:.1f}%"
    fig.update_layout(xaxis_title="日期", yaxis_title="回撤（%）",
                      height=320, showlegend=False, margin=dict(t=40))
    fig.update_layout(title=dict(text=title, font=dict(size=13)))
    return fig


def grade_returns_figure(result):
    """各等级平均前向收益柱状图（验证 A/B/C/D 单调性），带 bootstrap CI 误差棒。"""
    gfr = result.grade_forward_returns or {}
    gs = result.grade_signal or {}
    ci_by = gs.get("ci_by_grade", {})
    grades = ["A", "B", "C", "D"]
    means = [(sum(gfr.get(g, [])) / len(gfr.get(g, []))) * 100
             if gfr.get(g) else 0.0 for g in grades]
    samples = [len(gfr.get(g, [])) for g in grades]
    # 误差棒：bootstrap CI 上下半宽（不对称，分数转百分），无 CI 置 0
    err_plus, err_minus = [], []
    for i, g in enumerate(grades):
        m = means[i]  # 百分
        lo, hi = ci_by.get(g, (None, None))
        if lo is not None and hi is not None and samples[i] > 0:
            err_plus.append(max(hi * 100 - m, 0.0))
            err_minus.append(max(m - lo * 100, 0.0))
        else:
            err_plus.append(0.0)
            err_minus.append(0.0)
    fig = go.Figure(go.Bar(
        x=grades, y=means,
        marker_color=[GRADE_COLOR[g] for g in grades],
        text=[f"{m:+.2f}%" if n > 0 else "无样本" for m, n in zip(means, samples)],
        textposition="outside", width=0.55,
        error_y=dict(type="data", array=err_plus, arrayminus=err_minus,
                     visible=True, color="#555", thickness=1.2),
        hovertemplate="等级 %{x}<br>平均前向收益 %{y:+.2f}%<extra></extra>",
    ))
    fig.add_hline(y=0, line_color="#000", line_width=0.8)
    fig.update_layout(yaxis_title="平均前向收益（%）", xaxis_title="综合评分等级",
                      height=340, showlegend=False, margin=dict(t=20, b=10))
    return fig


def _fmt_price(val) -> str:
    """格式化价格为字符串，缺失（None）时返回 N/A；0 元如实显示。
    与 main.py 口径一致——避免 banner 在 latest_price=None（DCF 无估值、
    investment_advice 早返回）时 f"{None:.2f}" 触发 TypeError。"""
    return f"{val:.2f} 元" if val is not None else "N/A"


# -- 渲染：单股分析 ----------------------------------------
def render_single(res):
    ctx = res["ctx"]
    advice = res.get("advice", {}) or {}
    score = res.get("score", {}) or {}
    dcf = res.get("dcf", {}) or {}
    sentiment = res.get("sentiment", {}) or {}
    screening = res.get("screening", {}) or {}
    sensitivity = res.get("sensitivity", {}) or {}
    daily_df = res.get("daily_df")

    grade = score.get("grade", "-")
    gcolor = GRADE_COLOR.get(grade, "#666")
    price = advice.get("latest_price")

    # 顶部：评分徽章 + KPI
    c0, c1, c2, c3 = st.columns([1.3, 1, 1, 1.2])
    c0.markdown(
        f"<div style='padding:8px 0'>"
        f"<span style='font-size:42px;font-weight:700;color:{gcolor}'>{score.get('score', 0):.1f}</span>"
        f"<span style='font-size:18px;color:#888'> / 100</span> "
        f"<span style='font-size:30px;font-weight:700;color:{gcolor}'>{grade}</span>"
        f"</div>", unsafe_allow_html=True)
    c0.caption(f"基本面筛选：{'通过' if score.get('screened') else '未通过'}"
               f"　·　数据完整度：{score.get('completeness_tag', '-')}（{score.get('completeness', 0):.0f}）")
    c1.metric("当前股价", _fmt_price(price))
    c2.metric("市场情绪", sentiment.get("sentiment", "N/A"),
              f"{sentiment.get('percentile', 0):.0f}% 分位" if sentiment.get("percentile") is not None else None)
    c3.markdown(
        f"<div style='padding:8px 0'><div style='font-size:13px;color:#888'>操作建议</div>"
        f"<span style='font-size:24px;font-weight:700;color:{gcolor}'>{advice.get('recommendation','N/A')}</span></div>",
        unsafe_allow_html=True)

    # 估值走势
    st.markdown("#### 📈 估值走势（股价 vs 破产清算/保守/合理估值上限）")
    if dcf.get("valuations") and daily_df is not None and not daily_df.empty:
        st.plotly_chart(valuation_figure(daily_df, dcf), use_container_width=True)
    else:
        st.warning("估值数据不可用")

    # 两列表格
    lc, rc = st.columns(2)
    with lc:
        st.markdown("#### 🧾 基本面筛选")
        tbl = screening.get("table")
        if tbl is not None and not tbl.empty:
            st.dataframe(tbl.style.format(precision=2), use_container_width=True, hide_index=True)
        else:
            st.caption("不可用")
    with rc:
        st.markdown("#### 🧮 DCF 三情景估值")
        if dcf.get("valuations"):
            from config import SCENARIOS
            # 优先用 dcf 内随行业桶构造的 scenario_params（含 CAGR 推导的显性增长），
            # 回退到全局 SCENARIOS（"其他"桶口径，零回归）。
            params = dcf.get("scenario_params") or SCENARIOS
            rows = [{"情景": n, "增长率": f"{p['growth']:.0%}", "永续": f"{p['perpetual']:.0%}",
                     "WACC": f"{p['wacc']:.0%}",
                     "内在价值(元)": f"{dcf['valuations'][n]['intrinsic_value']:.2f}"}
                    for n, p in params.items()]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"基期 FCF：{dcf.get('base_fcf', 0)/1e8:.1f} 亿 · 总股本：{dcf.get('total_shares', 0)/1e8:.2f} 亿股")
            ceiling = dcf.get("fair_value_ceiling")
            if ceiling:
                pe_m = dcf.get("pe_median_5y")
                eps = dcf.get("current_eps")
                if pe_m is not None and eps is not None:
                    st.caption(f"合理估值上限: {ceiling:.2f} 元 (过去5年PE中位 {pe_m:.1f} × 当前EPS {eps:.2f})")
                else:
                    st.caption(f"合理估值上限: {ceiling:.2f} 元（取中性 DCF，PE 锚定数据不足）")
        else:
            st.caption("不可用")

    # 敏感性热力图
    st.markdown("#### 🔥 DCF 敏感性（永续增长率 × WACC → 每股内在价值，红线=现价）")
    sfig = sensitivity_figure(sensitivity, price)
    if sfig:
        st.plotly_chart(sfig, use_container_width=True)

    # 情绪明细 + 评分明细
    sc1, sc2 = st.columns([1, 1])
    with sc1:
        st.markdown("#### 🌡️ 市场情绪与个股估值分位")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("全市场 PE 中位", f"{sentiment.get('pe_median', 0):.2f}")
        _by = sentiment.get("bond_yield")
        m2.metric("10Y 国债", f"{_by * 100:.2f}%" if _by is not None else "N/A")
        m3.metric("个股 PE 分位",
                  f"{sentiment.get('pe_percentile', 0):.0f}%" if sentiment.get("pe_percentile") is not None else "N/A")
        m4.metric("个股 PB 分位",
                  f"{sentiment.get('pb_percentile', 0):.0f}%" if sentiment.get("pb_percentile") is not None else "N/A")
    with sc2:
        st.markdown("#### 🎯 综合评分明细（质量 40% / 估值 35% / 情绪 25%）")
        st.plotly_chart(score_bar(score), use_container_width=True)

    # 最终建议 banner
    st.markdown(
        f"<div style='background:linear-gradient(135deg,#1a3a5f,#2a72d0);color:#fff;"
        f"padding:18px 24px;border-radius:10px;margin-top:8px'>"
        f"<span style='font-size:14px;opacity:.85'>综合建议</span><br>"
        f"<span style='font-size:26px;font-weight:700'>{ctx.stock_label} · 当前 {_fmt_price(price)} → {advice.get('recommendation','N/A')}</span>"
        f"</div>", unsafe_allow_html=True)


# -- 渲染：批量排名 ---------------------------------------
def render_batch(df):
    st.markdown("#### 🏆 批量评分排名")
    st.dataframe(df, use_container_width=True, hide_index=True)
    fig = go.Figure(go.Bar(
        x=df["名称"], y=df["评分"],
        marker_color=[GRADE_COLOR.get(g, "#666") for g in df["等级"]],
        text=[f"{v:.0f}" for v in df["评分"]], textposition="outside", width=0.6,
    ))
    fig.update_layout(yaxis=dict(range=[0, 100], title="综合评分"),
                      xaxis_title="标的", height=360, showlegend=False, margin=dict(t=10))
    st.plotly_chart(fig, use_container_width=True)
    st.success(f"★ 推荐重点关注（评分前 3）：{', '.join(df.head(3)['名称'])}")


# -- 渲染：历史回测 ---------------------------------------
def render_backtest(result: BacktestResult):
    """渲染回测结果：业绩 KPI + 净值/回撤/等级图 + 持仓表 + 信号结论 + 限定。"""
    m = result.metrics or {}

    def _pct(v):
        return f"{v * 100:.2f}%" if v is not None else "N/A"

    def _num(v, d=2):
        return f"{v:.{d}f}" if v is not None else "N/A"

    st.markdown("#### 📊 回测业绩度量")
    r1 = st.columns(4)
    r1[0].metric("总收益", _pct(m.get("total_return")))
    r1[1].metric("年化收益 CAGR", _pct(m.get("cagr")))
    r1[2].metric("年化波动率", _pct(m.get("volatility")))
    r1[3].metric("最大回撤", _pct(m.get("max_drawdown")))
    r2 = st.columns(4)
    r2[0].metric("Sharpe 比率", _num(m.get("sharpe")))
    r2[1].metric("胜率", _pct(m.get("win_rate")))
    r2[2].metric("超额年化（vs 基准）", _pct(m.get("alpha")))
    r2[3].metric("Beta（vs 基准）", _num(m.get("beta")))
    st.caption(f"无风险利率：{_pct(m.get('risk_free'))}　·　调仓期数：{len(result.rebalance_dates)}")

    eq = result.equity_curve
    st.markdown("#### 📈 净值曲线（策略 vs 基准 · 点线=调仓日）")
    if eq is not None and not eq.empty:
        st.plotly_chart(equity_figure(result), use_container_width=True)
        st.markdown("#### 📉 水下回撤图")
        st.plotly_chart(drawdown_figure(result), use_container_width=True)
    else:
        st.warning("净值曲线为空（可能调仓期内无数据或全部退市）。")

    st.markdown("#### 🎯 各等级平均前向收益（信号单调性验证）")
    gcol, tcol = st.columns([1, 1.05])
    with gcol:
        st.plotly_chart(grade_returns_figure(result), use_container_width=True)
    with tcol:
        gfr = result.grade_forward_returns or {}
        gs = result.grade_signal or {}
        ci_by = gs.get("ci_by_grade", {})
        grades = ["A", "B", "C", "D"]
        rows = []
        for g in grades:
            seq = gfr.get(g, [])
            lo, hi = ci_by.get(g, (None, None))
            if seq:
                mean = sum(seq) / len(seq)
                ci_str = (f"[{lo * 100:+.2f}%, {hi * 100:+.2f}%]"
                          if lo is not None and hi is not None else "—")
                rows.append({"等级": g, "样本数": len(seq),
                             "平均前向收益": f"{mean * 100:+.2f}%", "95% CI": ci_str})
            else:
                rows.append({"等级": g, "样本数": 0, "平均前向收益": "—", "95% CI": "—"})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        verdict = gs.get("verdict", "样本不足")
        best = gs.get("best")
        worst = gs.get("worst")
        mean_by = gs.get("mean_by_grade", {})
        n_by = gs.get("n_by_grade", {})
        mono = gs.get("monotonic")
        gap_lo = gs.get("gap_ci_lo")
        gap_hi = gs.get("gap_ci_hi")
        gap_pt = gs.get("gap_point")
        if verdict == "有效" and best and worst:
            st.success(f"★ 信号**有效**：{best} 级 {mean_by[best] * 100:+.2f}% vs {worst} 级 "
                       f"{mean_by[worst] * 100:+.2f}%（差 {gap_pt * 100:+.2f}%，95% CI "
                       f"[{gap_lo * 100:+.2f}%, {gap_hi * 100:+.2f}%]，单调性 ✓）。")
        elif verdict == "无效" and best and worst:
            st.error(f"★ 信号**无效**：{best} 级 {mean_by[best] * 100:+.2f}% vs {worst} 级 "
                     f"{mean_by[worst] * 100:+.2f}%（差 {gap_pt * 100:+.2f}%，95% CI "
                     f"[{gap_lo * 100:+.2f}%, {gap_hi * 100:+.2f}%]，高等级显著跑输）。")
        elif verdict == "待定" and best and worst:
            ci_str = (f"差 {gap_pt * 100:+.2f}%，95% CI [{gap_lo * 100:+.2f}%, "
                      f"{gap_hi * 100:+.2f}%]" if gap_lo is not None else "无法估计差值 CI")
            st.warning(f"★ 信号**待定**：{best} 级 {mean_by[best] * 100:+.2f}% vs {worst} 级 "
                       f"{mean_by[worst] * 100:+.2f}%（{ci_str}，单调性 "
                       f"{'✓' if mono else '✗'}）。CI 跨 0 或单调性破缺，结论不显著。")
        else:
            nb = n_by.get(best, 0) if best else 0
            st.warning(f"★ 信号**样本不足**：最高级 {best or '—'} 仅 {nb} 个样本"
                       f"（需 ≥ {gs.get('min_sample')}），无法判定有效性。")

    st.markdown("#### 📋 持仓明细（逐调仓期）")
    pos_rows = []
    for pos in result.positions:
        dstr = pd.Timestamp(pos["date"]).strftime("%Y-%m-%d")
        for h in pos["holdings"]:
            fr = h.get("forward_return")
            pos_rows.append({
                "调仓日": dstr, "代码": h["symbol"], "等级": h["grade"],
                "评分": round(h["score"], 1), "权重": f"{h['weight'] * 100:.1f}%",
                "前向收益": f"{fr * 100:+.2f}%" if fr is not None else "—",
                "退市/停牌": "是" if h.get("delisted") else "",
            })
    if pos_rows:
        st.dataframe(pd.DataFrame(pos_rows), use_container_width=True,
                     hide_index=True, height=300)
    else:
        st.caption("无持仓记录（可能所有标的均未达 min_grade）。")

    st.info("⚠️ 本回测为「准 PIT」口径（AkShare 财务可能重述）+ 幸存者偏差"
            "（仅含当前在市标的）+ 简化成本（未计滑点/税/停牌流动性），"
            "非严格历史回测，结论仅供研究参考。")


# -- 主界面 -----------------------------------------------
st.title("📊 量化价值投资分析系统")
st.caption("基本面筛选 · DCF 估值 · 市场情绪 · 综合评分 · 敏感性 · 批量选股")

# -- 恢复上次启动时录入的批量排名 / 历史回测标的清单 --
_inputs_cache = _load_dashboard_inputs()
if "batch_symbols" not in st.session_state:
    _cached = _inputs_cache.get("batch_symbols")
    st.session_state["batch_symbols"] = _cached if _cached is not None else _batch_default_text()
if "bt_symbols" not in st.session_state:
    _cached = _inputs_cache.get("bt_symbols")
    st.session_state["bt_symbols"] = _cached if _cached is not None else _batch_default_text()

with st.sidebar:
    st.header("分析参数")
    demo = st.checkbox("Demo 模式（离线模拟数据）", value=False, help="无需联网，使用 seeded 模拟数据")
    # 刷新按钮触发一次强制重拉（绕过磁盘缓存），用 session_state 标记传递
    force_refresh = st.session_state.pop("force_refresh_stock_list", False)
    stock_list = get_stock_list(demo, force_refresh=force_refresh)

    # 实盘模式下股票列表获取失败：提供刷新入口
    # （force_refresh=True 绕过磁盘缓存强制重拉）
    if not demo and stock_list is None:
        st.error("⚠️ 实盘股票列表获取失败（AkShare 接口异常或网络受限）。")
        if st.button("🔄 重新获取股票列表", use_container_width=True):
            st.session_state["force_refresh_stock_list"] = True
            st.rerun()
        st.caption("可点上方刷新重试，或直接输入 6 位股票代码分析，也可勾选 Demo 模式。")

    query = st.text_input("输入股票名称或代码", value="平安银行",
                          help="支持名称片段与模糊匹配，如 平安 / 茅台 / 000001")
    matches = search_stocks(query, stock_list, limit=8) if stock_list is not None else []

    if matches:
        labels = [f"{n}（{c}）" for c, n, _ in matches]
        sel = st.selectbox(f"匹配到 {len(matches)} 只，选择标的", labels, index=0)
        idx = labels.index(sel) if sel in labels else 0
        code, name = matches[idx][0], matches[idx][1]
        st.caption(f"已选：{name}（{code}）")
        _c1, _c2 = st.columns(2)
        _pair_key = f"{code},{name}"
        with _c1:
            if st.button("➕ 批量排名", use_container_width=True):
                if _pair_key not in _active_input_lines(st.session_state.get("batch_symbols", "")):
                    # 追加到输入框文本（保留注释/空行），与右侧「批量排名」输入框同步
                    st.session_state["batch_symbols"] = (
                        st.session_state.get("batch_symbols", "") + "\n" + _pair_key
                    ).lstrip("\n")
                    _save_dashboard_inputs()
                else:
                    st.info(f"{name} 已在批量排名清单中", icon="ℹ️")
        with _c2:
            if st.button("➕ 历史回测", use_container_width=True):
                if _pair_key not in _active_input_lines(st.session_state.get("bt_symbols", "")):
                    st.session_state["bt_symbols"] = (
                        st.session_state.get("bt_symbols", "") + "\n" + _pair_key
                    ).lstrip("\n")
                    _save_dashboard_inputs()
                else:
                    st.info(f"{name} 已在历史回测清单中", icon="ℹ️")
    elif (not demo and stock_list is None
          and str(query).strip().isdigit() and len(str(query).strip()) == 6):
        # 列表不可用时，允许直接输入 6 位代码兜底（不依赖 stock_list 匹配）
        code, name = str(query).strip(), str(query).strip()
        st.info(f"列表不可用，将以代码 {code} 直接作为标的。")
    else:
        if not demo and stock_list is None:
            st.warning("未找到匹配。实盘股票列表获取失败，可直接输入 6 位股票代码"
                       "（如 000001），或勾选 Demo 模式。")
        else:
            st.warning("未找到匹配，请调整输入"
                       + ("（实盘需联网获取股票列表）" if not demo else ""))
        code, name = None, None
    col_y1, col_y2 = st.columns(2)
    _fy_max = int(END_DATE[:4])
    fin_start = col_y1.number_input("基本面起始年", min_value=2010, max_value=_fy_max, value=2021)
    fin_end = col_y2.number_input("基本面结束年", min_value=2010, max_value=_fy_max, value=2025)
    if st.button("🚀 开始分析", type="primary", use_container_width=True):
        if code:
            with st.spinner("正在执行四步分析 + 评分..."):
                try:
                    st.session_state["single"] = run_analysis(code, name, demo, int(fin_start), int(fin_end))
                except Exception as e:
                    st.error(f"分析失败：{e}")
        else:
            st.error("请先选择一个标的")
    st.divider()
    # -- 已添加至清单的标的（批量排名 / 历史回测，与右侧输入框同步）--
    st.caption("📋 已添加标的（与「批量排名 / 历史回测」输入框同步）")
    for _key, _label in [("batch_symbols", "批量排名"), ("bt_symbols", "历史回测")]:
        _lines = _active_input_lines(st.session_state.get(_key, ""))
        with st.expander(f"{_label}（{len(_lines)} 行）", expanded=False):
            if _lines:
                _to_remove = []
                for _i, _line in enumerate(_lines):
                    _l = st.columns([4, 1])
                    _l[0].caption(_line)
                    if _l[1].button("❌", key=f"rm_{_key}_{_i}", use_container_width=True):
                        _to_remove.append(_i)
                for _i in reversed(sorted(_to_remove)):
                    _remove_nth_active_line(_key, _i)  # 保留注释/空行，仅删该有效行
                if _to_remove:
                    _save_dashboard_inputs()
                    st.rerun()  # 重跑以刷新清单与输入框
            else:
                st.caption("在上方搜索股票后点「➕ 批量排名 / ➕ 历史回测」添加，"
                           "或在右侧输入框直接编辑。")
    st.divider()
    st.caption("实盘模式需联网与 AkShare；Demo 模式数据为模拟，非真实行情。")

tab_single, tab_batch, tab_backtest = st.tabs(["单股分析", "批量排名", "历史回测"])

with tab_single:
    if "single" in st.session_state:
        render_single(st.session_state["single"])
    else:
        st.info("👈 在左侧设置参数后点击「🚀 开始分析」")

with tab_batch:
    st.markdown("对多只标的逐只打分并按综合评分排序。")
    st.caption("当前模式：" + ("Demo（离线模拟数据）" if demo else "在线（逐只联网分析，较慢）")
               + "。可在左侧栏切换 Demo 模式；左侧栏搜索后可直接添加至本清单，或在此手动编辑。")
    batch_text = st.text_area(
        "批量标的（每行 `代码,名称` 或仅 `代码`；留空用内置清单）",
        key="batch_symbols", height=120,
        help="每行一只标的，格式 `代码,名称` 或仅代码；# 开头为注释。留空则用内置清单。"
             "清单自动保存，下次启动恢复上次输入。",
        on_change=_save_dashboard_inputs,
    )
    btn_label = "▶ 运行批量打分" + ("（Demo）" if demo else "（在线逐只联网）")
    if st.button(btn_label, type="primary"):
        items = _parse_batch_text(batch_text) if batch_text.strip() else BATCH_DEMO_LIST
        if not items:
            st.error("未解析到任何标的，请按每行 `代码,名称` 输入。")
        else:
            with st.spinner(f"批量分析中（{len(items)} 只 · {'Demo' if demo else '在线'}）..."):
                try:
                    st.session_state["batch"] = run_batch_silent(demo=demo, items=items)
                except Exception as e:
                    st.error(f"批量分析失败：{e}")
    if "batch" in st.session_state:
        render_batch(st.session_state["batch"])

with tab_backtest:
    st.markdown("验证综合评分信号在历史上是否有效（A/B 级是否跑赢 D 级与基准）。"
                "回测以数据注入方式复用四步+评分，**不改算法与权重**。")
    st.caption("当前模式：" + ("Demo（离线模拟数据，全程无网）" if demo else "在线（联网预取全量数据，较慢）")
               + "。可在左侧栏切换 Demo 模式。")

    # -- 回测参数 --
    bt_end_year = int(END_DATE[:4])
    p1, p2, p3, p4 = st.columns(4)
    bt_start_year = p1.number_input("回测起始年", min_value=2010, max_value=bt_end_year,
                                    value=bt_end_year - BACKTEST_LOOKBACK_YEARS,
                                    help="回测区间起始年份（数据需覆盖该年起）")
    bt_end_in = p2.number_input("回测结束年", min_value=2010, max_value=bt_end_year,
                                value=bt_end_year,
                                help="回测区间结束年份（含该年全年数据）")
    _freq_map = {"M": "M（每月 Monthly）", "Q": "Q（每季 Quarterly）", "Y": "Y（每年 Yearly）"}
    _grade_map = {"A": "A（最优）", "B": "B（良好）", "C": "C（一般）", "D": "D（较差）"}
    _weight_map = {"equal": "equal（等权）", "score": "score（按评分加权）"}
    freq_label = p3.selectbox("调仓频率", list(_freq_map.values()),
                              index=list(_freq_map.keys()).index(BACKTEST_REBALANCE_FREQ),
                              help="调仓频率：M=每月 / Q=每季度 / Y=每年")
    freq = list(_freq_map.keys())[list(_freq_map.values()).index(freq_label)]
    min_grade_label = p4.selectbox("最低入选等级", list(_grade_map.values()),
                                   index=list(_grade_map.keys()).index(BACKTEST_MIN_GRADE),
                                   help="仅纳入综合评分 ≥ 该等级的个股（A 最严 → D 最宽）")
    min_grade = list(_grade_map.keys())[list(_grade_map.values()).index(min_grade_label)]
    q1, q2, q3, q4 = st.columns(4)
    top_n = int(q1.number_input("每期 top_n", min_value=1, max_value=50,
                                value=BACKTEST_TOP_N, step=1,
                                help="每次调仓入选的标的数量（按评分从高到低取前 N）"))
    weight_label = q2.selectbox("组合权重", list(_weight_map.values()),
                                index=list(_weight_map.keys()).index(BACKTEST_WEIGHT),
                                help="等权=各标的均分资金 / 按评分加权=分高权重更大")
    weight = list(_weight_map.keys())[list(_weight_map.values()).index(weight_label)]
    txn = q3.number_input("单边交易成本(%)", min_value=0.0, max_value=2.0,
                          value=BACKTEST_TXN_COST * 100, step=0.05,
                          help="单次买入或卖出收取的交易成本（含佣金/印花税/冲击成本的近似）") / 100.0
    hold = int(q4.number_input("持有期(交易日,0=至下期)", min_value=0, max_value=252,
                               value=BACKTEST_HOLD_PERIOD or 0, step=1,
                               help="调仓后固定持有天数再卖出；0 表示持有至下一调仓日才卖出"))
    hold_days = None if hold == 0 else hold

    bt_text = st.text_area(
        "标的清单（每行 `代码,名称` 或仅 `代码`；留空用内置清单）",
        key="bt_symbols", height=120,
        help="每行一只标的，格式 `代码,名称` 或仅代码；# 开头为注释。留空则用内置清单。"
             "左侧栏搜索后可直接添加至本清单；清单自动保存，下次启动恢复上次输入。",
        on_change=_save_dashboard_inputs,
    )
    bt_label = "▶ 运行回测" + ("（Demo）" if demo else "（在线逐只联网预取）")
    if st.button(bt_label, type="primary"):
        items = _parse_batch_text(bt_text) if bt_text.strip() else BATCH_DEMO_LIST
        if not items:
            st.error("未解析到任何标的，请按每行 `代码,名称` 输入。")
        else:
            start = f"{int(bt_start_year)}0101"
            end = f"{int(bt_end_in)}1231"
            with st.spinner(f"回测中（{len(items)} 只 · {start}~{end} · "
                            f"freq={freq} · {'Demo' if demo else '在线'}，请稍候...）"):
                try:
                    st.session_state["backtest"] = run_backtest_silent(
                        items, demo=demo, start=start, end=end,
                        freq=freq, hold_days=hold_days, top_n=top_n,
                        min_grade=min_grade, weight=weight, txn_cost=txn,
                        benchmark=BACKTEST_BENCHMARK)
                except Exception as e:
                    st.error(f"回测失败：{e}")
    if "backtest" in st.session_state:
        render_backtest(st.session_state["backtest"])
    else:
        st.info("👆 设置回测参数后点击「▶ 运行回测」。Demo 模式可即刻离线验证回测机制。")
