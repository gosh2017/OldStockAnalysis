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
  - 支持 --demo 离线模式（无需网络）

复用 main.main(ctx, quiet=True) 的完整分析管线，本文件只负责交互与可视化呈现。
"""
import io
import re
from contextlib import redirect_stdout

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import StockContext
from data import fetch_stock_list, generate_stock_list, search_stocks
from main import main, run_batch, BATCH_DEMO_LIST

# -- 配色（与 matplotlib 图表 / HTML 报告保持一致）---------
COLORS = {
    "price": "#1a73e8", "cons": "#d32f2f", "neu": "#f57c00", "opt": "#2e7d32",
}
GRADE_COLOR = {"A": "#2e7d32", "B": "#1565c0", "C": "#ef6c00", "D": "#c62828"}
SCENES = [
    ("保守 (Conservative)", COLORS["cons"], "dash"),
    ("中性 (Neutral)",      COLORS["neu"],  "solid"),
    ("乐观 (Optimistic)",   COLORS["opt"],  "dot"),
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


# -- plotly 图表构建 ---------------------------------------
def valuation_figure(daily_df, dcf):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=daily_df["日期"], y=daily_df["收盘"], name="收盘价",
        line=dict(color=COLORS["price"], width=1.5),
    ))
    for name, color, dash in SCENES:
        if dcf and dcf.get("valuations") and name in dcf["valuations"]:
            v = dcf["valuations"][name]["intrinsic_value"]
            fig.add_hline(y=v, line_dash=dash, line_color=color, line_width=1.5,
                          annotation_text=f"{name.split(' ')[0]} {v:.2f}",
                          annotation_position="top left")
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
    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        z=z, x=list(grid.columns), y=list(grid.index),
        colorscale=[[0, "#eaf1fb"], [0.5, "#6fa3e6"], [1, "#0d47a1"]],
        text=[[f"{v:.1f}" for v in row] for row in z],
        texttemplate="%{text}", textfont=dict(size=10),
        hovertemplate="增长 %{y} × WACC %{x}<br>内在价值 %{z:.2f} 元<extra></extra>",
        colorbar=dict(title="元/股"),
    ))
    if price and price > 0:
        fig.add_trace(go.Contour(
            z=z, x=list(grid.columns), y=list(grid.index),
            contours=dict(start=price, end=price, size=1),
            line=dict(color=COLORS["cons"], width=2.5, dash="dash"),
            showscale=False, name=f"现价 {price:.2f}",
        ))
    fig.update_layout(xaxis_title="折现率 WACC", yaxis_title="未来 5 年增长率",
                      height=440, margin=dict(t=20))
    return fig


def score_bar(score):
    subs = [("质量", score.get("quality"), COLORS["opt"]),
            ("估值", score.get("valuation"), COLORS["neu"]),
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
    c0.caption(f"基本面筛选：{'通过' if score.get('screened') else '未通过'}")
    c1.metric("当前股价", f"{price:.2f} 元" if price else "N/A")
    c2.metric("市场情绪", sentiment.get("sentiment", "N/A"),
              f"{sentiment.get('percentile', 0):.0f}% 分位" if sentiment.get("percentile") is not None else None)
    c3.markdown(
        f"<div style='padding:8px 0'><div style='font-size:13px;color:#888'>操作建议</div>"
        f"<span style='font-size:24px;font-weight:700;color:{gcolor}'>{advice.get('recommendation','N/A')}</span></div>",
        unsafe_allow_html=True)

    # 估值走势
    st.markdown("#### 📈 估值走势（股价 vs 三情景内在价值）")
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
            rows = [{"情景": n, "增长率": f"{p['growth']:.0%}", "永续": f"{p['perpetual']:.0%}",
                     "WACC": f"{p['wacc']:.0%}",
                     "内在价值(元)": f"{dcf['valuations'][n]['intrinsic_value']:.2f}"}
                    for n, p in SCENARIOS.items()]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"基期 FCF：{dcf.get('base_fcf', 0)/1e8:.1f} 亿 · 总股本：{dcf.get('total_shares', 0)/1e8:.2f} 亿股")
        else:
            st.caption("不可用")

    # 敏感性热力图
    st.markdown("#### 🔥 DCF 敏感性（增长率 × WACC → 每股内在价值，红线=现价）")
    sfig = sensitivity_figure(sensitivity, price)
    if sfig:
        st.plotly_chart(sfig, use_container_width=True)

    # 情绪明细 + 评分明细
    sc1, sc2 = st.columns([1, 1])
    with sc1:
        st.markdown("#### 🌡️ 市场情绪与个股估值分位")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("全市场 PE 中位", f"{sentiment.get('pe_median', 0):.2f}")
        m2.metric("10Y 国债", f"{sentiment.get('bond_yield', 0)*100:.2f}%")
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
        f"<span style='font-size:26px;font-weight:700'>{ctx.stock_label} · 当前 {price:.2f} 元 → {advice.get('recommendation','N/A')}</span>"
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


# -- 主界面 -----------------------------------------------
st.title("📊 量化价值投资分析系统")
st.caption("基本面筛选 · DCF 估值 · 市场情绪 · 综合评分 · 敏感性 · 批量选股")

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
    fin_start = col_y1.number_input("基本面起始年", min_value=2010, max_value=2025, value=2021)
    fin_end = col_y2.number_input("基本面结束年", min_value=2010, max_value=2025, value=2025)
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
    st.caption("实盘模式需联网与 AkShare；Demo 模式数据为模拟，非真实行情。")

tab_single, tab_batch = st.tabs(["单股分析", "批量排名"])

with tab_single:
    if "single" in st.session_state:
        render_single(st.session_state["single"])
    else:
        st.info("👈 在左侧设置参数后点击「🚀 开始分析」")

with tab_batch:
    st.markdown("对多只标的逐只打分并按综合评分排序。")
    st.caption("当前模式：" + ("Demo（离线模拟数据）" if demo else "在线（逐只联网分析，较慢）")
               + "。可在左侧栏切换 Demo 模式；在线模式留空用内置清单亦可逐只联网打分。")
    batch_text = st.text_area(
        "批量标的（每行 `代码,名称` 或仅 `代码`；留空用内置清单）",
        value=_batch_default_text(), height=120,
        help="每行一只标的，格式 `代码,名称` 或仅代码；# 开头为注释。留空则用内置清单。",
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
