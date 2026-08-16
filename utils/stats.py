# -*- coding: utf-8 -*-
"""
统计工具 — 分位数计算。

优先使用 scipy.stats.percentileofscore（更严谨），scipy 不可用时回退到
等价的 numpy 实现，保证离线 / 未装 scipy 的环境（如 demo）也能正确计算。
两种实现语义一致，便于在 tests/test_percentile.py 中交叉验证。
"""
from __future__ import annotations

try:
    from scipy.stats import percentileofscore as _scipy_pos

    def percentile_of_score(scores, x, kind: str = "rank") -> float:
        """返回 x 在 scores 中的百分位数（0–100）。

        kind 语义同 scipy.stats.percentileofscore：
          - 'rank'   (默认) 连续值下等价于 (weak + strict) / 2
          - 'weak'   scores 中 ≤ x 的占比
          - 'strict' scores 中 < x 的占比
        """
        return float(_scipy_pos(scores, x, kind=kind))

except ImportError:  # scipy 未安装：numpy 等价实现
    import numpy as np

    def percentile_of_score(scores, x, kind: str = "rank") -> float:
        arr = np.sort(np.asarray(scores, dtype=float))
        n = len(arr)
        if n == 0:
            return 0.0
        x = float(x)
        le = int(np.count_nonzero(arr <= x))  # ≤ x
        lt = int(np.count_nonzero(arr < x))   # < x
        if kind == "weak":
            return le / n * 100.0
        if kind == "strict":
            return lt / n * 100.0
        # 'rank' / 'mean'：取 weak 与 strict 的平均（scipy rank 连续值下行为）
        return (le + lt) / (2 * n) * 100.0
