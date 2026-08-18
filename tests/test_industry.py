# -*- coding: utf-8 -*-
"""
行业分桶基础设施测试（P1）。

覆盖：
  - map_to_industry_bucket 纯函数：已知申万一级行业名 → 桶，None/未知 → "其他"
  - demo 行业数据：000001→银行、600519→消费，结构完整（含 bucket/total_shares/source）
  - INDUSTRY_PROFILES 6 桶齐全且画像字段完整，"其他" 桶与现行 SCENARIOS 口径等价
  - SW_TO_BUCKET 覆盖计划约定的一级行业
"""
from config import (
    INDUSTRY_BUCKETS, SW_TO_BUCKET, INDUSTRY_PROFILES, DCF_GROWTH_CAGR_CLIP,
    INDUSTRY_INFO_TTL_HOURS, MIN_PASSING_YEARS, SCENARIOS,
)
from data import map_to_industry_bucket, generate_industry_info, generate_all_demo_data
from config import StockContext


# -- map_to_industry_bucket ----------------------------------------------

def test_map_to_industry_bucket_known():
    """已知申万一级行业名 → 对应桶。"""
    assert map_to_industry_bucket("银行") == "银行"
    assert map_to_industry_bucket("证券") == "非银金融"
    assert map_to_industry_bucket("保险") == "非银金融"
    assert map_to_industry_bucket("食品饮料") == "消费"
    assert map_to_industry_bucket("医药生物") == "消费"
    assert map_to_industry_bucket("钢铁") == "周期"
    assert map_to_industry_bucket("化工") == "周期"
    assert map_to_industry_bucket("电子") == "成长"
    assert map_to_industry_bucket("计算机") == "成长"


def test_map_to_industry_bucket_none_and_unknown():
    """None / 空串 / 未知行业 → "其他"。"""
    assert map_to_industry_bucket(None) == "其他"
    assert map_to_industry_bucket("") == "其他"
    assert map_to_industry_bucket("不存在的行业XYZ") == "其他"
    # 含空格也应 strip 后匹配
    assert map_to_industry_bucket(" 银行 ") == "银行"


# -- demo 行业数据 --------------------------------------------------------

def test_demo_industry_info_000001_bank():
    """demo 默认标的 000001 → 行业=银行、桶=银行、含总股本与 source。"""
    info = generate_industry_info("000001")
    assert info["industry"] == "银行"
    assert info["bucket"] == "银行"
    assert info["total_shares"] == 197.56e8
    assert info["source"] == "demo"


def test_demo_industry_info_600519_consumer():
    """600519 贵州茅台 → 食品饮料 → 消费桶。"""
    info = generate_industry_info("600519")
    assert info["industry"] == "食品饮料"
    assert info["bucket"] == "消费"


def test_demo_industry_info_unknown_falls_to_other():
    """未在 _DEMO_INDUSTRY 中的代码 → industry=None、桶=其他（不抛错）。"""
    info = generate_industry_info("999999")
    assert info["industry"] is None
    assert info["bucket"] == "其他"


def test_generate_all_demo_data_has_industry_key(ctx):
    """generate_all_demo_data 返回字典含 industry_info 键且结构合法。"""
    data = generate_all_demo_data(ctx)
    assert "industry_info" in data
    info = data["industry_info"]
    assert info["bucket"] in INDUSTRY_BUCKETS
    assert info["source"] == "demo"
    assert info["total_shares"] > 0


# -- INDUSTRY_PROFILES 完整性 --------------------------------------------

def test_industry_profiles_complete():
    """6 桶齐全且每桶画像字段完整（与 P2/P3b 消费契约一致）。"""
    assert set(INDUSTRY_PROFILES.keys()) == set(INDUSTRY_BUCKETS)
    required = {"wacc", "perpetual", "roe_benchmark", "is_financial", "eps_method", "growth_clip"}
    for bucket, profile in INDUSTRY_PROFILES.items():
        assert required.issubset(profile.keys()), f"{bucket} 缺字段: {required - set(profile.keys())}"
        assert profile["eps_method"] in ("normalized", "shiller"), f"{bucket} eps_method 非法"
        assert profile["growth_clip"] == DCF_GROWTH_CAGR_CLIP


def test_other_bucket_matches_legacy_scenarios():
    """'其他' 桶 == 现行 SCENARIOS 口径（等价回退路径，保证零回归）。"""
    other = INDUSTRY_PROFILES["其他"]
    assert other["wacc"] == SCENARIOS["中性 (Neutral)"]["wacc"] == 0.095
    assert other["perpetual"] == SCENARIOS["中性 (Neutral)"]["perpetual"] == 0.015


def test_financial_buckets_flagged():
    """银行 / 非银金融 is_financial=True（评分跳过 debt/ocf）。"""
    assert INDUSTRY_PROFILES["银行"]["is_financial"] is True
    assert INDUSTRY_PROFILES["非银金融"]["is_financial"] is True
    # 非金融桶应为 False
    assert INDUSTRY_PROFILES["其他"]["is_financial"] is False


def test_cyclic_uses_shiller_eps():
    """周期桶 eps_method=shiller（item 4 席勒平滑）。"""
    assert INDUSTRY_PROFILES["周期"]["eps_method"] == "shiller"
    # 其余桶均 normalized
    for b in ("银行", "非银金融", "消费", "成长", "其他"):
        assert INDUSTRY_PROFILES[b]["eps_method"] == "normalized"


def test_bank_roe_benchmark_lower():
    """银行 roe_benchmark=11（结构性低 ROE，子分不再以 15 满分）。"""
    assert INDUSTRY_PROFILES["银行"]["roe_benchmark"] == 11.0
    assert INDUSTRY_PROFILES["非银金融"]["roe_benchmark"] == 12.0
    assert INDUSTRY_PROFILES["其他"]["roe_benchmark"] == 15.0


# -- 先期落地的 P2/P3a 常量 ----------------------------------------------

def test_forward_constants_landed():
    """P1 先落地、供 P2/P3a 使用的常量已就位。"""
    assert DCF_GROWTH_CAGR_CLIP == (-0.05, 0.12)
    assert MIN_PASSING_YEARS == 3
    assert INDUSTRY_INFO_TTL_HOURS == 720


def test_sw_to_bucket_covers_major_industries():
    """SW_TO_BUCKET 覆盖计划约定的一级行业（抽样校验）。"""
    # 银行
    assert SW_TO_BUCKET["银行"] == "银行"
    # 非银金融子行业
    for sub in ("证券", "保险", "多元金融"):
        assert SW_TO_BUCKET[sub] == "非银金融"
    # 消费
    for sub in ("食品饮料", "家用电器", "商业贸易", "纺织服装", "农林牧渔", "医药生物"):
        assert SW_TO_BUCKET[sub] == "消费"
    # 周期
    for sub in ("采掘", "钢铁", "有色金属", "化工", "建筑材料", "建筑装饰", "交通运输"):
        assert SW_TO_BUCKET[sub] == "周期"
    # 成长
    for sub in ("电子", "计算机", "传媒", "通信", "电气设备", "汽车"):
        assert SW_TO_BUCKET[sub] == "成长"
