# -*- coding: utf-8 -*-
"""验证批量排名的基本面年份窗口正确生效、且进度缓存按年份隔离。

背景：批量排名此前不暴露年份入口，固定用 config.FIN_START..FIN_END；且
partial 落盘共用一个文件名，改年份后 resume / 进程恢复会把旧口径的分数当成
本次结果复用而不提示。

覆盖：
  1. batch_partial_path：按年份分片、years=None 回退 config 默认；
  2. run_batch：years 透传到 ctx.fin_start/fin_end；
     resume=True 不跨年份窗口复用（换年份全量重算），同窗口内仍复用（断点续跑不失效）；
  3. run_batch_silent：years/resume 透传给 run_batch（仪表盘接线段）；
  4. 仪表盘：批量 tab 的年份输入在位、改动落盘 / 跨启动恢复、年份区间非法时阻止运行。
全程离线（fake main / Demo 模式，不联网）。
"""
import json
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import main as main_mod
from config import FIN_START, FIN_END

REPO = Path(__file__).resolve().parent.parent
APP_PATH = str(REPO / "app.py")
CACHE_FILE = str(REPO / ".cache" / "dashboard_inputs.json")
ITEMS = [("000001", "平安银行"), ("600519", "贵州茅台")]


def _fake_main(calls):
    """替身 main：记录 ctx 的年份窗口，返回 run_batch 汇总所需的最小结果结构。"""
    def _inner(ctx, *, quiet=False):
        calls.append((ctx.symbol, int(ctx.fin_start), int(ctx.fin_end)))
        return {
            "score": {"score": 62.0, "grade": "C", "quality": None, "valuation": 0.0,
                      "sentiment": None, "screened": True,
                      "completeness_tag": "中", "completeness": 50.0},
            "advice": {"recommendation": "买入"},
        }
    return _inner


@pytest.fixture
def tmp_partial(monkeypatch, tmp_path):
    """把 partial 落盘根目录指向临时目录，避免污染 .cache/ 里的真实进度文件。"""
    monkeypatch.setattr(main_mod, "BATCH_PARTIAL_PKL",
                        str(tmp_path / "batch_partial.pkl"))
    return tmp_path


def test_batch_partial_path_isolated_by_years(tmp_partial, monkeypatch):
    p2020 = main_mod.batch_partial_path((2020, 2024))
    p2021 = main_mod.batch_partial_path((FIN_START, FIN_END))
    assert p2020 != p2021, "不同年份窗口必须落到不同文件"
    assert "2020_2024" in os.path.basename(p2020)
    assert "2021_2025" in os.path.basename(p2021)
    # years=None 与显式 config 默认值命中同一文件（未传年份的调用方与仪表盘默认一致）
    assert main_mod.batch_partial_path(None) == p2021
    assert os.path.dirname(p2020) == str(tmp_partial)


def test_run_batch_years_reach_ctx_and_resume_isolated_by_years(
        tmp_partial, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "main", _fake_main(calls))

    # 第一轮：2020–2024，全量计算
    df1 = main_mod.run_batch(ITEMS, demo=True, years=(2020, 2024), resume=True)
    assert len(df1) == len(ITEMS)
    assert len(calls) == len(ITEMS), "首轮应逐只计算"
    assert all((a, b) == (2020, 2024) for _, a, b in calls), \
        "years 未透传到 ctx 的年份窗口"
    assert Path(main_mod.batch_partial_path((2020, 2024))).exists()

    # 第二轮：换 2021–2025，即使 resume=True 也不得复用上一轮结果
    calls.clear()
    df2 = main_mod.run_batch(ITEMS, demo=True, years=(FIN_START, FIN_END), resume=True)
    assert len(calls) == len(ITEMS), "换年份后仍全量重算（未跨窗口复用）"
    assert all((a, b) == (FIN_START, FIN_END) for _, a, b in calls)
    assert len(df2) == len(ITEMS)
    assert Path(main_mod.batch_partial_path((FIN_START, FIN_END))).exists()
    assert not Path(main_mod.BATCH_PARTIAL_PKL).exists(), "不再写旧版单文件"

    # 第三轮：同窗口再跑，断点续跑仍应复用（resume 语义未被隔离逻辑破坏）
    calls.clear()
    df3 = main_mod.run_batch(ITEMS, demo=True, years=(FIN_START, FIN_END), resume=True)
    assert calls == [], "同年份窗口内 resume 应直接复用落盘结果"
    assert len(df3) == len(ITEMS)


def test_run_batch_silent_forwards_years(monkeypatch):
    """run_batch_silent 把 years/resume 透传给 run_batch（仪表盘「批量排名」的接线段）。

    app.py 是脚本，AppTest 每帧会重新 exec 模块、覆盖模块级 monkeypatch，故此处直接
    调用函数验证（裸模式导入仅产生 Streamlit bare-mode 警告，无网络）。"""
    import app as app_mod

    captured = {}

    def _fake_run_batch(items, **kw):
        captured["items"] = items          # run_batch_silent 以位置参数传入
        captured.update(kw)
        return "DF"

    monkeypatch.setattr(app_mod, "run_batch", _fake_run_batch)
    out = app_mod.run_batch_silent(demo=True, items=ITEMS,
                                   years=(2018, 2024), resume=True)
    assert out == "DF"
    assert captured["years"] == (2018, 2024), "years 未透传"
    assert captured["resume"] is True
    assert captured["demo"] is True
    assert captured["items"] == ITEMS


def test_dashboard_batch_years_widgets_and_persistence():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    try:
        os.chdir(REPO)
        at = AppTest.from_file(APP_PATH)
        at.run()
        at.sidebar.checkbox[0].check()      # 切到 Demo（离线）
        at.run()

        start_in = at.number_input(key="batch_fin_start")
        end_in = at.number_input(key="batch_fin_end")
        assert start_in.value == FIN_START and end_in.value == FIN_END

        start_in.set_value(2018)
        end_in.set_value(2024)
        at.run()
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        assert cache["batch_years"] == [2018, 2024]

        # 模拟重启：年份随清单一同恢复
        at2 = AppTest.from_file(APP_PATH)
        at2.run()
        at2.sidebar.checkbox[0].check()
        at2.run()
        assert at2.number_input(key="batch_fin_start").value == 2018
        assert at2.number_input(key="batch_fin_end").value == 2024

        # 起始年晚于结束年：阻止运行并提示（否则 range() 空区间 → 静默全 0 分）
        at2.number_input(key="batch_fin_start").set_value(2024)
        at2.number_input(key="batch_fin_end").set_value(2020)
        at2.run()
        [b for b in at2.button if "运行批量打分" in b.label][0].click()
        at2.run()
        assert any("不能晚于结束年" in str(e.value) for e in at2.error)
        assert "batch_job" not in at2.session_state, "年份非法时不应启动批量任务"
    finally:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
