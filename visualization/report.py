# -*- coding: utf-8 -*-
"""
HTML 投资报告导出 — 生成自包含的 standalone HTML 报告，便于分享与存档。

内容：
  - 头部：标的、分析日期、综合评分徽章（A–D）
  - 估值走势图（valuation PNG 内嵌为 base64）
  - 基本面筛选表、DCF 三情景估值表、敏感性网格表
  - 市场情绪 + 个股 PE/PB 自身分位
  - 综合评分明细（质量/估值/情绪子分）
  - 最终操作建议
  - 免责声明

不依赖外部资源（图表以 base64 内嵌），单个 .html 文件即可打开。
"""
from __future__ import annotations

import base64
import os
from datetime import datetime

import pandas as pd

from config import SCENARIOS

# 等级配色（A=绿/B=蓝/C=橙/D=红；字母为首要标识，颜色非唯一编码）
_GRADE_COLOR = {"A": "#2e7d32", "B": "#1565c0", "C": "#ef6c00", "D": "#c62828"}


def _img_b64(path: str) -> str | None:
    """读取图片并返回 base64 字符串，文件不存在返回 None。"""
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _df_to_html(df: pd.DataFrame, index: bool = False) -> str:
    """DataFrame → 紧凑 HTML 表格。"""
    if df is None or df.empty:
        return "<p style='color:#888'>数据不可用</p>"
    return df.to_html(index=index, border=0, classes="tbl", float_format=lambda x: f"{x:.2f}")


def _dcf_table(dcf: dict) -> str:
    if not dcf or not dcf.get("valuations"):
        return "<p style='color:#888'>DCF 估值不可用</p>"
    # 优先用 dcf 内随行业桶构造的 scenario_params（含 CAGR 推导的显性增长），
    # 回退到全局 SCENARIOS（"其他"桶口径，零回归）。
    params = dcf.get("scenario_params") or SCENARIOS
    rows = []
    for name, p in params.items():
        v = dcf["valuations"][name]
        rows.append({
            "情景": name,
            "增长率": f"{p['growth']:.1%}",
            "永续增长": f"{p['perpetual']:.1%}",
            "WACC": f"{p['wacc']:.1%}",
            "每股内在价值(元)": f"{v['intrinsic_value']:.2f}",
        })
    return _df_to_html(pd.DataFrame(rows))


def _anchor_cap_table(dcf: dict) -> str:
    """合理估值上限锚定表：PE中位 × EPS 与 中性DCF 取小。"""
    if not dcf or dcf.get("fair_value_ceiling") is None:
        return ""
    rows = [{
        "项目": "过去5年PE中位数",
        "值": f"{dcf.get('pe_median_5y'):.2f}" if dcf.get('pe_median_5y') else "N/A",
    }, {
        "项目": "当前EPS(元)",
        "值": f"{dcf.get('current_eps'):.2f}" if dcf.get('current_eps') else "N/A",
    }, {
        "项目": "PE锚定值(元)",
        "值": f"{dcf.get('pe_anchor_value'):.2f}" if dcf.get('pe_anchor_value') else "N/A",
    }, {
        "项目": "中性DCF(元)",
        "值": f"{dcf.get('neutral_raw'):.2f}" if dcf.get('neutral_raw') else "N/A",
    }, {
        "项目": "合理估值上限(元)",
        "值": f"{dcf.get('fair_value_ceiling'):.2f}",
    }]
    return _df_to_html(pd.DataFrame(rows))


def _score_table(score: dict) -> str:
    if not score:
        return "<p style='color:#888'>评分不可用</p>"
    rows = [{
        "类别": "质量 (40%)",
        "得分": _fmt(score.get("quality")),
        "说明": "ROE 水平/稳定性、股息、利润质量",
    }, {
        "类别": "估值 (35%)",
        "得分": _fmt(score.get("valuation")),
        "说明": "相对保守/中性估值的安全边际",
    }, {
        "类别": "情绪 (25%)",
        "得分": _fmt(score.get("sentiment")),
        "说明": "股债性价比分位 + 个股 PE/PB 分位",
    }]
    return _df_to_html(pd.DataFrame(rows))


def _fmt(x) -> str:
    return f"{x:.1f}" if x is not None else "N/A"


_CSS = """
<style>
  body { font-family: "Microsoft YaHei","PingFang SC",system-ui,sans-serif; background:#f5f6f8; color:#222; margin:0; padding:24px; }
  .wrap { max-width:920px; margin:0 auto; background:#fff; border-radius:12px; box-shadow:0 1px 8px rgba(0,0,0,.06); overflow:hidden; }
  .head { padding:24px 28px; background:linear-gradient(135deg,#1a3a5f,#2a72d0); color:#fff; }
  .head h1 { margin:0 0 6px; font-size:22px; }
  .head .meta { opacity:.9; font-size:13px; }
  .score { display:flex; align-items:center; gap:12px; margin-top:14px; }
  .score .num { font-size:40px; font-weight:700; }
  .score .grade { font-size:26px; font-weight:700; width:52px; height:52px; line-height:52px; text-align:center; border-radius:50%; background:#fff; }
  .section { padding:20px 28px; border-top:1px solid #eee; }
  .section h2 { margin:0 0 12px; font-size:16px; color:#1a3a5f; }
  table.tbl { border-collapse:collapse; width:100%; font-size:13px; }
  table.tbl th, table.tbl td { padding:6px 10px; border-bottom:1px solid #eef; text-align:right; }
  table.tbl th { background:#f0f4fa; color:#1a3a5f; text-align:center; }
  table.tbl td:first-child, table.tbl th:first-child { text-align:left; }
  .rec { font-size:18px; font-weight:700; padding:12px 16px; border-radius:8px; background:#e8f0fe; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
  img.chart { width:100%; border-radius:8px; border:1px solid #eee; }
  .foot { padding:16px 28px; font-size:11px; color:#999; border-top:1px solid #eee; }
  .tag { display:inline-block; padding:2px 8px; border-radius:10px; font-size:12px; background:#eef; color:#1a3a5f; }
</style>
"""


def render_html_report(ctx, daily_df, screening, dcf, sensitivity,
                       sentiment, stock_indicator, advice, score) -> str | None:
    """渲染并保存 HTML 投资报告，返回文件路径。"""
    os.makedirs(ctx.report_dir, exist_ok=True)
    grade = (score or {}).get("grade", "-")
    grade_color = _GRADE_COLOR.get(grade, "#666")
    today = datetime.now().strftime("%Y%m%d")
    path = f"{ctx.report_dir}/report_{ctx.symbol}_{today}.html"

    # 估值图 base64 内嵌
    chart_b64 = _img_b64(f"{ctx.chart_dir}/valuation_{ctx.symbol}.png")
    chart_html = (f'<img class="chart" src="data:image/png;base64,{chart_b64}" />'
                  if chart_b64 else "<p style='color:#888'>估值图不可用</p>")

    # 各表
    fund_html = _df_to_html((screening or {}).get("table"))
    dcf_html = _dcf_table(dcf)
    anchor_html = _anchor_cap_table(dcf)
    sens_html = _df_to_html((sensitivity or {}).get("grid"), index=True)
    score_html = _score_table(score)

    # 情绪数据
    s = sentiment or {}
    sentiment_lines = "".join(
        f"<div><span class='tag'>{k}</span> <b>{v}</b></div>"
        for k, v in [
            ("全市场 PE 中位数", f"{s.get('pe_median'):.2f}" if s.get('pe_median') else "N/A"),
            ("10Y 国债收益率", f"{s.get('bond_yield')*100:.2f}%" if s.get('bond_yield') else "N/A"),
            ("股债性价比 ERP", f"{s.get('equity_risk_premium')*100:.2f}%" if s.get('equity_risk_premium') else "N/A"),
            ("ERP 历史分位", f"{s.get('percentile'):.1f}%" if s.get('percentile') is not None else "N/A"),
            ("市场情绪", s.get('sentiment', 'N/A')),
            ("个股 PE 分位", f"{s.get('pe_percentile'):.1f}%" if s.get('pe_percentile') is not None else "N/A"),
            ("个股 PB 分位", f"{s.get('pb_percentile'):.1f}%" if s.get('pb_percentile') is not None else "N/A"),
        ]
    )

    a = advice or {}
    rec = a.get("recommendation", "N/A")
    price_str = f"{a['latest_price']:.2f}" if a.get("latest_price") else "N/A"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{ctx.name}（{ctx.symbol}）投资报告</title>{_CSS}</head>
<body><div class="wrap">
  <div class="head">
    <h1>{ctx.name}（{ctx.symbol}）价值投资分析报告</h1>
    <div class="meta">分析日期：{datetime.now().strftime('%Y-%m-%d')} · 数据范围：{ctx.start_date} ~ {ctx.end_date} · 基本面：{ctx.fin_start}–{ctx.fin_end}</div>
    <div class="score">
      <span class="num">{(score or {}).get('score', 0):.1f}<span style="font-size:16px;font-weight:400;opacity:.8"> / 100</span></span>
      <span class="grade" style="color:{grade_color}">{grade}</span>
      <span style="opacity:.85;font-size:13px">基本面筛选：{'通过' if (score or {}).get('screened') else '未通过'}</span>
      <span style="opacity:.7;font-size:13px">· 数据完整度：{(score or {}).get('completeness_tag', '-')}（{(score or {}).get('completeness', 0):.0f}）</span>
    </div>
  </div>

  <div class="section">
    <h2>一、估值走势</h2>
    {chart_html}
  </div>

  <div class="grid">
    <div class="section">
      <h2>二、基本面筛选</h2>
      {fund_html}
    </div>
    <div class="section">
      <h2>三、DCF 三情景估值</h2>
      {dcf_html}
      <p style="font-size:12px;color:#666;margin-top:8px">基期 FCF：{(dcf or {}).get('base_fcf', 0)/1e8:.1f} 亿元 · 总股本：{(dcf or {}).get('total_shares', 0)/1e8:.2f} 亿股</p>
      {anchor_html}
    </div>
  </div>

  <div class="section">
    <h2>四、DCF 敏感性（每股内在价值，元）</h2>
    <p style="font-size:12px;color:#666;margin-bottom:8px">行 = 永续增长率，列 = 折现率 WACC</p>
    {sens_html}
  </div>

  <div class="grid">
    <div class="section">
      <h2>五、市场情绪与个股估值分位</h2>
      {sentiment_lines}
    </div>
    <div class="section">
      <h2>六、综合评分明细</h2>
      {score_html}
    </div>
  </div>

  <div class="section">
    <h2>七、最终操作建议</h2>
    <div class="rec">当前股价 {price_str} 元 → <span style="color:{grade_color}">【{rec}】</span></div>
  </div>

  <div class="foot">
    本报告由量化价值投资分析系统自动生成，仅供学习与研究参考，不构成投资建议。
    数据来源：AkShare；--demo 模式为模拟数据，非真实行情。
  </div>
</div></body></html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  [OK] HTML 投资报告已保存: {path}")
    return path
