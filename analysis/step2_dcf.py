# -*- coding: utf-8 -*-
"""
第二步：估值锚定 — 自由现金流折现模型（DCF，成熟期「老登股」适配）

基期 FCF = 过去 5 年自由现金流加权均值（近年权重大，含负值不剔除）
自由现金流 = 经营性现金流净额 - 资本性支出 × 0.7   （仅 70% capex 视为维持性支出）

三情景（由 scenarios_for(bucket) 按行业画像构造；"其他"桶 == 历史口径）：
  保守（0% 永续）/ 中性（CAGR 推导的显性增长率 + 行业永续）/ 破产清算（0 增长，折旧摊销不计入）
  破产清算现金流 = FCF - 折旧摊销（移除非现金加回）；D&A 不可得时按 FCF×0.5 估算清算口径
  （与基期 FCF 缺失时 base_fcf×0.5 兜底一致，避免回退净利润导致 liquidation > conservative）。

合理估值上限 = min(中性 DCF, 过去 5 年 PE 中位数 × 当前 EPS)，
作为最终估值天花板，避免 DCF 对成熟股过度外推。
当前 EPS 按行业 eps_method 取：normalized（近 5 年净利均）/ shiller（周期股平滑）。

输出：每股内在价值（破产清算 / 保守 / 中性）+ 合理估值上限 + 敏感性网格
"""
import numpy as np
import pandas as pd

from config import (
    FIN_START, FIN_END, SCENARIOS, DCF_SENSITIVITY,
    INDUSTRY_PROFILES, DCF_GROWTH_CAGR_CLIP,
)
from utils import sep, find_col_in


def scenarios_for(bucket: str) -> dict:
    """由行业画像构造与 SCENARIOS 同形的三情景参数。

    保守 / 破产清算 恒为 0 增长 0 永续；中性 growth 取行业永续作为基线，
    实际显性增长率由 dcf_valuation 内 derive_explicit_growth 覆盖。
    "其他"桶 == 现行 SCENARIOS（等价回退路径，保证零回归）。
    """
    p = INDUSTRY_PROFILES.get(bucket) or INDUSTRY_PROFILES["其他"]
    wacc, perp = p["wacc"], p["perpetual"]
    return {
        "保守 (Conservative)":  {"growth": 0.000, "perpetual": 0.000, "wacc": wacc},
        "中性 (Neutral)":        {"growth": perp, "perpetual": perp, "wacc": wacc},
        "破产清算 (Liquidation)": {"growth": 0.000, "perpetual": 0.000, "wacc": wacc, "liquidation": True},
    }


def derive_explicit_growth(net_profit_values: dict, clip=DCF_GROWTH_CAGR_CLIP) -> float | None:
    """由 ≥3 年归母净利润推算显性增长率（CAGR），clip 到给定区间（item A2）。

    口径由原"首末两点 CAGR"改为对 log(净利润) 序列做最小二乘线性回归：
    斜率 s 即隐含连续增长率，CAGR = exp(s) - 1。利用全部年份而非仅首末两点，
    避免周期股首末恰好落在峰/谷导致的严重失真。

    回退 None（→ 行业永续）的条件：
      - 不足 3 个数据点；
      - 任一净利润 ≤ 0（log 无定义；首末非正即触发，含原回退语义）；
      - 时间跨度 ≤ 0。
    """
    vals = sorted((int(y), float(v)) for y, v in net_profit_values.items())
    if len(vals) < 3:
        return None
    years = [v[0] for v in vals]
    profits = [v[1] for v in vals]
    n_years = years[-1] - years[0]
    if n_years <= 0 or any(p <= 0 for p in profits):
        return None
    # 最小二乘：log(利润) 对年份的线性回归，斜率 = 隐含连续增长率
    slope, _ = np.polyfit(np.array(years, dtype=float), np.log(profits), 1)
    cagr = float(np.exp(slope) - 1.0)
    lo, hi = clip
    return float(max(lo, min(hi, cagr)))


def dcf_valuation(
    symbol: str,
    fin_abstract: pd.DataFrame,
    cashflow_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    fin_start: int | None = None,
    fin_end: int | None = None,
    stock_indicator: pd.DataFrame | None = None,
    industry_info: dict | None = None,
) -> dict:
    """执行 DCF 三情景估值，返回包含估值结果的字典。

    fin_start/fin_end 默认回退到 config.FIN_START/FIN_END（--years 可覆盖）。
    stock_indicator（个股历史 PE/PB）用于「过去 5 年 PE 中位数 × 当前 EPS」锚定上限。
    industry_info（行业归属 + 总股本，来自 fetch_industry_info）：决定 DCF 参数 /
    EPS 算法 / 总股本来源；None → "其他"桶（== 现行口径，零回归）。

    返回 dict 字段（本任务 A1/A4/A5 新增/调整）：
      capex_estimated : bool，是否有年份 capex 走了行业比例兜底（item A1），
                        供下游完整度置信度接线使用（本任务只暴露，不接 scoring）。
      各情景 intrinsic_value : wacc≤永续时该情景置 None（item A4，主路径防御性 guard）。
      base_fcf_liquidation : D&A 不可得时按 FCF×0.5 估算（item A5），不再回退归母净利润。

    TODO（item A3）：估值安全边际非线性映射（恢复极端值区分度）归入 prompt B
    （scoring.py 的 margin 子分改造），本层只暴露 conservative/neutral 原始值，不在此处映射。
    """
    sep("第二步：估值锚定 — 自由现金流折现模型（DCF）")

    # -- 行业画像（item 1/4）--
    bucket = (industry_info or {}).get("bucket") or "其他"
    profile = INDUSTRY_PROFILES.get(bucket) or INDUSTRY_PROFILES["其他"]
    eps_method = profile["eps_method"]

    fin_start = fin_start if fin_start is not None else FIN_START
    fin_end = fin_end if fin_end is not None else FIN_END
    years = list(range(fin_start, fin_end + 1))

    # -- 提取经营性现金流 & 归母净利润（fin_abstract）--
    ocf_values = {}
    net_profit_values = {}

    if fin_abstract is not None and not fin_abstract.empty:
        date_col = find_col_in(["报告日期", "报告期", "report"], fin_abstract)
        ocf_col  = find_col_in(["经营活动产生的现金流量净额", "经营活动现金"], fin_abstract)
        np_col   = find_col_in(["归属于上市公司股东的净利润", "归母净利润", "净利润"], fin_abstract)
        if date_col:
            fin_abstract[date_col] = pd.to_datetime(fin_abstract[date_col], errors="coerce")
            fin_abstract["年份"] = fin_abstract[date_col].dt.year
            for year in years:
                year_data = fin_abstract[fin_abstract["年份"] == year]
                if not year_data.empty:
                    row = year_data.sort_values(date_col).iloc[-1]
                    if ocf_col:
                        try:
                            ocf_values[year] = float(row[ocf_col])
                        except (ValueError, TypeError):
                            pass
                    if np_col:
                        try:
                            net_profit_values[year] = float(row[np_col])
                        except (ValueError, TypeError):
                            pass

    # -- 提取资本性支出 & 折旧摊销（cashflow_df）--
    capex_values = {}
    da_values = {}

    if cashflow_df is not None and not cashflow_df.empty:
        date_col_cf = find_col_in(["报告期", "报告日期", "report"], cashflow_df)
        capex_col   = find_col_in(["购建固定资产", "资本性支出", "购建长期资产"], cashflow_df)
        # D&A：优先合并列；否则分别取折旧 + 摊销求和（兼容 sina 分列）
        da_col = find_col_in(["折旧与摊销", "折旧及摊销"], cashflow_df)
        dep_col = da_col if da_col else find_col_in(
            ["固定资产折旧", "油气资产折耗", "生产性生物资产折旧", "折旧"], cashflow_df)
        am_col = find_col_in(["无形资产摊销", "长期待摊费用摊销", "摊销"], cashflow_df) if not da_col else None
        if date_col_cf and (capex_col or da_col or dep_col):
            cashflow_df[date_col_cf] = pd.to_datetime(cashflow_df[date_col_cf], errors="coerce")
            cashflow_df["年份"] = cashflow_df[date_col_cf].dt.year
            for year in years:
                year_data = cashflow_df[cashflow_df["年份"] == year]
                if not year_data.empty:
                    row = year_data.sort_values(date_col_cf).iloc[-1]
                    if capex_col:
                        try:
                            capex_values[year] = float(row[capex_col])
                        except (ValueError, TypeError):
                            pass
                    if da_col or dep_col:
                        try:
                            val = float(row[da_col]) if da_col else 0.0
                            if not da_col:
                                if dep_col:
                                    val += abs(float(row[dep_col]))
                                if am_col:
                                    val += abs(float(row[am_col]))
                            if val != 0.0:
                                da_values[year] = val
                        except (ValueError, TypeError):
                            pass
    da_available = bool(da_values)

    # -- 计算 FCF（FCF = OCF - CAPEX×0.7）--
    print(f"\n  [DATA] 各年现金流数据（单位：亿元）\n")
    fcf_values = {}
    has_da = da_available
    header = (f"  {'年份':>6s}  {'经营现金流':>12s}  {'资本性支出':>12s}"
              + (f"  {'折旧摊销':>12s}" if has_da else "")
              + f"  {'自由现金流':>12s}")
    print(header)
    print(f"  {'-' * (len(header) - 2)}")

    # item A1：capex 兜底按行业 capex_ratio（重资产高、轻资产低），不再固定 0.20
    capex_ratio = profile["capex_ratio"]
    capex_estimated_years = set()   # 走兜底的年份，供完整度置信度标记（item A1）

    for year in years:
        ocf = ocf_values.get(year, 0)
        capex = capex_values.get(year, 0)
        if year not in capex_values:
            capex = abs(ocf) * capex_ratio  # 按行业比例估算维持性 capex
            capex_estimated_years.add(year)
        fcf = ocf - capex * 0.7     # 仅 70% capex 视为维持性支出
        fcf_values[year] = fcf
        da = da_values.get(year, 0)
        if has_da:
            print(f"  {year:>6d}  {ocf / 1e8:>12.1f}  {capex / 1e8:>12.1f}"
                  f"  {da / 1e8:>12.1f}  {fcf / 1e8:>12.1f}")
        else:
            print(f"  {year:>6d}  {ocf / 1e8:>12.1f}  {capex / 1e8:>12.1f}"
                  f"  {fcf / 1e8:>12.1f}")
    if capex_estimated_years:
        print(f"  [!] capex 缺失年份 {sorted(capex_estimated_years)} 按 OCF×{capex_ratio:.2f}（{bucket}桶）估算")

    # -- 显性增长率（CAGR 推导，item 1）--
    explicit_growth = derive_explicit_growth(net_profit_values, profile["growth_clip"])

    # -- 三情景参数（行业化 + 中性显性增长覆盖）--
    scenario_params = scenarios_for(bucket)
    if explicit_growth is not None:
        scenario_params["中性 (Neutral)"]["growth"] = explicit_growth

    # -- 基期 FCF（加权均值，含负值不剔，item 3）--
    all_fcf = [v for v in fcf_values.values() if v is not None]
    has_negative_fcf = bool(any(v < 0 for v in all_fcf))
    if not all_fcf:
        print("\n  [X] 无法计算有效 FCF，跳过 DCF 估值。")
        return {"valuations": None, "base_fcf": None, "base_fcf_liquidation": None,
                "total_shares": None, "conservative": None, "neutral": None,
                "neutral_raw": None, "liquidation": None, "fair_value_ceiling": None,
                "pe_median_5y": None, "current_eps": None, "pe_anchor_value": None,
                "da_available": da_available, "capex_estimated": bool(capex_estimated_years),
                "bucket": bucket,
                "scenario_params": scenario_params, "explicit_growth": explicit_growth,
                "has_negative_fcf": has_negative_fcf, "eps_method": eps_method}

    weights = np.linspace(0.5, 1.0, len(all_fcf))  # 近年权重大
    base_fcf = float(np.average(all_fcf, weights=weights))
    print(f"\n  [PIN] 基期 FCF（加权均值，含负值）: {base_fcf / 1e8:.1f} 亿元"
          + ("  [含负值年份]" if has_negative_fcf else ""))

    # -- 破产清算基期 FCF（加权；FCF - D&A；D&A 不可得按 FCF×0.5 估算清算口径，item A5）--
    # 破产清算语义应"移除非现金加回"。D&A 不可得时不再回退归母净利润
    # （会把非现金收益加回，常致 liquidation > conservative）；改按 FCF×0.5
    # 估算清算口径，与 all_liq 为空时的 base_fcf*0.5 一致，自然保证 ≤ 保守。
    liquidation_fcf_values = {}
    for year in years:
        fcf = fcf_values.get(year, 0)
        if da_available:
            liquidation_fcf_values[year] = fcf - da_values.get(year, 0)
        else:
            liquidation_fcf_values[year] = fcf * 0.5
    all_liq = [v for v in liquidation_fcf_values.values() if v is not None]
    if all_liq:
        w_liq = np.linspace(0.5, 1.0, len(all_liq))
        base_fcf_liquidation = float(np.average(all_liq, weights=w_liq))
    else:
        base_fcf_liquidation = base_fcf * 0.5
    if da_available:
        print(f"  [PIN] 破产清算基期 FCF（FCF - 折旧摊销）: {base_fcf_liquidation / 1e8:.1f} 亿元")
    else:
        print(f"  [!] D&A 不可得，按 FCF×0.5 估算清算口径: {base_fcf_liquidation / 1e8:.1f} 亿元")

    # -- 总股本（item 2：industry_info 优先 + 无兜底 None 守卫）--
    total_shares = _get_total_shares(symbol, fin_abstract, daily_df, industry_info)
    if not total_shares:
        print("\n  [X] 无法获取总股本（行业信息 / 日频 / 财务摘要均缺失），跳过 DCF 估值。")
        return {"valuations": None, "base_fcf": base_fcf, "base_fcf_liquidation": base_fcf_liquidation,
                "total_shares": None, "conservative": None, "neutral": None,
                "neutral_raw": None, "liquidation": None, "fair_value_ceiling": None,
                "pe_median_5y": None, "current_eps": None, "pe_anchor_value": None,
                "da_available": da_available, "capex_estimated": bool(capex_estimated_years),
                "bucket": bucket,
                "scenario_params": scenario_params, "explicit_growth": explicit_growth,
                "has_negative_fcf": has_negative_fcf, "eps_method": eps_method}
    print(f"  [PIN] 总股本: {total_shares / 1e8:.2f} 亿股")

    # -- 三情景 DCF --
    print(f"\n  [DATA] DCF 三情景估值结果（行业桶：{bucket}）\n")
    print(f"  {'情景':>20s}  {'增长率':>8s}  {'永续增长':>8s}  {'WACC':>8s}  {'内在价值':>10s}")
    print(f"  {'-' * 66}")

    valuations = {}
    for scenario_name, params in scenario_params.items():
        g, perp_g, wacc = params["growth"], params["perpetual"], params["wacc"]
        base = base_fcf_liquidation if params.get("liquidation") else base_fcf

        # item A4：防御性 guard——wacc ≤ 永续增长时 terminal value 发散（除零/负值），
        # 跳过该情景（intrinsic_value 置 None），保证不产生负/无穷内在价值。
        # 现行画像配置安全（永续 ≤0.025 < wacc ≥0.085），仅作配置被改/永续抬升时的安全网。
        if wacc <= perp_g:
            print(f"  [!] wacc≤永续，跳过该情景: {scenario_name} (wacc={wacc:.1%}, perp={perp_g:.1%})")
            valuations[scenario_name] = {
                "intrinsic_value": None,
                "enterprise_value": None,
                "pv_operations": None,
                "pv_terminal": None,
                "terminal_value": None,
            }
            continue

        pv_sum = sum(
            base * ((1 + g) ** t) / ((1 + wacc) ** t)
            for t in range(1, 6)
        )

        terminal_fcf = base * ((1 + g) ** 5) * (1 + perp_g)
        terminal_value = terminal_fcf / (wacc - perp_g)
        pv_terminal = terminal_value / ((1 + wacc) ** 5)

        intrinsic_value = (pv_sum + pv_terminal) / total_shares

        valuations[scenario_name] = {
            "intrinsic_value": intrinsic_value,
            "enterprise_value": pv_sum + pv_terminal,
            "pv_operations": pv_sum,
            "pv_terminal": pv_terminal,
            "terminal_value": terminal_value,
        }

        print(f"  {scenario_name:>20s}  {g:>7.1%}  {perp_g:>7.1%}  {wacc:>7.1%}"
              f"  {intrinsic_value:>9.2f} 元")

    if explicit_growth is not None:
        print(f"  [INFO] 中性显性增长率由净利 CAGR 推导: {explicit_growth:.2%}"
              f"（clip 到 {profile['growth_clip']}）")

    conservative = valuations["保守 (Conservative)"]["intrinsic_value"]
    neutral_raw  = valuations["中性 (Neutral)"]["intrinsic_value"]
    liquidation  = valuations["破产清算 (Liquidation)"]["intrinsic_value"]

    # item A4 兜底：某情景因 wacc≤永续被跳过（intrinsic_value=None）时，
    # 给下游一个安全的非空值，避免 investment_advice 直接索引 None 而崩溃。
    # 零增长 0 永续口径下保守情景不会触发，此处仅作 None→0 安全网。
    if conservative is None:
        conservative = 0.0
        valuations["保守 (Conservative)"]["intrinsic_value"] = conservative
    if neutral_raw is None:
        neutral_raw = conservative
        valuations["中性 (Neutral)"]["intrinsic_value"] = neutral_raw
    if liquidation is None:
        liquidation = conservative
        valuations["破产清算 (Liquidation)"]["intrinsic_value"] = liquidation

    # 安全钳位：保证 破产清算 <= 保守（D&A 可得时 inert；A5 后 FCF×0.5 口径自然成立）
    if liquidation > conservative:
        liquidation = conservative
        valuations["破产清算 (Liquidation)"]["intrinsic_value"] = liquidation

    # -- 合理估值上限 = min(中性 DCF, 过去 5 年 PE 中位数 × 当前 EPS) --
    # item 4：当前 EPS 按行业 eps_method 取 normalized / shiller。
    pe_median_5y, current_eps, pe_anchor_value = None, None, None
    fair_value_ceiling = neutral_raw
    try:
        if net_profit_values and total_shares:
            np_sorted = sorted(net_profit_values.items())  # [(year, val)]
            if eps_method == "shiller":
                recent = np_sorted[-10:]   # 周期股：≤10 年净利均值平滑峰谷
            else:
                recent = np_sorted[-5:]    # normalized：近 5 年均值
            mean_np = float(np.mean([v for _, v in recent]))
            current_eps = mean_np / total_shares
        if stock_indicator is not None and not stock_indicator.empty:
            pe_col = find_col_in(["市盈率PE", "市盈率", "pe", "PE"], stock_indicator)
            date_col_si = find_col_in(["日期", "date", "trade_date"], stock_indicator)
            if pe_col and date_col_si:
                si = stock_indicator.copy()
                si[date_col_si] = pd.to_datetime(si[date_col_si], errors="coerce")
                si["_year"] = si[date_col_si].dt.year
                cutoff_year = si["_year"].max()
                pe_5y = pd.to_numeric(si[pe_col], errors="coerce")
                pe_5y = pe_5y[(si["_year"] >= cutoff_year - 4) & (pe_5y > 0)]
                if len(pe_5y) > 0:
                    pe_median_5y = float(pe_5y.median())
        if pe_median_5y and current_eps and pe_median_5y > 0 and current_eps > 0:
            pe_anchor_value = pe_median_5y * current_eps
            fair_value_ceiling = min(neutral_raw, pe_anchor_value)
    except Exception:
        pass  # 锚定失败 → 上限 = 中性 DCF（兜底）

    # 保证完整阶梯：破产清算 ≤ 保守 ≤ 合理估值上限。
    # 低 PE 股（银行/周期股）的 PE 锚定会低于 DCF 内在价值，把天花板压到 DCF 之下，
    # 此时 保守/破产清算 让位于市场天花板，避免「底值 > 上限」并把建议分档搞反。
    if fair_value_ceiling < conservative:
        conservative = fair_value_ceiling
        valuations["保守 (Conservative)"]["intrinsic_value"] = conservative
    if liquidation > conservative:
        liquidation = conservative
        valuations["破产清算 (Liquidation)"]["intrinsic_value"] = liquidation

    print(f"\n  -- 每股内在价值估值区间 --")
    print(f"  [GRY] 破产清算估值: {liquidation:.2f} 元")
    print(f"  [RED] 保守估值: {conservative:.2f} 元")
    print(f"  [YLW] 中性估值(原始): {neutral_raw:.2f} 元")
    if pe_anchor_value is not None:
        print(f"  [BLU] PE锚定值: {pe_anchor_value:.2f} 元 "
              f"(过去5年PE中位 {pe_median_5y:.1f} × 当前EPS {current_eps:.2f} [{eps_method}])")
    print(f"  [GRN] 合理估值上限: {fair_value_ceiling:.2f} 元")
    print(f"  [RULER] 估值区间: [{liquidation:.2f}, {fair_value_ceiling:.2f}] 元")

    return {
        "valuations": valuations,
        "base_fcf": base_fcf,
        "base_fcf_liquidation": base_fcf_liquidation,
        "total_shares": total_shares,
        "conservative": conservative,
        "neutral": fair_value_ceiling,        # 封顶后（下游 scoring/advice 零改动读取）
        "neutral_raw": neutral_raw,           # 原始中性 DCF（透明度）
        "liquidation": liquidation,
        "fair_value_ceiling": fair_value_ceiling,
        "pe_median_5y": pe_median_5y,
        "current_eps": current_eps,
        "pe_anchor_value": pe_anchor_value,
        "da_available": da_available,
        "capex_estimated": bool(capex_estimated_years),  # item A1：是否有年份 capex 走了行业兜底
        "bucket": bucket,
        "scenario_params": scenario_params,
        "explicit_growth": explicit_growth,
        "has_negative_fcf": has_negative_fcf,
        "eps_method": eps_method,
    }


def dcf_sensitivity(base_fcf, total_shares) -> dict:
    """
    DCF 敏感性分析：在 永续增长率 × 折现率 网格上扫描每股内在价值，
    每格显性期增长率 = 该行永续增长率（成熟股稳态口径）。

    纯函数，便于单测与报告复用。返回：
      {"grid": DataFrame(行=永续增长,列=wacc), "perpetual_axis": [...], "wacc_axis": [...]}
    base_fcf/total_shares 缺失时返回空网格。
    """
    if not base_fcf or not total_shares:
        return {"grid": None, "perpetual_axis": [], "wacc_axis": []}

    p_lo, p_hi, p_step = DCF_SENSITIVITY["perpetual_range"]
    w_lo, w_hi, w_step = DCF_SENSITIVITY["wacc_range"]

    # 用四舍五入消除浮点步进噪声，确保端点闭合
    n_p = int(round((p_hi - p_lo) / p_step)) + 1
    n_w = int(round((w_hi - w_lo) / w_step)) + 1
    perpetual_axis = [round(p_lo + i * p_step, 6) for i in range(n_p)]
    wacc_axis = [round(w_lo + i * w_step, 6) for i in range(n_w)]

    rows = {}
    for perp in perpetual_axis:
        row = {}
        for w in wacc_axis:
            if w <= perp:
                row[w] = np.nan
                continue
            g = perp  # 显性期增长率 = 永续增长率
            pv_sum = sum(base_fcf * (1 + g) ** t / (1 + w) ** t for t in range(1, 6))
            terminal_value = base_fcf * (1 + g) ** 5 * (1 + perp) / (w - perp)
            pv_terminal = terminal_value / (1 + w) ** 5
            row[w] = (pv_sum + pv_terminal) / total_shares
        rows[perp] = row

    grid = pd.DataFrame(rows).T  # 行=永续增长, 列=wacc
    grid.index = [f"{p * 100:.1f}%" for p in perpetual_axis]
    grid.columns = [f"{w * 100:.1f}%" for w in wacc_axis]
    return {"grid": grid, "perpetual_axis": perpetual_axis, "wacc_axis": wacc_axis}


def _get_total_shares(symbol: str, fin_abstract: pd.DataFrame,
                      daily_df: pd.DataFrame, industry_info: dict | None = None) -> float | None:
    """获取总股本。优先级：行业信息(EM f84) > 日频数据 > 财务摘要。
    三源全失败 → 返回 None（不兜底，避免对非 000001 标的错估每股价值）。

    行业信息来自 fetch_industry_info（akshare stock_individual_info_em 的「总股本」），
    月级缓存、口径权威；实盘优先依赖。
    """
    # 1. 行业信息（live 的 EM f84 总股本）
    if industry_info:
        ts = industry_info.get("total_shares")
        if ts is not None:
            try:
                total = float(ts)
                if total > 0:
                    return total
            except (ValueError, TypeError):
                pass

    # 2. 从日频数据获取
    if daily_df is not None and not daily_df.empty:
        for col in ["outstanding_share", "总股本", "total_share", "total_shares"]:
            if col in daily_df.columns:
                val = pd.to_numeric(daily_df[col], errors="coerce").dropna()
                if len(val) > 0:
                    total = float(val.iloc[-1])
                    if total > 0:
                        return total
        # 检查 总股本(万) 等带单位的列
        share_col = find_col_in(["总股本", "股份总数", "total_share"], daily_df)
        if share_col:
            val = pd.to_numeric(daily_df[share_col], errors="coerce").dropna()
            if len(val) > 0:
                total = float(val.iloc[-1])
                if total > 0:
                    # 单位转换：若数值 < 10万（即 < 10e4），可能是万股
                    if total < 1e4:
                        total *= 1e4
                    return total

    # 3. 从财务摘要获取
    if fin_abstract is not None and not fin_abstract.empty:
        share_col = find_col_in(["总股本", "股份总数", "total_share"], fin_abstract)
        if share_col:
            try:
                last_row = fin_abstract.sort_values(fin_abstract.columns[0]).iloc[-1]
                total = float(last_row[share_col])
                if total > 0:
                    return total
            except Exception:
                pass

    # 三源均不可得 → None（不再兜底 197.56e8）
    print(f"  [!] 总股本三源均不可得（行业信息 / 日频 / 财务摘要），返回 None。")
    return None
