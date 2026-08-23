# -*- coding: utf-8 -*-
"""
综合建议（step4）测试：_judge_price 分档颜色 + _adjust_for_sentiment 兜底保留。

回归点：此前 _adjust_for_sentiment 未匹配的情绪（如「合理」「未知」）落到默认
分支返回空串 emoji，把 _judge_price 已分配的颜色丢掉。修复后默认分支沿用价格
档位 emoji。
"""
from analysis.step4_advice import _judge_price, _adjust_for_sentiment


def _judge(margin_c=0.0, **kw):
    """_judge_price 便捷包装：补默认 margin_c。"""
    return _judge_price(kw.get("price", 1.0), kw.get("liq", 0.5),
                        kw.get("c", 2.0), kw.get("ceiling", 3.0), margin_c)


def test_judge_price_returns_color_per_tier():
    assert _judge(price=1.0, liq=2.0, c=3.0, ceiling=4.0)[1] == "[GRY]"      # price<liq 极度低估
    assert _judge(price=2.5, liq=1.0, c=4.0, ceiling=5.0)[1] == "[GRN]"     # price<c 大幅买入
    assert _judge(price=4.5, liq=1.0, c=2.0, ceiling=5.0)[1] == "[YLW]"     # price<ceiling 分批建仓
    assert _judge(price=6.0, liq=1.0, c=2.0, ceiling=5.0)[1] == "[RED]"      # price>=ceiling 持有或减仓


def test_neutral_sentiment_keeps_price_tier_emoji():
    """「合理」情绪未匹配任何分支 → 默认分支应沿用价格档位 emoji，而非空串。"""
    for action, emoji in [("极度低估", "[GRY]"), ("大幅买入", "[GRN]"),
                          ("分批建仓", "[YLW]"), ("持有或减仓", "[RED]")]:
        fa, fe = _adjust_for_sentiment(action, "合理", emoji)
        assert (fa, fe) == (action, emoji), f"合理+{action} 丢了颜色 {emoji}"


def test_unknown_sentiment_keeps_price_tier_emoji():
    """缺数据默认情绪「未知」同样不应丢色。"""
    fa, fe = _adjust_for_sentiment("持有或减仓", "未知", "[RED]")
    assert (fa, fe) == ("持有或减仓", "[RED]")


def test_matched_sentiment_still_overrides():
    """匹配分支仍按情绪微调动作与颜色（修复不应破坏原有覆盖）。"""
    assert _adjust_for_sentiment("分批建仓", "低估", "[YLW]") == ("逢低布局", "[GRN]")
    assert _adjust_for_sentiment("分批建仓", "高估", "[YLW]") == ("谨慎建仓", "[ORG]")
    assert _adjust_for_sentiment("持有或减仓", "极度高估", "[RED]") == ("建议减仓", "[RED]")
    # 低估 + 大幅买入：动作不变、情绪确认绿色
    assert _adjust_for_sentiment("大幅买入", "低估", "[GRN]") == ("大幅买入", "[GRN]")
