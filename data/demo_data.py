# -*- coding: utf-8 -*-
"""
Demo / Mock 数据生成器 — 无需联网即可运行完整分析流程。

用于：
  - 在无网络或 AkShare 接口异常时验证分析逻辑
  - 单元测试与快速演示
  - CI 环境下的端到端验证

所有数据基于平安银行（000001）的典型财务特征生成，
并非真实数据，仅用于功能验证。
"""
import numpy as np
import pandas as pd

from config import (
    STOCK_CODE, START_DATE, END_DATE,
    FIN_START, FIN_END,
)


def generate_daily_data(
    symbol: str = STOCK_CODE,
    start: str = START_DATE,
    end: str = END_DATE,
    seed: int = 42,
) -> pd.DataFrame:
    """
    生成日频交易数据（前复权）。
    用几何布朗运动模拟股价，基价 ~14 元（平安银行近年区间）。
    """
    np.random.seed(seed)
    dates = pd.bdate_range(start=start, end=end, freq="B")

    n = len(dates)
    base_price = 14.0
    annual_vol = 0.25
    daily_vol = annual_vol / np.sqrt(250)
    drift = 0.04 / 250

    # 几何布朗运动
    log_returns = np.random.normal(drift, daily_vol, n)
    price = base_price * np.exp(np.cumsum(log_returns))
    price = np.clip(price, 6, 35)

    close = np.round(price, 2)
    # 开盘/最高/最低围绕收盘随机波动
    open_p = close + np.round(np.random.uniform(-0.15, 0.15, n), 2)
    high_p = np.maximum(close, open_p) + np.round(np.random.uniform(0, 0.2, n), 2)
    low_p = np.minimum(close, open_p) - np.round(np.random.uniform(0, 0.2, n), 2)
    low_p = np.maximum(low_p, 1)
    volume = np.random.randint(5e6, 5e7, n)
    amount = close * volume * np.random.uniform(0.95, 1.05, n)

    df = pd.DataFrame({
        "日期": dates,
        "开盘": open_p,
        "收盘": close,
        "最高": high_p,
        "最低": low_p,
        "成交量": volume,
        "成交额": np.round(amount, 0),
    })
    return df


def generate_financial_abstract(
    symbol: str = STOCK_CODE,
    start_year: int = FIN_START,
    end_year: int = FIN_END,
    seed: int = 43,
    quality: float = 1.0,
) -> pd.DataFrame:
    """
    生成财务摘要数据。
    模拟平安银行 2021-2025 年核心指标：
      ROE ~11-13%（银行股偏低但稳健）
      资产负债率 ~90-93%（银行典型）
      经营现金流 ~1200-1600 亿
      净利润 ~1100-1300 亿
      归母权益 ~18000-21000 亿
      总股本 ~197.56 亿股

    quality（默认 1.0）：按标的"质量因子"等比缩放 ROE/净利/OCF/权益（资产负债率
    与总股本不缩放——前者行业结构性、后者口径稳定），用于回测 demo 制造截面
    分散。quality=1.0 时与历史口径逐字节一致（x*1.0==x），保证 main --demo 零回归。
    """
    np.random.seed(seed)
    years = list(range(start_year, end_year + 1))
    rows = []

    for year in years:
        t = (year - start_year)
        roe = (11.5 + t * 0.3 + np.random.uniform(-0.5, 0.5)) * quality
        debt_ratio = 91 + t * 0.2 + np.random.uniform(-0.3, 0.3)
        net_profit = (1150 + t * 20 + np.random.uniform(-30, 30)) * quality
        # OCF scaled for realistic DCF: ~180亿 vs actual ~1300亿
        # DCF per-share val = ~15-20元 (matching simulated price)
        ocf = (180 + t * 12 + np.random.uniform(-8, 8)) * quality
        equity = (18500 + t * 500 + np.random.uniform(-200, 200)) * quality
        total_shares = 197.56e8

        rows.append({
            "报告期": pd.Timestamp(f"{year}-12-31"),
            "加权净资产收益率(%)": round(roe, 2),
            "资产负债率(%)": round(debt_ratio, 2),
            # OCF scaled for realistic DCF per-share valuation (~15-20 yuan)
            "经营活动产生的现金流量净额": int(ocf * 1e8),
            "归属于上市公司股东的净利润": int(net_profit * 1e8),
            "归属母公司股东权益": int(equity * 1e8),
            "总股本": int(total_shares),
        })

    return pd.DataFrame(rows)


def generate_cashflow_detail(
    symbol: str = STOCK_CODE,
    start_year: int = FIN_START,
    end_year: int = FIN_END,
    seed: int = 44,
    quality: float = 1.0,
) -> pd.DataFrame:
    """
    生成现金流量表数据：
      - "购建固定资产..."：资本性支出（CAPEX，用于 FCF = OCF − CAPEX×0.7）
      - "折旧与摊销"：D&A（用于破产清算情景 FCF − D&A）

    quality 等比缩放 CAPEX/D&A（随业务规模变化）；1.0 时与历史口径逐字节一致。
    """
    np.random.seed(seed)
    years = list(range(start_year, end_year + 1))
    rows = []
    for year in years:
        t = year - start_year
        capex = (50 + np.random.uniform(-10, 10)) * quality
        # 折旧+摊销，银行典型 ~32-43 亿/年，随年小幅上升
        da = (35 + t * 1.5 + np.random.uniform(-3, 3)) * quality
        rows.append({
            "报告期": pd.Timestamp(f"{year}-12-31"),
            "购建固定资产、无形资产和其他长期资产支付的现金": int(capex * 1e8),
            "折旧与摊销": int(da * 1e8),
        })
    return pd.DataFrame(rows)


def generate_dividend(
    symbol: str = STOCK_CODE,
    start_year: int = FIN_START,
    end_year: int = FIN_END,
    seed: int = 45,
    quality: float = 1.0,
) -> pd.DataFrame:
    """
    生成分红记录数据，列名匹配 stock_history_dividend_detail 的输出。
    格式约定：
      - "派息"列存储每10股金额（与 stock_history_dividend_detail 一致）
      - 公告日期 = 年报发布年份（即分红年份 + 1）
    平安银行近年每10股派息约 4.0-5.0 元（即每股 0.40-0.50 元）。

    quality 等比缩放每10股派息金额（高质量标的分红更厚）；1.0 时与历史口径逐字节一致。
    """
    np.random.seed(seed)
    years = list(range(start_year, end_year + 1))
    rows = []
    for year in years:
        # 每10股金额（不是每股！）
        div_per_10shares = (4.0 + np.random.uniform(-0.3, 0.7)) * quality
        month = np.random.randint(6, 9)
        day = np.random.randint(5, 25)
        rows.append({
            "公告日期": pd.Timestamp(f"{year + 1}-{month:02d}-{day:02d}"),
            "送股": 0,
            "转增": 0,
            "派息": round(div_per_10shares, 4),
            "配股": 0,
            "除权除息日": pd.Timestamp(f"{year + 1}-{month:02d}-{day + 5:02d}"),
            "除权除息基准日": pd.Timestamp(f"{year + 1}-{month:02d}-{day + 4:02d}"),
            "权益分派进度": "实施",
        })
    return pd.DataFrame(rows)


def generate_market_overview(seed: int = 46) -> pd.DataFrame:
    """
    生成全市场快照数据，仅需"市盈率-动态"列用于 PE 中位数计算。
    模拟 ~5000 只股票，PE 分布均值 ~25，中位数 ~18。
    """
    np.random.seed(seed)
    n = 4800
    # 对数正态分布模拟 PE：中位数 ~18（A 股典型），长右尾
    pe_raw = np.random.lognormal(mean=np.log(18), sigma=0.6, size=n)
    pe_raw = np.clip(pe_raw, 0.5, 500)
    pe = np.round(pe_raw, 2)

    codes = [f"{i:06d}" for i in range(100000, 100000 + n)]
    names = [f"股票{i}" for i in range(n)]
    prices = np.round(np.random.uniform(3, 80, n), 2)

    return pd.DataFrame({
        "代码": codes,
        "名称": names,
        "最新价": prices,
        "市盈率-动态": pe,
    })


def generate_bond_yield_10y() -> float:
    """
    返回近似的 10 年期国债收益率（2025 年 ~2.3%）。
    """
    return 0.023


def generate_market_pe_history(seed: int = 46) -> pd.DataFrame:
    """
    mock 市场历史市盈率序列，对应实盘 fetch_market_pe_history（乐咕·上证主板）。

    约 10 年月频（item 12：扩窗至 2016 起，与 SENTIMENT_HISTORY_DAYS=365*10 对齐），
    围绕 ~18 波动（A 股主板典型），末段趋势性下移至 ~15，使"当前 PE"略低于
    历史均值 → ERP 偏高 → 情绪偏"低估"，与旧 demo 行为一致；用于市场情绪
    ERP 历史分位代码路径的离线验证。固定种子（市场情绪为全市场口径，不应
    按标的差异化）。
    """
    np.random.seed(seed)
    dates = pd.date_range(start="2016-01-01", end=END_DATE, freq="ME")
    n = len(dates)
    pe = 18 + np.sin(np.linspace(0, 4 * np.pi, n)) * 2.5 + np.random.normal(0, 0.4, n)
    pe = pe - np.linspace(0, 3, n)        # 末段趋势性下移
    pe = np.clip(pe, 12, 28)
    return pd.DataFrame({"日期": dates, "市盈率": np.round(pe, 2)})


def generate_bond_yield_history(seed: int = 46) -> pd.DataFrame:
    """
    mock 10 年期国债收益率历史序列，对应实盘 fetch_bond_yield_history。
    约 10 年月频（item 12：与 generate_market_pe_history 同扩至 2016 起），~2.3%
    小幅波动，与 generate_market_pe_history 日期对齐。
    """
    np.random.seed(seed + 1000)           # 与 PE 序列解耦
    dates = pd.date_range(start="2016-01-01", end=END_DATE, freq="ME")
    n = len(dates)
    bond = 0.023 + np.sin(np.linspace(0, 3 * np.pi, n)) * 0.003 + np.random.normal(0, 0.002, n)
    bond = np.clip(bond, 0.018, 0.035)
    return pd.DataFrame({"日期": dates, "国债收益率": np.round(bond, 4)})


def _seed_for(symbol: str, base: int) -> int:
    """由股票代码派生稳定种子（不同标的得到不同但确定的序列，
    便于批量 demo 产生有差异的得分排名；同一标的跨运行稳定）。"""
    return base + (sum(ord(c) for c in str(symbol)) % 1000)


def _quality_for(symbol: str) -> float:
    """由股票代码派生稳定"质量因子" q（回测 demo 专用，截面分散用）。

    q ∈ [0.85, 1.35]：高质量标的 ROE/净利/OCF/PE 等比上移，低质量下移，
    与时间趋势、价格 GBM 共同制造 A/B/C/D 等级分散，使回测 demo 有意义。
    同一标的跨运行稳定（确定性、按种子派生，非随机）。
    """
    return 0.85 + (sum(ord(c) for c in str(symbol)) % 100) / 200.0


def generate_stock_indicator(
    symbol: str = STOCK_CODE,
    start: str = START_DATE,
    end: str = END_DATE,
    seed: int = 47,
    quality: float = 1.0,
) -> pd.DataFrame:
    """
    生成个股历史估值指标（PE / PB / 股息率），用于真实的历史分位计算。
    模拟平安银行（银行股典型低估值）：PE ~4–6，PB ~0.5–0.7。

    quality 等比缩放 PE/PB 中枢与 clip 上下界（高质量标的估值中枢上移，
    估值更贵 → 估值/情绪分适度下移，与高 ROE 的质量分对冲，制造 A/B/C/D
    二维分散而非单调全 A）；1.0 时与历史口径逐字节一致。
    """
    np.random.seed(seed)
    dates = pd.bdate_range(start=start, end=end, freq="B")
    n = len(dates)
    # 缓慢漂移 + 噪声，避免分位极端化
    drift = np.linspace(0, 0.4, n)
    pe = np.clip(np.random.normal(4.8, 0.5, n) * quality + drift,
                 3.5 * quality, 7.5 * quality)
    pb = np.clip(np.random.normal(0.55, 0.06, n) * quality + drift / 10,
                 0.4 * quality, 0.95 * quality)
    div = np.clip(np.random.normal(1.3, 0.12, n), 0.8, 2.2)
    return pd.DataFrame({
        "日期": dates,
        "市盈率PE": np.round(pe, 2),
        "市净率PB": np.round(pb, 3),
        "股息率": np.round(div, 2),
    })


# 内置常见 A 股清单，供离线 demo 的名称模糊搜索使用（非全市场，仅演示）。
_DEMO_STOCK_LIST = [
    ("000001", "平安银行"), ("000002", "万科A"), ("000063", "中兴通讯"),
    ("000333", "美的集团"), ("000651", "格力电器"), ("000858", "五粮液"),
    ("002594", "比亚迪"), ("002714", "牧原股份"), ("600000", "浦发银行"),
    ("600036", "招商银行"), ("600276", "恒瑞医药"), ("600309", "万华化学"),
    ("600519", "贵州茅台"), ("600887", "伊利股份"), ("601012", "隆基绿能"),
    ("601166", "兴业银行"), ("601318", "中国平安"), ("601398", "工商银行"),
    ("601888", "中国中免"), ("601899", "紫金矿业"), ("600900", "长江电力"),
    ("601628", "中国人寿"), ("600030", "中信证券"), ("000725", "京东方A"),
    ("300750", "宁德时代"), ("600585", "海螺水泥"), ("000568", "泸州老窖"),
    ("600809", "山西汾酒"), ("002475", "立讯精密"), ("300059", "东方财富"),
]


def generate_stock_list() -> pd.DataFrame:
    """返回内置常见 A 股 代码-名称 列表，供离线名称模糊搜索演示。"""
    return pd.DataFrame(_DEMO_STOCK_LIST, columns=["代码", "名称"])


# 标的 → 申万一级行业名（demo 离线口径，供 generate_industry_info 返回）。
# 覆盖 _DEMO_STOCK_LIST + BATCH_DEMO_LIST 已知行业；未命中 → "其他"。
_DEMO_INDUSTRY = {
    # 银行
    "000001": "银行", "600000": "银行", "600036": "银行", "601166": "银行",
    "601398": "银行",
    # 非银金融
    "601318": "非银金融", "600030": "非银金融", "300059": "非银金融",
    "601628": "非银金融",
    # 消费
    "600519": "食品饮料", "000651": "家用电器", "000858": "食品饮料",
    "600887": "食品饮料", "000568": "食品饮料", "600809": "食品饮料",
    "000333": "家用电器", "600276": "医药生物", "601888": "社会服务",
    "002714": "农林牧渔",
    # 周期
    "601899": "有色金属", "600309": "基础化工", "600585": "建筑材料",
    "600900": "公用事业", "000002": "房地产",
    # 成长
    "000725": "电子", "300750": "电力设备", "002475": "电子", "601012": "电力设备",
    "000063": "通信", "002594": "汽车",
}


def generate_industry_info(symbol: str = STOCK_CODE) -> dict:
    """
    返回 demo 标的的行业归属与总股本，对应实盘 fetch_industry_info。

    总股本沿用 demo fin_abstract 的 197.56e8（平安银行口径），
    与 dcf_valuation 现行兜底一致；行业取 _DEMO_INDUSTRY，未命中 → "其他"。
    source 标 "demo" 以区分实盘 "em"。
    """
    # 局部导入避免 fetcher↔demo_data 循环依赖；复用同一纯映射函数保证口径一致
    from data.fetcher import map_to_industry_bucket
    industry = _DEMO_INDUSTRY.get(symbol)
    bucket = map_to_industry_bucket(industry)
    return {
        "industry": industry,
        "bucket": bucket,
        "total_shares": 197.56e8,
        "source": "demo",
    }


def generate_all_demo_data(ctx=None, *, backtest: bool = False) -> dict:
    """
    一次性生成所有分析步骤所需的模拟数据。
    返回的字典键与主流程中的 fetch_* 函数返回类型一致。

    可选传入 StockContext：按其标的/日期范围生成，并按标的派生种子，
    使批量 demo 的各标的得分存在差异（非真实数据，仅验证逻辑）。

    backtest（默认 False）：True 时生成回测专用的**宽跨度多报告期序列**——
    财务/现金流/分红/PE-PB 跨 year(START_DATE)−5 ~ year(END_DATE) 全期，并按
    标的派生质量因子 quality 缩放，使 D1 截断到任意 as_of 都能取到"该时点已知
    的多期财务"且截面有 A/B/C/D 分散。backtest=False（默认）仍取 FIN_START..FIN_END
    最近若干年、quality=1.0，与 main --demo 口径逐字节一致（零回归）。
    """
    sym = ctx.symbol if ctx is not None else STOCK_CODE
    start = ctx.start_date if ctx is not None else START_DATE
    end = ctx.end_date if ctx is not None else END_DATE

    if backtest:
        # 宽跨度财务期：覆盖日线区间 + PIT 披露滞后缓冲（earliest 调仓日仍有多期年报）
        fin_start = int(START_DATE[:4]) - 5          # 2011
        fin_end = int(END_DATE[:4])                 # 今年
        quality = _quality_for(sym)                  # 按标的派生质量因子
    else:
        fin_start, fin_end, quality = FIN_START, FIN_END, 1.0

    return {
        "daily_df":     generate_daily_data(sym, start, end, seed=_seed_for(sym, 42)),
        "fin_abstract": generate_financial_abstract(sym, fin_start, fin_end,
                                                   seed=_seed_for(sym, 43), quality=quality),
        "cashflow_df":  generate_cashflow_detail(sym, fin_start, fin_end,
                                                seed=_seed_for(sym, 44), quality=quality),
        "dividend_df":  generate_dividend(sym, fin_start, fin_end,
                                         seed=_seed_for(sym, 45), quality=quality),
        "market_df":    generate_market_overview(seed=46),
        "bond_yield":   generate_bond_yield_10y(),
        "stock_indicator": generate_stock_indicator(sym, start, end,
                                                   seed=_seed_for(sym, 47), quality=quality),
        # 市场情绪历史：市场口径（非按标的差异化），固定种子
        "market_pe_history":  generate_market_pe_history(seed=46),
        "bond_yield_history":  generate_bond_yield_history(seed=46),
        # 行业归属与总股本（demo 口径，对应实盘 fetch_industry_info）
        "industry_info":  generate_industry_info(sym),
    }


def generate_benchmark_daily(
    symbol: str = "000300",
    start: str = START_DATE,
    end: str = END_DATE,
    seed: int = 99,
) -> pd.DataFrame:
    """
    生成确定性模拟基准日线（沪深 300 口径），对应实盘 fetch_benchmark_daily。

    与 demo 标的日线同种子流派（_seed_for 之外的固定种子 99，与市场口径一致——
    基准为全市场口径不应按标的差异化），GBM 基价 ~3500（沪深 300 近年区间），
    输出与 fetch_daily_data 同构的 [日期, 收盘] 表。供 --backtest-demo 离线可复现。
    """
    np.random.seed(seed)
    dates = pd.bdate_range(start=start, end=end, freq="B")
    n = len(dates)
    base_price = 3500.0
    annual_vol = 0.18
    daily_vol = annual_vol / np.sqrt(250)
    drift = 0.06 / 250
    log_returns = np.random.normal(drift, daily_vol, n)
    price = base_price * np.exp(np.cumsum(log_returns))
    price = np.clip(price, 1500, 6500)
    return pd.DataFrame({
        "日期": dates,
        "收盘": np.round(price, 2),
    })