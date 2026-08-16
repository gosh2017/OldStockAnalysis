# -*- coding: utf-8 -*-
"""
分位数工具测试：验证 numpy 兜底实现与 scipy 语义一致（scipy 未装时也成立），
并锁定"高 ERP → 高分位"这一情绪方向修正的核心不变量。
"""
from utils import percentile_of_score


def test_percentile_basic_rank():
    data = [1, 2, 3, 4, 5]
    # rank = (weak + strict) / 2：score=3 → weak=60, strict=40 → 50
    assert percentile_of_score(data, 3) == 50.0
    # 最大值 → rank 90（非 100，因 rank 对极端值取弱/严平均）
    assert percentile_of_score(data, 5) == 90.0
    # 最小值 → rank 10
    assert percentile_of_score(data, 1) == 10.0


def test_percentile_kinds():
    data = [1, 2, 3, 4, 5]
    assert percentile_of_score(data, 3, kind="weak") == 60.0
    assert percentile_of_score(data, 3, kind="strict") == 40.0


def test_percentile_monotonic():
    """高 ERP → 高分位（市场情绪方向修正的核心：高分位=便宜=低估）。"""
    hist = [0.01, 0.02, 0.03, 0.04, 0.05]
    assert percentile_of_score(hist, 0.045) > percentile_of_score(hist, 0.015)
    assert percentile_of_score(hist, 0.05) > percentile_of_score(hist, 0.0)


def test_percentile_non_integer():
    data = [1, 2, 3, 4, 5]
    # score=3.5：weak(≤3.5)=3→60, strict(<3.5)=3→60, rank=60
    assert percentile_of_score(data, 3.5) == 60.0


def test_percentile_empty():
    assert percentile_of_score([], 1.0) == 0.0
