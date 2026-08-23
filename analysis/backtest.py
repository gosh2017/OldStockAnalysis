# -*- coding: utf-8 -*-
"""
历史回测验证模块（提示词 D）— 信号有效性的历史验证。

回测是现有分析层的**消费者**：以"数据注入 + 静默"方式复用 step1–4 / scoring，
**不改其内部任何一行**。四个层次：
  - analyze_as_of ：时点分析适配器（D2）——接收截断 bundle，调现有四步+评分，静默。
  - run_backtest  ：回测引擎（D3）——调仓/选股/持有/换仓成本/退市兜底 + 日频净值。
  - compute_grade_signal：等级信号有效性判定（D3b）——bootstrap CI + 单调性 + 3 态判定。
  - compute_metrics：业绩度量（D4）——纯 numpy，总收益/CAGR/波动/回撤/Sharpe/胜率/Alpha/Beta。

诚实限定（详见 README / CHANGELOG）：
  - **准 PIT**：AkShare 财务/现金流可能重述，回测按"截止 as-of 日 T"截断所有输入序列，
    但无法消除重述偏差——非严格历史可得。
  - **幸存者偏差**：回测标的清单仅含当前在市标的（实盘 fetch 即如此）。
  - **简化成本**：仅计双边交易费率，未计滑点/税/停牌流动性冲击。
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import (
    StockContext, START_DATE, END_DATE,
    BACKTEST_PUB_LAG_DAYS, BACKTEST_TXN_COST,
    BACKTEST_MIN_SAMPLE, BACKTEST_BOOTSTRAP_ITERS,
    BACKTEST_BOOTSTRAP_SEED, BACKTEST_CI_LEVEL,
)
from utils import find_col_in, bootstrap_ci
from data import as_of_bundle, generate_all_demo_data, generate_benchmark_daily
from analysis import (
    fundamental_screening,
    dcf_valuation,
    market_sentiment,
    investment_advice,
    compute_score,
)

# 等级 → 排序权重（A 最高），用于 min_grade 选股比较
_GRADE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}


# =====================================================================
# D2 — 时点分析适配器
# =====================================================================

def _derive_fin_window(fin, ctx) -> tuple[int, int]:
    """从 PIT 截断后的财务摘要取最近年报年作 fin_end，fin_start = fin_end − 4
    （与 main() 的 5 年窗口一致）。空/无报告期列时回退 ctx 默认。"""
    if fin is None or not isinstance(fin, pd.DataFrame) or fin.empty:
        return ctx.fin_end, ctx.fin_start
    date_col = find_col_in(["报告期", "报告日期", "日期", "report"], fin)
    if date_col is None:
        return ctx.fin_end, ctx.fin_start
    ts = pd.to_datetime(fin[date_col], errors="coerce").dropna()
    if ts.empty:
        return ctx.fin_end, ctx.fin_start
    fin_end = int(ts.max().year)
    fin_start = fin_end - 4
    return fin_end, fin_start


def _market_pe_from_history(market_pe_history) -> float | None:
    """从截断后的市场历史 PE 取末值（复用 main() 同款口径）。

    与 main() 的 market_pe 提取一致：pe>0 & pe<500 过滤后取末值，供
    estimate_dividend_yield 的隐含市值口径使用。"""
    if market_pe_history is None or not isinstance(market_pe_history, pd.DataFrame) \
            or market_pe_history.empty:
        return None
    try:
        pe_col = next((c for c in market_pe_history.columns if "市盈率" in str(c)), None)
        if pe_col is None:
            return None
        pe_s = pd.to_numeric(market_pe_history[pe_col], errors="coerce")
        pe_s = pe_s[(pe_s > 0) & (pe_s < 500)].dropna()
        if len(pe_s) > 0:
            return float(pe_s.iloc[-1])
    except Exception:
        return None
    return None


def analyze_as_of(ctx_as_of, bundle: dict) -> dict:
    """时点分析适配器：接收 D1 的 as_of_bundle 截断数据 + end_date=as_of 的
    StockContext，按 main() 同序调用 step1–4 + compute_score，**静默**（抑制 sep
    打印，redirect_stdout 兜底，因现有步骤函数无 quiet 参数）。

    fin 窗口从 bundle 截断后的 fin_abstract 派生（fin_end=最近年报年、fin_start=fin_end−4），
    显式传给 fundamental_screening / dcf_valuation（覆盖 ctx 默认）。bundle 的 daily
    已截断到 <= as_of，故 investment_advice 的 iloc[-1] 即"截至 T 的最新价/日"，天然 PIT 正确。

    不复用 main()（避免每日重取数与大量打印），是对现有纯函数的薄编排，不改 step/scoring
    内部任何一行。返回 {score, grade, recommendation, latest_price, screened, as_of, ctx}
    及透传各 full 结果供下游/调试。
    """
    fin_end, fin_start = _derive_fin_window(bundle.get("fin_abstract"), ctx_as_of)
    bucket = (bundle.get("industry_info") or {}).get("bucket") or "其他"
    market_pe = _market_pe_from_history(bundle.get("market_pe_history"))

    daily_df = bundle.get("daily_df")
    fin_abstract = bundle.get("fin_abstract")
    cashflow_df = bundle.get("cashflow_df")
    dividend_df = bundle.get("dividend_df")
    stock_indicator = bundle.get("stock_indicator")
    market_pe_history = bundle.get("market_pe_history")
    bond_yield_history = bundle.get("bond_yield_history")
    bond_yield = bundle.get("bond_yield")
    market_df = bundle.get("market_df")
    industry_info = bundle.get("industry_info")

    # 静默：抑制 step 函数的 sep/print（现有函数无 quiet 参数，用 redirect_stdout 兜底）
    with redirect_stdout(io.StringIO()):
        screening = fundamental_screening(
            ctx_as_of.symbol, fin_abstract, daily_df, dividend_df,
            fin_start, fin_end, bucket=bucket, market_pe=market_pe)
        dcf = dcf_valuation(
            ctx_as_of.symbol, fin_abstract, cashflow_df, daily_df,
            fin_start, fin_end, stock_indicator=stock_indicator,
            industry_info=industry_info, risk_free=bond_yield)
        sentiment = market_sentiment(
            market_df, bond_yield, stock_indicator, market_pe_history, bond_yield_history)
        advice = investment_advice(daily_df, dcf, sentiment, screening)
        score = compute_score(screening, dcf, sentiment, advice, ctx_as_of)

    latest_price = None
    if daily_df is not None and not daily_df.empty and "收盘" in daily_df.columns:
        try:
            latest_price = float(daily_df["收盘"].iloc[-1])
        except Exception:
            latest_price = None

    return {
        "score": score.get("score", 0.0),
        "grade": score.get("grade", "D"),
        "recommendation": advice.get("recommendation"),
        "latest_price": latest_price,
        "screened": score.get("screened", False),
        "as_of": bundle.get("_as_of", ctx_as_of.end_date),
        "ctx": ctx_as_of,
        # 透传完整结果（供调试 / 扩展图表）
        "score_obj": score,
        "screening": screening,
        "dcf": dcf,
        "sentiment": sentiment,
        "advice": advice,
        "fin_end": fin_end,
        "fin_start": fin_start,
    }


# =====================================================================
# D3 — 回测引擎
# =====================================================================

@dataclass
class BacktestResult:
    """回测结果容器。"""
    equity_curve: pd.Series            # 日期→净值（日频 mark-to-market）
    positions: list = field(default_factory=list)   # 每期持仓与收益
    trades: list = field(default_factory=list)      # 换仓明细
    benchmark_curve: pd.Series = None               # 基准净值（同日历归一）
    grade_forward_returns: dict = field(default_factory=dict)  # 等级→前向收益序列
    grade_returns_panel: list = field(default_factory=list)  # [{date,grade,return,delisted}] 保日期配对
    grade_signal: dict = field(default_factory=dict)         # 等级信号有效性判定（供 UI/图表）
    metrics: dict = field(default_factory=dict)
    rebalance_dates: list = field(default_factory=list)


def _rebalance_dates(freq: str, start, end) -> list:
    """生成 [start, end] 内的调仓日（M/Q/Y → 月末/季末/年末）。

    无 dateutil 依赖，纯 pandas date_range。freq 映射到 pandas 末频：
      M→ME（月末）, Q→QE（季末）, Y→YE（年末）。返回 Timestamp 列表（含 start/end）。
    """
    freq_map = {"M": "ME", "Q": "QE", "Y": "YE"}
    pf = freq_map.get(str(freq).upper(), "QE")
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    dates = pd.date_range(start=s, end=e, freq=pf)
    # date_range 的末频锚定到区间内各期末日；若 start 本身为某期末日，已包含。
    out = list(dates)
    # 确保覆盖：若区间内一个期末日都没有（区间短于一个周期），用 e 兜底
    if not out:
        out = [e]
    return out


def _forward_return(full_daily, buy_date, hold_days, next_date, end) -> tuple[float | None, bool]:
    """用未截断全量日线计算 buy_date→sell 的真实前向收益（退市按末值强制清仓）。

    buy 价 = buy_date 当日（或之前最近交易日）收盘；sell 价：
      - hold_days 给定：buy 之后第 hold_days 个交易日收盘；若越过末交易日（退市/
        数据耗尽）→ 按末值清仓、delisted=True；
      - hold_days=None：next_date（下一调仓日）之前最近交易日收盘；数据在 next_date
        之前耗尽（退市）→ 按末值清仓、delisted=True；无 next_date 用 end。

    delisted=True 表示"持有期内数据耗尽，按末值强制清仓兑现"——已捕捉退市下跌到
    末值（买在末日记 0）。**末期（next_date=None）回测终点数据天然止于 end，无法
    区分退市与回测结束，故末期不判 delisted（保守不报）。** 买在末日/无可对齐交易
    日 → 收益记 0、delisted=True。
    """
    if full_daily is None or not isinstance(full_daily, pd.DataFrame) or full_daily.empty:
        return None, True
    d = full_daily.sort_values("日期").reset_index(drop=True)
    buy_mask = d["日期"] <= pd.Timestamp(buy_date)
    if not buy_mask.any():
        return None, True
    bi = int(np.where(buy_mask.values)[0][-1])
    last_idx = len(d) - 1
    last_data_date = d["日期"].iloc[last_idx]
    is_last = next_date is None  # 末期：回测终点，数据天然止于 end，不判退市

    delisted = False
    if hold_days is not None:
        intended = bi + int(hold_days)
        if intended > last_idx:
            si = last_idx
            delisted = not is_last   # 末期不报（无法与回测结束区分）
        else:
            si = intended
    else:
        sell_target = pd.Timestamp(next_date) if next_date is not None else pd.Timestamp(end)
        if (not is_last) and (last_data_date < sell_target):
            si = last_idx
            delisted = True          # 数据在目标卖出日之前耗尽 → 按末值清仓
        else:
            sell_mask = d["日期"] <= sell_target
            si = int(np.where(sell_mask.values)[0][-1]) if sell_mask.any() else last_idx

    if si <= bi:
        # 无前向可交易日（buy 即末日/退市）→ 按买价清仓，0 收益
        return 0.0, True
    try:
        p_buy = float(d["收盘"].iloc[bi])
        p_sell = float(d["收盘"].iloc[si])
    except Exception:
        return 0.0, True
    if p_buy <= 0:
        return 0.0, True
    return p_sell / p_buy - 1.0, delisted


def _prefetch_live(symbols, end) -> tuple[dict, dict]:
    """实盘预取：每标的一次全量数据入缓存（市场级数据取一次共享）。

    本环境无网，此路径需联网复验（CHANGELOG 已标注）。返回 (caches, market_shared)。
    """
    from data import (
        fetch_daily_data, fetch_financial_abstract, fetch_cashflow_detail,
        fetch_dividend, fetch_stock_indicator, fetch_industry_info,
        fetch_market_pe_history, fetch_bond_yield_history, fetch_bond_yield_10y,
    )
    # 市场级数据（全市场口径，取一次共享）
    market_pe_history = fetch_market_pe_history()
    bond_yield_history = fetch_bond_yield_history(end)
    if bond_yield_history is not None and not bond_yield_history.empty:
        bond_yield = float(bond_yield_history["国债收益率"].iloc[-1])
    else:
        bond_yield = fetch_bond_yield_10y(end)

    caches = {}
    for sym, _ in symbols:
        caches[sym] = {
            "daily_df": fetch_daily_data(sym, START_DATE, end),
            "fin_abstract": fetch_financial_abstract(sym),
            "cashflow_df": fetch_cashflow_detail(sym),
            "dividend_df": fetch_dividend(sym),
            "stock_indicator": fetch_stock_indicator(sym),
            "market_pe_history": market_pe_history,
            "bond_yield_history": bond_yield_history,
            "bond_yield": bond_yield,
            "market_df": None,
            "industry_info": fetch_industry_info(sym),
        }
    shared = {"market_pe_history": market_pe_history,
              "bond_yield_history": bond_yield_history,
              "bond_yield": bond_yield}
    return caches, shared


def _build_daily_returns_matrix(full_daily_by_sym: dict, universe: list,
                                start_dt, end_dt) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """构建日频收益矩阵（calendar × universe），用于逐日 mark-to-market 净值。

    calendar = 全部标的交易日在 [start_dt, end_dt] 内的并集；每标的收盘按 calendar
    reindex + ffill，再 pct_change（缺失/停牌日 fillna 0）。返回 (rets_df, calendar)。
    """
    all_dates = set()
    for sym in universe:
        d = full_daily_by_sym.get(sym)
        if d is not None and not d.empty:
            ds = pd.to_datetime(d["日期"], errors="coerce").dropna()
            all_dates.update(ds[(ds >= start_dt) & (ds <= end_dt)])

    calendar = pd.DatetimeIndex(sorted(all_dates))
    if len(calendar) == 0:
        return pd.DataFrame(), calendar

    closes = pd.DataFrame(index=calendar, columns=universe, dtype=float)
    for sym in universe:
        d = full_daily_by_sym.get(sym)
        if d is None or d.empty:
            continue
        s = d.set_index(pd.to_datetime(d["日期"]))["收盘"].astype(float)
        s = s[~s.index.duplicated(keep="last")]
        closes[sym] = s.reindex(calendar, method="ffill")
    rets = closes.pct_change().fillna(0.0)
    return rets, calendar


def _build_equity_curve(periods, rets_df: pd.DataFrame, calendar: pd.DatetimeIndex,
                        txn_cost: float) -> tuple[pd.Series, pd.Series]:
    """逐日 mark-to-market 净值曲线。

    periods: [(t_start, t_end, weights_dict), ...]，weights 在 (t_start, t_end] 期内持有。
    日期 d 的组合收益 = Σ(持有权重 × 个股当日收益)；持有权重 = 最近一次 < d 的调仓设定。
    换仓日（d == t_start）在计提当日收益后扣双边成本（buy_amt+sell_amt，各 txn_cost）。
    返回 (日频净值 Series, 调仓间期收益 Series 用于胜率)。

    口径注：净值曲线恒按"下一调仓日切换权重"，与 hold_days 无关；hold_days 仅用于
    _forward_return 算等级前向收益（grade_forward_returns / panel）。故 hold_days=None
    （默认）时两者卖出点一致（均下一调仓日收盘）；hold_days≠None 时前向收益按固定交易日
    卖、净值仍按调仓日切——两者口径分歧，属已知简化（默认路径不受影响）。
    """
    if len(calendar) == 0 or not periods:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    rebal_starts = pd.DatetimeIndex([p[0] for p in periods])
    # 持有期索引：对日期 d，held_idx = (严格 < d 的调仓数) - 1
    #   d == t_i（换仓日）→ held_idx = i-1（用旧权重计当日收益，盘后切到 i）
    #   d ∈ (t_i, t_{i+1}) → held_idx = i

    nav = 1.0
    nav_vals = []
    for d in calendar:
        held_idx = int(np.searchsorted(rebal_starts.values, pd.Timestamp(d).to_datetime64(),
                                       side="left")) - 1
        if 0 <= held_idx < len(periods):
            w = periods[held_idx][2]
            if w:
                # 当日组合收益 = Σ w·r（仅取在 rets_df 列中的标的）
                r = 0.0
                for sym, wt in w.items():
                    if sym in rets_df.columns:
                        r += wt * float(rets_df.loc[d, sym])
                r = float(r)
            else:
                r = 0.0
        else:
            r = 0.0
        nav *= (1.0 + r)

        # 换仓日：计提成本（旧权重 → 新权重 的 buy/sell 各扣 txn_cost）
        i = int(np.searchsorted(rebal_starts.values, pd.Timestamp(d).to_datetime64(),
                                side="left"))
        if i < len(periods) and rebal_starts[i] == pd.Timestamp(d):
            old_w = periods[i - 1][2] if i - 1 >= 0 else {}
            new_w = periods[i][2]
            syms = set(new_w) | set(old_w)
            buy_amt = sum(max(new_w.get(s, 0.0) - old_w.get(s, 0.0), 0.0) for s in syms)
            sell_amt = sum(max(old_w.get(s, 0.0) - new_w.get(s, 0.0), 0.0) for s in syms)
            cost = txn_cost * (buy_amt + sell_amt)
            nav *= (1.0 - cost)

        nav_vals.append(nav)

    equity = pd.Series(nav_vals, index=calendar, name="nav")

    # 调仓间期收益（胜率用）：各调仓日净值 pct_change
    nav_at_rebal = equity.reindex(rebal_starts, method="ffill")
    period_returns = nav_at_rebal.pct_change().dropna()
    return equity, period_returns


def run_backtest(symbols, *, start, end, freq="Q", hold_days=None,
                 top_n=10, min_grade="B", weight="equal", txn_cost=BACKTEST_TXN_COST,
                 benchmark="000300", demo=False) -> BacktestResult:
    """回测引擎：调仓日序列 → 每标的 analyze_as_of → 选股 → 持有 → 换仓成本 → 日频净值。

    参数:
      symbols  : [(code, name), ...] 标的清单
      start/end: 回测区间（YYYYMMDD str / Timestamp）
      freq     : 调仓频率 M/Q/Y
      hold_days: 持有期交易日数；None=持有至下一调仓日
      top_n    : 每期最多选入数（按 score 降序）
      min_grade: 选股最低等级（grade >= 该等级才入选）
      weight   : equal（等权）/ score（按 score 归一加权）
      txn_cost : 单边交易成本（0.1%）；换仓双边各扣一次
      benchmark: 基准指数代码
      demo     : demo 模式（用 generate_all_demo_data 宽跨度模拟数据，全程无网）

    返回 BacktestResult（equity_curve 日频 / positions / trades / benchmark_curve /
    grade_forward_returns / metrics / rebalance_dates）。
    """
    universe = [s for s, _ in symbols]
    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)

    # -- 1. 预取全量数据 --
    if demo:
        caches = {}
        for sym, _ in symbols:
            sym_ctx = StockContext(symbol=sym, name=sym, demo=True, no_chart=True,
                                   start_date=START_DATE, end_date=end)
            caches[sym] = generate_all_demo_data(sym_ctx, backtest=True)
        bench_daily = generate_benchmark_daily(benchmark, START_DATE, end)
    else:
        caches, shared = _prefetch_live(symbols, end)
        from data import fetch_benchmark_daily
        bench_daily = fetch_benchmark_daily(benchmark, START_DATE, end)

    full_daily_by_sym = {}
    for sym in universe:
        d = caches[sym].get("daily_df")
        if d is not None and not d.empty:
            full_daily_by_sym[sym] = d.sort_values("日期").reset_index(drop=True)
        else:
            full_daily_by_sym[sym] = pd.DataFrame()

    # -- 2. 调仓日 + 对齐到最近交易日 --
    rebal = _rebalance_dates(freq, start, end)
    trading_days = sorted(set().union(*[
        set(pd.to_datetime(full_daily_by_sym[s]["日期"]).dropna())
        for s in universe if not full_daily_by_sym[s].empty
    ])) if any(not full_daily_by_sym[s].empty for s in universe) else []
    td_idx = pd.DatetimeIndex(trading_days)

    rebal_eff = []
    for rd in rebal:
        pos = td_idx.get_indexer([pd.Timestamp(rd)], method="pad")[0] if len(td_idx) else -1
        if pos >= 0:
            d_eff = td_idx[pos]
            if d_eff not in rebal_eff:  # 去重（同交易日的多调仓日合并）
                rebal_eff.append(d_eff)

    # 把 start 补为首个调仓日（首个 >= start 的交易日），用满整段窗口——
    # 避免 freq=Y 且 start=年初时首个调仓日要等到年末、回测实际晚近近一年。
    # start 本身是末日（如 12-31）时 pad 已覆盖首日，backfill 结果 >= 首个末日，
    # 不前插，避免把"年末非交易日"前移到次年。
    if len(td_idx):
        s_pos = td_idx.get_indexer([pd.Timestamp(start)], method="backfill")[0]
        if 0 <= s_pos < len(td_idx):
            s_eff = td_idx[s_pos]
            if not rebal_eff or s_eff < rebal_eff[0]:
                rebal_eff.insert(0, s_eff)
    if not rebal_eff:
        return BacktestResult(equity_curve=pd.Series(dtype=float),
                              grade_forward_returns={"A": [], "B": [], "C": [], "D": []},
                              rebalance_dates=[])

    # -- 3. 逐调仓日分析 + 等级前向收益 --
    grade_fwd = {"A": [], "B": [], "C": [], "D": []}
    grade_panel = []  # [{date, grade, return, delisted}] 保日期配对（供 bootstrap）
    min_rank = _GRADE_RANK.get(str(min_grade).upper(), 0)
    per_rebal = []  # [{date, results, selected, weights}]

    for i, T in enumerate(rebal_eff):
        next_T = rebal_eff[i + 1] if i + 1 < len(rebal_eff) else None
        T_str = pd.Timestamp(T).strftime("%Y%m%d")
        results = {}
        fr_cache = {}  # sym -> (fr, delisted)，供 positions 复用，不重算
        for sym, name in symbols:
            d_full = full_daily_by_sym.get(sym)
            # 退市排除：末交易日 < T 的标的在 T 买不到，不进分析/选股/信号收益
            if d_full is None or d_full.empty:
                continue
            if pd.Timestamp(d_full["日期"].max()) < pd.Timestamp(T):
                continue
            bundle = as_of_bundle(sym, T, caches[sym], demo=demo,
                                  pub_lag_days=BACKTEST_PUB_LAG_DAYS)
            sym_ctx = StockContext(symbol=sym, name=name, demo=demo, no_chart=True,
                                   start_date=START_DATE, end_date=T_str)
            res = analyze_as_of(sym_ctx, bundle)
            results[sym] = res
            # 全部存活标的（不限入选）按等级分桶，记 hold 期前向收益
            fr, dl = _forward_return(d_full, T, hold_days, next_T, end_dt)
            fr_cache[sym] = (fr, dl)
            if fr is not None:
                grade_fwd.setdefault(res["grade"], []).append(fr)
                grade_panel.append({"date": pd.Timestamp(T), "grade": res["grade"],
                                    "return": fr, "delisted": dl})

        # 选股：grade >= min_grade 且 score 降序 top_n（仅存活标的）
        cands = [(sym, results[sym]) for sym in universe
                 if sym in results
                 and _GRADE_RANK.get(results[sym]["grade"], 0) >= min_rank]
        cands.sort(key=lambda x: x[1]["score"], reverse=True)
        selected = cands[:top_n]

        # 权重
        if not selected:
            weights = {}
        elif weight == "score":
            scores = np.array([max(r["score"], 1e-6) for _, r in selected], dtype=float)
            warr = scores / scores.sum()
            weights = {selected[j][0]: float(warr[j]) for j in range(len(selected))}
        else:  # equal
            n = len(selected)
            weights = {selected[j][0]: 1.0 / n for j in range(n)}

        per_rebal.append({"date": T, "next_date": next_T, "results": results,
                          "selected": [s for s, _ in selected], "weights": weights,
                          "hold_days": hold_days, "fr_cache": fr_cache})

    # -- 4. 日频净值曲线 --
    # 持有期：periods[i] = (t_start, t_end, weights)；t_end = 下一调仓日 or end_dt
    periods = []
    for i, pr in enumerate(per_rebal):
        t_start = pr["date"]
        t_end = per_rebal[i + 1]["date"] if i + 1 < len(per_rebal) else end_dt
        periods.append((t_start, t_end, pr["weights"]))

    rets_df, calendar = _build_daily_returns_matrix(full_daily_by_sym, universe,
                                                      rebal_eff[0], end_dt)
    equity_curve, period_returns = _build_equity_curve(periods, rets_df, calendar, txn_cost)

    # -- 5. 基准曲线（同日历归一） --
    if bench_daily is not None and not bench_daily.empty:
        b = bench_daily.copy()
        b["日期"] = pd.to_datetime(b["日期"])
        b = b.sort_values("日期").drop_duplicates(subset=["日期"]).set_index("日期")["收盘"].astype(float)
        if not equity_curve.empty:
            bench_aligned = b.reindex(equity_curve.index, method="ffill")
            start_val = bench_aligned.iloc[0] if not bench_aligned.empty else None
            benchmark_curve = bench_aligned / start_val if (start_val and start_val > 0) else bench_aligned
        else:
            benchmark_curve = pd.Series(dtype=float)
    else:
        benchmark_curve = pd.Series(dtype=float)

    # -- 6. positions / trades --
    positions = []
    for i, pr in enumerate(per_rebal):
        holdings = []
        for sym in pr["selected"]:
            fr, dl = pr.get("fr_cache", {}).get(sym, (None, True))
            holdings.append({
                "symbol": sym,
                "grade": pr["results"][sym]["grade"],
                "score": pr["results"][sym]["score"],
                "recommendation": pr["results"][sym]["recommendation"],
                "weight": pr["weights"].get(sym, 0.0),
                "forward_return": fr,
                "delisted": dl,
            })
        # 当期组合收益（调仓间期净值变化）
        if not equity_curve.empty:
            nav_t = float(equity_curve.asof(pr["date"]))
            if pr["next_date"] is not None and pr["next_date"] in equity_curve.index:
                nav_next = float(equity_curve.loc[pr["next_date"]])
            else:
                nav_next = float(equity_curve.iloc[-1])
            port_ret = nav_next / nav_t - 1 if nav_t > 0 else 0.0
        else:
            port_ret = 0.0
        positions.append({"date": pr["date"], "holdings": holdings,
                          "portfolio_return": port_ret})

    def _close_at(sym, date):
        """取 sym 在 date（或之前最近交易日）的收盘价，供 trades 记录成交价。"""
        d = full_daily_by_sym.get(sym)
        if d is None or d.empty:
            return None
        sub = d[d["日期"] <= pd.Timestamp(date)]
        if sub.empty:
            return None
        try:
            return float(sub["收盘"].iloc[-1])
        except Exception:
            return None

    trades = []
    prev_sel = set()
    for pr in per_rebal:
        cur_sel = set(pr["selected"])
        for sym in cur_sel - prev_sel:
            trades.append({"date": pr["date"], "symbol": sym, "action": "buy",
                           "weight": pr["weights"].get(sym, 0.0),
                           "price": _close_at(sym, pr["date"])})
        for sym in prev_sel - cur_sel:
            trades.append({"date": pr["date"], "symbol": sym, "action": "sell",
                           "weight": 0.0, "price": _close_at(sym, pr["date"])})
        prev_sel = cur_sel

    # -- 7. 度量 --
    risk_free = None
    # 取国债历史区间均值年化（市场级，共享）；demo/live 均从任一 cache 取（市场口径一致）。
    # 用区间均值而非末值——Sharpe 扣减全程应用一个利率，均值更贴近全程真实无风险水平。
    for sym in universe:
        bh = caches[sym].get("bond_yield_history")
        if bh is not None and not bh.empty:
            try:
                by = pd.to_numeric(bh["国债收益率"], errors="coerce").dropna()
                if len(by) > 0:
                    risk_free = float(by.mean())
                    break
            except Exception:
                pass

    metrics = compute_metrics(equity_curve, benchmark_curve, risk_free,
                             period_returns=period_returns)

    grade_signal = compute_grade_signal(grade_panel)

    return BacktestResult(
        equity_curve=equity_curve,
        positions=positions,
        trades=trades,
        benchmark_curve=benchmark_curve,
        grade_forward_returns=grade_fwd,
        grade_returns_panel=grade_panel,
        grade_signal=grade_signal,
        metrics=metrics,
        rebalance_dates=list(rebal_eff),
    )


# =====================================================================
# D3b — 等级信号有效性判定（bootstrap CI + 单调性 + 3 态）
# =====================================================================
def compute_grade_signal(panel, *, min_sample: int = BACKTEST_MIN_SAMPLE,
                         n_iter: int = BACKTEST_BOOTSTRAP_ITERS,
                         ci_level: float = BACKTEST_CI_LEVEL,
                         seed: int = BACKTEST_BOOTSTRAP_SEED) -> dict:
    """从 grade_returns_panel 算各等级 n/均值/bootstrap CI + 单调性 + best-worst 差 CI，
    出 3 态判定：有效 / 待定 / 无效（样本不足另列）。

    诚实口径：
      - best=有样本的最高级、worst=有样本的最低级（沿用既有 best/worst 语义）。
      - 单调性：连续有样本等级的均值非递增（允许中间缺级跳过）。
      - best-worst 差 CI：优先按调仓日**配对**重抽样（每日 best 均值−worst 均值得
        diff_d，对 diff_d 序列 bootstrap）——配对能控制当日市场环境，比独立
        resample 更稳健；无配对日则退化到各级 CI 不重叠判定（更保守）。
      - 判定：best/worst 任一 n < min_sample → "样本不足"；
              单调且 gap_ci_lo > 0 → "有效"；gap_ci_hi < 0 → "无效"（高等级显著
              跑输）；其余（CI 跨 0 或单调性破缺但未显著反转）→ "待定"。

    返回 {verdict, best, worst, n_by_grade, mean_by_grade, ci_by_grade,
          gap_ci_lo, gap_ci_hi, gap_point, monotonic, ci_level, min_sample}。
    """
    grades = ["A", "B", "C", "D"]
    by_grade = {g: [] for g in grades}
    by_date = {}  # date -> {grade: [returns]}，用于配对
    for row in panel:
        g, r, d = row.get("grade"), row.get("return"), row.get("date")
        if g in by_grade and r is not None:
            by_grade[g].append(float(r))
            by_date.setdefault(d, {}).setdefault(g, []).append(float(r))

    n_by = {g: len(by_grade[g]) for g in grades}
    mean_by = {g: (sum(by_grade[g]) / len(by_grade[g]) if by_grade[g] else None)
               for g in grades}
    ci_by = {}
    for g in grades:
        lo, hi, _ = bootstrap_ci(by_grade[g], n_iter=n_iter, ci=ci_level, seed=seed)
        ci_by[g] = (lo, hi)

    present = [g for g in grades if n_by[g] > 0]
    best = present[0] if present else None
    worst = present[-1] if present else None

    # 单调性：present 序列均值非递增
    monotonic = True
    for a, b in zip(present, present[1:]):
        if mean_by[a] < mean_by[b]:
            monotonic = False
            break

    # best-worst 差的配对 bootstrap（无配对日则置 None，判定时退化到 CI 不重叠）
    gap_lo = gap_hi = gap_point = None
    if best and worst and best != worst and mean_by[best] is not None \
            and mean_by[worst] is not None:
        gap_point = float(mean_by[best] - mean_by[worst])
        diffs = []
        for _d, gm in by_date.items():
            if best in gm and worst in gm:
                diffs.append(sum(gm[best]) / len(gm[best])
                             - sum(gm[worst]) / len(gm[worst]))
        if len(diffs) >= 2:
            gap_lo, gap_hi, _ = bootstrap_ci(diffs, n_iter=n_iter,
                                             ci=ci_level, seed=seed)

    # 判定
    verdict = "待定"
    if not best or not worst or best == worst \
            or n_by[best] < min_sample or n_by[worst] < min_sample:
        verdict = "样本不足"
    elif gap_lo is not None and gap_hi is not None:
        if monotonic and gap_lo > 0:
            verdict = "有效"
        elif gap_hi < 0:
            verdict = "无效"
        else:
            verdict = "待定"
    else:
        # 无配对日：退化到各级 CI 不重叠判定（更保守）
        b_lo, b_hi = ci_by[best]
        w_lo, w_hi = ci_by[worst]
        if b_lo is not None and w_hi is not None and monotonic and b_lo > w_hi:
            verdict = "有效"
        elif b_hi is not None and w_lo is not None and b_hi < w_lo:
            verdict = "无效"
        else:
            verdict = "待定"

    return {
        "verdict": verdict, "best": best, "worst": worst,
        "n_by_grade": n_by, "mean_by_grade": mean_by, "ci_by_grade": ci_by,
        "gap_ci_lo": gap_lo, "gap_ci_hi": gap_hi, "gap_point": gap_point,
        "monotonic": monotonic, "ci_level": ci_level, "min_sample": min_sample,
    }


# =====================================================================
# D4 — 业绩度量层（纯 numpy）
# =====================================================================

def _f(x) -> float | None:
    """数值 float() 包裹；None/nan/inf → None。"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return v


def compute_metrics(equity_curve, benchmark_curve=None, risk_free=None, *,
                    periods_per_year: int = 252, period_returns=None) -> dict:
    """纯 numpy 业绩度量：总收益/CAGR/年化波动/最大回撤/Sharpe/胜率/Alpha/Beta。

    空/常数序列回退 None 不抛异常（Sharpe 在 vol=0 时 None、Beta 在 var(bench)=0 时 None）。
    period_returns 给定时胜率用其（调仓间期收益）；缺省回退曲线 pct_change。
    全部数值 float() 包裹。
    """
    eq = pd.Series(equity_curve, dtype=float).dropna()
    if eq.empty or len(eq) < 2:
        return _empty_metrics(risk_free)

    rets = eq.pct_change().dropna()
    v_start = float(eq.iloc[0])
    v_end = float(eq.iloc[-1])
    total_return = v_end / v_start - 1.0 if v_start > 0 else None

    # 年数：优先日期跨度，回退长度/periods_per_year
    if isinstance(eq.index, pd.DatetimeIndex) and len(eq) > 1:
        years = (eq.index[-1] - eq.index[0]).days / 365.25
    else:
        years = len(eq) / periods_per_year
    years = float(years) if years > 0 else None

    cagr = None
    if v_start > 0 and years and years > 0:
        cagr = (v_end / v_start) ** (1.0 / years) - 1.0

    # 年化波动率
    vol = None
    if len(rets) > 1:
        sd = float(np.std(rets.values, ddof=1))
        vol = sd * np.sqrt(periods_per_year)

    # 最大回撤
    cummax = eq.cummax()
    dd = (cummax - eq) / cummax.replace(0, np.nan)
    max_dd = float(dd.max()) if (not dd.empty and dd.max() == dd.max()) else None

    # Sharpe
    ann_return = cagr if cagr is not None else (
        float(rets.mean()) * periods_per_year if len(rets) > 0 else None)
    rf = float(risk_free) if risk_free is not None else 0.0
    sharpe = None
    if vol and vol > 0 and ann_return is not None:
        sharpe = (ann_return - rf) / vol

    # 胜率
    if period_returns is not None:
        pr = pd.Series(period_returns, dtype=float).dropna()
    else:
        pr = rets
    win_rate = float((pr > 0).mean()) if len(pr) > 0 else None

    # Alpha / Beta（vs 基准）
    alpha = None
    beta = None
    if benchmark_curve is not None:
        bc = pd.Series(benchmark_curve, dtype=float).dropna()
        if len(bc) >= 2:
            brets = bc.pct_change().dropna()
            sr, br = rets, brets
            aligned = False
            if isinstance(eq.index, pd.DatetimeIndex) and isinstance(bc.index, pd.DatetimeIndex):
                common = sr.index.intersection(br.index)
                if len(common) > 1:
                    sr = sr.loc[common]
                    br = br.loc[common]
                    aligned = True
            elif len(sr) == len(br):
                aligned = True
            if aligned and len(sr) > 1:
                var_b = float(np.var(br.values, ddof=1))
                if var_b > 0:
                    beta = float(np.cov(sr.values, br.values, ddof=1)[0, 1] / var_b)
                    # Alpha ≈ 策略 CAGR − 基准 CAGR
                    bc_start = float(bc.iloc[0])
                    bc_end = float(bc.iloc[-1])
                    if isinstance(bc.index, pd.DatetimeIndex) and len(bc) > 1:
                        byears = (bc.index[-1] - bc.index[0]).days / 365.25
                    else:
                        byears = len(bc) / periods_per_year
                    if bc_start > 0 and byears and byears > 0:
                        bench_cagr = (bc_end / bc_start) ** (1.0 / byears) - 1.0
                        if cagr is not None:
                            alpha = cagr - bench_cagr

    return {
        "total_return": _f(total_return),
        "cagr": _f(cagr),
        "volatility": _f(vol),
        "max_drawdown": _f(max_dd),
        "sharpe": _f(sharpe),
        "win_rate": _f(win_rate),
        "alpha": _f(alpha),
        "beta": _f(beta),
        "risk_free": _f(rf),
    }


def _empty_metrics(risk_free) -> dict:
    """空/不足序列的兜底度量（全 None，不抛异常）。"""
    rf = _f(risk_free)
    return {"total_return": None, "cagr": None, "volatility": None,
            "max_drawdown": None, "sharpe": None, "win_rate": None,
            "alpha": None, "beta": None, "risk_free": rf}
