# -*- coding: utf-8 -*-
"""
第四步：综合投资建议

结合估值和情绪给出操作建议（成熟期「老登股」四档口径）：
  当前股价 < 破产清算估值  → 极度低估（接近清算底值）
  破产清算 ≤ 当前股价 < 保守  → 大幅买入
  保守 ≤ 当前股价 < 合理估值上限  → 分批建仓
  当前股价 ≥ 合理估值上限  → 持有或减仓

「合理估值上限」= min(中性 DCF, 过去5年PE中位数 × 当前EPS)，作为估值天花板。
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
                "liquidation": None, "conservative": None, "neutral": None,
                "fair_value_ceiling": None,
                "sentiment": None, "screened": False}

    latest_price = float(daily_df["收盘"].iloc[-1])
    latest_date  = daily_df["日期"].iloc[-1]

    # 价格缺失/为 0（停牌、退市）或 NaN 时，分位比较与安全边际均无意义，
    # 直接按「数据不足」返回，避免 margin_c 除零及 price<liq 比较误落档位。
    if not latest_price or pd.isna(latest_price):
        print("  [X] 当前股价缺失或为 0，无法给出建议。")
        return {"recommendation": "数据不足", "latest_price": None,
                "liquidation": None, "conservative": None, "neutral": None,
                "fair_value_ceiling": None,
                "sentiment": None, "screened": False}

    liquidation  = valuations["破产清算 (Liquidation)"]["intrinsic_value"]
    conservative = valuations["保守 (Conservative)"]["intrinsic_value"]
    ceiling      = dcf_result.get("fair_value_ceiling") or valuations["中性 (Neutral)"]["intrinsic_value"]

    print(f"\n  [PIN] 当前股价: {latest_price:.2f} 元（{latest_date.strftime('%Y-%m-%d')}）")
    print(f"  [GRY] 破产清算估值: {liquidation:.2f} 元")
    print(f"  [RED] 保守估值: {conservative:.2f} 元")
    print(f"  [GRN] 合理估值上限: {ceiling:.2f} 元")

    margin_c = (conservative - latest_price) / latest_price * 100
    print(f"\n  [DATA] 安全边际分析:")
    print(f"     vs 保守估值: {margin_c:+.1f}%")

    # -- 价格区间判断 --
    action, emoji, explanation = _judge_price(
        latest_price, liquidation, conservative, ceiling, margin_c)

    # -- 结合市场情绪 --
    sentiment  = sentiment_result.get("sentiment", "未知")
    percentile = sentiment_result.get("percentile", 50)
    print(f"\n  [DATA] 市场情绪: {sentiment}（{percentile:.0f}% 分位数）")

    final_action, final_emoji = _adjust_for_sentiment(action, sentiment, emoji)

    # -- 基本面 --
    screened = screening_result.get("screened", False)
    print(f"  [DATA] 基本面筛选: {'通过' if screened else '未通过'}")

    print(f"\n  -- 综合建议 --")
    print(f"\n  {explanation}")
    print(f"\n  {final_emoji} 最终操作建议: 【{final_action}】")

    return {
        "recommendation": final_action,
        "latest_price": latest_price,
        "liquidation": liquidation,
        "conservative": conservative,
        "neutral": ceiling,
        "fair_value_ceiling": ceiling,
        "sentiment": sentiment,
        "screened": screened,
    }


def _judge_price(price: float, liq: float, c: float, ceiling: float,
                 margin_c: float) -> tuple:
    """根据股价与清算/保守/上限的相对位置，返回 (操作, emoji, 解释)。"""
    if price < liq:
        return "极度低估", "[GRY]", (
            f"当前股价 {price:.2f} 元低于破产清算估值 {liq:.2f} 元，"
            f"接近清算底值，极度低估。建议重点关注。"
        )
    elif price < c:
        return "大幅买入", "[GRN]", (
            f"当前股价 {price:.2f} 元低于保守估值 {c:.2f} 元，"
            f"安全边际充足（{margin_c:.1f}%）。建议大幅买入。"
        )
    elif price < ceiling:
        return "分批建仓", "[YLW]", (
            f"当前股价 {price:.2f} 元介于保守估值 {c:.2f} 元"
            f"与合理估值上限 {ceiling:.2f} 元之间，估值合理偏低。"
            f"建议分批建仓，控制仓位。"
        )
    else:
        return "持有或减仓", "[RED]", (
            f"当前股价 {price:.2f} 元已达合理估值上限 {ceiling:.2f} 元，"
            f"估值偏贵。建议持有或适当减仓，锁定利润。"
        )


def _adjust_for_sentiment(action: str, sentiment: str, emoji: str) -> tuple:
    """根据市场情绪微调最终操作建议。

    未匹配的情绪（如「合理」「未知」或低估+持有或减仓等冲突组合）沿用价格档位
    的 emoji，避免情绪兜底分支把 _judge_price 已分配的颜色丢成空串。
    """
    if sentiment in ("极度低估", "低估") and action in ("大幅买入", "极度低估"):
        return action, "[GRN]"
    if sentiment in ("极度低估", "低估") and action == "分批建仓":
        return "逢低布局", "[GRN]"
    if sentiment in ("高估", "极度高估") and action in ("大幅买入", "极度低估", "分批建仓"):
        return "谨慎建仓", "[ORG]"
    if sentiment in ("高估", "极度高估") and action == "持有或减仓":
        return "建议减仓", "[RED]"
    return action, emoji
