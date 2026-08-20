# -*- coding: utf-8 -*-
"""
全局配置参数 — 所有模块统一从此处读取。

本模块是整个系统的"单一事实源"：标的、日期范围、DCF 参数、筛选阈值、
评分权重、敏感性网格、输出目录，以及贯穿调用链的 StockContext 上下文，
全部集中在此，便于一处修改、全局生效。

导航：行业化口径见 README「行业分桶」小节——下方 INDUSTRY_BUCKETS /
SW_TO_BUCKET / INDUSTRY_PROFILES 三者共同决定各桶的 DCF 参数、ROE 基准、
EPS 算法与评分权重差异化；tests/test_industry.py 守护其完整性。
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

# -- DCF 默认参数（成熟期「老登股」适配）------------------
# WACC 固定 9.5%；显性 5 年增长率 = 永续增长率（成熟股无独立高增长期）。
DCF_GROWTH    = 0.015          # 显性 5 年增长率（= 中性永续）
DCF_PERPETUAL = 0.015          # 永续增长率（中性）
DCF_WACC      = 0.095          # 加权平均资本成本（固定）

# -- 三情景参数（成熟期口径）------------------------------
# 删乐观；保守 0% 永续、中性 1.5% 永续、破产清算 0 增长且折旧摊销不计入。
# "liquidation": True 标记该情景用 FCF − D&A 作现金流基（D&A 不可得回退归母净利润）。
SCENARIOS = {
    "保守 (Conservative)":  {"growth": 0.000, "perpetual": 0.000, "wacc": 0.095},
    "中性 (Neutral)":        {"growth": 0.015, "perpetual": 0.015, "wacc": 0.095},
    "破产清算 (Liquidation)": {"growth": 0.000, "perpetual": 0.000, "wacc": 0.095, "liquidation": True},
}

# -- DCF 敏感性分析网格 -----------------------------------
# 在 永续增长率 × WACC 网格上扫描每股内在价值，每格显性期增长率 = 该行永续增长率。
DCF_SENSITIVITY = {
    "perpetual_range": (0.00, 0.03, 0.005),   # (start, stop, step) 行：永续增长率
    "wacc_range":      (0.07, 0.12, 0.005),   # 列：折现率 WACC
}

# -- 行业分桶基础设施（P1 落地，供 P2/P3b 消费）--------------
# 根因修复：不同行业结构性地该用不同的 DCF 参数 / 筛选阈值 / 评分权重。
# 此处先落基础设施（桶定义、申万一级映射、行业画像、缓存 TTL），
# 行业数据获取器（fetch_industry_info）与下游消费在后续阶段接入。
INDUSTRY_BUCKETS = ["银行", "非银金融", "消费", "周期", "成长", "其他"]

# 申万一级行业名 → 桶（含常见子行业名兜底，未命中 → "其他"）
SW_TO_BUCKET = {
    # 银行
    "银行": "银行",
    # 非银金融（含子行业名兜底）
    "非银金融": "非银金融", "证券": "非银金融", "保险": "非银金融", "多元金融": "非银金融",
    # 消费
    "食品饮料": "消费", "家用电器": "消费", "商业贸易": "消费", "纺织服装": "消费",
    "农林牧渔": "消费", "医药生物": "消费", "轻工制造": "消费", "休闲服务": "消费",
    "社会服务": "消费", "美容护理": "消费", "商贸零售": "消费",
    # 周期
    "采掘": "周期", "钢铁": "周期", "有色金属": "周期", "化工": "周期", "基础化工": "周期",
    "建筑材料": "周期", "建筑装饰": "周期", "交通运输": "周期", "房地产": "周期",
    "公用事业": "周期", "煤炭": "周期", "石油石化": "周期",
    # 成长
    "电子": "成长", "计算机": "成长", "传媒": "成长", "通信": "成长",
    "电气设备": "成长", "电力设备": "成长", "汽车": "成长", "机械设备": "成长",
    "国防军工": "成长",
}

# 行业画像：每桶差异化 DCF 参数 + 评分基准。
#   wacc          —— 折现率（成长股低、周期股高）
#   perpetual     —— 中性永续增长率（保守 / 破产清算恒为 0，由 scenarios_for 保证）
#   roe_benchmark —— 评分中 ROE 满分基准（%）：银行 11、非银 12，其余 15
#   is_financial  —— 金融桶：评分跳过资产负债率 / OCF 子分（结构不可比）
#   eps_method    —— 基期 EPS 算法：normalized（近 5 年净利均）/ shiller（周期股平滑）
#   growth_clip   —— 显性增长率 CAGR 裁剪区间（P2 derive_explicit_growth 用）
#   capex_ratio   —— capex 取不到时按 OCF 的该比例估算维持性支出（item A1）。
#                    重资产（周期 0.45）高、轻资产（消费 0.15）低，避免固定 0.20
#                    使重资产股 FCF 虚高、轻资产股 FCF 虚低污染 DCF 基数。
DCF_GROWTH_CAGR_CLIP = (-0.05, 0.12)
INDUSTRY_PROFILES = {
    "银行":    {"wacc": 0.095, "perpetual": 0.015, "roe_benchmark": 11.0, "is_financial": True,  "eps_method": "normalized", "growth_clip": DCF_GROWTH_CAGR_CLIP, "capex_ratio": 0.10},
    "非银金融": {"wacc": 0.095, "perpetual": 0.015, "roe_benchmark": 12.0, "is_financial": True,  "eps_method": "normalized", "growth_clip": DCF_GROWTH_CAGR_CLIP, "capex_ratio": 0.10},
    "消费":    {"wacc": 0.090, "perpetual": 0.020, "roe_benchmark": 15.0, "is_financial": False, "eps_method": "normalized", "growth_clip": DCF_GROWTH_CAGR_CLIP, "capex_ratio": 0.15},
    "周期":    {"wacc": 0.100, "perpetual": 0.010, "roe_benchmark": 12.0, "is_financial": False, "eps_method": "shiller",    "growth_clip": DCF_GROWTH_CAGR_CLIP, "capex_ratio": 0.45},
    "成长":    {"wacc": 0.085, "perpetual": 0.025, "roe_benchmark": 15.0, "is_financial": False, "eps_method": "normalized", "growth_clip": DCF_GROWTH_CAGR_CLIP, "capex_ratio": 0.25},
    "其他":    {"wacc": 0.095, "perpetual": 0.015, "roe_benchmark": 15.0, "is_financial": False, "eps_method": "normalized", "growth_clip": DCF_GROWTH_CAGR_CLIP, "capex_ratio": 0.20},
}

# 行业信息磁盘缓存 TTL（行业归属 + 总股本，月级稳定）
INDUSTRY_INFO_TTL_HOURS = 720

# -- 第一步：基本面筛选阈值（可配置）----------------------
# 判定规则：有数据年份须"中位数达标 + ≥ MIN_PASSING_YEARS 年达标 + 覆盖 ≥ MIN_COVERAGE_YEARS"。
ROE_THRESHOLD      = 15.0     # ROE 达标线（%）
DIV_THRESHOLD       = 2.0      # 股息率达标线（%）
MIN_COVERAGE_YEARS  = 4        # 最少可用年数（容忍最近一年未披露完；严格 5 年可改此处）
MIN_PASSING_YEARS   = 3        # P3a 中数判定：达标年数下限（中位数过线且 ≥N 年达标才 pass）

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

# 行业化质量权重覆盖（item 6）：与 SCORE_QUALITY_W 合并后使用。
# - 非金融桶（其他/消费/周期/成长）：资产负债率仍有横向比较意义，debt 0→0.20，
#   相应下调 roe/dividend；_category_score 会就现存子指标重归一，故无需严格求和为 1。
# - 金融桶（银行/非银金融）：资产负债率结构性偏高（银行 ~90%）无判别力，debt 恒 0；
#   经营现金流/净利润口径对金融业不适用，ocf_quality 置 0 且 _quality_subscores 跳过计算。
SCORE_QUALITY_W_BY_BUCKET = {
    "其他":     {"roe": 0.25, "dividend": 0.15, "debt": 0.20},
    "消费":     {"roe": 0.25, "dividend": 0.15, "debt": 0.20},
    "周期":     {"roe": 0.25, "dividend": 0.15, "debt": 0.20},
    "成长":     {"roe": 0.25, "dividend": 0.15, "debt": 0.20},
    "银行":     {"ocf_quality": 0.00},
    "非银金融": {"ocf_quality": 0.00},
}

SCORE_VALUATION_W = {
    "margin_neutral":     0.40,   # 相对中性估值的安全边际
    "margin_conservative":0.30,   # 相对保守估值的安全边际
    "pb_roe":             0.30,   # PB-ROE 独立锚（item 8）：实际 PB 低于公允 PB → 高分
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
SENTIMENT_HISTORY_DAYS = 365 * 10   # 近 10 年（item 12：扩窗以稳定分位估计）


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
