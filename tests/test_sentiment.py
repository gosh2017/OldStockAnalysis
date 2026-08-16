# -*- coding: utf-8 -*-
"""
市场情绪（step3）测试：真实历史 ERP 分位的边界、单调性、与各来源回退。

覆盖 _historical_erp_series + market_sentiment 的关键路径：
  - 提供市场 PE 历史 + 国债历史 → 用真实序列算分位（来源 history）
  - 当前 PE 越高 → ERP 越低 → 分位越低（市场情绪方向的核心不变量）
  - 无 PE 历史但有 market_df 快照 → 用中位数（来源 snapshot）
  - 全无历史 → 回退合成分布（来源 default）
"""
import pandas as pd

from analysis import market_sentiment


def _pe_history(last_pe: float, n: int = 8) -> pd.DataFrame:
    """合成市场 PE 历史，末值为 last_pe，其余铺成 13→25 的等差。"""
    base = [13.0, 15.0, 17.0, 19.0, 21.0, 23.0, 25.0]
    pe = (base + [last_pe])[:n] if n <= len(base) + 1 else base + [last_pe] * (n - len(base))
    dates = pd.date_range("2024-01-01", periods=len(pe), freq="ME")
    return pd.DataFrame({"日期": dates, "市盈率": pe})


def _bond_history(n: int = 8) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="ME")
    return pd.DataFrame({"日期": dates, "国债收益率": [0.023] * n})


def test_sentiment_in_range():
    s = market_sentiment(None, 0.023, None, _pe_history(20.0), _bond_history())
    assert 0 <= s["percentile"] <= 100
    assert s["sentiment"] in ("极度高估", "高估", "合理", "低估", "极度低估")
    assert s["market_pe_source"] == "history"
    assert s["pe_median"] == 20.0          # 末值


def test_sentiment_monotonic_in_pe():
    """当前 PE 越高 → ERP 越低 → 历史分位越低（高分位=便宜=低估）。"""
    high_pe = market_sentiment(None, 0.023, None, _pe_history(25.0), _bond_history())
    low_pe = market_sentiment(None, 0.023, None, _pe_history(13.0), _bond_history())
    assert low_pe["percentile"] > high_pe["percentile"]


def test_sentiment_pe_history_without_bond_history():
    """仅有 PE 历史、无国债历史 → 用标量 bond 充满，仍得合法分位。"""
    s = market_sentiment(None, 0.023, None, _pe_history(20.0), None)
    assert 0 <= s["percentile"] <= 100
    assert s["market_pe_source"] == "history"


def test_sentiment_snapshot_fallback():
    """无 PE 历史、有 market_df 快照 → 用快照中位数作当前 PE。"""
    market_df = pd.DataFrame({"代码": ["000001"] * 5, "市盈率-动态": [13, 15, 17, 19, 21]})
    s = market_sentiment(market_df, 0.023, None)
    assert s["market_pe_source"] == "snapshot"
    assert s["pe_median"] == 17.0          # median


def test_sentiment_fallback_mock():
    """全无历史 → 回退合成分布 generate_historical_erp（来源 default）。"""
    s = market_sentiment(None, 0.023, None)
    assert 0 <= s["percentile"] <= 100
    assert s["market_pe_source"] == "default"
    assert s["pe_median"] == 20.0          # 默认
