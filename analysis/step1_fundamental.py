# -*- coding: utf-8 -*-
"""
第一步：基本面筛选（质量评估）

计算过去 5 年（FIN_START ~ FIN_END）每年的核心财务指标：
  - ROE（加权净资产收益率）
  - 股息率（TTM）
  - 资产负债率
  - 经营性现金流净额 / 净利润（利润质量）

判断标准（阈值可在 config 中调整）：
  有数据的年份须"全部达标"且覆盖年数 ≥ MIN_COVERAGE_YEARS。
  默认 ROE > 15% 且 股息率 > 2% → "初步通过筛选"，否则 → "不满足"。
"""
import pandas as pd

from config import FIN_START, FIN_END, ROE_THRESHOLD, DIV_THRESHOLD, MIN_COVERAGE_YEARS
from utils import sep, find_col_in, estimate_dividend_yield


def fundamental_screening(
    symbol: str,
    fin_abstract: pd.DataFrame,
    daily_df: pd.DataFrame,
    dividend_df: pd.DataFrame,
    fin_start: int | None = None,
    fin_end: int | None = None,
) -> dict:
    """执行基本面筛选，返回包含 screening 结果和财务指标表的字典。
    fin_start/fin_end 默认回退到 config.FIN_START/FIN_END（--years 可覆盖）。"""
    sep("第一步：基本面筛选（质量评估）")

    fin_start = fin_start if fin_start is not None else FIN_START
    fin_end = fin_end if fin_end is not None else FIN_END
    years = list(range(fin_start, fin_end + 1))
    results = []

    if fin_abstract is None or fin_abstract.empty:
        print("  [X] 财务数据不可用，跳过基本面分析。")
        return {"screened": False, "table": None, "roe_pass": False, "div_pass": False}

    # -- 识别关键列名 --
    date_col   = find_col_in(["报告日期", "报告期", "report"], fin_abstract)
    roe_col    = find_col_in(["加权净资产收益率", "ROE", "净资产收益率"], fin_abstract)
    debt_col   = find_col_in(["资产负债率"], fin_abstract)
    ocf_col    = find_col_in(["经营活动产生的现金流量净额", "经营活动现金"], fin_abstract)
    np_col     = find_col_in(["净利润", "归属于上市公司股东的净利润"], fin_abstract)
    equity_col = find_col_in(["归属母公司股东权益", "所有者权益合计"], fin_abstract)

    # -- 提取年度数据 --
    if date_col:
        fin_abstract[date_col] = pd.to_datetime(fin_abstract[date_col], errors="coerce")
        fin_abstract["年份"] = fin_abstract[date_col].dt.year
        annual = fin_abstract[fin_abstract["年份"].isin(years)].copy()
        if annual.empty:
            print("  [!] 未找到指定年份范围的财务数据。")
            return {"screened": False, "table": None, "roe_pass": False, "div_pass": False}
    else:
        print("  [X] 无法识别报告日期列。")
        return {"screened": False, "table": None, "roe_pass": False, "div_pass": False}

    # -- 构建每年核心指标表 --
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

        row = year_data.sort_values(year_data.columns[0]).iloc[-1]

        roe_val = _safe_pct(row, roe_col)
        debt_val = _safe_pct(row, debt_col)

        ocf_ratio = None
        if ocf_col and np_col:
            try:
                ocf_val = float(row[ocf_col])
                np_val = float(row[np_col])
                if np_val != 0:
                    ocf_ratio = ocf_val / np_val
            except (ValueError, TypeError):
                pass

        div_yield = estimate_dividend_yield(
            year, row, equity_col, dividend_df, daily_df, roe_col, np_col
        )

        results.append({
            "年份": year,
            "ROE(%)": round(roe_val, 2) if roe_val is not None else None,
            "资产负债率(%)": round(debt_val, 2) if debt_val is not None else None,
            "经营现金流/净利润": round(ocf_ratio, 2) if ocf_ratio is not None else None,
            "股息率(%)": round(div_yield, 2) if div_yield else None,
        })

    # -- 打印结果 --
    result_df = pd.DataFrame(results)
    print(f"\n  [DATA] {symbol} 过去 {FIN_END - FIN_START + 1} 年核心财务指标\n")
    print(result_df.to_string(index=False))

    # -- 判断是否通过筛选 --
    roe_series = result_df["ROE(%)"].dropna()
    div_series = result_df["股息率(%)"].dropna()

    roe_pass = bool(len(roe_series) >= MIN_COVERAGE_YEARS and (roe_series > ROE_THRESHOLD).all())
    div_pass = bool(len(div_series) >= MIN_COVERAGE_YEARS and (div_series > DIV_THRESHOLD).all())

    print(f"\n  -- 筛选判断（达标线: ROE>{ROE_THRESHOLD:.0f}%, 股息率>{DIV_THRESHOLD:.0f}%, "
          f"至少 {MIN_COVERAGE_YEARS} 年且全部达标）--")
    if len(roe_series) > 0:
        print(f"  - ROE 连续 > {ROE_THRESHOLD:.0f}%：{'[PASS] 通过' if roe_pass else '[FAIL] 未通过'}"
              f"  (ROE 范围: {roe_series.min():.1f}% ~ {roe_series.max():.1f}%, "
          f"覆盖 {len(roe_series)}/{len(years)} 年)")
    else:
        print("  - ROE 数据不足")
    if len(div_series) > 0:
        print(f"  - 股息率 > {DIV_THRESHOLD:.0f}%：{'[PASS] 通过' if div_pass else '[FAIL] 未通过'}"
              f"  (股息率范围: {div_series.min():.1f}% ~ {div_series.max():.1f}%, "
          f"覆盖 {len(div_series)}/{len(years)} 年)")
    else:
        print("  - 股息率数据不足")

    screened = roe_pass and div_pass
    print(f"\n  [TAG]  筛选结论：{'【初步通过筛选】' if screened else '【不满足长线价值投资标准】'}")

    return {
        "screened": screened,
        "table": result_df,
        "roe_pass": roe_pass,
        "div_pass": div_pass,
    }


def _safe_pct(row, col: str | None) -> float | None:
    """安全读取百分比值，若 > 100 则除以 100。"""
    if not col:
        return None
    try:
        val = float(row[col])
        return val / 100 if val > 100 else val
    except (ValueError, TypeError):
        return None
