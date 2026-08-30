# -*- coding: utf-8 -*-
"""批量筛选「行业」列的兜底链测试（全离线，无 Streamlit、无网络）。

背景（用户报告：批量筛选行业缺失，连银行业都没有）：
  Hikyuu 本地行业板块由 import_hikyuu_industry_blocks.py 从东财导入 496 个；该导入
  失败/部分成功时本地只剩十几个板块（实测 19），行业列大面积为空且**没有「银行」
  板块**。旧兜底直接降级新浪行业，而新浪把银行/证券/保险合并成一个「金融行业」
  → 桶误判为「非银金融」，银行股的打分口径整体错。

本文件守护三点：
  1. _fetch_sw_industry_map 把行业代码去 ".SI" 后缀（申万成份端点只认裸码，
     带后缀返空 → akshare 抛 KeyError）。这是申万链路长期失效的根因。
  2. 兜底链顺序：申万（含银行/非银分列）优先，新浪仅作残留补齐。
  3. 申万覆盖后仍空缺的行（如北交所）不得被新浪误标成「金融行业」。
"""
import pandas as pd

from data.fetcher import (_apply_industry_map, _fill_industry_from_sina,
                          _fetch_sw_industry_map, _fill_industry_from_sw)

# 模拟申万端点：行业代码（裸码）→ (成份代码列表, 行业名)。各行互不重叠（真实形态：
# 申万 31 个一级行业对个股互斥），便于断言映射归属。
_SW_CODES = {
    "801780": (["601398", "601288"], "银行"),
    "801790": (["000001", "601318"], "非银金融"),
    "801010": (["000058", "002714"], "农林牧渔"),
}


def _sw_first_info_df():
    """模拟 ak.sw_index_first_info 返回（代码带 .SI 后缀，真实形态）。"""
    return pd.DataFrame([
        {"行业代码": "801780.SI", "行业名称": "银行"},
        {"行业代码": "801790.SI", "行业名称": "非银金融"},
        {"行业代码": "801010.SI", "行业名称": "农林牧渔"},
    ])


def _sw_comp(symbol):
    """模拟 ak.index_component_sw（证券代码为 6 位裸码）。"""
    codes, name = _SW_CODES[symbol]
    return pd.DataFrame({"证券代码": list(codes), "证券名称": [name] * len(codes)})


def _screening_df():
    """模拟 Hikyuu 本地取数结果：仅 1 只有行业（本地板块未导入的常见形态）。"""
    return pd.DataFrame({
        "代码": ["601398", "000001", "300750", "830799"],
        "名称": ["工商银行", "平安银行", "宁德时代", "北交所某股"],
        "总市值": [1.0e12, 1.0e11, 1.0e12, 1.0e10],
        "行业": [None, None, "环保行业", None],
        "桶": ["其他", "其他", "周期", "其他"],
    })


def _sina_map():
    """新浪行业映射：金融无银行粒度（银行 → 「金融行业」）。"""
    return {"601398": "金融行业", "000001": "金融行业",
            "300750": "电气设备", "830799": "其他"}


def _at(df, code, col):
    """按代码取单行单元格值。"""
    return df.loc[df["代码"] == code, col].iloc[0]


def _patch_sw(monkeypatch, df):
    """把 sw_index_first_info / index_component_sw / time.sleep 打桩到 fetcher。"""
    import data.fetcher as fetcher
    monkeypatch.setattr(fetcher.ak, "sw_index_first_info", lambda: df,
                        raising=False)
    monkeypatch.setattr(fetcher.ak, "index_component_sw", _sw_comp, raising=False)
    monkeypatch.setattr(fetcher.time, "sleep", lambda *_a: None)


def _run_chain(sw_map, sina_map, df=None):
    """按生产代码同样的两级门槛跑兜底链，返回 (覆盖数, 调用顺序, df)。

    就地替换模块属性并在 finally 还原，保证用例间互不污染。
    """
    import data.fetcher as fetcher
    calls = []
    orig = (fetcher._fetch_sw_industry_map, fetcher._fetch_sina_industry_map)
    fetcher._fetch_sw_industry_map = lambda on_progress=None: (
        calls.append("sw"), sw_map)[1]
    fetcher._fetch_sina_industry_map = lambda on_progress=None: (
        calls.append("sina"), sina_map)[1]
    try:
        df = df if df is not None else _screening_df()
        have_ind = int(df["行业"].notna().sum())
        have_ind = _fill_industry_from_sw(df, have_ind)
        if have_ind < len(df) * 0.95:
            have_ind = _fill_industry_from_sina(df, have_ind)
        return have_ind, calls, df
    finally:
        (fetcher._fetch_sw_industry_map, fetcher._fetch_sina_industry_map) = orig


# -- 1. 行业代码去 .SI 后缀 ---------------------------------------------

def test_sw_industry_map_strips_si_suffix(monkeypatch):
    """申万成份端点只认裸码：801780.SI 必须被截成 801780 再传 index_component_sw。

    回归根因：带后缀时申万端点返回 results=[]，akshare 在列选择处抛
    KeyError("证券代码 ... not in index")——确定性空响应，重试无效。
    """
    seen = []
    import data.fetcher as fetcher
    _patch_sw(monkeypatch, _sw_first_info_df())
    orig = fetcher.ak.index_component_sw
    fetcher.ak.index_component_sw = (
        lambda symbol: seen.append(symbol) or orig(symbol))

    mapping = _fetch_sw_industry_map()

    assert seen == ["801780", "801790", "801010"], \
        f"行业代码须去 .SI 后缀再请求，实得 {seen}"
    assert mapping["601398"] == "银行"
    assert mapping["000001"] == "非银金融"
    assert mapping["000058"] == "农林牧渔"
    assert len(mapping) == 6


def test_sw_industry_map_keeps_bare_codes(monkeypatch):
    """已是裸码（无后缀）时不得误截，行为不变。"""
    seen = []
    df = _sw_first_info_df().copy()
    df["行业代码"] = df["行业代码"].str.replace(".SI", "", regex=False)
    _patch_sw(monkeypatch, df)
    import data.fetcher as fetcher
    orig = fetcher.ak.index_component_sw
    fetcher.ak.index_component_sw = (
        lambda symbol: seen.append(symbol) or orig(symbol))

    assert _fetch_sw_industry_map()
    assert seen == ["801780", "801790", "801010"]


def test_sw_industry_map_skips_blank_codes(monkeypatch):
    """空/异常代码行跳过（截后缀后为空），不产生空参数请求。"""
    seen = []
    df = _sw_first_info_df()
    df.loc[1, "行业代码"] = "   .SI"        # 截后缀后为空
    df.loc[2, "行业代码"] = ""
    _patch_sw(monkeypatch, df)
    import data.fetcher as fetcher
    orig = fetcher.ak.index_component_sw
    fetcher.ak.index_component_sw = (
        lambda symbol: seen.append(symbol) or orig(symbol))

    assert _fetch_sw_industry_map()
    assert seen == ["801780"]


def test_sw_industry_map_last_industry_wins_on_overlap(monkeypatch):
    """同一代码出现在两个行业时确定性取最后遍历到的行业（申万一级行业互斥，仅记录行为）。"""
    import data.fetcher as fetcher
    _patch_sw(monkeypatch, _sw_first_info_df())
    orig = fetcher.ak.index_component_sw

    def _overlap(symbol):
        if symbol == "801790":
            return pd.DataFrame({"证券代码": ["601398", "601288"],
                                 "证券名称": ["非银金融", "非银金融"]})
        return orig(symbol)

    fetcher.ak.index_component_sw = _overlap

    mapping = _fetch_sw_industry_map()
    assert mapping["601398"] == "非银金融"      # 后遍历者覆盖前者（601398 原属「银行」）


# -- 2. 兜底链顺序：申万优先，新浪仅补残留 ------------------------------

def test_fallback_chain_sw_first_sina_fills_residual():
    """本地覆盖不足 → 申万先跑（银行/非银分列），新浪只补申万漏掉的行。"""
    have_ind, calls, df = _run_chain({"601398": "银行", "300750": "电子"},
                                     _sina_map())

    assert calls == ["sw", "sina"], f"应先申万后新浪，实得 {calls}"
    assert have_ind == 4
    assert _at(df, "601398", "行业") == "银行"
    # 申万漏掉的行由新浪兜底（至少行业列非空）
    assert _at(df, "000001", "行业") == "金融行业"
    assert _at(df, "830799", "行业") == "其他"
    # 本地已有行业的行不受任何兜底影响
    assert _at(df, "300750", "行业") == "环保行业"


def test_banks_kept_by_sw_not_overwritten_by_sina():
    """申万给出的「银行」不被新浪残留补齐覆盖（fillna 语义 + 顺序保证）。

    这是「银行业消失」回归的直接守护：工行须落在桶「银行」，
    而非新浪的「金融行业」→ 桶「非银金融」。
    """
    have_ind, calls, df = _run_chain({"601398": "银行", "300750": "电子"},
                                     _sina_map())
    assert calls == ["sw", "sina"]
    assert _at(df, "601398", "行业") == "银行", \
        "工行须归「银行」，不得被新浪标成「金融行业」"
    assert _at(df, "601398", "桶") == "银行"
    assert _at(df, "000001", "桶") == "非银金融"   # 申万漏掉 → 新浪兜底


def test_fallback_chain_skips_sina_when_sw_covers_all():
    """申万已补齐全部空缺（覆盖 100%）→ 新浪不触发，银行不会被粗分类误标。"""
    have_ind, calls, df = _run_chain(
        {"601398": "银行", "000001": "银行", "300750": "电子", "830799": "机械设备"},
        _sina_map())

    assert calls == ["sw"], f"申万全覆盖时不应触发新浪，实得 {calls}"
    assert have_ind == 4
    assert "非银金融" not in set(df["桶"]), "银行股不得被新浪粗分类误标为非银金融"


def test_fallback_chain_full_coverage_skips_sw(monkeypatch):
    """本地行业板块已完整（≥98% 覆盖）→ 兜底链不触发、不联网。"""
    import data.fetcher as fetcher
    rows = [{"代码": f"{i:06d}", "名称": "x", "总市值": 1.0e9,
             "行业": "半导体" if i < 99 else None, "桶": "成长"}
            for i in range(100)]
    df = pd.DataFrame(rows)
    called = {"n": 0}
    monkeypatch.setattr(fetcher, "_fetch_sw_industry_map",
                        lambda on_progress=None: (called.__setitem__("n", 1), {})[1])

    have_ind = _fill_industry_from_sw(df, int(df["行业"].notna().sum()))

    assert called["n"] == 0, "覆盖率已达 98% 时不应触发申万兜底"
    assert have_ind == 99


def test_fallback_chain_sw_failure_falls_to_sina(monkeypatch):
    """申万拉取失败（返 {}）→ 不改 df，交由新浪兜底（行业列非空胜过全 None）。"""
    import data.fetcher as fetcher
    df = _screening_df()
    have_ind = int(df["行业"].notna().sum())
    monkeypatch.setattr(fetcher, "_fetch_sw_industry_map", lambda on_progress=None: {})
    monkeypatch.setattr(fetcher, "_fetch_sina_industry_map",
                        lambda on_progress=None: _sina_map())

    h1 = _fill_industry_from_sw(df, have_ind)
    assert h1 == have_ind, "申万失败时不应改动 df"
    assert pd.isna(_at(df, "601398", "行业"))

    h2 = _fill_industry_from_sina(df, h1)
    assert h2 == 4
    assert df["行业"].notna().all()


def test_apply_industry_map_fills_na_only_and_recomputes_bucket():
    """_apply_industry_map 只填 NaN 行（不覆盖本地已有行业），且必须重算「桶」。"""
    df = _screening_df()
    out = _apply_industry_map(df, {"000001": "银行", "300750": "半导体",
                                   "830799": "机械设备"}, "测试")
    assert out == 3, "只有 3 行原本为空（300750 已有本地行业，不应被覆盖）"
    # 已有本地行业的行不被覆盖
    assert _at(df, "300750", "行业") == "环保行业"
    assert _at(df, "300750", "桶") == "周期"
    # 新填的行同步重算桶（否则残留旧的「其他」）
    assert _at(df, "000001", "桶") == "银行"
    assert _at(df, "830799", "桶") == "成长"


# -- 3. 分桶映射：申万 31 个一级行业名 ----------------------------------

def test_sw_names_map_to_buckets():
    """申万一级行业名全部命中桶（仅「综合」conglomerate 落「其他」，符合预期）。

    守护批量筛选改走申万后行业列的桶正确性（SW_TO_BUCKET 精确项 +
    INDUSTRY_KEYWORDS 有序兜底）。
    """
    from data.fetcher import map_to_industry_bucket
    expected = {
        "银行": "银行", "非银金融": "非银金融",
        "农林牧渔": "消费", "基础化工": "周期", "钢铁": "周期", "有色金属": "周期",
        "电子": "成长", "汽车": "成长", "家用电器": "消费", "食品饮料": "消费",
        "纺织服饰": "消费", "轻工制造": "消费", "医药生物": "消费",
        "公用事业": "周期", "交通运输": "周期", "房地产": "周期", "商贸零售": "消费",
        "社会服务": "消费", "综合": "其他", "建筑材料": "周期", "建筑装饰": "周期",
        "电力设备": "成长", "机械设备": "成长", "国防军工": "成长", "计算机": "成长",
        "传媒": "成长", "通信": "成长", "煤炭": "周期", "石油石化": "周期",
        "环保": "周期", "美容护理": "消费",
    }
    assert len(expected) == 31
    wrong = {name: (want, map_to_industry_bucket(name))
             for name, want in expected.items()
             if map_to_industry_bucket(name) != want}
    assert not wrong, f"申万行业名分桶不符: {wrong}"


def test_banks_bucket_comes_from_sw_or_em_names():
    """回归「银行业消失了」：银行桶只能由申万/东财板块名产生，新浪粗分类产生不了。"""
    from data.fetcher import map_to_industry_bucket
    assert map_to_industry_bucket("银行") == "银行"
    assert map_to_industry_bucket("城商行") == "银行"
    assert map_to_industry_bucket("农商行") == "银行"
    # 新浪的粗分类无银行粒度 → 非银金融（已知局限，仅作最后兜底）
    assert map_to_industry_bucket("金融行业") == "非银金融"
    assert map_to_industry_bucket(None) == "其他"
