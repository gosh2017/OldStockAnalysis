# -*- coding: utf-8 -*-
"""
全局配置参数 — 所有模块统一从此处读取。

本模块是整个系统的"单一事实源"：标的、日期范围、DCF 参数、筛选阈值、
评分权重、敏感性网格、输出目录，以及贯穿调用链的 StockContext 上下文，
全部集中在此，便于一处修改、全局生效。
"""
import os
from dataclasses import dataclass
from datetime import datetime

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
# 结束日期动态取"今天"，避免硬编码导致数据范围过期
TODAY = datetime.now().strftime("%Y%m%d")
START_DATE = "20160101"        # 日线起始日期
END_DATE   = TODAY            # 日线结束日期（动态）
FIN_START  = 2021             # 基本面起始年份
FIN_END    = 2025             # 基本面结束年份

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

# -- DCF 敏感性分析网格 -----------------------------------
# 在 growth × WACC 网格上扫描每股内在价值，固定永续增长率。
DCF_SENSITIVITY = {
    "growth_range": (0.05, 0.15, 0.01),   # (start, stop, step)
    "wacc_range":   (0.06, 0.12, 0.01),
    "perpetual":    DCF_PERPETUAL,
}

# -- 第一步：基本面筛选阈值（可配置）----------------------
# 判定规则：有数据的年份须"全部达标"，且覆盖年数 ≥ MIN_COVERAGE_YEARS。
ROE_THRESHOLD      = 15.0     # ROE 达标线（%）
DIV_THRESHOLD       = 2.0      # 股息率达标线（%）
MIN_COVERAGE_YEARS  = 4        # 最少可用年数（容忍最近一年未披露完；严格 5 年可改此处）

# -- 综合评分权重 -----------------------------------------
# 总分 = 质量 × 0.40 + 估值 × 0.35 + 情绪 × 0.25
# 任一类内若某子指标缺失，则在该类内丢弃并重新归一化子权重。
SCORE_WEIGHTS = {"quality": 0.40, "valuation": 0.35, "sentiment": 0.25}

SCORE_QUALITY_W = {
    "roe":          0.35,   # ROE 水平：20% → 100 分
    "roe_stability":0.20,   # ROE 稳定性：标准差越小越高
    "dividend":     0.25,   # 股息率：4% → 100 分，乘以分红连续性
    "ocf_quality":  0.20,   # 经营现金流/净利润：≥1 → 100 分
    "debt":         0.00,   # 资产负债率：默认权重 0（行业差异大，如银行 ~90%）
}

SCORE_VALUATION_W = {
    "margin_neutral":     0.55,   # 相对中性估值的安全边际
    "margin_conservative":0.45,   # 相对保守估值的安全边际
}

SCORE_SENTIMENT_W = {
    "market_erp":    0.40,   # 全市场股债性价比分位数（高=便宜=高分）
    "individual_pe": 0.30,   # 个股市盈率分位（低=便宜=高分）
    "individual_pb": 0.30,   # 个股市净率分位（低=便宜=高分）
}

# 等级划分（从高到低匹配，命中即止）
GRADE_BANDS = [("A", 80), ("B", 65), ("C", 50), ("D", 0)]

# -- 输出 -------------------------------------------------
CHART_DIR  = "charts"
REPORT_DIR = "reports"
OUT_DIR    = "output"

# -- 本地磁盘缓存 -----------------------------------------
# 实盘模式下 A 股代码-名称列表、个股 PE/PB 等相对稳定的数据落盘缓存，
# 避免每次启动 streamlit / 关闭 Demo 后都全量联网拉取。失败结果不落盘。
# .cache/ 已在 .gitignore，不会污染仓库。
CACHE_DIR            = ".cache"
STOCK_LIST_TTL_HOURS  = 24   # 股票列表（~5500 只）日级更新足够
STOCK_INDICATOR_TTL_HOURS = 12   # 个股 PE/PB 历史，半日级
MARKET_PE_TTL_HOURS   = 24   # 全市场历史 PE（乐咕，日级更新）
BOND_HISTORY_TTL_HOURS = 24  # 国债收益率历史（日级）

# 市场情绪历史数据源：乐咕乐股主板市盈率（stock_market_pe_lg）。
# akshare 1.17.85 源码确认返回 [date, close, pe] 完整历史序列，结束于当日，
# 一次调用即得"当前市场 PE"+"历史序列"，取代不可靠的 spot 快照与合成 mock。
# 可选：上证 / 深证 / 创业板 / 科创版（"上证"最具主板代表性，最稳定）。
MARKET_PE_BOARD       = "上证"
# 市场情绪历史 ERP 回退天数（与国债历史对齐的窗口）
SENTIMENT_HISTORY_DAYS = 365 * 5   # 近 5 年


# -- 贯穿调用链的上下文（去全局化的地基）------------------
@dataclass(frozen=True)
class StockContext:
    """
    一次分析所需的全部参数，由 _cli() 一次性构建后传递。
    取代散落的 symbol/stock_name/demo 全局引用，使标的、日期、
    输出目录等在整条调用链上保持一致。
    """
    symbol: str
    name: str
    start_date: str = START_DATE
    end_date: str = END_DATE
    fin_start: int = FIN_START
    fin_end: int = FIN_END
    demo: bool = False
    cache: bool = False
    no_chart: bool = False
    report: bool = False
    chart_dir: str = CHART_DIR
    report_dir: str = REPORT_DIR
    out_dir: str = OUT_DIR

    @property
    def stock_label(self) -> str:
        """标的显示标签：名称（代码）"""
        return f"{self.name}（{self.symbol}）"
