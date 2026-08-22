# -*- coding: utf-8 -*-
"""验证「批量排名 / 历史回测」输入清单的跨启动持久化。

用 streamlit.testing.AppTest 模拟仪表盘运行：
  - 编辑输入框 → on_change 落盘
  - 侧边栏「➕」追加到输入框并落盘
  - 「❌」从输入框移除并落盘
  - 新建 AppTest（模拟重启）→ 输入框恢复上次的输入
全程 Demo 模式（离线，不联网）。
"""
import json
import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

REPO = Path(__file__).resolve().parent.parent
APP_PATH = str(REPO / "app.py")
CACHE_FILE = str(REPO / ".cache" / "dashboard_inputs.json")


def _new_app(demo=True):
    """启动一个 AppTest 实例并跑首帧（CWD 固定为仓库根，确保 .cache/ 落点一致）。"""
    os.chdir(REPO)
    at = AppTest.from_file(APP_PATH)
    at.run()  # 首帧：demo=False，读 stock_list 磁盘缓存（离线）
    if demo:
        at.sidebar.checkbox[0].check()  # 切到 Demo 模式（内置清单，全程无网）
        at.run()
    return at


def _read_cache():
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def test_edit_persists_and_restores():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    try:
        # --- 第一次会话：编辑「批量排名」输入框并触发 on_change 落盘 ---
        at = _new_app()
        custom = "000001,平安银行\n600519,贵州茅台"
        at.text_area(key="batch_symbols").input(custom).run()
        cache = _read_cache()
        assert cache["batch_symbols"] == custom

        # --- 模拟重启：全新 AppTest（session_state 清空）应恢复上次输入 ---
        at2 = _new_app()
        assert at2.text_area(key="batch_symbols").value == custom
        assert at2.session_state["batch_symbols"] == custom
    finally:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)


def test_backtest_edit_persists_and_restores():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    try:
        at = _new_app()
        custom = "300750,宁德时代\n# 注释行\n002594,比亚迪"
        at.text_area(key="bt_symbols").input(custom).run()
        assert _read_cache()["bt_symbols"] == custom

        at2 = _new_app()
        assert at2.text_area(key="bt_symbols").value == custom
    finally:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)


def test_sidebar_add_appends_to_textarea():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    try:
        at = _new_app()
        # 清空批量输入框，避免默认 demo 清单干扰
        at.text_area(key="batch_symbols").input("").run()
        # 点侧边栏「➕ 批量排名」按钮（搜索默认"平安银行"应能匹配）
        add_btns = [b for b in at.sidebar.button if "批量排名" in b.label]
        assert add_btns, "未找到「➕ 批量排名」按钮（可能股票列表未加载）"
        add_btns[0].click().run()
        val = at.session_state["batch_symbols"]
        assert "平安银行" in val
        # 输入框与 session_state 同步
        assert at.text_area(key="batch_symbols").value == val
        # 已落盘
        assert _read_cache()["batch_symbols"] == val
    finally:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)


def test_remove_button_splices_line():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    try:
        at = _new_app()
        custom = "000001,平安银行\n600519,贵州茅台\n300750,宁德时代"
        at.text_area(key="batch_symbols").input(custom).run()
        # 「已添加标的」expander 里批量排名的首个 ❌（key=rm_batch_symbols_0）
        rm_btns = [b for b in at.sidebar.button if b.key == "rm_batch_symbols_0"]
        assert rm_btns, "未找到首个 ❌ 移除按钮"
        rm_btns[0].click().run()
        val = at.session_state["batch_symbols"]
        assert "000001,平安银行" not in val  # 首行被删
        assert "600519,贵州茅台" in val      # 其余保留
        assert val == "600519,贵州茅台\n300750,宁德时代"
        # 注释/空行保留逻辑另由 _remove_nth_active_line 单测覆盖
    finally:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)


def test_remove_preserves_comment_lines():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    try:
        at = _new_app()
        custom = "# 我的清单\n000001,平安银行\n600519,贵州茅台"
        at.text_area(key="batch_symbols").input(custom).run()
        rm = [b for b in at.sidebar.button if b.key == "rm_batch_symbols_0"]
        assert rm
        rm[0].click().run()
        val = at.session_state["batch_symbols"]
        # 注释行保留，仅删首个有效行
        assert val == "# 我的清单\n600519,贵州茅台"
    finally:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)


def test_empty_cache_falls_back_to_default():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    try:
        at = _new_app()
        val = at.text_area(key="batch_symbols").value
        # 无缓存时回退到内置 demo 清单
        assert "平安银行" in val
    finally:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
