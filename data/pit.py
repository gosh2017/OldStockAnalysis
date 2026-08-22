# -*- coding: utf-8 -*-
"""
时点数据截断层（Point-in-Time, PIT）— 回测专用。

AkShare 的财务/现金流接口返回全历史且常含**重述后**数据，并非严格 PIT。
若直接在历史日 T 复用 main()，会用"今天才有的（且可能重述后的）"财务数据 →
未来函数。本层在回测调用分析前，按"截止 as-of 日 T"显式截断所有输入序列，
保证每个调仓日 T 看到的数据严格不晚于 T（准 PIT 口径，重述/幸存者偏差限定
见 README）。

提供三个纯函数：
  - truncate_to_date       ：通用按日期列截断到 <= as_of
  - filter_reports_by_pub_lag：财务/现金流按"报告期 + 披露滞后"过滤
  - as_of_bundle           ：组合产出回测一次调用的全部截断数据

均为纯函数（无副作用、不联网），便于单测与离线 demo 复现。
"""
from __future__ import annotations

import pandas as pd

from config import BACKTEST_PUB_LAG_DAYS
from utils import find_col_in


def truncate_to_date(df, date_col, as_of, inclusive: bool = True) -> pd.DataFrame:
    """按日期列截断 DataFrame 到 <= as_of（或 < as_of）。

    date_col 为列名提示（如 "日期"/"报告期"），经 find_col_in 模糊匹配定位实际
    列名（大小写不敏感、子串命中），复用 utils.helpers 既有列名匹配工具。
    as_of 统一转 pd.Timestamp；空 df / 无 df / 无可识别日期列时安全返回空 df
    （空 df 原样返回，不抛异常）。

    参数:
      df        : 含日期列的 DataFrame
      date_col  : 日期列名提示（模糊匹配）
      as_of     : 截断时点（str / pd.Timestamp / datetime）
      inclusive : True(默认)=保留 <= as_of；False=保留 < as_of

    返回:
      截断后的 DataFrame（copy，不污染入参）；入参为空/无 df 时返回空 DataFrame。
    """
    if df is None:
        return pd.DataFrame()
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame()
    if df.empty:
        return df.copy()

    # 模糊定位日期列：优先用调用方给的提示，回退常见日期列名
    col = find_col_in([date_col], df) if date_col else None
    if col is None:
        col = find_col_in(["日期", "报告期", "报告日期", "date", "Datetime"], df)
    if col is None:
        # 无可识别日期列 → 无法按日期截断，原样返回（调用方应保证传入带日期列的表）
        return df.copy()

    ts = pd.to_datetime(df[col], errors="coerce")
    as_of_ts = pd.Timestamp(as_of)
    mask = (ts <= as_of_ts) if inclusive else (ts < as_of_ts)
    # 日期不可解析的行（NaT）一律视为越界，剔除以保证严格 PIT
    return df[mask.fillna(False)].copy()


def filter_reports_by_pub_lag(fin_df, as_of, lag_days: int = BACKTEST_PUB_LAG_DAYS) -> pd.DataFrame:
    """财务/现金流按"报告期 + 披露滞后"过滤——仅保留报告期末 <= as_of − lag_days 的行。

    年报次年 4–5 月才披露（A 股年报法定 4 月底前），按"报告期日历年末"取数会把
    尚未披露的年报当已知 → 未来函数。本函数按披露时点而非日历年末取数：仅保留
    报告期 <= as_of − lag_days 的行。lag_days=120≈4 个月为保守口径（覆盖 4–5 月
    披露窗口），季报亦据此过滤（属可接受保守降级，已在 docstring 标注）。

    参数:
      fin_df  : 含报告期日期列的财务/现金流 DataFrame
      as_of   : 截断时点
      lag_days: 披露滞后天数（默认 config.BACKTEST_PUB_LAG_DAYS=120）

    返回:
      过滤后的 DataFrame；入参为空/无 df 时返回空 DataFrame。
    """
    if fin_df is None:
        return pd.DataFrame()
    if not isinstance(fin_df, pd.DataFrame):
        return pd.DataFrame()
    if fin_df.empty:
        return fin_df.copy()

    date_col = find_col_in(["报告期", "报告日期", "日期", "report"], fin_df)
    if date_col is None:
        return fin_df.copy()

    ts = pd.to_datetime(fin_df[date_col], errors="coerce")
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=lag_days)
    mask = ts <= cutoff
    return fin_df[mask.fillna(False)].copy()


def as_of_bundle(symbol: str, as_of, live_cache: dict, *,
                 demo: bool = False,
                 pub_lag_days: int = BACKTEST_PUB_LAG_DAYS) -> dict:
    """组合产出回测一次调用所需的全部截断数据（供 analyze_as_of 直接消费）。

    live 模式从 live_cache（run_backtest 预取的全量数据缓存）取数后逐项截断；
    demo 模式从 generate_all_demo_data 派生的 wide-span 序列截断（live_cache
    在 demo 下持有该派生数据）。两模式截断口径一致，demo 标志仅影响 bond_yield
    兜底来源标注，便于调用方区分。

    截断口径：
      - daily / dividend / stock_indicator / market_pe_history / bond_yield_history：
        按"日期/公告日期 <= as_of"截断（情绪分位须在"截止 T 的历史窗口"上算，
        不得偷看 T 之后）。
      - fin_abstract / cashflow_df：按 filter_reports_by_pub_lag 过滤（披露滞后）。
      - bond_yield：取截断后国债历史末值（与 main() 口径一致）。
      - industry_info / market_df：月级稳定/快照口径，不截断直接透传（实盘取最新
        即可，demo 透传）——此简化已在 docstring 标注，属回测已知限定。

    参数:
      symbol     : 标的代码
      as_of      : 截断时点
      live_cache : 预取的全量数据 dict（键同 generate_all_demo_data）
      demo       : 是否 demo 模式
      pub_lag_days: 披露滞后天数

    返回:
      与 generate_all_demo_data 同构的 dict，各值为截断后的子集。
    """
    raw = live_cache or {}

    daily_df = truncate_to_date(raw.get("daily_df"), "日期", as_of)
    fin_abstract = filter_reports_by_pub_lag(raw.get("fin_abstract"), as_of, pub_lag_days)
    cashflow_df = filter_reports_by_pub_lag(raw.get("cashflow_df"), as_of, pub_lag_days)
    dividend_df = truncate_to_date(raw.get("dividend_df"), "公告日期", as_of)
    stock_indicator = truncate_to_date(raw.get("stock_indicator"), "日期", as_of)
    market_pe_history = truncate_to_date(raw.get("market_pe_history"), "日期", as_of)
    bond_yield_history = truncate_to_date(raw.get("bond_yield_history"), "日期", as_of)

    # bond_yield 取截断后国债历史末值（截断口径，PIT 正确）；缺失回退预取标量
    bond_yield = None
    if bond_yield_history is not None and not bond_yield_history.empty:
        try:
            by = pd.to_numeric(bond_yield_history["国债收益率"], errors="coerce").dropna()
            if len(by) > 0:
                bond_yield = float(by.iloc[-1])
        except Exception:
            bond_yield = None
    if bond_yield is None:
        bond_yield = raw.get("bond_yield")

    # industry_info / market_df 不截断透传（月级稳定 / 快照口径，已知简化）
    return {
        "daily_df": daily_df,
        "fin_abstract": fin_abstract,
        "cashflow_df": cashflow_df,
        "dividend_df": dividend_df,
        "market_df": raw.get("market_df"),
        "bond_yield": bond_yield,
        "stock_indicator": stock_indicator,
        "market_pe_history": market_pe_history,
        "bond_yield_history": bond_yield_history,
        "industry_info": raw.get("industry_info"),
        "_as_of": pd.Timestamp(as_of),
        "_demo": demo,
    }
