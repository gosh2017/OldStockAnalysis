# -*- coding: utf-8 -*-
"""
市场情绪（step3）测试：真实历史 ERP 分位的边界、单调性、与各来源回退。

覆盖 _historical_erp_series + market_sentiment 的关键路径：
  - 提供市场 PE 历史 + 国债历史 → 用真实序列算分位（来源 history）
  - 当前 PE 越高 → ERP 越低 → 分位越低（市场情绪方向的核心不变量）
  - 无 PE 历史但有 market_df 快照 → 用中位数（来源 snapshot）
  - 全无历史 → 回退合成分布（来源 default）

P4 新增（items 10/12）：erp_source 可信度标注（real/real_partial/synthetic）、
10 年历史窗口（SENTIMENT_HISTORY_DAYS=365*10，demo 历史起始 2016）。
"""
import pandas as pd

import config
from data.demo_data import generate_market_pe_history
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
    assert "current_pb" in s
    assert "erp_source" in s
    assert s["erp_source"].startswith("real")      # PE+国债均真实 → real


def test_sentiment_monotonic_in_pe():
    """当前 PE 越高 → ERP 越低 → 历史分位越低（高分位=便宜=低估）。"""
    high_pe = market_sentiment(None, 0.023, None, _pe_history(25.0), _bond_history())
    low_pe = market_sentiment(None, 0.023, None, _pe_history(13.0), _bond_history())
    assert low_pe["percentile"] > high_pe["percentile"]
    assert "current_pb" in low_pe and "current_pb" in high_pe


def test_sentiment_pe_history_without_bond_history():
    """仅有 PE 历史、无国债历史 → 用标量 bond 充满，仍得合法分位。"""
    s = market_sentiment(None, 0.023, None, _pe_history(20.0), None)
    assert 0 <= s["percentile"] <= 100
    assert s["market_pe_source"] == "history"
    assert "current_pb" in s
    assert "erp_source" in s
    assert s["erp_source"] == "real_partial"      # 仅 PE 真实、国债标量兜底


def test_sentiment_snapshot_fallback():
    """无 PE 历史、有 market_df 快照 → 用快照中位数作当前 PE。"""
    market_df = pd.DataFrame({"代码": ["000001"] * 5, "市盈率-动态": [13, 15, 17, 19, 21]})
    s = market_sentiment(market_df, 0.023, None)
    assert s["market_pe_source"] == "snapshot"
    assert s["pe_median"] == 17.0          # median
    assert "current_pb" in s
    assert "erp_source" in s


def test_sentiment_fallback_mock():
    """全无历史 → 回退合成分布 generate_historical_erp（来源 default）。"""
    s = market_sentiment(None, 0.023, None)
    assert 0 <= s["percentile"] <= 100
    assert s["market_pe_source"] == "default"
    assert s["pe_median"] == 20.0          # 默认
    assert "current_pb" in s
    assert "erp_source" in s
    assert s["erp_source"] == "synthetic"        # 无真实 PE 历史 → 合成兜底


def test_current_pb_exposed():
    """有 stock_indicator 时 current_pb/current_pe 暴露当前值；erp_source 标注分位来源。"""
    si = pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=5, freq="ME"),
        "市盈率PE": [10, 12, 14, 16, 18],
        "市净率PB": [0.5, 0.6, 0.7, 0.8, 0.9],
    })
    s = market_sentiment(None, 0.023, si, _pe_history(20.0), _bond_history())
    assert s["current_pe"] == 18.0          # 末值
    assert s["current_pb"] == 0.9           # 末值
    assert s["erp_source"] in ("real", "real_partial", "synthetic")


# -- P4 新增：10 年历史窗口 + erp_source 可信度标注（items 10, 12）-------------

def test_history_window_10y():
    """item 12：情绪历史窗口扩至 10 年；demo 市场历史起始 2016。"""
    assert config.SENTIMENT_HISTORY_DAYS == 365 * 10
    pe_hist = generate_market_pe_history()
    first = pd.to_datetime(pe_hist["日期"]).iloc[0]
    assert first.year == 2016


def test_erp_source_real():
    """item 10：市场 PE 历史 + 国债历史均真实 → erp_source='real'。"""
    s = market_sentiment(None, 0.023, None, _pe_history(20.0), _bond_history())
    assert s["erp_source"] == "real"


def test_erp_source_real_partial():
    """item 10：仅 PE 真实、无国债历史（标量兜底）→ erp_source='real_partial'。"""
    s = market_sentiment(None, 0.023, None, _pe_history(20.0), None)
    assert s["erp_source"] == "real_partial"


def test_erp_source_synthetic():
    """item 10：无市场 PE 历史 → 回退合成分布 → erp_source='synthetic'。"""
    s = market_sentiment(None, 0.023, None)
    assert s["erp_source"] == "synthetic"


# -- item C1：个股 PE/PB 滚动窗口分位 ------------------------------------

def test_individual_pe_rolling_window_drift_aware():
    """item C1：估值中枢长期漂移时，滚动 5 年窗口分位 ≠ 全历史分位且更合理。
    构造 11 年 PE 序列：前 5 年(2015-2019)中枢 ~20、后 6 年(2020-2025)中枢 ~8
    （如银行 PE 从 20→5）。当前末值 8.5 在全历史里偏低（陈旧高中枢拉高整体 →
    8.5 被判"便宜"），但在近 5 年窗口里偏高（新中枢 ~8）→ 滚动分位 > 全历史分位，
    反映当前相对近期中枢而非陈旧中枢。5 年窗口恰好覆盖后 6 期（cutoff=2020-12-31），
    旧中枢 5 期被排除。
    """
    import config
    from analysis.step3_sentiment import _recent_window
    from utils import percentile_of_score

    pe_old = [18.0, 19.0, 20.0, 21.0, 22.0]      # 2015-2019：中枢 ~20（陈旧）
    pe_new = [6.0, 7.0, 8.0, 8.0, 9.0, 8.5]     # 2020-2025：中枢 ~8（含末值 8.5）
    pe_vals = pe_old + pe_new
    dates = pd.date_range("2015-01-31", periods=len(pe_vals), freq="YE")
    si = pd.DataFrame({"日期": dates, "市盈率PE": pe_vals, "市净率PB": [p / 20 for p in pe_vals]})

    # 末值 = 8.5
    pe_s = pd.to_numeric(si["市盈率PE"], errors="coerce")
    pe_s = pe_s[(pe_s > 0) & (pe_s < 1000)].dropna()
    current_pe = float(pe_s.iloc[-1])
    assert abs(current_pe - 8.5) < 1e-9

    # 滚动窗口（cutoff=2020-12-31 → 恰好覆盖后 6 期新中枢，旧 5 期被排除）
    pe_win, used_full = _recent_window(pe_s, si["日期"], config.INDIVIDUAL_PERCENTILE_WINDOW_YEARS)
    assert used_full is False
    assert len(pe_win) == 6                       # 后 6 期新中枢
    assert all(v < 10.0 for v in pe_win.tolist())  # 旧中枢（~20）已全部排除
    pct_window = percentile_of_score(pe_win.tolist(), current_pe)
    pct_full = percentile_of_score(pe_s.tolist(), current_pe)
    # 核心：滚动分位 > 全历史分位（当前值在近 5 年偏高，在全历史偏低）
    assert pct_window > pct_full
    # 合理性：滚动窗口分位偏高（>50，8.5 在新中枢里属偏高），全历史分位偏低（<50）
    assert pct_window > 50.0
    assert pct_full < 50.0


def test_individual_pe_window_insufficient_falls_back_to_full():
    """item C1：窗口内不足 2 期 → 回退全历史，used_full=True。
    两个数据点跨度 14 年（2010 vs 2024），5 年窗口 cutoff=2019-12-31 仅含 2024 一期
    （<2）→ 回退全历史 2 期。
    """
    import config
    from analysis.step3_sentiment import _recent_window

    pe_vals = [20.0, 8.0]
    dates = pd.to_datetime(["2010-12-31", "2024-12-31"])
    si = pd.DataFrame({"日期": dates, "市盈率PE": pe_vals})
    pe_s = pd.to_numeric(si["市盈率PE"], errors="coerce").dropna()

    pe_win, used_full = _recent_window(pe_s, si["日期"], config.INDIVIDUAL_PERCENTILE_WINDOW_YEARS)
    assert used_full is True
    assert len(pe_win) == 2                        # 回退全历史


def test_individual_pe_index_alignment_after_dropna():
    """item C1：series 经过前置 dropna/过滤后与 date_series 长度/索引不一致，
    _recent_window 须按共同行对齐，保证日期与数值一一对应（回归守护）。
    构造含 NaN 的 PE，使其 dropna 后短于日期列，验证不串位。"""
    import config
    from analysis.step3_sentiment import _recent_window
    from utils import percentile_of_score

    # 中间一行为 NaN；dropna 后 pe_s 长度 = 9，dates 长度 = 10（索引不对齐）
    pe_vals = [10, 11, 12, None, 8, 9, 8.5, 9, 8, 8.5]
    dates = pd.date_range("2016-01-31", periods=10, freq="YE")
    si = pd.DataFrame({"日期": dates, "市盈率PE": pe_vals})
    pe_s = pd.to_numeric(si["市盈率PE"], errors="coerce")
    pe_s = pe_s[(pe_s > 0) & (pe_s < 1000)].dropna()
    assert len(pe_s) == 9                       # 掉了 NaN 那行

    pe_win, used_full = _recent_window(pe_s, si["日期"], config.INDIVIDUAL_PERCENTILE_WINDOW_YEARS)
    # 近 5 年窗口内有值且未回退全历史（末值 8.5 在近 5 年偏高）
    assert used_full is False
    current_pe = float(pe_s.iloc[-1])
    pct_window = percentile_of_score(pe_win.tolist(), current_pe)
    pct_full = percentile_of_score(pe_s.tolist(), current_pe)
    assert pct_window > pct_full
