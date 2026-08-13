# -*- coding: utf-8 -*-
"""
第四步：综合投资建议

结合估值和情绪给出操作建议：
  当前股价 < 保守估值  → 大幅买入
  保守 ≤ 当前股价 < 中性  → 分批建仓
  中性 ≤ 当前股价 < 乐观  → 持有观望
  当前股价 > 乐观估值  → 持有或减仓
"""
import pandas as pd

from utils import sep


def investment_advice(
    daily_df: pd.DataFrame,
    dcf_result: dict,
    sentiment_result: dict,
    screening_result: dict,
) -> dict:
    """综合估值、情绪、基本面筛选，输出最终操作建议。"""
    sep("第四步：综合投资建议")

    valuations = dcf_result.get("valuations")
    if valuations is None or daily_df.empty:
        print("  [X] 估值或交易数据不可用，无法给出建议。")
        return {"recommendation": "数据不足", "latest_price": None,
                "conservative": None, "neutral": None, "optimistic": None,
                "sentiment": None, "screened": False}

    latest_price = float(daily_df["收盘"].iloc[-1])
    latest_date  = daily_df["日期"].iloc[-1]

    conservative = valuations["保守 (Conservative)"]["intrinsic_value"]
    neutral      = valuations["中性 (Neutral)"]["intrinsic_value"]
    optimistic   = valuations["乐观 (Optimistic)"]["intrinsic_value"]

    print(f"\n  [PIN] 当前股价: {latest_price:.2f} 元（{latest_date.strftime('%Y-%m-%d')}）")
    print(f"  [RED] 保守估值: {conservative:.2f} 元")
    print(f"  [YLW] 中性估值: {neutral:.2f} 元")
    print(f"  [GRN] 乐观估值: {optimistic:.2f} 元")

    margin_c = (conservative - latest_price) / latest_price * 100
    margin_n = (neutral - latest_price) / latest_price * 100
    print(f"\n  [DATA] 安全边际分析:")
    print(f"     vs 保守估值: {margin_c:+.1f}%")
    print(f"     vs 中性估值: {margin_n:+.1f}%")

    # -- 价格区间判断 --
    action, emoji, explanation = _judge_price(latest_price, conservative, neutral, optimistic, margin_c)

    # -- 结合市场情绪 --
    sentiment  = sentiment_result.get("sentiment", "未知")
    percentile = sentiment_result.get("percentile", 50)
    print(f"\n  [DATA] 市场情绪: {sentiment}（{percentile:.0f}% 分位数）")

    final_action, final_emoji = _adjust_for_sentiment(action, sentiment)

    # -- 基本面 --
    screened = screening_result.get("screened", False)
    print(f"  [DATA] 基本面筛选: {'通过' if screened else '未通过'}")

    print(f"\n  -- 综合建议 --")
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


def _judge_price(price: float, c: float, n: float, o: float, margin_c: float) -> tuple:
    """根据股价与三情景估值的相对位置，返回 (操作, emoji, 解释)。"""
    if price < c:
        return "大幅买入", "[GRN]", (
            f"当前股价 {price:.2f} 元显著低于保守估值 {c:.2f} 元，"
            f"安全边际充足（{margin_c:.1f}%）。建议大幅买入。"
        )
    elif price < n:
        return "分批建仓", "[YLW]", (
            f"当前股价 {price:.2f} 元介于保守估值 {c:.2f} 元"
            f"与中性估值 {n:.2f} 元之间，估值有一定安全边际。"
            f"建议分批建仓，控制仓位。"
        )
    elif price < o:
        return "持有观望", "[ORG]", (
            f"当前股价 {price:.2f} 元介于中性估值 {n:.2f} 元"
            f"与乐观估值 {o:.2f} 元之间，价格较为合理。"
            f"建议持有观望，等待更好入场机会。"
        )
    else:
        return "持有或减仓", "[RED]", (
            f"当前股价 {price:.2f} 元已高于乐观估值 {o:.2f} 元，"
            f"估值偏贵。建议持有或适当减仓，锁定利润。"
        )


def _adjust_for_sentiment(action: str, sentiment: str) -> tuple:
    """根据市场情绪微调最终操作建议。"""
    if sentiment in ("极度低估", "低估") and action in ("大幅买入", "分批建仓"):
        return action, "[GRN]"
    if sentiment in ("极度低估", "低估") and action == "持有观望":
        return "逢低布局", "[YLW]"
    if sentiment in ("高估", "极度高估") and action in ("大幅买入", "分批建仓"):
        return "谨慎建仓", "[ORG]"
    if sentiment in ("高估", "极度高估") and action == "持有或减仓":
        return "建议减仓", "[RED]"
    return action, ""
