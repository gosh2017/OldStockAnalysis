# -*- coding: utf-8 -*-
"""
第一步：基本面筛选（质量评估）

计算过去 5 年（FIN_START ~ FIN_END）每年的核心财务指标：
  - ROE（加权净资产收益率）
  - 股息率（TTM）
  - 资产负债率
  - 经营性现金流净额 / 净利润（利润质量）

判断标准（阈值可在 config 中调整）：
  有数据的年份须"中位数达标 + ≥ MIN_PASSING_YEARS 年达标 + 覆盖年数 ≥ MIN_COVERAGE_YEARS"。
  默认 ROE > 15% 且 股息率 > 2% → "初步通过筛选"，否则 → "不满足"。
  相比"全部达标"，中位数口径允许个别异常年（如 2020 疫情）而不误杀稳健蓝筹。
"""
import pandas as pd

from config import (FIN_START, FIN_END, ROE_THRESHOLD, DIV_THRESHOLD,
                    MIN_COVERAGE_YEARS, MIN_PASSING_YEARS)
from utils import sep, find_col_in, estimate_dividend_yield, pick_annual_row


def fundamental_screening(
    symbol: str,
    fin_abstract: pd.DataFrame,
    daily_df: pd.DataFrame,
    dividend_df: pd.DataFrame,
    fin_start: int | None = None,
    fin_end: int | None = None,
    bucket: str = "其他",
    market_pe: float | None = None,
) -> dict:
    """执行基本面筛选，返回包含 screening 结果和财务指标表的字典。
    fin_start/fin_end 默认回退到 config.FIN_START/FIN_END（--years 可覆盖）。

    item C2：bucket/market_pe 为可选参数，由 main.py 传入
    （industry_info.get("bucket") 与 market_pe_history 末值），用于
    estimate_dividend_yield 的行业分红率假设与隐含市值口径，向后兼容。
    item C3：年内取数改用 pick_annual_row（年报优先），季报年透明标注于
    '报告期类型' 列（仅标注不改值，不影响评分 _completeness 的 real 占比）。"""
    sep("第一步：基本面筛选（质量评估）")

    fin_start = fin_start if fin_start is not None else FIN_START
    fin_end = fin_end if fin_end is not None else FIN_END
    years = list(range(fin_start, fin_end + 1))
    results = []

    if fin_abstract is None or fin_abstract.empty:
        print("  [X] 财务数据不可用，跳过基本面分析。")
        return {"screened": False, "table": None, "roe_pass": False, "div_pass": False}

    # item A3：隔离入参，避免下方 to_datetime / 新增"年份"列原地污染调用方 DataFrame
    fin_abstract = fin_abstract.copy()

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
                "分红来源": None,
                "报告期类型": None,
            })
            continue

        # item C3：年报优先（dt.month==12）；否则取年内最大日期行（季报），透明标注
        row, is_annual = pick_annual_row(year_data, date_col)
        if row is None:
            # date_col 无法解析该行日期时回退旧逻辑，避免取数中断
            # 频率未知，按非年报标注（line 62 预解析后此分支实为死路径，仅兜底）
            row = year_data.sort_values(date_col).iloc[-1]
            is_annual = False
        report_type = "年报" if is_annual else "季报*"
        if not is_annual:
            print(f"  [!] {year} 年仅季报，数据待年报")

        roe_val = _safe_pct(row, roe_col)
        debt_val = _safe_pct(row, debt_col)

        ocf_ratio = None
        if ocf_col and np_col:
            try:
                ocf_val = float(row[ocf_col])
                np_val = float(row[np_col])
                # item A6：仅盈利年算比率。亏损年（np≤0）置 None，避免 OCF 正/净利润负
                # 算出负比率反向惩罚良好现金质量；scoring 的 ocf_quality 取中位数时
                # dropna 排除 None，口径更准（不再被亏损年负值扭曲）。
                if np_val > 0:
                    ocf_ratio = ocf_val / np_val
            except (ValueError, TypeError):
                pass

        div_yield, div_source = estimate_dividend_yield(
            year, row, equity_col, dividend_df, daily_df, roe_col, np_col,
            bucket=bucket, market_pe=market_pe,
        )

        results.append({
            "年份": year,
            "ROE(%)": round(roe_val, 2) if roe_val is not None else None,
            "资产负债率(%)": round(debt_val, 2) if debt_val is not None else None,
            "经营现金流/净利润": round(ocf_ratio, 2) if ocf_ratio is not None else None,
            # item A1：用 is not None 而非真值判断——0.0 是合法股息率，不应被当缺失吞掉
            "股息率(%)": round(div_yield, 2) if div_yield is not None else None,
            "分红来源": div_source,
            "报告期类型": report_type,
        })

    # -- 打印结果 --
    result_df = pd.DataFrame(results)
    print(f"\n  [DATA] {symbol} 过去 {len(years)} 年核心财务指标\n")
    print(result_df.to_string(index=False))

    # -- 判断是否通过筛选 --
    roe_series = result_df["ROE(%)"].dropna()
    # 仅"real"来源（实际每股分红 / 年末股价）参与 div_pass 判定；
    # estimated_roe / estimated_np 是行业分红率假设凑出的估算值，可展示但不参与筛选，
    # 避免从未分红的次新股靠行业模板被误判为"高股息"而通过。
    # missing 年股息率存 0.0，同样排除。
    div_series = result_df.loc[result_df["分红来源"] == "real", "股息率(%)"].dropna()

    _est_mask = result_df["分红来源"].isin(["estimated_roe", "estimated_np"])
    _est_years = result_df.loc[_est_mask, "年份"].tolist()
    if _est_years:
        _est_sources = ", ".join(
            sorted(result_df.loc[_est_mask, "分红来源"].astype(str).unique()))
        print(f"  [!] 以下年份股息率为估算值（{_est_sources}），"
              f"仅展示不参与筛选：{_est_years}")

    # 中位数口径：允许个别异常年，避免"全部达标"误杀稳健蓝筹。
    # 三条件：覆盖年数 ≥ MIN_COVERAGE_YEARS、中位数 > 阈值、达标年数 ≥ MIN_PASSING_YEARS。
    roe_pass = bool(
        len(roe_series) >= MIN_COVERAGE_YEARS
        and roe_series.median() > ROE_THRESHOLD
        and (roe_series > ROE_THRESHOLD).sum() >= MIN_PASSING_YEARS
    )
    div_pass = bool(
        len(div_series) >= MIN_COVERAGE_YEARS
        and div_series.median() > DIV_THRESHOLD
        and (div_series > DIV_THRESHOLD).sum() >= MIN_PASSING_YEARS
    )

    print(f"\n  -- 筛选判断（达标线: ROE>{ROE_THRESHOLD:.0f}%, 股息率>{DIV_THRESHOLD:.0f}%；"
          f"股息率仅取 real 来源；中位数达标 + ≥{MIN_PASSING_YEARS} 年达标 + 覆盖 ≥{MIN_COVERAGE_YEARS} 年）--")
    if len(roe_series) > 0:
        print(f"  - ROE > {ROE_THRESHOLD:.0f}%：{'[PASS] 通过' if roe_pass else '[FAIL] 未通过'}"
              f"  (中位数 {roe_series.median():.1f}%，{(roe_series > ROE_THRESHOLD).sum()}/{len(roe_series)} 年达标，"
              f"覆盖 {len(roe_series)}/{len(years)} 年)")
    else:
        print("  - ROE 数据不足")
    if len(div_series) > 0:
        print(f"  - 股息率 > {DIV_THRESHOLD:.0f}%：{'[PASS] 通过' if div_pass else '[FAIL] 未通过'}"
              f"  (中位数 {div_series.median():.1f}%，{(div_series > DIV_THRESHOLD).sum()}/{len(div_series)} 年达标，"
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
    """安全读取百分比值。

    AkShare 财务摘要按 0-100 尺度返回百分比（如 ROE 12.31 表 12.31%），直接用即可。
    历史「>100 则除以 100」守卫会把资不抵债的资产负债率（>100%）或超高 ROE
    压成 ~1，故移除。debt 权重为 0（见 config.SCORE_QUALITY_W），影响仅展示，
    但口径更诚实。
    """
    if not col:
        return None
    try:
        return float(row[col])
    except (ValueError, TypeError):
        return None
