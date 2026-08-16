# -*- coding: utf-8 -*-
"""
名称/代码模糊搜索测试：精确名称、名称片段、精确代码、错字近似、无匹配。
"""
from data import generate_stock_list, search_stocks


def test_search_exact_name():
    lst = generate_stock_list()
    m = search_stocks("平安银行", lst)
    assert m and m[0][0] == "000001" and m[0][1] == "平安银行"


def test_search_partial_name():
    """名称片段：茅台 → 贵州茅台。"""
    lst = generate_stock_list()
    m = search_stocks("茅台", lst)
    assert m and m[0][0] == "600519"


def test_search_exact_code():
    lst = generate_stock_list()
    m = search_stocks("000001", lst)
    assert m and m[0][0] == "000001" and m[0][2] == 100.0


def test_search_code_prefix():
    """代码前缀：600 → 返回若干 6 开头的股票。"""
    lst = generate_stock_list()
    m = search_stocks("6005", lst)
    assert m and all(c.startswith("6005") for c, _, _ in m)


def test_search_fuzzy_typo():
    """错字近似：贵州茅苔 → 贵州茅台。"""
    lst = generate_stock_list()
    m = search_stocks("贵州茅苔", lst)
    assert m and m[0][0] == "600519"


def test_search_no_match():
    lst = generate_stock_list()
    assert search_stocks("zzzzzzzzz", lst) == []


def test_search_empty_query():
    lst = generate_stock_list()
    assert search_stocks("", lst) == []
    assert search_stocks("  ", lst) == []
