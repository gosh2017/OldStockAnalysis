# -*- coding: utf-8 -*-
"""
统计工具 — 分位数计算。

优先使用 scipy.stats.percentileofscore（更严谨），scipy 不可用时回退到
等价的 numpy 实现，保证离线 / 未装 scipy 的环境（如 demo）也能正确计算。
两种实现语义一致，便于在 tests/test_percentile.py 中交叉验证。
"""
from __future__ import annotations

import numpy as np

try:
    from scipy.stats import percentileofscore as _scipy_pos

    def percentile_of_score(scores, x, kind: str = "rank") -> float:
        """返回 x 在 scores 中的百分位数（0–100）。

        kind 语义同 scipy.stats.percentileofscore：
          - 'rank'   (默认) 连续值下等价于 (weak + strict) / 2
          - 'weak'   scores 中 ≤ x 的占比
          - 'strict' scores 中 < x 的占比
        """
        # 空样本 → 0（scipy 返回 nan，约定兜底为 0，与 numpy 实现一致）
        try:
            if len(scores) == 0:
                return 0.0
        except TypeError:
            scores = list(scores)
            if not scores:
                return 0.0
        if kind == "weak":
            return float(_scipy_pos(scores, x, kind="weak"))
        if kind == "strict":
            return float(_scipy_pos(scores, x, kind="strict"))
        # 'rank' / 'mean'：(weak + strict) / 2。显式取平均而非 scipy kind='rank'——
        # scipy ≥1.x 对「值命中数据点」的 rank 口径与 weak 相同，与本模块约定的
        # (weak+strict)/2 不一致；显式平均保证与下方 numpy 兜底实现语义一致
        # （test_percentile.py 交叉验证两种实现等价）。
        weak = float(_scipy_pos(scores, x, kind="weak"))
        strict = float(_scipy_pos(scores, x, kind="strict"))
        return (weak + strict) / 2.0

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


# ---------------------------------------------------------------------
# 均值 bootstrap 置信区间（回测"等级信号有效性"判定用）
# ---------------------------------------------------------------------
# 纯 numpy 实现：bootstrap 的百分位置信区间 scipy 与 numpy 语义无实质差异，
# 且 numpy 为 pandas 硬依赖恒可用，故不取 scipy/numpy 双分支（与 percentile_of_score
# 的双分支目的不同——后者因 scipy 版本间 rank 口径不一需显式控制）。固定 seed
# 保证 demo / 回测可复现（与 demo_data 的确定性范式一致）。


def _bootstrap_arr(samples) -> "np.ndarray | None":
    """规整样本为 1d float 数组，剔除非有限值；空/不可数值返回 None。"""
    try:
        arr = np.asarray(list(samples), dtype=float)
    except (TypeError, ValueError):
        return None
    arr = arr[np.isfinite(arr)]
    return arr if arr.size > 0 else None


def bootstrap_ci(samples, *, n_iter: int = 2000, ci: float = 0.95,
                 seed: int = 42) -> tuple:
    """均值 bootstrap 百分位置信区间。

    对 samples 有放回重抽样 n_iter 次，取各重抽样均值，返回其 (1−ci)/2 与
    (1+ci)/2 分位作为 CI 下/上界。固定 seed 可复现。返回 (lo, hi, point)；
    point 为样本原均值。空样本 / 不可数值 → (None, None, None)。

    参数:
      samples : 数值可迭代对象
      n_iter  : 重抽样次数（默认 2000）
      ci      : 置信水平（默认 0.95）
      seed    : 随机种子（默认 42，保证可复现）
    """
    arr = _bootstrap_arr(samples)
    if arr is None:
        return None, None, None
    point = float(arr.mean())
    if arr.size == 1:
        return point, point, point
    rng = np.random.default_rng(int(seed))
    boots = rng.choice(arr, size=(int(n_iter), arr.size), replace=True).mean(axis=1)
    lo_p = (1.0 - float(ci)) / 2.0 * 100.0
    hi_p = (1.0 + float(ci)) / 2.0 * 100.0
    lo, hi = np.percentile(boots, [lo_p, hi_p])
    return float(lo), float(hi), point
