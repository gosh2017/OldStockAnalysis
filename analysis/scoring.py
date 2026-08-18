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
  - DCF 无法估值（base_fcf 缺失）时估值类直接记 0，作为"无法估值→谨慎"信号；
  - item 11：另算 completeness/completeness_tag（完整性置信度），独立于评分，
    汇总覆盖/DCF 数据/股息来源/erp_source 四信号，供展示层标注结果可信度。

纯函数：仅依赖传入的结果 dict，不访问网络/全局，便于单元测试。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import (
    SCORE_WEIGHTS, SCORE_QUALITY_W, SCORE_QUALITY_W_BY_BUCKET,
    SCORE_VALUATION_W, SCORE_SENTIMENT_W, GRADE_BANDS,
    INDUSTRY_PROFILES,
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


def _quality_subscores(screening: dict, bucket: str = "其他") -> dict:
    """从 screening['table'] 提取质量子分（按行业桶差异化口径）。

    item 5：ROE 水平按桶基准归一（INDUSTRY_PROFILES[bucket]['roe_benchmark']），
            稳定性改用变异系数 CV（std/mean），跨行业可比且无量纲。
    item 6：金融桶（is_financial）跳过经营现金流口径（金融业 OCF/净利润不可比）。
    """
    table = screening.get("table")
    subs = {"roe": None, "roe_stability": None, "dividend": None,
            "ocf_quality": None, "debt": None}
    if table is None or not isinstance(table, pd.DataFrame) or table.empty:
        return subs

    profile = INDUSTRY_PROFILES.get(bucket, INDUSTRY_PROFILES["其他"])
    is_financial = profile.get("is_financial", False)
    roe_benchmark = profile.get("roe_benchmark", 15.0)

    def col(cand):
        for c in table.columns:
            if cand in str(c):
                return c
        return None

    # ROE 水平（按桶基准归一）与稳定性（变异系数 CV 口径，无量纲、跨行业可比）
    roe_col = col("ROE")
    if roe_col:
        roe_s = pd.to_numeric(table[roe_col], errors="coerce").dropna()
        if len(roe_s) > 0:
            subs["roe"] = _clip(roe_s.mean() / roe_benchmark * 100)   # 基准→100
            if len(roe_s) > 1:
                mean = roe_s.mean()
                if mean > 1e-9:
                    subs["roe_stability"] = _clip(100 - (roe_s.std() / mean) * 100)
                else:
                    subs["roe_stability"] = _clip(100 - roe_s.std() * 5)  # 均值≈0 回退

    # 股息率水平 + 分红连续性
    div_col = col("股息率")
    if div_col:
        div_s = pd.to_numeric(table[div_col], errors="coerce").dropna()
        if len(div_s) > 0:
            consistency = (div_s > 0).mean()                       # 有分红的年占比
            subs["dividend"] = _clip(div_s.mean() / 4 * 100) * consistency  # 4% → 100

    # 经营现金流 / 净利润（利润质量）—— 金融桶口径不可比，跳过
    if not is_financial:
        ocf_col = col("经营现金流")
        if ocf_col:
            ocf_s = pd.to_numeric(table[ocf_col], errors="coerce").dropna()
            if len(ocf_s) > 0:
                subs["ocf_quality"] = _clip(ocf_s.mean() * 100)         # 比例 ≥1 → 100

    # 资产负债率（行业差异大；是否计入由 SCORE_QUALITY_W_BY_BUCKET 的 debt 权重控制）
    debt_col = col("资产负债率")
    if debt_col:
        debt_s = pd.to_numeric(table[debt_col], errors="coerce").dropna()
        if len(debt_s) > 0:
            subs["debt"] = _clip(100 - max(0, debt_s.max() - 50) * 2)  # 50%→100, 100%→0
    return subs


def _roe_mean_from_screening(screening: dict) -> float | None:
    """从 screening 结果表提取 ROE 均值（%），供 PB-ROE 锚使用。"""
    table = (screening or {}).get("table")
    if table is None or not isinstance(table, pd.DataFrame) or table.empty:
        return None
    for c in table.columns:
        if "ROE" in str(c):
            s = pd.to_numeric(table[c], errors="coerce").dropna()
            if len(s) > 0:
                return float(s.mean())
    return None


def _valuation_subscores(dcf: dict, advice: dict,
                         sentiment: dict | None = None,
                         screening: dict | None = None) -> dict:
    """从 DCF 结果与当前价计算估值安全边际子分 + PB-ROE 独立锚（item 8）。

    PB-ROE 锚：公允 PB = ROE均值/15（15% ROE → PB=1 线性锚），
    实际 PB 低于公允 → 高分。该锚独立于 DCF（不依赖 base_fcf），
    current_pb 或 ROE 缺失 → pb_roe=None（类内重归一回退纯 margin 权重）。
    """
    subs = {"margin_neutral": None, "margin_conservative": None, "pb_roe": None}

    # -- 安全边际子分（依赖 DCF 可估值 + 当前价）--
    if dcf is not None and dcf.get("base_fcf") is not None:
        price = advice.get("latest_price") if advice else None
        if price and price > 0:
            neutral = dcf.get("neutral")
            conservative = dcf.get("conservative")
            if neutral:
                m = (neutral - price) / price * 100
                subs["margin_neutral"] = _clip(m + 50)         # +50% 上行→100, -50%→0
            if conservative:
                m = (conservative - price) / price * 100
                subs["margin_conservative"] = _clip(m + 50)

    # -- PB-ROE 独立锚（不依赖 DCF）--
    current_pb = (sentiment or {}).get("current_pb")
    roe_mean = _roe_mean_from_screening(screening)
    if current_pb and current_pb > 0 and roe_mean and roe_mean > 0:
        implied_pb = roe_mean / 15.0                       # 15% ROE → PB=1 线性锚
        subs["pb_roe"] = _clip((implied_pb - current_pb) / current_pb * 50 + 50)
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


# item 11：完整性置信度加权（覆盖 30% + DCF 数据 30% + 股息来源 real 占比 20% + erp_source 20%）
_COMPLETENESS_W = {"coverage": 0.30, "dcf_data": 0.30, "div_real": 0.20, "erp_source": 0.20}
# erp_source → 可信度分（real 100 / real_partial 60 / synthetic 20 / 缺失 0）
_ERP_SOURCE_SCORE = {"real": 100.0, "real_partial": 60.0, "synthetic": 20.0}


def _completeness(screening: dict, dcf: dict, sentiment: dict) -> tuple[float, str]:
    """汇总各信号可信度，算完整性置信度（item 11）。

    加权（_COMPLETENESS_W）：
      - coverage   ：screening 表中 ROE 非空年数 / 总年数（数据覆盖度）
      - dcf_data   ：base_fcf + 中性估值均可得 → 100；仅 base_fcf → 50；全无 → 0
      - div_real   ：分红来源列中 "real" 占比（非空里）
      - erp_source ：real 100 / real_partial 60 / synthetic 20 / 缺失 0
    返回 (completeness 0-100, completeness_tag ∈ {"高","中","低"}，≥80/≥50/<50)。
    """
    table = (screening or {}).get("table")
    has_table = table is not None and isinstance(table, pd.DataFrame) and not table.empty

    # 覆盖：ROE 非空年数 / 总年数
    coverage = 0.0
    if has_table:
        n_years = len(table)
        roe_col = next((c for c in table.columns if "ROE" in str(c)), None)
        if roe_col is not None and n_years > 0:
            non_null = pd.to_numeric(table[roe_col], errors="coerce").notna().sum()
            coverage = non_null / n_years * 100

    # DCF 数据可得性
    dcf = dcf or {}
    dcf_data = 0.0
    if dcf.get("base_fcf") is not None:
        dcf_data = 50.0
        if dcf.get("neutral") is not None:
            dcf_data = 100.0

    # 股息来源 real 占比
    div_real = 0.0
    if has_table:
        src_col = next((c for c in table.columns if "分红来源" in str(c)), None)
        if src_col is not None:
            src_s = table[src_col].dropna()
            if len(src_s) > 0:
                div_real = (src_s == "real").sum() / len(src_s) * 100

    # erp_source 可信度
    erp_score = _ERP_SOURCE_SCORE.get((sentiment or {}).get("erp_source"), 0.0)

    completeness = (coverage * _COMPLETENESS_W["coverage"]
                    + dcf_data * _COMPLETENESS_W["dcf_data"]
                    + div_real * _COMPLETENESS_W["div_real"]
                    + erp_score * _COMPLETENESS_W["erp_source"])
    completeness = _clip(completeness)
    if completeness >= 80:
        tag = "高"
    elif completeness >= 50:
        tag = "中"
    else:
        tag = "低"
    return completeness, tag


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
      {score, grade, quality, valuation, sentiment, subscores, screened,
       completeness, completeness_tag}
    """
    advice = advice or {}

    # 行业桶贯穿质量/估值子分（item 5/6/8）；DCF 未带 bucket → 默认"其他"等价回退
    bucket = (dcf or {}).get("bucket", "其他")
    q_sub = _quality_subscores(screening, bucket)
    v_sub = _valuation_subscores(dcf, advice, sentiment, screening)
    s_sub = _sentiment_subscores(sentiment)

    # 质量权重：桶覆盖合并（金融桶 ocf/debt 置 0；非金融桶 debt 提至 0.20）
    quality_weights = {**SCORE_QUALITY_W,
                       **SCORE_QUALITY_W_BY_BUCKET.get(bucket, {})}
    quality = _category_score(q_sub, quality_weights)
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

    # item 11：完整性置信度（汇总各信号可信度，独立于评分，供展示层标注）
    completeness, completeness_tag = _completeness(screening, dcf, sentiment)

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
        "completeness": round(completeness, 1),
        "completeness_tag": completeness_tag,
    }


def score_summary(score: dict) -> str:
    """单行评分摘要，供批量排名表使用。"""
    if not score:
        return "N/A"
    base = (f"{score['score']:.1f} ({score['grade']}) "
            f"质{score['quality'] if score['quality'] is not None else '-'}/"
            f"估{score['valuation']}/"
            f"情{score['sentiment'] if score['sentiment'] is not None else '-'}")
    tag = score.get("completeness_tag")
    if tag:
        base += f" [完整度{tag}]"
    return base
