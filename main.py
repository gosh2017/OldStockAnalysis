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

所有标的/日期/输出目录信息封装在 StockContext 中，由 _cli() 一次性构建，
贯穿整条调用链，避免 config 全局常量被直接引用导致多标的错位。
"""
import argparse
import io
import re
import sys
import unicodedata
from contextlib import redirect_stdout
from datetime import datetime

import pandas as pd

# 注意：UTF-8 stdout 重包装已移至 _cli() 内，避免被 Streamlit/测试
# 等导入本模块时干扰其 stdout 处理。

from config import (
    STOCK_CODE, STOCK_NAME, StockContext, END_DATE,
    BACKTEST_REBALANCE_FREQ, BACKTEST_HOLD_PERIOD, BACKTEST_TOP_N,
    BACKTEST_MIN_GRADE, BACKTEST_WEIGHT, BACKTEST_TXN_COST,
    BACKTEST_BENCHMARK, BACKTEST_LOOKBACK_YEARS, BACKTEST_RISK_FREE,
    BOND_SMOOTH_POINTS,
)
from utils import sep, recent_value
from data import (
    fetch_daily_data,
    fetch_financial_abstract,
    fetch_cashflow_detail,
    fetch_dividend,
    fetch_bond_yield_10y,
    fetch_bond_yield_history,
    fetch_market_pe_history,
    fetch_stock_indicator,
    fetch_industry_info,
    fetch_stock_list,
    generate_all_demo_data,
    generate_stock_list,
    search_stocks,
)
from analysis import (
    fundamental_screening,
    dcf_valuation,
    dcf_sensitivity,
    market_sentiment,
    investment_advice,
    compute_score,
    run_backtest,
    BacktestResult,
)
from visualization import (
    plot_valuation_chart,
    plot_sensitivity_heatmap,
    render_html_report,
    plot_equity_curve,
    plot_drawdown,
    plot_grade_forward_returns,
)


def main(ctx: StockContext, *, quiet: bool = False) -> dict:
    """
    对单只标的执行完整四步分析。

    quiet=True 时抑制分节打印（批量模式下逐只调用时使用），返回结果字典
    供批量排名汇总。
    """
    if not quiet:
        print(f"\n{'=' * 70}")
        print(f"  量化价值投资分析系统")
        print(f"  标的: {ctx.name}（{ctx.symbol}）")
        if ctx.demo:
            print(f"  [!] DEMO MODE (offline mock data)")
        print(f"  分析日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'=' * 70}")

    # -- 1. 获取数据 --
    if ctx.demo:
        if not quiet:
            print("\n[INFO] 使用模拟数据（--demo 模式），跳过网络请求...")
        demo_data = generate_all_demo_data(ctx)
        daily_df        = demo_data["daily_df"]
        fin_abstract    = demo_data["fin_abstract"]
        cashflow_df     = demo_data["cashflow_df"]
        dividend_df     = demo_data["dividend_df"]
        market_df       = demo_data["market_df"]
        bond_yield      = demo_data["bond_yield"]
        stock_indicator = demo_data["stock_indicator"]
        market_pe_history  = demo_data["market_pe_history"]
        bond_yield_history = demo_data["bond_yield_history"]
        industry_info   = demo_data["industry_info"]
    else:
        daily_df        = fetch_daily_data(ctx.symbol, ctx.start_date, ctx.end_date)
        fin_abstract    = fetch_financial_abstract(ctx.symbol)
        cashflow_df     = fetch_cashflow_detail(ctx.symbol)
        dividend_df     = fetch_dividend(ctx.symbol)
        # 市场情绪改用乐咕真实历史市场 PE + 国债历史序列计算 ERP 分位，
        # 弃用不可靠的 spot 快照（东财 spot_em 持续断连、新浪 spot 无 PE 列）。
        market_pe_history  = fetch_market_pe_history()
        bond_yield_history = fetch_bond_yield_history(ctx.end_date)
        if bond_yield_history is not None and not bond_yield_history.empty:
            # 近 N 日中位数降噪（序列不足 2N 取末值，见 recent_value）
            bond_yield = recent_value(bond_yield_history["国债收益率"], BOND_SMOOTH_POINTS)
        else:
            bond_yield = fetch_bond_yield_10y(ctx.end_date)
        market_df       = None
        stock_indicator = fetch_stock_indicator(ctx.symbol)
        industry_info   = fetch_industry_info(ctx.symbol)

    # -- 2. Step 1：基本面筛选 --
    # item C2：传入行业桶（行业分红率假设）与市场 PE（隐含市值口径）。
    # market_pe 取 market_pe_history 末值；取不到传 None（estimate_dividend_yield
    # 内部回退 20）。industry_info 在 demo / live 两分支均可用。
    market_pe = None
    if market_pe_history is not None and not market_pe_history.empty:
        try:
            pe_col_mp = next((c for c in market_pe_history.columns if "市盈率" in str(c)), None)
            if pe_col_mp is not None:
                pe_s_mp = pd.to_numeric(market_pe_history[pe_col_mp], errors="coerce")
                pe_s_mp = pe_s_mp[(pe_s_mp > 0) & (pe_s_mp < 500)].dropna()
                if len(pe_s_mp) > 0:
                    market_pe = float(pe_s_mp.iloc[-1])
        except (KeyError, ValueError, TypeError):
            # 列名缺失/取值异常时回退 None（market_pe 为 best-effort 输入）
            market_pe = None

    screening = fundamental_screening(ctx.symbol, fin_abstract, daily_df, dividend_df,
                                       ctx.fin_start, ctx.fin_end,
                                       bucket=(industry_info or {}).get("bucket") or "其他",
                                       market_pe=market_pe)

    # -- 3. Step 2：DCF 估值（行业画像决定参数 / EPS 算法 / 总股本来源）--
    dcf_result = dcf_valuation(ctx.symbol, fin_abstract, cashflow_df, daily_df,
                                ctx.fin_start, ctx.fin_end, stock_indicator=stock_indicator,
                                industry_info=industry_info, risk_free=bond_yield)

    # -- 3b. DCF 敏感性分析 --
    sensitivity = dcf_sensitivity(dcf_result.get("base_fcf"), dcf_result.get("total_shares"))
    if not quiet and sensitivity.get("grid") is not None:
        sep("第二步·补充：DCF 敏感性分析")
        print("  每股内在价值（元）随 永续增长率 × WACC 的变化：")
        print(sensitivity["grid"].to_string(float_format=lambda x: f"{x:6.2f}"))

    # -- 4. Step 3：市场情绪 --
    sentiment = market_sentiment(market_df, bond_yield, stock_indicator,
                                market_pe_history, bond_yield_history)

    # -- 5. Step 4：综合建议 --
    advice = investment_advice(daily_df, dcf_result, sentiment, screening)

    # -- 6. 综合评分 --
    score = compute_score(screening, dcf_result, sentiment, advice, ctx)
    if not quiet:
        sep("综合评分")
        print(f"  ★ 综合得分: {score['score']:.1f} / 100（等级 {score['grade']}）")
        print(f"  质量分: {_fmt_num(score['quality'])} | "
              f"估值分: {_fmt_num(score['valuation'])} | "
              f"情绪分: {_fmt_num(score['sentiment'])}")
        print(f"  基本面筛选: {'通过' if score['screened'] else '未通过'}"
              f"  |  数据完整度: {score['completeness_tag']}（{score['completeness']:.0f}/100）")
        print(f"  [INFO] 完整度折让: ×{score['completeness_factor']:.2f}")

    # -- 7. 图表 --
    if not ctx.no_chart:
        if dcf_result.get("valuations"):
            plot_valuation_chart(daily_df, dcf_result["valuations"], sentiment, dcf_result, ctx)
        if sensitivity.get("grid") is not None:
            plot_sensitivity_heatmap(sensitivity, ctx, advice.get("latest_price"))

    # -- 8. HTML 报告（--report 时生成）--
    if ctx.report:
        render_html_report(ctx, daily_df, screening, dcf_result, sensitivity,
                           sentiment, stock_indicator, advice, score)

    # -- 最终摘要 --
    if not quiet:
        _print_summary(ctx, advice, score, industry_info)

    return {"ctx": ctx, "screening": screening, "dcf": dcf_result,
            "sentiment": sentiment, "advice": advice, "score": score,
            "sensitivity": sensitivity, "stock_indicator": stock_indicator,
            "daily_df": daily_df, "fin_abstract": fin_abstract,
            "industry_info": industry_info}


def _fmt_price(val) -> str:
    """格式化价格为字符串，缺失（None）时返回 N/A；0 元如实显示。"""
    return f"{val:.2f} 元" if val is not None else "N/A"


def _fmt_num(x) -> str:
    """格式化评分数值，None 时返回 N/A。"""
    return f"{x:.1f}" if x is not None else "N/A"


def _disp_width(s: str) -> int:
    """估算字符串在终端的显示宽度：CJK/全角算 2，其余算 1。"""
    w = 0
    for ch in str(s):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1
    return w


def _pad(s: str, width: int) -> str:
    """按显示宽度右补空格到 width。"""
    return str(s) + " " * max(0, width - _disp_width(s))


def _print_summary(ctx: StockContext, advice: dict, score: dict | None = None,
                   industry_info: dict | None = None) -> None:
    """打印最终分析摘要表格（动态列宽，正确处理 CJK 对齐）。"""
    sep("分析摘要")

    # 行业归属（来自 fetch_industry_info / demo）；缺失时显示 N/A
    ind = (industry_info or {}).get("industry") or "N/A"
    bucket = (industry_info or {}).get("bucket") or "其他"
    industry_label = f"{ind}（{bucket}）" if ind not in ("N/A", None) else "N/A"

    rows = [
        ("标的", ctx.stock_label),
        ("行业归属", industry_label),
        ("当前股价", _fmt_price(advice.get("latest_price"))),
        ("破产清算估值", _fmt_price(advice.get("liquidation"))),
        ("保守估值", _fmt_price(advice.get("conservative"))),
        ("合理估值上限", _fmt_price(advice.get("fair_value_ceiling"))),
        ("基本面筛选", "通过" if advice.get("screened") else "未通过"),
        ("市场情绪", str(advice.get("sentiment", "N/A"))),
    ]
    if score:
        rows.append(("综合评分", f"{score['score']:.1f} / 100（{score['grade']}）"))
    rows.append(("★ 操作建议", str(advice.get("recommendation", "N/A"))))

    label_w = max(_disp_width(lbl) for lbl, _ in rows)
    value_w = max(_disp_width(val) for _, val in rows)
    inner = label_w + value_w + 6  # 两侧各 2 空格 + 中间分隔
    border = "+" + "-" * (inner + 2) + "+"

    print(border)
    for i, (label, value) in enumerate(rows):
        print(f"|  {_pad(label, label_w)}  |  {_pad(value, value_w)}  |")
        # 在估值块与判断块之间插入分隔线，增强可读性
        if label in ("合理估值上限", "市场情绪") and i < len(rows) - 1:
            print(f"|{'-' * (inner + 2)}|")
    print(border)

    if advice.get("recommendation"):
        print("分析完成。")
    else:
        print("部分分析因数据获取失败未能完成。")
    print()


# --batch-demo 的内置标的清单（demo 模式下为 000001 形态的模拟数据，
# 仅用于演示批量打分排名逻辑，非真实数据）
BATCH_DEMO_LIST = [
    ("000001", "平安银行"),
    ("600519", "贵州茅台"),
    ("000651", "格力电器"),
    ("600036", "招商银行"),
    ("601318", "中国平安"),
]


def _read_batch_file(path: str) -> list:
    """读取批量标的文件，每行 '代码,名称'（# 开头为注释，空行跳过）。"""
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"[,，\s]+", line, maxsplit=1)
            code = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else code
            if code:
                items.append((code, name))
    return items


def run_batch(items: list, demo: bool = False, years=None) -> pd.DataFrame:
    """对多只标的逐只执行分析并按综合评分排名。

    years: 可选 (起始年, 结束年)，覆盖 config 默认基本面年份区间。
    """
    mode = "demo" if demo else "live"
    print(f"\n{'=' * 70}\n  批量选股打分（{len(items)} 只标的 · {mode} 模式）\n{'=' * 70}")

    rows = []
    for symbol, name in items:
        # 批量模式抑制图表与逐只步骤打印，静默分析后仅汇总排名
        ctx_kwargs = dict(symbol=symbol, name=name, demo=demo, no_chart=True)
        if years:
            ctx_kwargs["fin_start"], ctx_kwargs["fin_end"] = years
        ctx = StockContext(**ctx_kwargs)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                res = main(ctx, quiet=True)
            sc = res.get("score", {}) or {}
            adv = res.get("advice", {}) or {}
            rows.append({
                "代码": symbol, "名称": name,
                "评分": sc.get("score", 0.0), "等级": sc.get("grade", "-"),
                "完整度": f"{sc.get('completeness_tag', '-')}({sc.get('completeness', 0):.0f})",
                "基本面": "通过" if sc.get("screened") else "未通过",
                "建议": adv.get("recommendation", "N/A"),
            })
        except Exception as e:  # 单只失败不影响整体批量
            rows.append({"代码": symbol, "名称": name, "评分": 0.0, "等级": "-",
                         "完整度": "-", "基本面": "错误", "建议": f"出错:{e}"})

    df = pd.DataFrame(rows).sort_values("评分", ascending=False).reset_index(drop=True)
    sep("批量评分排名")
    print(df.to_string(index=False))
    top = ", ".join(str(n) for n in df.head(3)["名称"])
    print(f"\n  ★ 推荐重点关注（评分前 3）：{top}")
    return df


def run_backtest_flow(items, *, demo: bool, years, no_chart: bool, out_dir: str | None) -> None:
    """历史回测入口：run_backtest → 图表 → 打印业绩表 + 等级前向收益表 + 结论。

    --years 复用为回测区间（YYYY；转 YYYYMMDD 起止）；缺省 end=今天、start=今天−
    BACKTEST_LOOKBACK_YEARS 年。回测参数取 config.BACKTEST_* 集中配置。
    """
    if years:
        if years[0] > years[1]:
            print(f"[X] --years 起始年份 {years[0]} 晚于结束年份 {years[1]}，请调换顺序")
            return
        start, end = f"{years[0]}0101", f"{years[1]}1231"
    else:
        end = END_DATE
        end_year = int(END_DATE[:4])
        start = f"{end_year - BACKTEST_LOOKBACK_YEARS}0101"

    kwargs = dict(symbol="backtest", name="历史回测", demo=demo, no_chart=no_chart)
    if out_dir:
        kwargs["out_dir"] = out_dir
        kwargs["chart_dir"] = f"{out_dir}/charts"
        kwargs["report_dir"] = f"{out_dir}/reports"
    ctx = StockContext(**kwargs)

    mode = "demo" if demo else "live"
    print(f"\n{'=' * 70}\n  历史回测验证（{len(items)} 只标的 · {start} ~ {end} · {mode} 模式）\n{'=' * 70}")
    if demo:
        print("[INFO] --backtest-demo：使用 seeded 模拟数据，全程无网（非真实行情）")

    result = run_backtest(
        items, start=start, end=end,
        freq=BACKTEST_REBALANCE_FREQ, hold_days=BACKTEST_HOLD_PERIOD,
        top_n=BACKTEST_TOP_N, min_grade=BACKTEST_MIN_GRADE,
        weight=BACKTEST_WEIGHT, txn_cost=BACKTEST_TXN_COST,
        benchmark=BACKTEST_BENCHMARK, demo=demo,
    )

    if not no_chart:
        plot_equity_curve(result, ctx)
        plot_drawdown(result, ctx)
        plot_grade_forward_returns(result, ctx)

    _print_backtest_summary(result, demo)


def _print_backtest_summary(result: BacktestResult, demo: bool) -> None:
    """打印回测业绩表 + 各等级前向收益表 + 信号有效性结论。"""
    m = result.metrics or {}

    def _pct(v):
        return f"{v * 100:.2f}%" if v is not None else "N/A"

    sep("回测业绩度量")
    rows = [
        ("总收益",       _pct(m.get("total_return"))),
        ("年化收益 CAGR", _pct(m.get("cagr"))),
        ("年化波动率",    _pct(m.get("volatility"))),
        ("最大回撤",      _pct(m.get("max_drawdown"))),
        ("Sharpe 比率",   f"{m.get('sharpe'):.2f}" if m.get("sharpe") is not None else "N/A"),
        ("胜率",          _pct(m.get("win_rate"))),
        ("超额年化（vs 基准）", _pct(m.get("alpha"))),
        ("Beta（vs 基准）",  f"{m.get('beta'):.2f}" if m.get("beta") is not None else "N/A"),
        ("无风险利率",     _pct(m.get("risk_free"))),
        ("调仓期数",       str(len(result.rebalance_dates))),
    ]
    label_w = max(_disp_width(lbl) for lbl, _ in rows)
    value_w = max(_disp_width(val) for _, val in rows)
    inner = label_w + value_w + 6
    border = "+" + "-" * (inner + 2) + "+"
    print(border)
    for lbl, val in rows:
        print(f"|  {_pad(lbl, label_w)}  |  {_pad(val, value_w)}  |")
    print(border)

    # 各等级前向收益表
    sep("各等级前向收益（信号单调性验证）")
    gfr = result.grade_forward_returns or {}
    gs = result.grade_signal or {}
    ci_by = gs.get("ci_by_grade", {})
    grades = ["A", "B", "C", "D"]
    g_rows = []
    for g in grades:
        seq = gfr.get(g, [])
        n = len(seq)
        mean = (sum(seq) / len(seq)) if seq else None
        lo, hi = ci_by.get(g, (None, None))
        ci_str = (f"[{lo * 100:+.2f}%, {hi * 100:+.2f}%]"
                  if (lo is not None and hi is not None) else "—")
        g_rows.append({
            "等级": g,
            "样本数": n,
            "平均前向收益": f"{mean * 100:+.2f}%" if mean is not None else "—",
            "95% CI": ci_str,
        })
    print(pd.DataFrame(g_rows).to_string(index=False))

    # 结论：3 态判定（有效 / 待定 / 无效 / 样本不足），读 grade_signal
    verdict = gs.get("verdict", "样本不足")
    best_g = gs.get("best")
    worst_g = gs.get("worst")
    mean_by = gs.get("mean_by_grade", {})
    n_by = gs.get("n_by_grade", {})
    mono = gs.get("monotonic")
    gap_lo = gs.get("gap_ci_lo")
    gap_hi = gs.get("gap_ci_hi")
    gap_pt = gs.get("gap_point")
    mono_str = "是" if mono else "否"
    print()
    if verdict == "有效" and best_g and worst_g:
        print(f"  ★ 信号有效：{best_g} 级 {mean_by[best_g] * 100:+.2f}% vs {worst_g} 级 "
              f"{mean_by[worst_g] * 100:+.2f}%（差 {gap_pt * 100:+.2f}%，95% CI "
              f"[{gap_lo * 100:+.2f}%, {gap_hi * 100:+.2f}%]，单调性 {mono_str}）。")
    elif verdict == "无效" and best_g and worst_g:
        print(f"  ★ 信号无效：{best_g} 级 {mean_by[best_g] * 100:+.2f}% vs {worst_g} 级 "
              f"{mean_by[worst_g] * 100:+.2f}%（差 {gap_pt * 100:+.2f}%，95% CI "
              f"[{gap_lo * 100:+.2f}%, {gap_hi * 100:+.2f}%]，高等级显著跑输）。")
    elif verdict == "待定" and best_g and worst_g:
        ci_str = (f"差 {gap_pt * 100:+.2f}%，95% CI [{gap_lo * 100:+.2f}%, {gap_hi * 100:+.2f}%]"
                  if gap_lo is not None else "无法估计差值 CI")
        print(f"  ★ 信号待定：{best_g} 级 {mean_by[best_g] * 100:+.2f}% vs {worst_g} 级 "
              f"{mean_by[worst_g] * 100:+.2f}%（{ci_str}，单调性 {mono_str}）。"
              f"CI 跨 0 或单调性破缺，结论不显著。")
    else:
        nb = n_by.get(best_g, 0) if best_g else 0
        print(f"  ★ 信号样本不足：最高级 {best_g or '—'} 仅 {nb} 个样本"
              f"（需 ≥ {gs.get('min_sample')}），无法判定有效性。")

    # 诚实限定
    print("\n  [限定] 本回测为「准 PIT」口径（AkShare 财务可能重述）+ 幸存者偏差"
          "（仅含当前在市标的）+ 简化成本（未计滑点/税/停牌流动性），"
          "非严格历史回测，结论仅供研究参考。")
    if demo:
        print("  [限定] --backtest-demo 使用模拟数据，前向收益为随机序列，"
              "仅验证回测机制（引擎/度量/图表）可运行，不构成真实信号证据。")
    print()





def _lookup_stock_name(code: str, demo: bool) -> str | None:
    """按 6 位代码反查股票名称（best-effort：列表不可用/未命中返回 None，
    由调用方兜底为代码本身，避免非默认标的被误标为默认名称）。"""
    stock_list = generate_stock_list() if demo else fetch_stock_list()
    if stock_list is None or stock_list.empty:
        return None
    hit = stock_list.loc[stock_list["代码"].astype(str) == code]
    return None if hit.empty else str(hit["名称"].iloc[0])


def resolve_symbol(query: str, demo: bool) -> tuple:
    """
    把用户输入解析为 (code, name)。支持：
      - 6 位代码 → 反查股票列表取名称（best-effort，取不到由 _cli 兜底为代码）
      - 名称/片段 → 模糊搜索；唯一或高置信匹配直接采用，多匹配列出候选。
    解析失败返回 (None, None)。
    """
    q = str(query).strip()
    if not q:
        return None, None
    if re.fullmatch(r"\d{6}", q):
        return q, _lookup_stock_name(q, demo)
    # 名称/模糊 → 搜索
    stock_list = generate_stock_list() if demo else fetch_stock_list()
    matches = search_stocks(q, stock_list, limit=8)
    if not matches:
        print(f"[X] 未找到匹配「{q}」的股票。")
        return None, None
    # 唯一匹配 或 首个分数 >= 95（精确命中）→ 直接采用
    if len(matches) == 1 or matches[0][2] >= 95:
        code, name = matches[0][0], matches[0][1]
        print(f"[OK] 「{q}」→ {name}（{code}）")
        return code, name
    print(f"[!] 「{q}」匹配到 {len(matches)} 只，请用代码重试：")
    for code, name, _ in matches:
        print(f"    {code}  {name}")
    return None, None


def _cli():
    # Force UTF-8 stdout on Windows (avoids GBK encoding errors with Chinese/emoji)
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="量化价值投资分析系统 — A 股长线价值投资分析"
    )
    parser.add_argument(
        "symbol", nargs="?", default=STOCK_CODE,
        help=f"股票代码或名称（如 000001 或 平安银行，名称支持模糊搜索；默认 {STOCK_CODE}）",
    )
    parser.add_argument(
        "-n", "--name", default=None,
        help="股票名称（用于图表标题）；不指定时默认标的取 平安银行，其它代码反查或回退代码",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="使用模拟数据离线运行（无需网络连接）",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="生成自包含 HTML 投资报告（reports/report_<代码>_<日期>.html）",
    )
    parser.add_argument(
        "--no-chart", action="store_true",
        help="不生成图表文件",
    )
    parser.add_argument(
        "--out-dir", default=None,
        help="输出目录（图表/报告将存于其下的 charts/ 与 reports/ 子目录）",
    )
    parser.add_argument(
        "--years", nargs=2, type=int, metavar=("START", "END"), default=None,
        help="基本面年份范围，如 --years 2020 2024（默认 config 的 2021 2025）",
    )
    # 四种运行模式互斥（同时指定两种 → argparse 报错，而非静默取先到者）
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--batch", default=None,
        help="批量选股：传入含 '代码,名称' 的文本文件路径逐只打分排名",
    )
    mode_group.add_argument(
        "--batch-demo", action="store_true",
        help="批量选股 demo：用内置标的清单 + 模拟数据打分排名（无需网络）",
    )
    mode_group.add_argument(
        "--backtest", default=None,
        help="历史回测：传入含 '代码,名称' 的文本文件，验证信号历史有效性（实盘联网）",
    )
    mode_group.add_argument(
        "--backtest-demo", action="store_true",
        help="历史回测 demo：内置标的清单 + seeded 模拟数据，全程无网验证回测机制",
    )
    args = parser.parse_args()

    # -- 回测模式分发 --
    if args.backtest_demo:
        run_backtest_flow(BATCH_DEMO_LIST, demo=True, years=args.years,
                          no_chart=args.no_chart, out_dir=args.out_dir)
        return
    if args.backtest:
        items = _read_batch_file(args.backtest)
        if not items:
            print(f"[X] 未从 {args.backtest} 读到任何标的（每行格式：代码,名称）")
            return
        run_backtest_flow(items, demo=args.demo, years=args.years,
                          no_chart=args.no_chart, out_dir=args.out_dir)
        return

    # -- 批量模式分发 --
    if args.batch_demo:
        run_batch(BATCH_DEMO_LIST, demo=True, years=args.years)
        return
    if args.batch:
        items = _read_batch_file(args.batch)
        if not items:
            print(f"[X] 未从 {args.batch} 读到任何标的（每行格式：代码,名称）")
            return
        run_batch(items, demo=args.demo, years=args.years)
        return

    # 解析标的：代码反查名称 / 名称模糊搜索
    code, name = resolve_symbol(args.symbol, args.demo)
    if not code:
        return
    stock_name = name or args.name
    if stock_name is None:
        # 未显式指定名称：默认标的用 STOCK_NAME，其它代码用代码本身兜底，
        # 避免把非默认标的误标为 平安银行
        stock_name = STOCK_NAME if code == STOCK_CODE else code

    kwargs = dict(symbol=code, name=stock_name, demo=args.demo,
                  report=args.report, no_chart=args.no_chart)
    if args.out_dir:
        kwargs["out_dir"] = args.out_dir
        kwargs["chart_dir"] = f"{args.out_dir}/charts"
        kwargs["report_dir"] = f"{args.out_dir}/reports"
    if args.years:
        kwargs["fin_start"], kwargs["fin_end"] = args.years
    ctx = StockContext(**kwargs)
    main(ctx)


if __name__ == "__main__":
    _cli()
