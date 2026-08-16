# -*- coding: utf-8 -*-
"""
综合评分系统 — 把基本面质量、估值安全边际、市场情绪融合为一个
0–100 的综合得分与 A–D 等级，便于横向比较与批量筛选。

总分 = 质量 × 0.40 + 估值 × 0.35 + 情绪 × 0.25  （权重见 config.SCORE_WEIGHTS）

设计要点（价值投资视角）：
  - 质量+估值合计 75%：价值投资 = 高质量 + 好价格，二者为主；
  - 情绪 25%：择时修饰，股债性价比与个股自身估值分位提供"贵/便宜"信号；
  - 缺失数据"类内重归一化"：某子指标无数据则在该类内丢弃其权重并重新归一，
    保证 demo 与数据不全的标的仍能得到稳定可比的分数；
  - DCF 无法估值（base_fcf 缺失）时估值类直接记 0，作为"无法估值→谨慎"信号。

纯函数：仅依赖传入的结果 dict，不访问网络/全局，便于单元测试。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import (
    SCORE_WEIGHTS, SCORE_QUALITY_W, SCORE_VALUATION_W, SCORE_SENTIMENT_W,
    GRADE_BANDS,
)


def _clip(x, lo=0.0, hi=100.0) -> float:
    """限制到 [lo, hi]。"""
    return float(max(lo, min(hi, x)))


def _category_score(scores: dict, weights: dict) -> float | None:
    """类内加权平均：跳过 None 与 0 权重项，其余权重重新归一化。
    全部缺失时返回 None（由上层决定是否跳过该类）。"""
    total_w = 0.0
    acc = 0.0
    for key, w in weights.items():
        if w == 0:
            continue
        val = scores.get(key)
        if val is None:
            continue
        acc += val * w
        total_w += w
    return acc / total_w if total_w > 0 else None


def _grade(score: float) -> str:
    """由总分映射等级（从高到低匹配）。"""
    for letter, threshold in GRADE_BANDS:
        if score >= threshold:
            return letter
    return GRADE_BANDS[-1][0]


def _quality_subscores(screening: dict) -> dict:
    """从 screening['table'] 提取质量子分。"""
    table = screening.get("table")
    subs = {"roe": None, "roe_stability": None, "dividend": None,
            "ocf_quality": None, "debt": None}
    if table is None or not isinstance(table, pd.DataFrame) or table.empty:
        return subs

    def col(cand):
        for c in table.columns:
            if cand in str(c):
                return c
        return None

    # ROE 水平与稳定性
    roe_col = col("ROE")
    if roe_col:
        roe_s = pd.to_numeric(table[roe_col], errors="coerce").dropna()
        if len(roe_s) > 0:
            subs["roe"] = _clip(roe_s.mean() / 20 * 100)           # 20% → 100
            if len(roe_s) > 1:
                subs["roe_stability"] = _clip(100 - roe_s.std() * 5)  # 标准差越小越高

    # 股息率水平 + 分红连续性
    div_col = col("股息率")
    if div_col:
        div_s = pd.to_numeric(table[div_col], errors="coerce").dropna()
        if len(div_s) > 0:
            consistency = (div_s > 0).mean()                       # 有分红的年占比
            subs["dividend"] = _clip(div_s.mean() / 4 * 100) * consistency  # 4% → 100

    # 经营现金流 / 净利润（利润质量）
    ocf_col = col("经营现金流")
    if ocf_col:
        ocf_s = pd.to_numeric(table[ocf_col], errors="coerce").dropna()
        if len(ocf_s) > 0:
            subs["ocf_quality"] = _clip(ocf_s.mean() * 100)         # 比例 ≥1 → 100

    # 资产负债率（默认权重 0，仅记录；行业差异大）
    debt_col = col("资产负债率")
    if debt_col:
        debt_s = pd.to_numeric(table[debt_col], errors="coerce").dropna()
        if len(debt_s) > 0:
            subs["debt"] = _clip(100 - max(0, debt_s.max() - 50) * 2)  # 50%→100, 100%→0
    return subs


def _valuation_subscores(dcf: dict, advice: dict) -> dict:
    """从 DCF 结果与当前价计算估值安全边际子分。"""
    subs = {"margin_neutral": None, "margin_conservative": None}
    if dcf is None or dcf.get("base_fcf") is None:
        return subs  # 无法估值 → 类内全 None → 估值类记 0
    price = advice.get("latest_price") if advice else None
    if not price or price <= 0:
        return subs
    neutral = dcf.get("neutral")
    conservative = dcf.get("conservative")
    if neutral:
        m = (neutral - price) / price * 100
        subs["margin_neutral"] = _clip(m + 50)         # +50% 上行→100, -50%→0
    if conservative:
        m = (conservative - price) / price * 100
        subs["margin_conservative"] = _clip(m + 50)
    return subs


def _sentiment_subscores(sentiment: dict) -> dict:
    """从市场 ERP 分位与个股 PE/PB 分位计算情绪子分。
    高 ERP 分位 = 便宜 = 高分；个股低 PE/PB 分位 = 便宜 = 高分。"""
    subs = {"market_erp": None, "individual_pe": None, "individual_pb": None}
    if sentiment:
        pct = sentiment.get("percentile")
        if pct is not None:
            subs["market_erp"] = _clip(pct)            # 直接用：高分位=便宜=高分
        pe_pct = sentiment.get("pe_percentile")
        if pe_pct is not None:
            subs["individual_pe"] = _clip(100 - pe_pct)  # 低分位=便宜=高分
        pb_pct = sentiment.get("pb_percentile")
        if pb_pct is not None:
            subs["individual_pb"] = _clip(100 - pb_pct)
    return subs


def compute_score(screening: dict, dcf: dict, sentiment: dict,
                  advice: dict | None = None, ctx=None) -> dict:
    """
    计算综合评分。

    参数:
      screening: fundamental_screening 的返回（含 table/screened）
      dcf:       dcf_valuation 的返回（含 base_fcf/neutral/conservative）
      sentiment: market_sentiment 的返回（含 percentile/pe_percentile/pb_percentile）
      advice:    investment_advice 的返回（含 latest_price）
      ctx:       可选 StockContext（当前未使用，保留以备扩展）

    返回:
      {score, grade, quality, valuation, sentiment, subscores, screened}
    """
    advice = advice or {}

    q_sub = _quality_subscores(screening)
    v_sub = _valuation_subscores(dcf, advice)
    s_sub = _sentiment_subscores(sentiment)

    quality = _category_score(q_sub, SCORE_QUALITY_W)
    sentiment_cat = _category_score(s_sub, SCORE_SENTIMENT_W)

    # 估值类：base_fcf 缺失 → 0（无法估值→谨慎信号）；否则类内加权
    if dcf is None or dcf.get("base_fcf") is None:
        valuation = 0.0
    else:
        valuation = _category_score(v_sub, SCORE_VALUATION_W)
        if valuation is None:
            valuation = 0.0

    # 顶层加权（缺失的类跳过并重归一；估值恒为数值故始终计入）
    cats = {"quality": quality, "valuation": valuation, "sentiment": sentiment_cat}
    total_w = 0.0
    acc = 0.0
    for cat, sc in cats.items():
        if sc is None:
            continue
        w = SCORE_WEIGHTS.get(cat, 0)
        acc += sc * w
        total_w += w
    score = acc / total_w if total_w > 0 else 0.0
    score = _clip(score)

    return {
        "score": round(score, 1),
        "grade": _grade(score),
        "quality": None if quality is None else round(quality, 1),
        "valuation": round(valuation, 1),
        "sentiment": None if sentiment_cat is None else round(sentiment_cat, 1),
        "subscores": {
            "quality": q_sub,
            "valuation": v_sub,
            "sentiment": s_sub,
        },
        "screened": (screening or {}).get("screened", False),
    }


def score_summary(score: dict) -> str:
    """单行评分摘要，供批量排名表使用。"""
    if not score:
        return "N/A"
    return (f"{score['score']:.1f} ({score['grade']}) "
            f"质{score['quality'] if score['quality'] is not None else '-'}/"
            f"估{score['valuation']}/"
            f"情{score['sentiment'] if score['sentiment'] is not None else '-'}")
