# -*- coding: utf-8 -*-
"""「批量筛选」标签页测试。

两层覆盖：
  1. AppTest 集成（Demo 模式，全程离线）：加载筛选表 → 校验 session_state 落表
     的行数/列/行业桶；市值区间 number_input 与行业 multiselect 过滤生效；
     「加入批量排名 / 历史回测」按钮在位、空选时给出提示。
  2. 纯单元测试（无 Streamlit）：demo 筛选表形状/确定性/行业映射，以及与
     app.py 完全一致的过滤掩码逻辑（市值区间 + 行业多选）。

注：streamlit AppTest 在 1.61 尚无 data_editor 代理，无法驱动 CheckboxColumn
的勾选→「加入」成功路径。该路径的追加/去重逻辑由 _append_pairs_to_input 承担，
与侧边栏单只「➕」按钮同口径，已由 test_dashboard_inputs_cache 覆盖；此处以
空选→提示 的按钮接线测试 + 过滤掩码单元测试补齐，避免依赖脆弱的勾选交互。
"""
import os
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from config import INDUSTRY_BUCKETS
from data import generate_stock_list, generate_stock_screening_data

REPO = Path(__file__).resolve().parent.parent
APP_PATH = str(REPO / "app.py")
CACHE_FILE = str(REPO / ".cache" / "dashboard_inputs.json")
_RUN_TIMEOUT = 120  # 首帧含 akshare 等冷导入，放宽超时


# -- AppTest 辅助 ----------------------------------------------------------

def _new_demo_app():
    """启动 AppTest 并切到 Demo 模式（离线），CWD 固定仓库根以保证 .cache 一致。"""
    os.chdir(REPO)
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=_RUN_TIMEOUT)            # 首帧：读 stock_list 磁盘缓存
    assert not at.exception, f"首帧异常：{at.exception}"
    at.sidebar.checkbox[0].check()          # 切 Demo
    at.run(timeout=_RUN_TIMEOUT)
    assert not at.exception, f"Demo 切换异常：{at.exception}"
    return at


def _ss_get(at, key, default=None):
    """at.session_state 是 SafeSessionState 代理，无 .get()；按 key 取值，缺失返回 default。"""
    try:
        return at.session_state[key]
    except (KeyError, AttributeError):
        return default


def _click_load(at):
    """点击「🔍 加载筛选表」并跑帧，返回更新后的 at。"""
    btns = [b for b in at.button if "加载筛选表" in b.label]
    assert btns, "未找到「加载筛选表」按钮"
    btns[0].click().run(timeout=_RUN_TIMEOUT)
    return at


def _btns(at, substr):
    """按标签子串取主区按钮（避开 emoji，仅匹配中文部分）。"""
    return [b for b in at.button if substr in b.label]


def _caption(at, substr):
    """取含子串的 caption 文本列表。"""
    return [c.value for c in at.caption if substr in str(c.value)]


def _filtered_count(at):
    """解析「符合筛选条件：**N** 只」caption 中的 N。"""
    caps = _caption(at, "符合筛选条件")
    assert caps, "未找到「符合筛选条件」计数 caption"
    # caption 形如 "符合筛选条件：**12** 只"，取首段连续数字
    import re
    m = re.search(r"(\d+)\s*\*?\*?\s*只", caps[0])
    assert m, f"无法从 caption 解析计数：{caps[0]!r}"
    return int(m.group(1))


# -- AppTest 集成 ----------------------------------------------------------

@pytest.fixture
def clean_inputs():
    """每个用例从干净的输入清单缓存起步，结束时清理。"""
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    yield
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)


def test_load_demo_screening_table(clean_inputs):
    """Demo 加载筛选表 → session_state['screening_df'] 为 30 行、列齐、行业桶合法。"""
    at = _new_demo_app()
    at = _click_load(at)
    assert not at.exception, f"加载后异常：{at.exception}"
    sdf = _ss_get(at, "screening_df")
    assert isinstance(sdf, pd.DataFrame)
    assert list(sdf.columns) == ["代码", "名称", "总市值", "行业", "桶"]
    assert len(sdf) == 30
    # 桶值必须落在合法集合
    assert set(sdf["桶"].unique()).issubset(set(INDUSTRY_BUCKETS))
    # 代码为 6 位数字串
    assert sdf["代码"].astype(str).str.fullmatch(r"\d{6}").all()
    # 总市值单位为元（亿元 × 1e8），故均远大于 1e8（最小 ~50 亿）
    assert (sdf["总市值"] >= 1e8).all()


def test_market_cap_filter_shrinks_count(clean_inputs):
    """市值下限 number_input 生效：默认 0→30 只；下限 10000 亿→0 只并提示。"""
    at = _new_demo_app()
    at = _click_load(at)
    # 默认（0 / None）= 不限 → 全量
    assert _filtered_count(at) == 30

    # 设下限 = 10000 亿元（高于 demo 最大 ~7760 亿）→ 0 只 → 早退提示
    lo = [n for n in at.number_input if "市值下限" in n.label]
    assert lo, "未找到「市值下限（亿元）」number_input"
    lo[0].set_value(10000).run(timeout=_RUN_TIMEOUT)
    assert _filtered_count(at) == 0
    assert any("无符合筛选条件" in str(w.value) for w in at.warning), "0 只时应提示放宽条件"

    # 回到一个能筛出部分结果的阈值：下限 5000 亿 → 0 < n < 30
    lo[0].set_value(5000).run(timeout=_RUN_TIMEOUT)
    n5k = _filtered_count(at)
    assert 0 < n5k < 30, f"下限 5000 亿应筛出部分（0<n<30），实得 {n5k}"


def test_industry_multiselect_filters(clean_inputs):
    """行业 multiselect 选项来自筛选表行业列；选「银行」→ 仅剩 5 只银行股。"""
    at = _new_demo_app()
    at = _click_load(at)
    ms = [m for m in at.multiselect if "行业" in m.label]
    assert ms, "未找到行业 multiselect"
    opts = set(ms[0].options)
    assert "银行" in opts and "食品饮料" in opts
    # 选「银行」
    ms[0].select("银行").run(timeout=_RUN_TIMEOUT)
    assert _filtered_count(at) == 5, "Demo 银行股应为 5 只（000001/600000/600036/601166/601398）"


def test_add_buttons_present_and_empty_selection_warns(clean_inputs):
    """加载后「加入批量排名 / 加入历史回测」按钮在位；空选时点击给提示。"""
    at = _new_demo_app()
    at = _click_load(at)
    assert _btns(at, "加入批量排名"), "未找到「加入批量排名」按钮"
    assert _btns(at, "加入历史回测"), "未找到「加入历史回测」按钮"
    # data_editor 默认全不勾选 → 点击「加入批量排名」应提示先勾选
    _btns(at, "加入批量排名")[0].click().run(timeout=_RUN_TIMEOUT)
    assert any("请先在表格中勾选" in str(w.value) for w in at.warning), "空选点击应提示先勾选"


def _selall_btn(at):
    """「☑️ 全选」按钮（避开「☐ 取消全选」也含「全选」二字）。"""
    btns = [b for b in at.button if "全选" in b.label and "取消" not in b.label]
    assert btns, "未找到「☑️ 全选」按钮"
    return btns[0]


def _deselall_btn(at):
    """「☐ 取消全选」按钮。"""
    btns = [b for b in at.button if "取消全选" in b.label]
    assert btns, "未找到「☐ 取消全选」按钮"
    return btns[0]


def _active_batch_count(at):
    """session_state['batch_symbols'] 中的有效行数（非空、非注释）。"""
    from app import _active_input_lines
    text = _ss_get(at, "batch_symbols", "")
    return len(_active_input_lines(text or ""))


def _active_batch_codes(at):
    """session_state['batch_symbols'] 中有效行的代码集合（行首逗号前部分）。"""
    return {ln.split(",", 1)[0] for ln in _ss_get(at, "batch_symbols", "").splitlines()
            if ln.strip() and not ln.strip().startswith("#")}


def _sidebar_expander_count(at, substr):
    """侧边栏中标签含 substr 的展开器，解析其「（N 行）」中的 N；缺失返回 None。

    侧边栏「已添加标的」展开器标签形如「批量排名（30 行）」，其行数取自
    _active_input_lines(session_state[key])，与「批量排名 / 历史回测」输入框同源。
    """
    import re
    for e in at.sidebar.expander:
        if substr in e.label:
            m = re.search(r"[（(]\s*(\d+)\s*行", e.label)
            if m:
                return int(m.group(1))
    return None


def test_select_all_then_add_all_to_batch(clean_inputs):
    """「☑️ 全选」→「➕ 加入批量排名」：全部 30 只筛选结果一次入清单。

    AppTest 无法驱动 data_editor 的 CheckboxColumn 逐行勾选，但全选走的是
    数据列驱动路径——全选标志置 True 后，_disp['选择'] 整列 True，新 key 重
    挂载的表格丢弃旧逐行补丁，data_editor 返回值 _ed['选择'] 全 True → _sel
    取到全部 30 行 → _append_pairs_to_input 把 30 只追加进 batch_symbols。

    注：默认 batch_symbols 文本含 BATCH_DEMO_LIST 的 5 只（000001/600519/000651
    /600036/601318），而这 5 只恰都在 demo 筛选表 30 只内，故按 `代码,名称` 去重
    后净增 25、成功提示「已添加 25 只」；但清单最终应覆盖全部 30 只筛选代码——
    以此断言全选确实覆盖了全部筛选结果，而非仅未默认存在的 25 只。
    """
    from data import generate_stock_screening_data
    demo_codes = set(generate_stock_screening_data()["代码"].astype(str))

    at = _new_demo_app()
    at = _click_load(at)
    assert _filtered_count(at) == 30

    _selall_btn(at).click().run(timeout=_RUN_TIMEOUT)
    assert not at.exception, f"全选后异常：{at.exception}"
    _btns(at, "加入批量排名")[0].click().run(timeout=_RUN_TIMEOUT)
    assert not at.exception, f"加入后异常：{at.exception}"

    # 默认 5 只去重 → 净增 25
    assert any("已添加 25 只" in str(s.value) for s in at.success), \
        "应提示已添加 25 只（30 − 5 默认去重）"
    # 清单最终含全部 30 只 demo 代码 → 全选确实覆盖了全部筛选结果
    assert _active_batch_codes(at) == demo_codes, \
        f"清单未覆盖全部 30 只筛选代码：缺 {demo_codes - _active_batch_codes(at)}"


def test_select_all_deselect_all_then_warns(clean_inputs):
    """「☑️ 全选」→「☐ 取消全选」→「➕ 加入批量排名」：取消全选后空选给提示。

    验证取消全选确有生效——翻转标志为 False 并 bump key 重挂载，_ed['选择']
    全 False → _sel 空 → 点击加入提示先勾选（与默认空选同口径，但此处显式
    走完全选再取消的往返，覆盖两个新按钮的接线）。
    """
    at = _new_demo_app()
    at = _click_load(at)

    _selall_btn(at).click().run(timeout=_RUN_TIMEOUT)
    assert not at.exception, f"全选后异常：{at.exception}"
    _deselall_btn(at).click().run(timeout=_RUN_TIMEOUT)
    assert not at.exception, f"取消全选后异常：{at.exception}"
    _btns(at, "加入批量排名")[0].click().run(timeout=_RUN_TIMEOUT)
    assert not at.exception, f"加入后异常：{at.exception}"
    assert any("请先在表格中勾选" in str(w.value) for w in at.warning), \
        "取消全选后应回到空选 → 提示先勾选"


def test_select_all_re_add_dedup(clean_inputs):
    """「☑️ 全选」→ 加入 → 再加入：去重，提示「均已在清单中」。

    _append_pairs_to_input 按 `代码,名称` 去重并 _save_dashboard_inputs 落盘；
    第二次全选加入时清单已含全部 30 只（经缓存文件跨 run 恢复）→ 0 新增、走
    st.info 分支。坐实全选+加入可安全重复点击不产生重复行。
    """
    at = _new_demo_app()
    at = _click_load(at)
    _selall_btn(at).click().run(timeout=_RUN_TIMEOUT)
    _btns(at, "加入批量排名")[0].click().run(timeout=_RUN_TIMEOUT)
    assert any("已添加 25 只" in str(s.value) for s in at.success), \
        "首次应净增 25（30 − 5 默认去重）"
    n_after_first = _active_batch_count(at)
    assert n_after_first == 30

    # 再次全选 + 加入：清单已含全部 30 只（经缓存跨 run 恢复）→ 去重
    _selall_btn(at).click().run(timeout=_RUN_TIMEOUT)
    _btns(at, "加入批量排名")[0].click().run(timeout=_RUN_TIMEOUT)
    assert any("均已在「批量排名」清单中" in str(i.value) for i in at.info), \
        "重复加入应提示均已在清单中"
    assert _active_batch_count(at) == n_after_first, "去重后清单行数不应增长"


def test_select_all_add_syncs_sidebar_targets(clean_inputs):
    """「全选」→「加入批量排名 / 加入历史回测」后侧边栏「已添加标的」立即同步。

    回归用户报告：追加后侧边栏仍显旧值。根因是追加发生在 tab_screen（侧边栏
    之后渲染），不 rerun 则侧边栏当帧读不到新值、仍显旧。修复在 append 后
    st.rerun()：重跑帧侧边栏先于 tab_screen 渲染、读到的就是新清单。默认
    BATCH_DEMO_LIST 5 只（均在 demo 30 内）→ 全选加入后去重净增 25、清单 30；
    侧边栏两个展开器标签应同步从「（5 行）」变到「（30 行）」，且成功提示经
    rerun 重放仍在。
    """
    at = _new_demo_app()
    at = _click_load(at)
    assert _filtered_count(at) == 30

    # 前置：默认 BATCH_DEMO_LIST 各 5 只 → 侧边栏展开器各「（5 行）」
    assert _sidebar_expander_count(at, "批量排名") == 5, "前置默认批量排名应为 5 行"
    assert _sidebar_expander_count(at, "历史回测") == 5, "前置默认历史回测应为 5 行"

    _selall_btn(at).click().run(timeout=_RUN_TIMEOUT)
    _btns(at, "加入批量排名")[0].click().run(timeout=_RUN_TIMEOUT)
    assert not at.exception, f"加入批量排名后异常：{at.exception}"
    # 侧边栏「批量排名」立即同步到 30（不再卡在旧值 5）；成功提示经 rerun 重放
    assert _sidebar_expander_count(at, "批量排名") == 30, "侧边栏批量排名应同步到 30 行"
    assert any("已添加 25 只" in str(s.value) for s in at.success), \
        "append 后 st.rerun() 应在重跑帧重放「已添加 25 只」"

    # 全选态在 rerun 后保持（join handler 不碰 _selall 标志）→ 加入历史回测同样追加 30
    _btns(at, "加入历史回测")[0].click().run(timeout=_RUN_TIMEOUT)
    assert not at.exception, f"加入历史回测后异常：{at.exception}"
    assert _sidebar_expander_count(at, "历史回测") == 30, "侧边栏历史回测应同步到 30 行"
    assert any("已添加 25 只" in str(s.value) for s in at.success), \
        "历史回测 append 后同样应重放「已添加 25 只」"


# -- 实盘失败路径（live 模式，mock 取数；回归"点加载却无结果"的根因）-----

def _new_live_app(monkeypatch, screening_returns, stock_list_df=None):
    """启动 live 模式（Demo 未勾）AppTest，并 mock 取数以全程离线、确定性。

    data 包在 __init__ 里 from .fetcher import fetch_stock_screening_data，
    故 app.py 的 `from data import fetch_stock_screening_data` 实际取 data 包属性；
    在 data 模块对象上 setattr 即可影响 AppTest 重跑 app.py 时的名字解析。

    :param screening_returns: fetch_stock_screening_data 的返回序列。
        单值视为一次性；list 按调用次序逐个消费（模拟"加载成功→刷新失败"等）。
    """
    import data as datamod
    monkeypatch.setattr(datamod, "fetch_stock_list",
                        lambda force_refresh=False: stock_list_df or generate_stock_list())
    _seq = screening_returns if isinstance(screening_returns, list) else [screening_returns]
    _it = iter(_seq)

    def _fake_screening(**kw):
        try:
            return next(_it)
        except StopIteration:
            return None

    monkeypatch.setattr(datamod, "fetch_stock_screening_data", _fake_screening)
    os.chdir(REPO)
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=_RUN_TIMEOUT)                 # 首帧：mock 后不联网
    assert not at.exception, f"首帧异常：{at.exception}"
    assert not at.sidebar.checkbox[0].value, "应为 live 模式（Demo 未勾选）"
    return at


def test_live_fetch_none_shows_actionable_error(monkeypatch, clean_inputs):
    """live 模式 fetch 静默返回 None（akshare 失败的典型路径）→ 显示可操作
    st.error（切 Demo / 刷新重试），而非误报「完成」又回落到"点击加载"引导。

    这正是用户报告的「点了加载筛选表，结果没展示出来」的根因：fetch 失败时
    fetch_stock_screening_data 返回 None 不抛异常，旧逻辑仍 progress(1.0,完成)
    且 session_state 无表 → 只显示"点击加载"引导，既无表也无错误。
    """
    at = _new_live_app(monkeypatch, None)
    at = _click_load(at)
    assert not at.exception, f"加载后异常：{at.exception}"
    errs = [str(e.value) for e in at.error]
    assert errs, "fetch 返回 None 时应 st.error，而非静默回落到引导"
    joined = " ".join(errs)
    assert "筛选表加载失败" in joined
    assert "Demo" in joined and "刷新" in joined, "错误须含可操作提示（Demo/刷新）"
    # 不应渲染结果表，也不应残留误导性的「完成」成功文案
    assert not _caption(at, "符合筛选条件"), "失败时不应渲染筛选结果表"
    assert _ss_get(at, "screening_df") is None


def test_live_fetch_exception_shows_error(monkeypatch, clean_inputs):
    """live 模式 fetch 抛异常 → try/except 兜底同样显示错误并携带原始信息。"""
    import data as datamod

    def _raise(**kw):
        raise RuntimeError("akshare 网络超时")

    monkeypatch.setattr(datamod, "fetch_stock_list",
                        lambda force_refresh=False: generate_stock_list())
    monkeypatch.setattr(datamod, "fetch_stock_screening_data", _raise)
    os.chdir(REPO)
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=_RUN_TIMEOUT)
    at = _click_load(at)
    assert not at.exception
    errs = [str(e.value) for e in at.error]
    assert any("筛选表加载失败" in e for e in errs)
    assert any("网络超时" in e for e in errs), "应携带原始异常信息"


def test_live_refresh_failure_keeps_old_table_and_warns(monkeypatch, clean_inputs):
    """刷新失败但旧表仍在：保留上一次成功结果 + 顶部 warning，而非清空表格。"""
    _ok = generate_stock_screening_data()
    at = _new_live_app(monkeypatch, [_ok, None])   # 第 1 次成功、第 2 次（刷新）失败
    at = _click_load(at)
    assert not at.exception
    assert _filtered_count(at) == 30, "首次加载应成功出表"
    # 点「🔄 刷新」→ 第 2 次返回 None
    _refresh = [b for b in at.button if "刷新" in b.label]
    assert _refresh, "未找到「🔄 刷新」按钮"
    _refresh[0].click().run(timeout=_RUN_TIMEOUT)
    assert not at.exception
    # 旧表仍在（caption 存在）+ 顶部 warning 提示本次刷新未成功
    assert _caption(at, "符合筛选条件"), "刷新失败应保留并渲染上次的旧表"
    warns = [str(w.value) for w in at.warning]
    assert any("筛选表加载失败" in w for w in warns), "刷新失败应 st.warning"


# -- 纯单元测试（无 Streamlit）-------------------------------------------

def test_demo_screening_data_shape_and_mapping():
    """generate_stock_screening_data 形状/确定性/行业映射正确。"""
    df1 = generate_stock_screening_data()
    df2 = generate_stock_screening_data()
    # 确定性：两次生成逐字节一致（固定种子）
    pd.testing.assert_frame_equal(df1, df2)
    assert list(df1.columns) == ["代码", "名称", "总市值", "行业", "桶"]
    assert len(df1) == 30
    assert set(df1["桶"].unique()).issubset(set(INDUSTRY_BUCKETS))
    # 总市值单位为元（50 亿 ~ 8000 亿 → 5e9 ~ 8e11 元）；用 max>1e10 区分"元"与"亿元"口径
    assert (df1["总市值"] >= 1e8).all()
    assert df1["总市值"].max() > 1e10   # 亿元口径 max≈8000，元口径 max≈7.6e11
    # 桶映射与 map_to_industry_bucket 一致（None → 其他）
    from data.fetcher import map_to_industry_bucket
    expected_bucket = df1["行业"].apply(map_to_industry_bucket)
    assert (df1["桶"] == expected_bucket).all()


def test_filter_mask_logic():
    """复刻 app.py 的过滤掩码（市值区间 + 行业多选），验证计数口径一致。

    与 _render_screening_tab 中以下逻辑严格同构：
        mask = 全 True
        if cap_min:  mask &= 总市值 >= cap_min * 1e8   # 0/None → 不限
        if cap_max:  mask &= 总市值 <= cap_max * 1e8   # None → 不限
        if sel_industries: mask &= 行业.isin(sel)
    """
    df = generate_stock_screening_data()
    n_all = len(df)

    def count(cap_min=0, cap_max=None, sel_industries=None):
        mask = pd.Series(True, index=df.index)
        if cap_min:          # 0 / None → 不限
            mask &= df["总市值"] >= float(cap_min) * 1e8
        if cap_max:
            mask &= df["总市值"] <= float(cap_max) * 1e8
        if sel_industries:
            mask &= df["行业"].isin(sel_industries)
        return int(mask.sum())

    # 默认 = 不限 → 全量
    assert count() == n_all == 30
    # 仅行业 = 银行 → 5
    assert count(sel_industries=["银行"]) == 5
    # 仅行业 = 食品饮料 → 5（600519/000858/600887/000568/600809）
    assert count(sel_industries=["食品饮料"]) == 5
    # 下限 7000 亿 → 少量且 < 全量
    n7k = count(cap_min=7000)
    assert 0 < n7k < n_all
    # 下限 10000 亿 → 0
    assert count(cap_min=10000) == 0
    # 上限 300 亿 → 极少（仅 ~213 亿那只）
    n300 = count(cap_max=300)
    assert 0 < n300 <= 2
    # 组合：银行 + 下限 0 → 仍 5（下限 0 不限）
    assert count(cap_min=0, sel_industries=["银行"]) == 5
    # 组合：上限 0 视为不限（与 app.py `if cap_max:` 口径一致）→ 全量
    assert count(cap_max=0) == n_all


# -- Hikyuu 数据源切换后的映射 / 降级单元测试 ---------------------------
# 批量筛选已从 akshare 实时 HTTP 切到 Hikyuu 本地库。下列用例坐实：
#   - 行业名 → 桶 的 Hikyuu 次表分支（申万次表优先，未命中 → 其他）；
#   - hikyuu 未装 / 本地库未导入 时 _fetch_stock_screening_data_hikyuu 返回 None
#     （不抛、app 层 st.error 提示切 Demo 或先跑导入）。


def test_map_to_industry_bucket_hikyuu_subtable():
    """map_to_industry_bucket 次表：Hikyuu 板块名经 HIKYUU_INDUSTRY_TO_BUCKET 分桶。

    申万次表 SW_TO_BUCKET 优先（单股 fetch_industry_info 路径零回归）；Hikyuu 专属
    名（不在申万表）命中次表；均未命中 → 其他。验证批量筛选切 Hikyuu 后行业
    降级仍正确（Hikyuu 库未导入行业板块时全列 None → 桶全「其他」）。
    """
    from data.fetcher import map_to_industry_bucket
    # Hikyuu 次表命中（这些板块名不在申万 SW_TO_BUCKET）
    assert map_to_industry_bucket("半导体") == "成长"
    assert map_to_industry_bucket("酿酒") == "消费"
    assert map_to_industry_bucket("港口") == "周期"
    assert map_to_industry_bucket("证券") == "非银金融"
    # 申万次表优先：申万名命中 SW_TO_BUCKET 即用，不查次表
    assert map_to_industry_bucket("食品饮料") == "消费"   # 申万表
    assert map_to_industry_bucket("银行") == "银行"       # 两表均有，申万优先
    # 未命中 → 其他
    assert map_to_industry_bucket("某未知行业板块") == "其他"
    # None / 空 / 纯空白 → 其他（Hikyuu 行业未导入时全列 None 的降级路径）
    assert map_to_industry_bucket(None) == "其他"
    assert map_to_industry_bucket("") == "其他"
    assert map_to_industry_bucket("  ") == "其他"


def test_hikyuu_fetch_returns_none_when_not_importable(monkeypatch):
    """hikyuu 未安装时 _fetch_stock_screening_data_hikyuu 返回 None（不抛）。

    sys.modules['hikyuu']=None 使 `import hikyuu` 抛 ImportError（Python 视 None
    为「不可导入」）；函数 try/except 吞之、返回 None → app 层 st.error 提示。
    """
    import sys
    import data.fetcher as fetcher
    monkeypatch.setitem(sys.modules, "hikyuu", None)
    assert fetcher._fetch_stock_screening_data_hikyuu() is None


def test_hikyuu_fetch_returns_none_when_db_not_loaded(monkeypatch):
    """本地库未导入时 load_hikyuu 抛异常 → 返回 None + 可操作提示（不抛）。

    注入伪 hikyuu 模块，load_hikyuu 抛 RuntimeError 模拟「stock.db 未导入 /
    no such table: block」；函数 except 吞之、打印提示、返回 None。
    """
    import sys
    from unittest.mock import MagicMock
    import data.fetcher as fetcher
    fake = MagicMock()
    fake.load_hikyuu.side_effect = RuntimeError("no such table: block")
    monkeypatch.setitem(sys.modules, "hikyuu", fake)
    assert fetcher._fetch_stock_screening_data_hikyuu() is None
