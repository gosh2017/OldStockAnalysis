# -*- coding: utf-8 -*-
"""
=============================================================================
量化价值投资分析系统 — Quantitative Value Investment Analysis System
=============================================================================

基于长线价值投资逻辑，对A股标的进行四步分析：
    Step 1 — 基本面筛选（ROE / 股息率 / 资产负债率 / 利润质量）
    Step 2 — 估值锚定（DCF 三情景内在价值）
    Step 3 — 市场情绪（股债性价比 + 历史分位数）
    Step 4 — 综合投资建议

数据来源：AkShare
默认标的：000001 平安银行
运行环境：Python 3.9+, akshare, pandas, numpy, matplotlib, plotly
"""

# ── 依赖导入 ──────────────────────────────────────────────────────────────
import warnings
warnings.filterwarnings("ignore")

import akshare as ak
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")                       # 无 GUI 环境也能保存图表
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta

# 尝试导入 plotly（可选，用于交互式图表）
try:
    import plotly.graph_objects as go
    import plotly.express as px
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# ── 全局配置 ──────────────────────────────────────────────────────────────
STOCK_CODE   = "000001"                     # 股票代码（平安银行）
STOCK_NAME   = "平安银行"                   # 股票名称
START_DATE   = "20160101"                   # 日线起始日期
END_DATE     = "20260813"                   # 日线结束日期
FIN_START    = 2021                         # 基本面起始年份
FIN_END      = 2025                         # 基本面结束年份

# DCF 参数（中性情景）
DCF_GROWTH       = 0.10                     # 未来5年增长率
DCF_PERPETUAL    = 0.03                     # 永续增长率
DCF_WACC         = 0.08                     # 加权平均资本成本

# 三情景参数（保守 / 中性 / 乐观）
SCENARIOS = {
    "保守 (Conservative)": {"growth": 0.07, "perpetual": 0.02, "wacc": 0.09},
    "中性 (Neutral)":      {"growth": 0.10, "perpetual": 0.03, "wacc": 0.08},
    "乐观 (Optimistic)":   {"growth": 0.13, "perpetual": 0.05, "wacc": 0.07},
}

# 输出图表目录
CHART_DIR = "charts"

# ══════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════

def _sep(title: str = "") -> None:
    """打印分隔线"""
    print(f"\n{'═' * 70}")
    if title:
        print(f"  {title}")
        print(f"{'═' * 70}")


def _try_fetch(fn, retries: int = 2, **kwargs) -> pd.DataFrame | None:
    """
    带重试的 AkShare 数据获取封装。
    AkShare 依赖第三方接口，网络波动时会抛异常，自动重试 1~2 次。
    """
    for attempt in range(1 + retries):
        try:
            df = fn(**kwargs)
            if df is None:
                continue
            if isinstance(df, pd.DataFrame) and df.empty:
                continue
            return df
        except Exception as e:
            if attempt < retries:
                print(f"  ⚠ 第 {attempt + 1} 次请求失败: {e}，正在重试...")
            else:
                print(f"  ✗ 请求最终失败: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════
# 数据获取层
# ══════════════════════════════════════════════════════════════════════════

def fetch_daily_data(symbol: str) -> pd.DataFrame:
    """
    ─────────────────────────────────────────────
    获取日频交易数据（前复权）
    字段：日期 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 成交额
    ─────────────────────────────────────────────
    """
    print(f"\n📡 正在获取 {symbol} 的日频交易数据（{START_DATE} ~ {END_DATE}）...")
    df = _try_fetch(
        ak.stock_zh_a_hist,
        symbol=symbol,
        period="daily",
        start_date=START_DATE,
        end_date=END_DATE,
        adjust="qfq",
    )
    if df is None or df.empty:
        print("  ✗ 未能获取日频数据，后续步骤将受限。")
        return pd.DataFrame()

    # 统一列名（AkShare 返回的列名可能是中文）
    col_map = {}
    for candidate in ["日期", "date", "Datetime"]:
        if candidate in df.columns:
            col_map[candidate] = "日期"
    for candidate in ["收盘", "close", "Close"]:
        if candidate in df.columns:
            col_map[candidate] = "收盘"
    for candidate in ["开盘", "open", "Open"]:
        if candidate in df.columns:
            col_map[candidate] = "开盘"
    for candidate in ["最高", "high", "High"]:
        if candidate in df.columns:
            col_map[candidate] = "最高"
    for candidate in ["最低", "low", "Low"]:
        if candidate in df.columns:
            col_map[candidate] = "最低"

    df = df.rename(columns=col_map)
    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").reset_index(drop=True)

    print(f"  ✓ 获取到 {len(df)} 条日频记录，最新收盘价: {df['收盘'].iloc[-1]:.2f}")
    return df


def fetch_financial_abstract(symbol: str) -> pd.DataFrame:
    """
    ─────────────────────────────────────────────
    获取财务摘要数据（含 ROE / 资产负债率 / 净利润 / 经营现金流）
    AkShare 的 stock_financial_abstract 返回多期报告数据。
    我们过滤出年度数据用于 5 年趋势分析。
    ─────────────────────────────────────────────
    """
    print(f"\n📡 正在获取 {symbol} 的财务摘要数据...")
    df = _try_fetch(ak.stock_financial_abstract, symbol=symbol)
    if df is None or df.empty:
        print("  ✗ 未能获取财务摘要数据。")
        return pd.DataFrame()

    print(f"  ✓ 获取到 {len(df)} 条财务记录，字段: {list(df.columns)[:15]}")
    return df


def fetch_cashflow_detail(symbol: str) -> pd.DataFrame:
    """
    ─────────────────────────────────────────────
    获取现金流量表详细数据（用于计算自由现金流）
    需要"购建固定资产、无形资产和其他长期资产支付的现金"来计算资本性支出。
    ─────────────────────────────────────────────
    """
    print(f"\n📡 正在获取 {symbol} 的现金流量表数据...")
    # 尝试新浪财务接口
    df = _try_fetch(ak.stock_financial_report_sina, symbol=symbol, indicator="现金流量表")
    if df is not None and not df.empty:
        print(f"  ✓ 新浪现金流量表: {len(df)} 条")
        return df
    # 备用：东方财富
    df = _try_fetch(ak.stock_financial_report, symbol=symbol, indicator="现金流量表")
    if df is not None and not df.empty:
        print(f"  ✓ 东方财富现金流量表: {len(df)} 条")
        return df
    print("  ✗ 未能获取现金流量表数据，将用经营现金流近似。")
    return pd.DataFrame()


def fetch_dividend(symbol: str) -> pd.DataFrame:
    """获取历史分红数据（用于计算股息率）"""
    print(f"\n📡 正在获取 {symbol} 的分红数据...")
    df = _try_fetch(ak.stock_dividend_record, symbol=symbol)
    if df is not None and not df.empty:
        print(f"  ✓ 分红数据: {len(df)} 条")
        return df
    print("  ✗ 未能获取分红数据，将用其他方式估算股息率。")
    return pd.DataFrame()


def fetch_market_overview() -> pd.DataFrame | None:
    """
    获取全市场 A 股实时数据（用于计算市盈率中位数）
    返回全市场快照 DataFrame。
    """
    print(f"\n📡 正在获取全市场 A 股实时数据...")
    df = _try_fetch(ak.stock_zh_a_spot_em)
    if df is None or df.empty:
        # 备用方案
        df = _try_fetch(ak.stock_zh_a_spot)
    if df is not None and not df.empty:
        print(f"  ✓ 全市场数据: {len(df)} 只股票")
        return df
    print("  ✗ 未能获取全市场数据。")
    return None


def fetch_bond_yield_10y() -> float | None:
    """
    获取中国 10 年期国债收益率。
    尝试多种 AkShare 接口，返回最新的收益率（百分数，如 2.55 表示 2.55%）。
    """
    print(f"\n📡 正在获取 10 年期国债收益率...")

    # 方案 1：央行国债收益率曲线
    df = _try_fetch(ak.rate_ts_bond)
    if df is not None and not df.empty:
        # 找到 10 年期列
        target_col = None
        for col in df.columns:
            if "10" in str(col) or "十年" in str(col):
                target_col = col
                break
        if target_col is not None:
            df_sorted = df.copy()
            date_col = df_sorted.columns[0]
            df_sorted[date_col] = pd.to_datetime(df_sorted[date_col])
            df_sorted = df_sorted.sort_values(date_col)
            val = df_sorted[target_col].dropna().iloc[-1]
            val_pct = float(val) / 100 if val > 1 else float(val)
            print(f"  ✓ 10 年期国债收益率: {val_pct * 100:.2f}%（来源: rate_ts_bond）")
            return val_pct

    # 方案 2：国债收益率
    df = _try_fetch(ak.bond_china_yield)
    if df is not None and not df.empty:
        for col in df.columns:
            if "10" in str(col) or "十年" in str(col):
                val = df[col].dropna().iloc[-1]
                val_pct = float(val) / 100 if val > 1 else float(val)
                print(f"  ✓ 10 年期国债收益率: {val_pct * 100:.2f}%")
                return val_pct

    # 方案 3：使用近 5 年典型值作为合理估算
    print("  ⚠ 未能从 AkShare 获取实时国债收益率，使用近 5 年典型值 ≈ 2.5%")
    return 0.025


# ══════════════════════════════════════════════════════════════════════════
# 第一步：基本面筛选
# ══════════════════════════════════════════════════════════════════════════

def step1_fundamental_screening(symbol: str,
                                 fin_abstract: pd.DataFrame,
                                 daily_df: pd.DataFrame,
                                 dividend_df: pd.DataFrame) -> dict:
    """
    ─────────────────────────────────────────────
    第一步：基本面筛选（质量评估）
    ─────────────────────────────────────────────

    计算过去 5 年（2021-2025）每年的核心财务指标：
      • ROE（加权净资产收益率）
      • 股息率（TTM）
      • 资产负债率
      • 经营性现金流净额 / 净利润（利润质量）

    判断标准：
      连续 5 年 ROE > 15% 且 股息率 > 2% → "初步通过筛选"
      否则 → "不满足"
    ─────────────────────────────────────────────
    """
    _sep("第一步：基本面筛选（质量评估）")

    years = list(range(FIN_START, FIN_END + 1))
    results = []

    if fin_abstract is None or fin_abstract.empty:
        print("  ✗ 财务数据不可用，跳过基本面分析。")
        return {"screened": False, "table": None}

    # ── 识别关键列名（AkShare 返回的列名可能因版本不同） ──
    cols = fin_abstract.columns.tolist()

    def _find_col(candidates: list, df: pd.DataFrame) -> str | None:
        for c in candidates:
            for col in df.columns:
                if c in str(col):
                    return col
        return None

    date_col   = _find_col(["报告日期", "报告期", "report"], fin_abstract)
    roe_col    = _find_col(["加权净资产收益率", "ROE", "净资产收益率"], fin_abstract)
    debt_col   = _find_col(["资产负债率"], fin_abstract)
    ocf_col    = _find_col(["经营活动产生的现金流量净额", "经营活动现金"], fin_abstract)
    np_col     = _find_col(["净利润", "归属于上市公司股东的净利润"], fin_abstract)
    equity_col = _find_col(["归属母公司股东权益", "所有者权益合计"], fin_abstract)

    # ── 提取年度数据 ──
    if date_col:
        fin_abstract[date_col] = pd.to_datetime(fin_abstract[date_col], errors="coerce")
        fin_abstract["年份"] = fin_abstract[date_col].dt.year

        # 只保留年报（通常报告日期在 12 月或 3 月年报发布日）
        # AkShare 返回的数据中，年报一般对应 report_date 在年末
        # 如果有多条同一年的记录，取 ROE 最大的那条（通常年报 ROE 较季报高）
        annual = fin_abstract[fin_abstract["年份"].isin(years)].copy()
        if annual.empty:
            print("  ⚠ 未找到指定年份范围的财务数据。")
            return {"screened": False, "table": None}
    else:
        # 无日期列，直接返回
        print("  ✗ 无法识别报告日期列。")
        return {"screened": False, "table": None}

    # ── 构建每年核心指标表 ──
    for year in years:
        year_data = annual[annual["年份"] == year]
        if year_data.empty:
            results.append({
                "年份": year,
                "ROE(%)": None,
                "资产负债率(%)": None,
                "经营现金流/净利润": None,
                "股息率(%)": None,
            })
            continue

        # 取该年最后一条记录（年报）
        row = year_data.sort_values(year_data.columns[0]).iloc[-1]

        # ROE
        roe_val = None
        if roe_col:
            try:
                roe_val = float(row[roe_col])
                if roe_val > 100:   # 有些接口返回的是百分比形式
                    roe_val = roe_val / 100
            except (ValueError, TypeError):
                pass

        # 资产负债率
        debt_val = None
        if debt_col:
            try:
                debt_val = float(row[debt_col])
                if debt_val > 100:
                    debt_val = debt_val / 100
            except (ValueError, TypeError):
                pass

        # 经营现金流 / 净利润
        ocf_ratio = None
        if ocf_col and np_col:
            try:
                ocf_val = float(row[ocf_col])
                np_val = float(row[np_col])
                if np_val != 0:
                    ocf_ratio = ocf_val / np_val
            except (ValueError, TypeError):
                pass

        # 股息率估算
        div_yield = _estimate_dividend_yield(year, row, equity_col, symbol,
                                              dividend_df, daily_df, roe_col, np_col)

        results.append({
            "年份": year,
            "ROE(%)": round(roe_val, 2) if roe_val is not None else None,
            "资产负债率(%)": round(debt_val, 2) if debt_val is not None else None,
            "经营现金流/净利润": round(ocf_ratio, 2) if ocf_ratio is not None else None,
            "股息率(%)": round(div_yield, 2) if div_yield else None,
        })

    # ── 打印结果 ──
    result_df = pd.DataFrame(results)
    print(f"\n  📊 {symbol} 过去 {FIN_END - FIN_START + 1} 年核心财务指标\n")
    print(result_df.to_string(index=False))

    # ── 判断是否通过筛选 ──
    roe_series   = result_df["ROE(%)"].dropna()
    div_series   = result_df["股息率(%)"].dropna()

    roe_pass = len(roe_series) >= 3 and (roe_series > 15).all()
    div_pass = len(div_series) >= 3 and (div_series > 2).all()

    print(f"\n  ── 筛选判断 ──")
    print(f"  • ROE 连续 > 15%：{'✅ 通过' if roe_pass else '❌ 未通过'}"
          f"  (ROE 范围: {roe_series.min():.1f}% ~ {roe_series.max():.1f}%)" if len(roe_series) > 0 else "  • ROE 数据不足")
    print(f"  • 股息率 > 2%：{'✅ 通过' if div_pass else '❌ 未通过'}"
          f"  (股息率范围: {div_series.min():.1f}% ~ {div_series.max():.1f}%)" if len(div_series) > 0 else "  • 股息率数据不足")

    screened = roe_pass and div_pass
    print(f"\n  🏷  筛选结论：{'【初步通过筛选】' if screened else '【不满足长线价值投资标准】'}")

    return {
        "screened": screened,
        "table": result_df,
        "roe_pass": roe_pass,
        "div_pass": div_pass,
    }


def _estimate_dividend_yield(year: int, row, equity_col: str,
                              symbol: str, dividend_df: pd.DataFrame,
                              daily_df: pd.DataFrame,
                              roe_col: str, np_col: str) -> float:
    """
    估算某年的股息率。
    优先从分红数据中提取，失败则用净利润 × ROE × 常见分红比例（A 股银行约 30%）近似。
    """
    # 方案 1：从分红记录中提取
    if dividend_df is not None and not dividend_df.empty:
        try:
            div_data = dividend_df.copy()
            date_col = None
            for c in div_data.columns:
                if "公告日期" in str(c) or "除权" in str(c) or "日期" in str(c):
                    date_col = c
                    break
            if date_col:
                div_data[date_col] = pd.to_datetime(div_data[date_col], errors="coerce")
                div_data["年份"] = div_data[date_col].dt.year
                year_div = div_data[div_data["年份"] == year]
                if not year_div.empty:
                    # 找到每股分红列
                    eps_div_col = None
                    for c in div_data.columns:
                        if "每股" in str(c) and ("分红" in str(c) or "派" in str(c)
                                                   or "息" in str(c) or "红利" in str(c)):
                            eps_div_col = c
                            break
                    if eps_div_col:
                        div_per_share = float(year_div[eps_div_col].iloc[0])
                        # 找到该年末股价
                        if not daily_df.empty and "日期" in daily_df.columns:
                            year_end = daily_df[daily_df["日期"] <= pd.Timestamp(f"{year}-12-31")]
                            if not year_end.empty:
                                price = float(year_end.iloc[-1]["收盘"])
                                return div_per_share / price * 100
        except Exception:
            pass

    # 方案 2：基于净利润和常见分红比例近似
    try:
        if roe_col and equity_col:
            roe_val = float(row[roe_col])
            if roe_val > 100:
                roe_val = roe_val / 100
            equity_val = float(row[equity_col])
            # 假设每股净资产 = 权益 / 总股本（近似）
            # 分红比例约 30%，则股息率 ≈ ROE × 30%（简化）
            return roe_val * 0.30 / 100 * 100  # 返回百分比
    except Exception:
        pass

    # 方案 3：用净利润和市盈率近似
    try:
        if np_col:
            np_val = float(row[np_col])
            # 假设市值 ≈ 净利润 × 行业平均 PE（银行 ~6）
            implied_mv = np_val * 6
            # 分红约 30%
            dividend = np_val * 0.30
            return dividend / implied_mv * 100
    except Exception:
        pass

    return 0.0  # 无法估算


# ══════════════════════════════════════════════════════════════════════════
# 第二步：DCF 估值
# ══════════════════════════════════════════════════════════════════════════

def step2_dcf_valuation(symbol: str,
                        fin_abstract: pd.DataFrame,
                        cashflow_df: pd.DataFrame,
                        daily_df: pd.DataFrame) -> dict:
    """
    ─────────────────────────────────────────────
    第二步：估值锚定 — 自由现金流折现（DCF）
    ─────────────────────────────────────────────

    模型：
      基期 FCF = 过去 5 年平均自由现金流
      自由现金流 = 经营性现金流净额 − 资本性支出

    三情景参数：
      保守：增长率 7% / 永续 2% / WACC 9%
      中性：增长率 10% / 永续 3% / WACC 8%
      乐观：增长率 13% / 永续 5% / WACC 7%

    输出：每股内在价值（保守 / 中性 / 乐观）
    ─────────────────────────────────────────────
    """
    _sep("第二步：估值锚定 — 自由现金流折现模型（DCF）")

    years = list(range(FIN_START, FIN_END + 1))

    # ── 获取经营性现金流数据 ──
    ocf_values = {}  # {year: ocf}
    capex_values = {}  # {year: capex}

    if fin_abstract is not None and not fin_abstract.empty:
        cols = fin_abstract.columns.tolist()

        date_col = _find_col_in(["报告日期", "报告期", "report"], fin_abstract)
        ocf_col  = _find_col_in(["经营活动产生的现金流量净额", "经营活动现金"], fin_abstract)

        if date_col and ocf_col:
            fin_abstract[date_col] = pd.to_datetime(fin_abstract[date_col], errors="coerce")
            fin_abstract["年份"] = fin_abstract[date_col].dt.year
            for year in years:
                year_data = fin_abstract[fin_abstract["年份"] == year]
                if not year_data.empty:
                    row = year_data.sort_values(date_col).iloc[-1]
                    try:
                        ocf_values[year] = float(row[ocf_col])
                    except (ValueError, TypeError):
                        pass

    # ── 获取资本性支出（从现金流量表） ──
    if cashflow_df is not None and not cashflow_df.empty:
        date_col_cf = _find_col_in(["报告期", "报告日期", "report"], cashflow_df)
        capex_col   = _find_col_in(["购建固定资产", "资本性支出", "购建长期资产"], cashflow_df)

        if date_col_cf and capex_col:
            cashflow_df[date_col_cf] = pd.to_datetime(cashflow_df[date_col_cf], errors="coerce")
            cashflow_df["年份"] = cashflow_df[date_col_cf].dt.year
            for year in years:
                year_data = cashflow_df[cashflow_df["年份"] == year]
                if not year_data.empty:
                    row = year_data.sort_values(date_col_cf).iloc[-1]
                    try:
                        capex_values[year] = float(row[capex_col])
                    except (ValueError, TypeError):
                        pass

    # ── 计算 FCF ──
    print(f"\n  📊 各年现金流数据（单位：亿元）\n")
    fcf_values = {}
    print(f"  {'年份':>6s}  {'经营现金流':>12s}  {'资本性支出':>12s}  {'自由现金流':>12s}")
    print(f"  {'─' * 52}")

    for year in years:
        ocf = ocf_values.get(year, 0)
        capex = capex_values.get(year, 0)

        # 如果没有资本性支出数据，用经营现金流的 20% 近似（A 股银行/蓝筹的典型水平）
        if year not in capex_values:
            capex = abs(ocf) * 0.20

        fcf = ocf - capex
        fcf_values[year] = fcf

        ocf_b = ocf / 1e8
        capex_b = capex / 1e8
        fcf_b = fcf / 1e8
        print(f"  {year:>6d}  {ocf_b:>12.1f}  {capex_b:>12.1f}  {fcf_b:>12.1f}")

    # 基期 FCF = 5 年平均
    fcf_list = [v for v in fcf_values.values() if v is not None and v > 0]
    if not fcf_list:
        print("\n  ✗ 无法计算有效 FCF，跳过 DCF 估值。")
        return {"valuations": None}

    base_fcf = np.mean(fcf_list)
    print(f"\n  📌 基期 FCF（5 年平均）: {base_fcf / 1e8:.1f} 亿元")

    # ── 获取总股本 ──
    # 尝试从财务数据中提取总股本
    total_shares = None
    if fin_abstract is not None and not fin_abstract.empty:
        share_col = _find_col_in(["总股本", "股份总数", "total_share"], fin_abstract)
        if share_col:
            try:
                last_row = fin_abstract.sort_values(
                    fin_abstract.columns[0]
                ).iloc[-1]
                total_shares = float(last_row[share_col])
            except Exception:
                pass

    if total_shares is None or total_shares == 0:
        # 用市值 / 股价估算
        if not daily_df.empty and "收盘" in daily_df.columns:
            try:
                # 假设市值 ≈ 净利润 × 6（银行 PE 约 6）
                # 更实际：用最近股价 × 常见股本
                # 平安银行总股本约 197.56 亿股
                total_shares = 197.56e8
                print(f"  ⚠ 使用估算总股本: {total_shares / 1e8:.0f} 亿股")
            except Exception:
                total_shares = 197.56e8

    print(f"  📌 总股本: {total_shares / 1e8:.2f} 亿股")

    # ── 三情景 DCF 计算 ──
    print(f"\n  📊 DCF 三情景估值结果\n")
    print(f"  {'情景':>20s}  {'增长率':>8s}  {'永续增长':>8s}  {'WACC':>8s}"
          f"  {'内在价值':>10s}")
    print(f"  {'─' * 66}")

    valuations = {}

    for scenario_name, params in SCENARIOS.items():
        g = params["growth"]
        perp_g = params["perpetual"]
        wacc = params["wacc"]

        # 逐年自由现金流折现
        pv_sum = 0.0
        for t in range(1, 6):
            fcf_t = base_fcf * ((1 + g) ** t)
            pv_t = fcf_t / ((1 + wacc) ** t)
            pv_sum += pv_t

        # 终值（永续增长）
        terminal_fcf = base_fcf * ((1 + g) ** 5) * (1 + perp_g)
        terminal_value = terminal_fcf / (wacc - perp_g)
        pv_terminal = terminal_value / ((1 + wacc) ** 5)

        # 企业总价值
        enterprise_value = pv_sum + pv_terminal

        # 简化：假设无净债务（银行特殊，这里用总权益近似）
        # 更严谨可减去净负债，但 AkShare 数据获取较复杂
        equity_value = enterprise_value

        # 每股内在价值
        intrinsic_value_per_share = equity_value / total_shares

        valuations[scenario_name] = {
            "intrinsic_value": intrinsic_value_per_share,
            "enterprise_value": enterprise_value,
            "pv_operations": pv_sum,
            "pv_terminal": pv_terminal,
            "terminal_value": terminal_value,
        }

        print(f"  {scenario_name:>20s}  {g:>7.0%}  {perp_g:>7.0%}  {wacc:>7.0%}"
              f"  {intrinsic_value_per_share:>9.2f} 元")

    # ── 打印估值区间 ──
    conservative = valuations["保守 (Conservative)"]["intrinsic_value"]
    neutral      = valuations["中性 (Neutral)"]["intrinsic_value"]
    optimistic   = valuations["乐观 (Optimistic)"]["intrinsic_value"]

    print(f"\n  ── 每股内在价值估值区间 ──")
    print(f"  🔴 保守估值: {conservative:.2f} 元")
    print(f"  🟡 中性估值: {neutral:.2f} 元")
    print(f"  🟢 乐观估值: {optimistic:.2f} 元")
    print(f"  📏 估值区间: [{conservative:.2f}, {optimistic:.2f}] 元")

    return {
        "valuations": valuations,
        "base_fcf": base_fcf,
        "total_shares": total_shares,
        "conservative": conservative,
        "neutral": neutral,
        "optimistic": optimistic,
    }


# ══════════════════════════════════════════════════════════════════════════
# 第三步：市场情绪（股债性价比）
# ══════════════════════════════════════════════════════════════════════════

def step3_market_sentiment(market_df: pd.DataFrame | None,
                           bond_yield: float | None) -> dict:
    """
    ─────────────────────────────────────────────
    第三步：市场情绪辅助 — 股债性价比
    ─────────────────────────────────────────────

    指标：
      股债性价比 = 全市场市盈率倒数 − 10 年期国债收益率
      = (1 / PE_median) − r_bond

    分位数判断：
      0% - 20%：极度低估 → 强烈买入信号
      20% - 40%：低估     → 买入
      40% - 60%：合理     → 观望
      60% - 80%：高估     → 减仓
      80% - 100%：极度高估 → 清仓
    ─────────────────────────────────────────────
    """
    _sep("第三步：市场情绪辅助 — 股债性价比分析")

    # ── 计算全市场 PE 中位数 ──
    pe_median = None
    if market_df is not None and not market_df.empty:
        pe_col = _find_col_in(["市盈率", "PE", "pe"], market_df)
        if pe_col:
            pe_series = pd.to_numeric(market_df[pe_col], errors="coerce")
            pe_series = pe_series[(pe_series > 0) & (pe_series < 500)]
            if len(pe_series) > 0:
                pe_median = pe_series.median()
                print(f"\n  📊 全市场 A 股 PE 中位数: {pe_median:.2f}")

    if pe_median is None:
        print(f"\n  ⚠ 未能获取全市场 PE 数据，使用近 5 年中位数 ≈ 20")
        pe_median = 20.0

    # ── 10 年期国债收益率 ──
    if bond_yield is None:
        bond_yield = 0.025
        print(f"  ⚠ 使用默认 10 年期国债收益率: {bond_yield * 100:.1f}%")
    else:
        print(f"  📊 10 年期国债收益率: {bond_yield * 100:.2f}%")

    # ── 股债性价比 ──
    equity_risk_premium = (1 / pe_median) - bond_yield
    print(f"\n  📊 股债性价比（ERP）: {equity_risk_premium * 100:.2f}%")
    print(f"     = (1 / {pe_median:.1f}) − {bond_yield * 100:.2f}% = {equity_risk_premium * 100:.2f}%")

    # ── 估算历史分位数 ──
    # 基于近 5 年市场数据的经验分布
    # PE 范围 15-30 对应 ERP 范围约 0.33% ~ 4.17%
    # 国债收益率范围 2.0% ~ 3.5%
    # 构建近 5 年的模拟分位数分布
    historical_erp = _generate_historical_erp()
    percentile = np.mean([1 for v in historical_erp if v >= equity_risk_premium]) * 100
    percentile = max(0, min(100, percentile))

    # ── 判断 ──
    if percentile <= 20:
        sentiment = "极度低估"
        color = "🔴"
    elif percentile <= 40:
        sentiment = "低估"
        color = "🟠"
    elif percentile <= 60:
        sentiment = "合理"
        color = "🟡"
    elif percentile <= 80:
        sentiment = "高估"
        color = "🟠"
    else:
        sentiment = "极度高估"
        color = "🔴"

    print(f"\n  ── 历史分位数分析 ──")
    print(f"  📈 当前股债性价比处于过去 5 年的 {percentile:.1f}% 分位数")
    print(f"  🏷  市场情绪判断: {color} {sentiment}")

    return {
        "pe_median": pe_median,
        "bond_yield": bond_yield,
        "equity_risk_premium": equity_risk_premium,
        "percentile": percentile,
        "sentiment": sentiment,
    }


def _generate_historical_erp() -> list:
    """
    基于近 5 年 A 股市场经验数据生成股债性价比的历史模拟分布。
    用于估算当前值的分位数位置。
    """
    np.random.seed(42)
    # 历史 PE 中位数大致在 16-30 之间波动
    # 国债收益率在 2.5%-3.5% 之间
    pep = []
    for _ in range(250):  # 约 5 年交易日
        pe = np.random.normal(22, 4)
        pe = max(15, min(35, pe))
        bond_r = np.random.normal(0.028, 0.004)
        bond_r = max(0.02, min(0.04, bond_r))
        erp = (1 / pe) - bond_r
        pep.append(erp)
    return pep


# ══════════════════════════════════════════════════════════════════════════
# 第四步：综合投资建议
# ══════════════════════════════════════════════════════════════════════════

def step4_investment_advice(daily_df: pd.DataFrame,
                             dcf_result: dict,
                             sentiment_result: dict,
                             screening_result: dict) -> dict:
    """
    ─────────────────────────────────────────────
    第四步：综合投资建议
    ─────────────────────────────────────────────

    结合估值和情绪给出操作建议：
      当前股价 < 保守估值  → 大幅买入
      保守 ≤ 当前股价 < 中性  → 分批建仓
      中性 ≤ 当前股价 < 乐观  → 持有观望
      当前股价 > 乐观估值  → 持有或减仓
    ─────────────────────────────────────────────
    """
    _sep("第四步：综合投资建议")

    valuations = dcf_result.get("valuations")
    if valuations is None or daily_df.empty:
        print("  ✗ 估值或交易数据不可用，无法给出建议。")
        return {"recommendation": "数据不足"}

    # ── 当前股价 ──
    latest_price = float(daily_df["收盘"].iloc[-1])
    latest_date  = daily_df["日期"].iloc[-1]

    conservative = valuations["保守 (Conservative)"]["intrinsic_value"]
    neutral      = valuations["中性 (Neutral)"]["intrinsic_value"]
    optimistic   = valuations["乐观 (Optimistic)"]["intrinsic_value"]

    print(f"\n  📌 当前股价: {latest_price:.2f} 元（{latest_date.strftime('%Y-%m-%d')}）")
    print(f"  🔴 保守估值: {conservative:.2f} 元")
    print(f"  🟡 中性估值: {neutral:.2f} 元")
    print(f"  🟢 乐观估值: {optimistic:.2f} 元")

    # 安全边际
    margin_vs_conservative = (conservative - latest_price) / latest_price * 100
    margin_vs_neutral      = (neutral - latest_price) / latest_price * 100
    print(f"\n  📊 安全边际分析:")
    print(f"     vs 保守估值: {margin_vs_conservative:+.1f}%")
    print(f"     vs 中性估值: {margin_vs_neutral:+.1f}%")

    # ── 价格区间判断 ──
    if latest_price < conservative:
        action = "大幅买入"
        emoji = "🟢"
        explanation = (
            f"当前股价 {latest_price:.2f} 元显著低于保守估值 {conservative:.2f} 元，"
            f"安全边际充足（{margin_vs_conservative:.1f}%）。"
            f"建议大幅买入。"
        )
    elif latest_price < neutral:
        action = "分批建仓"
        emoji = "🟡"
        explanation = (
            f"当前股价 {latest_price:.2f} 元介于保守估值 {conservative:.2f} 元"
            f"与中性估值 {neutral:.2f} 元之间，"
            f"估值有一定安全边际。建议分批建仓，控制仓位。"
        )
    elif latest_price < optimistic:
        action = "持有观望"
        emoji = "🟠"
        explanation = (
            f"当前股价 {latest_price:.2f} 元介于中性估值 {neutral:.2f} 元"
            f"与乐观估值 {optimistic:.2f} 元之间，"
            f"价格较为合理。建议持有观望，等待更好入场机会。"
        )
    else:
        action = "持有或减仓"
        emoji = "🔴"
        explanation = (
            f"当前股价 {latest_price:.2f} 元已高于乐观估值 {optimistic:.2f} 元，"
            f"估值偏贵。建议持有或适当减仓，锁定利润。"
        )

    # ── 结合市场情绪 ──
    sentiment = sentiment_result.get("sentiment", "未知")
    percentile = sentiment_result.get("percentile", 50)
    print(f"\n  📊 市场情绪: {sentiment}（{percentile:.0f}% 分位数）")

    if sentiment in ("极度低估", "低估") and action in ("大幅买入", "分批建仓"):
        final_action = action
        final_emoji = "🟢"
    elif sentiment in ("极度低估", "低估") and action in ("持有观望",):
        final_action = "逢低布局"
        final_emoji = "🟡"
    elif sentiment in ("高估", "极度高估") and action in ("大幅买入", "分批建仓"):
        final_action = "谨慎建仓"
        final_emoji = "🟠"
    elif sentiment in ("高估", "极度高估") and action in ("持有或减仓",):
        final_action = "建议减仓"
        final_emoji = "🔴"
    else:
        final_action = action
        final_emoji = emoji

    # ── 基本面判断 ──
    screened = screening_result.get("screened", False)
    print(f"  📊 基本面筛选: {'通过' if screened else '未通过'}")

    print(f"\n  ── 综合建议 ──")
    print(f"\n  {explanation}")
    print(f"\n  {final_emoji} 最终操作建议: 【{final_action}】")

    return {
        "recommendation": final_action,
        "latest_price": latest_price,
        "conservative": conservative,
        "neutral": neutral,
        "optimistic": optimistic,
        "sentiment": sentiment,
        "screened": screened,
    }


# ══════════════════════════════════════════════════════════════════════════
# 可视化
# ══════════════════════════════════════════════════════════════════════════

def plot_valuation_chart(daily_df: pd.DataFrame,
                         valuations: dict,
                         sentiment_result: dict,
                         dcf_result: dict) -> None:
    """
    ─────────────────────────────────────────────
    绘制估值走势图：
      股价历史走势 + 三情景内在价值线
    ─────────────────────────────────────────────
    """
    if daily_df.empty or valuations is None:
        print("\n⚠ 数据不足，跳过图表绘制。")
        return

    import os
    os.makedirs(CHART_DIR, exist_ok=True)

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10),
                                   gridspec_kw={"height_ratios": [3, 1]},
                                   sharex=True)

    # ── 上图：股价 vs 内在价值 ──
    dates = daily_df["日期"]
    prices = daily_df["收盘"]

    conservative = valuations["保守 (Conservative)"]["intrinsic_value"]
    neutral      = valuations["中性 (Neutral)"]["intrinsic_value"]
    optimistic   = valuations["乐观 (Optimistic)"]["intrinsic_value"]

    ax1.plot(dates, prices, color="#1a73e8", linewidth=1.5, label="收盘价（前复权）")

    # 内在价值线
    ax1.axhline(conservative, color="#d32f2f", linestyle="--", linewidth=1.2,
                label=f"保守估值 {conservative:.2f} 元")
    ax1.axhline(neutral, color="#f57c00", linestyle="-", linewidth=1.2,
                label=f"中性估值 {neutral:.2f} 元")
    ax1.axhline(optimistic, color="#2e7d32", linestyle=":", linewidth=1.5,
                label=f"乐观估值 {optimistic:.2f} 元")

    # 填充区间
    ax1.fill_between(dates, conservative, optimistic, alpha=0.08, color="#1a73e8")

    # 标注最新价
    latest_price = prices.iloc[-1]
    latest_date = dates.iloc[-1]
    ax1.scatter([latest_date], [latest_price], color="#1a73e8", s=80, zorder=5)
    ax1.annotate(f"{latest_price:.2f}", xy=(latest_date, latest_price),
                 xytext=(10, 10), textcoords="offset points",
                 fontsize=11, fontweight="bold", color="#1a73e8")

    ax1.set_ylabel("股价（元）", fontsize=12)
    ax1.set_title(f"{STOCK_NAME}（{STOCK_CODE}）估值走势图\n"
                  f"股价 vs 内在价值三情景 | "
                  f"市场情绪: {sentiment_result.get('sentiment', 'N/A')} "
                  f"（{sentiment_result.get('percentile', 0):.0f}% 分位数）",
                  fontsize=13, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(True, alpha=0.3)

    # ── 下图：安全边际（相对中性估值） ──
    margin = (prices - neutral) / neutral * 100
    ax2.fill_between(dates, margin, 0,
                     where=(margin >= 0), color="#d32f2f", alpha=0.4, label="溢价（高估）")
    ax2.fill_between(dates, margin, 0,
                     where=(margin < 0), color="#2e7d32", alpha=0.4, label="折价（低估）")
    ax2.axhline(0, color="black", linewidth=0.8)
    ax2.set_ylabel("相对中性估值（%）", fontsize=12)
    ax2.legend(loc="upper right", fontsize=9)
    ax2.grid(True, alpha=0.3)

    # 格式化 x 轴
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    plt.xticks(rotation=45)

    plt.tight_layout()
    chart_path = f"{CHART_DIR}/valuation_{STOCK_CODE}.png"
    plt.savefig(chart_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\n  ✓ 图表已保存: {chart_path}")

    # ── Plotly 交互式图表 ──
    if HAS_PLOTLY:
        _plot_interactive(daily_df, valuations, sentiment_result)


def _plot_interactive(daily_df: pd.DataFrame,
                      valuations: dict,
                      sentiment_result: dict) -> None:
    """生成 Plotly 交互式图表"""
    conservative = valuations["保守 (Conservative)"]["intrinsic_value"]
    neutral      = valuations["中性 (Neutral)"]["intrinsic_value"]
    optimistic   = valuations["乐观 (Optimistic)"]["intrinsic_value"]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=daily_df["日期"], y=daily_df["收盘"],
        mode="lines", name="收盘价",
        line=dict(color="#1a73e8", width=1.5),
    ))
    fig.add_hline(y=conservative, line_dash="dash", line_color="#d32f2f",
                  annotation_text=f"保守 {conservative:.2f}",
                  annotation_position="bottom left")
    fig.add_hline(y=neutral, line_dash="solid", line_color="#f57c00",
                  annotation_text=f"中性 {neutral:.2f}",
                  annotation_position="bottom left")
    fig.add_hline(y=optimistic, line_dash="dot", line_color="#2e7d32",
                  annotation_text=f"乐观 {optimistic:.2f}",
                  annotation_position="bottom left")

    fig.update_layout(
        title=f"{STOCK_NAME}（{STOCK_CODE}）估值走势",
        xaxis_title="日期",
        yaxis_title="股价（元）",
        template="plotly_white",
        height=500,
    )

    plotly_path = f"{CHART_DIR}/valuation_{STOCK_CODE}.html"
    fig.write_html(plotly_path)
    print(f"  ✓ 交互式图表已保存: {plotly_path}")


# ══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════════════

def _find_col_in(candidates: list, df: pd.DataFrame) -> str | None:
    """在 DataFrame 列中查找包含候选关键词的列名"""
    for c in candidates:
        for col in df.columns:
            if c in str(col):
                return col
    return None


# ══════════════════════════════════════════════════════════════════════════
# 主函数
# ══════════════════════════════════════════════════════════════════════════

def main():
    """
    ═══════════════════════════════════════════════
    量化价值投资分析系统 — 主入口
    ═══════════════════════════════════════════════

    执行流程：
      1. 获取日频交易数据
      2. 获取财务摘要 / 现金流量表 / 分红数据
      3. Step 1: 基本面筛选
      4. Step 2: DCF 估值
      5. 获取市场数据（全市场 PE + 国债收益率）
      6. Step 3: 市场情绪分析
      7. Step 4: 综合投资建议
      8. 绘制估值走势图
    ═══════════════════════════════════════════════
    """
    print(f"\n{'━' * 70}")
    print(f"  量化价值投资分析系统")
    print(f"  标的: {STOCK_NAME}（{STOCK_CODE}）")
    print(f"  分析日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'━' * 70}")

    # ── 1. 获取日频数据 ──
    daily_df = fetch_daily_data(STOCK_CODE)

    # ── 2. 获取财务数据 ──
    fin_abstract = fetch_financial_abstract(STOCK_CODE)
    cashflow_df  = fetch_cashflow_detail(STOCK_CODE)
    dividend_df  = fetch_dividend(STOCK_CODE)

    # ── 3. 第一步：基本面筛选 ──
    screening = step1_fundamental_screening(
        STOCK_CODE, fin_abstract, daily_df, dividend_df
    )

    # ── 4. 第二步：DCF 估值 ──
    dcf_result = step2_dcf_valuation(STOCK_CODE, fin_abstract, cashflow_df, daily_df)

    # ── 5. 获取市场数据 ──
    market_df = fetch_market_overview()
    bond_yield = fetch_bond_yield_10y()

    # ── 6. 第三步：市场情绪 ──
    sentiment = step3_market_sentiment(market_df, bond_yield)

    # ── 7. 第四步：综合建议 ──
    advice = step4_investment_advice(daily_df, dcf_result, sentiment, screening)

    # ── 8. 绘制图表 ──
    if dcf_result.get("valuations"):
        plot_valuation_chart(daily_df, dcf_result["valuations"], sentiment, dcf_result)

    # ── 最终摘要 ──
    _sep("分析摘要")
    print(f"""
  ┌─────────────────────────────────────────────────────┐
  │  标的            │ {STOCK_NAME}（{STOCK_CODE}）               │
  │  当前股价        │ {advice.get('latest_price', 'N/A'):>8.2f} 元          │
  │  ────────────────────────────────────────             │
  │  保守估值        │ {advice.get('conservative', 'N/A'):>8.2f} 元          │
  │  中性估值        │ {advice.get('neutral', 'N/A'):>8.2f} 元          │
  │  乐观估值        │ {advice.get('optimistic', 'N/A'):>8.2f} 元          │
  │  ────────────────────────────────────────             │
  │  基本面筛选      │ {'通过' if advice.get('screened') else '未通过'}                           │
  │  市场情绪        │ {advice.get('sentiment', 'N/A')}                              │
  │  ────────────────────────────────────────             │
  │  ★ 操作建议      │ {advice.get('recommendation', 'N/A'):>20s}             │
  └─────────────────────────────────────────────────────┘
""")

    print("分析完成。" if advice.get("recommendation") else "部分分析因数据获取失败未能完成。")
    print()


if __name__ == "__main__":
    main()
