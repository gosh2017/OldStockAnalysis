# -*- coding: utf-8 -*-
"""
bootstrap 置信区间工具测试：CI 覆盖真实均值、空样本安全、同种子可复现。
"""
from utils import bootstrap_ci


def test_bootstrap_ci_covers_mean():
    data = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10]
    lo, hi, pt = bootstrap_ci(data, n_iter=500, seed=42)
    assert lo <= pt <= hi
    assert abs(pt - 0.055) < 1e-9


def test_bootstrap_ci_empty():
    assert bootstrap_ci([]) == (None, None, None)


def test_bootstrap_ci_single():
    m = 0.05
    lo, hi, pt = bootstrap_ci([m])
    assert lo == hi == pt == m


def test_bootstrap_ci_deterministic():
    """同种子两次调用结果完全一致（demo / 回测可复现）。"""
    data = [0.01, 0.02, 0.03, 0.04, 0.05]
    a = bootstrap_ci(data, n_iter=200, seed=7)
    b = bootstrap_ci(data, n_iter=200, seed=7)
    assert a == b


def test_bootstrap_ci_nonneg_width():
    data = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]
    lo, hi, _ = bootstrap_ci(data, n_iter=500, seed=42)
    assert lo <= hi


def test_bootstrap_ci_filters_nonfinite():
    """含 nan/inf 的样本被剔除，正常算 CI。"""
    data = [0.01, float("nan"), 0.03, float("inf"), 0.05, 0.06, 0.07, 0.08]
    lo, hi, pt = bootstrap_ci(data, n_iter=500, seed=42)
    assert lo is not None and hi is not None
    assert lo <= pt <= hi
