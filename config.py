# -*- coding: utf-8 -*-
"""
全局配置参数 — 所有模块统一从此处读取。
"""
import os

# -- 网络 / AkShare 会话配置 ------------------------------
# 设置请求头，避免被远程服务器拦截
# 环境变量 AKSHARE_TIMEOUT 可覆盖默认超时（秒）
AKSHARE_TIMEOUT = int(os.environ.get("AKSHARE_TIMEOUT", "20"))
AKSHARE_RETRIES = int(os.environ.get("AKSHARE_RETRIES", "3"))
AKSHARE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.eastmoney.com/",
}

# -- 标的信息 ---------------------------------------------
STOCK_CODE = "000001"          # 股票代码（平安银行）
STOCK_NAME = "平安银行"        # 股票名称

# -- 数据范围 ---------------------------------------------
START_DATE = "20160101"        # 日线起始日期
END_DATE   = "20260813"        # 日线结束日期
FIN_START  = 2021              # 基本面起始年份
FIN_END    = 2025              # 基本面结束年份

# -- DCF 默认参数（中性情景）------------------------------
DCF_GROWTH    = 0.10           # 未来 5 年增长率
DCF_PERPETUAL = 0.03           # 永续增长率
DCF_WACC      = 0.08           # 加权平均资本成本

# -- 三情景参数 -------------------------------------------
SCENARIOS = {
    "保守 (Conservative)": {"growth": 0.07, "perpetual": 0.02, "wacc": 0.09},
    "中性 (Neutral)":      {"growth": 0.10, "perpetual": 0.03, "wacc": 0.08},
    "乐观 (Optimistic)":   {"growth": 0.13, "perpetual": 0.05, "wacc": 0.07},
}

# -- 输出 -------------------------------------------------
CHART_DIR = "charts"
