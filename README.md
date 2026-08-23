# 量化价值投资分析系统

基于 **AkShare** 的 A 股长线价值投资量化分析工具。按"基本面筛选 → DCF 估值 → 市场情绪 → 综合建议"四步逻辑评估标的，并在此基础上扩展了**行业分桶、CAGR 推导显性增长、综合评分、PB-ROE 独立锚、数据完整度置信度、HTML 报告、批量选股**等能力。

## 项目结构

```
OldStockAnalysis/
├── main.py                  # 主入口：编排四步分析 + 评分 + 报告 + 批量
├── app.py                   # Streamlit Web 仪表盘（交互式）
├── config.py                # 全局配置 + 行业画像 + StockContext 上下文（贯穿调用链）
├── conftest.py              # pytest 根级配置与 fixtures
├── requirements.txt
├── utils/
│   ├── helpers.py           # 重试、会话注入、列名模糊匹配、股息率估算、ERP 模拟
│   └── stats.py             # 分位数计算（scipy 优先，numpy 兜底）
├── data/
│   ├── fetcher.py           # AkShare 数据拉取（日线/财务/现金流/分红/市场PE/国债/个股PE-PB/行业归属+总股本/股票列表/基准指数）
│   ├── demo_data.py         # 离线模拟数据（seeded，按标的派生种子支持批量差异化；backtest 宽跨度多报告期序列）
│   └── pit.py               # 时点数据截断层（truncate / 披露滞后过滤 / as_of_bundle，回测专用）
├── analysis/
│   ├── step1_fundamental.py # 基本面筛选（可配置阈值 ROE/股息率/覆盖年数）
│   ├── step2_dcf.py         # DCF 三情景估值（行业化参数）+ 敏感性网格 + CAGR 显性增长率推导
│   ├── step3_sentiment.py   # 股债性价比（10年ERP历史分位）+ 个股 PE/PB 自身历史分位
│   ├── step4_advice.py      # 综合投资建议
│   ├── scoring.py           # 综合评分系统（0–100 / A–D）+ 完整度置信度
│   └── backtest.py          # 历史回测引擎（analyze_as_of / run_backtest / compute_metrics）
├── visualization/
│   ├── charts.py            # 估值走势图（matplotlib/plotly）+ 敏感性热力图
│   ├── report.py            # 自包含 HTML 投资报告
│   └── backtest_charts.py   # 回测图表（净值曲线 / 水下回撤 / 等级前向收益柱状）
├── tests/                   # pytest：评分/DCF 数学/分位数/筛选逻辑/行业分桶/回测引擎与PIT截断
├── charts/                  # 输出图表（gitignore）
└── reports/                 # 输出 HTML 报告（gitignore）
```

## 功能概述

| 模块 | 内容 |
|------|------|
| 第一步 基本面 | ROE / 股息率 / 资产负债率 / 经营现金流-净利润比；阈值与覆盖年数可配置 |
| 第二步 DCF | 自由现金流折现三情景估值（破产清算/保守/中性）+ 敏感性网格；**参数随行业桶差异化**（WACC/永续/EPS 算法/capex 兜底比例）；中性显性增长率由净利**最小二乘** CAGR 推导；wacc≤永续时跳过该情景；合理估值上限用「5年PE中位数×当前EPS」封顶 |
| 第三步 情绪 | 市场历史 PE（乐咕）+ 10Y 国债历史 → 股债性价比（ERP）**近 10 年历史分位**；个股自身 PE/PB **近 5 年滚动窗口**历史分位 |
| 第四步 建议 | 价格 vs 破产清算/保守/合理估值上限 → 操作建议（可被市场情绪微调）|
| 行业分桶 | 申万一级行业 → 6 桶（银行/非银金融/消费/周期/成长/其他），各桶差异化 DCF 参数 / ROE 基准 / 评分权重；金融桶跳过资产负债率与经营现金流口径 |
| 综合评分 | 质量(40%)+估值(35%)+情绪(25%) → 0–100 分 + A–D 等级；估值含 PB-ROE 独立锚；**完整度温和折让**（数据稀疏标的不再虚高） |
| 完整度置信度 | 覆盖/DCF数据/股息来源/ERP来源 → 高/中/低 标签，并作为综合评分折让因子，标注结果可信度 |
| 批量选股 | 多标的逐只打分，按评分降序排名 |
| 历史回测 | 验证信号历史有效性：`analyze_as_of`（时点数据注入复用四步+评分，不改算法）→ `run_backtest`（调仓/选股/日频净值/换仓成本）→ `compute_metrics`（CAGR/波动/回撤/Sharpe/Alpha/Beta）；`grade_forward_returns` 验证"A/B 是否跑赢 D"。准 PIT 截断（`data/pit.py`）避免未来函数 |
| Web 仪表盘 | `streamlit run app.py`：交互式输入标的、查看估值图/敏感性热力图/评分；批量排名支持 Demo 与在线；**历史回测 tab**（调仓参数可调，净值/回撤/等级前向收益图 + 业绩 KPI + 持仓表 + 信号结论） |
| 名称模糊搜索 | 直接输入名称（平安银行/茅台/平安）自动解析为代码；代码或名称均可，支持片段与错字近似 |

### 行业分桶

不同行业结构性地该用不同的 DCF 参数、ROE 基准与评分口径。系统先通过 `fetch_industry_info`（akshare `stock_individual_info_em`）取申万一级行业名 + 总股本，经 `SW_TO_BUCKET` 映射到 6 桶，再用 `INDUSTRY_PROFILES` 的画像驱动下游：

| 桶 | WACC | β | 中性永续 | ROE 基准 | EPS 算法 | 金融桶 |
|----|------|---|----------|----------|----------|--------|
| 银行 | 9.5% | 1.20 | 1.5% | 11% | normalized | 是 |
| 非银金融 | 9.5% | 1.20 | 1.5% | 12% | normalized | 是 |
| 消费 | 9.0% | 1.12 | 2.0% | 15% | normalized | 否 |
| 周期 | 10.0% | 1.28 | 1.0% | 12% | **shiller**（10 年净利平滑） | 否 |
| 成长 | 8.5% | 1.03 | 2.5% | 15% | normalized | 否 |
| 其他 | 9.5% | 1.20 | 1.5% | 15% | normalized | 否 |

WACC 列为 Rf=2.3%（`RISK_FREE_REFERENCE`）校准锚下的静态兜底值；实盘动态 WACC = Rf + β × ERP（见下文「折现率 WACC」）。

- **DCF 参数**：`scenarios_for(bucket)` 按桶构造三情景；保守/破产清算恒为 0 增长 0 永续，中性永续取行业值，显性增长率由 `derive_explicit_growth`（对 `log(净利润)` 最小二乘拟合，clip 至 `[-5%, 12%]`）覆盖。
- **总股本**：行业信息（EM f84）为优先来源，其次日频 `outstanding_share`、财务摘要；三源全失败返回 `None` 并跳过 DCF（不再用 197.56 亿硬兜底，避免对非 000001 标的错估每股价值）。
- **行业归属获取失败**：回退 `{"bucket": "其他", ...}`，即等价于现行全局口径，零回归。
- 行业信息落盘缓存（`INDUSTRY_INFO_TTL_HOURS`，月级稳定，按 symbol 分文件）；失败结果不落盘。
- **行业结构性假设**：`INDUSTRY_PROFILES` 各桶另含 `capex_ratio`（capex 取不到时按 OCF 的该比例估算维持性支出，重资产周期高、轻资产消费/金融低）与 `payout_ratio`（无真实分红记录时的分红率假设，成长低、消费高），集中配置于 `config.py`。

### 综合评分方法论

总分 = 质量 × 0.40 + 估值 × 0.35 + 情绪 × 0.25（权重见 `config.SCORE_WEIGHTS`，可调）。

- **质量**：ROE 水平（按桶 `roe_benchmark` 归一，基准→100 分）/ ROE 稳定性（变异系数 CV × 水平调制 `min(1, ROE 均值/基准)`，"稳定地差"不再虚高）/ 股息率（4%→100 分，乘分红连续性）/ 经营现金流-净利润比（≥1→100，**取中位数**抗亏损年极值）/ 资产负债率（**取近 1 年值**，反映当前杠杆而非历史峰值）。**按桶差异化**：金融桶跳过资产负债率与经营现金流口径（结构不可比），非金融桶资产负债率权重提至 0.20（`SCORE_QUALITY_W_BY_BUCKET`）。
- **估值**：相对保守/中性估值的安全边际（+50% 上行→100 分，-50%→0 分，权重 0.40+0.30）+ **PB-ROE 独立锚**（公允 PB = ROE 均值/15，实际 PB 低于公允→高分，权重 0.30）。DCF 无法估值时记 0；PB-ROE 锚不依赖 DCF，`current_pb` 或 ROE 缺失时类内重归一回退纯安全边际权重。
- **情绪**：市场 ERP 历史分位（基于乐咕市场历史 PE + 国债历史序列；高=便宜=高分，权重 0.40）+ 个股 PE/PB 分位（低分位=便宜=高分，取 100−分位，权重 0.30+0.30）。

任一子指标数据缺失时，**在所属类别内丢弃其权重并重新归一化**，保证数据不全的标的仍能得到稳定可比的分数。归一后再按**完整度温和折让**：`score × (0.70 + 0.30 × 完整度/100)`（`SCORE_COMPLETENESS_PENALTY`，完整度 100→×1.0、0→×0.70），避免数据稀疏标的因重归一而虚高；终端打印 `×{factor}` 透明可见。等级：A≥80 / B 65–79 / C 50–64 / D<50。

**完整度置信度**（供展示层标注，并作为综合评分折让因子）：覆盖度 30% + DCF 数据可得性 30% + 股息真实来源占比 20% + ERP 来源可信度 20%（real 100 / real_partial 60 / synthetic 20）。≥80 高 / ≥50 中 / <50 低。完整度同时驱动上文的温和折让。

### DCF 估值口径（行业化）

- **自由现金流**：FCF = 经营现金流净额 − 资本性支出 × 0.7（仅 70% capex 视为维持性支出）。capex 缺失年按行业 `capex_ratio`（OCF 的该比例）兜底。基期 FCF 取近 5 年加权均值（近年权重大，含负值不剔除）。
- **折现率 WACC（利率联动）**：`WACC = Rf + β × ERP`。`Rf` 取实时 10Y 国债收益率（`risk_free`，main/backtest 传 `bond_yield`，PIT 正确）；`ERP = EQUITY_RISK_PREMIUM` = 6%（A 股长期股权风险溢价中位数，独立于 step3 情绪 ERP——后者 = 1/PE−Rf≈3% 是「便宜-vs-债券」指标、非资本成本输入）；`β` 取各桶 `INDUSTRY_PROFILES[bucket]["beta"]`（见上表）。β 校准使 Rf = `RISK_FREE_REFERENCE`（2.3%，现行 10Y）时动态值 == 上表 WACC 静态兜底值（零回归），Rf 漂移时 WACC 随 β 线性漂移；`risk_free` 不可得时回退静态 WACC。三情景同 WACC，不再随情景变化。`dcf` 返回 dict 含 `wacc_basis`（mode/risk_free/beta/erp/wacc），终端打印分解式。
- **三情景**：保守（0% 永续）/ 中性（行业永续 + CAGR 推导的显性增长率）/ 破产清算（0 增长且折旧摊销不计入）。保守/破产清算恒为 0 增长 0 永续。`wacc ≤ 永续增长`时跳过该情景（`intrinsic_value=None`），不产生负/无穷内在价值。
- **破产清算**：现金流 = FCF − 折旧摊销（移除非现金加回项），为估值底值；D&A 不可得时按 FCF×0.5 估算清算口径（与全空时一致，自然满足 `破产清算 ≤ 保守`，事后钳位降为 inert 安全网）。
- **合理估值上限**：`min(中性 DCF, 过去 5 年 PE 中位数 × 当前 EPS)`，作为估值天花板，避免 DCF 对成熟股过度外推。EPS 按行业 `eps_method` 取：normalized（近 5 年净利均）/ shiller（周期股近 10 年净利平滑峰谷）。低 PE 股（银行/周期）的 PE 锚定常低于 DCF 内在价值，此时天花板压低保守/破产清算以保完整阶梯。
- **敏感性**：永续增长率（行）× WACC（列）网格，每格显性增长率 = 该行永续增长率。

### 历史回测验证

回测层以"数据注入"方式复用 step1–4 / scoring，**不改其算法与权重**，验证综合评分信号在历史上是否有效。核心流程：`run_backtest` 在每个调仓日 T 对每标的调 `analyze_as_of`（用 `data/pit.py` 截断到 ≤ T 的准 PIT 数据，避免未来函数）得 score/grade/recommendation，按 `min_grade` + `top_n` 选股、等权或得分加权持有，日频 mark-to-market 记净值、扣双边换仓成本；`grade_forward_returns` 把**全部**标的按等级分桶记 hold 期前向收益，`compute_metrics` 算 CAGR/波动/最大回撤/Sharpe/胜率/Alpha/Beta。

- **时点完整性（准 PIT）**：AkShare 财务/现金流接口返回全历史且常含重述后数据，回测按"截止 as-of 日 T"显式截断所有输入序列（`truncate_to_date`），财报另按"报告期 + 披露滞后 120d"过滤（`filter_reports_by_pub_lag`，避免把未披露年报当已知）。**非严格历史可得**——重述/幸存者偏差见「已知限定」。
- **可配置**：`config.BACKTEST_*` 集中调仓频率（M/Q/Y）、持有期、top_n、最低等级、权重、交易成本、基准、回溯年数、披露滞后、无风险利率。
- **离线可复现**：`--backtest-demo` 用 seeded 模拟数据（跨 ~2011–今宽跨度多报告期 + 按标的派生 quality 因子制造 A/B/C/D 截面分散），全程无网；`--backtest FILE` 为实盘联网路径。

## 使用方法

```bash
# 安装依赖
pip install -r requirements.txt

# 离线演示（无需网络，使用模拟数据）
python main.py --demo

# 指定标的（实盘，需联网）
python main.py 600519 -n 贵州茅台

# 直接输入股票名称（自动解析为代码；支持模糊搜索）
python main.py --demo 平安银行      # 精确名称 → 000001
python main.py --demo 茅台          # 名称片段 → 贵州茅台(600519)
python main.py --demo 平安          # 歧义时列出候选供选择

# 生成自包含 HTML 投资报告
python main.py --demo --report

# 批量选股打分（实盘：传入 代码,名称 文本文件）
python main.py --batch stocks.txt

# 批量选股 demo（内置 5 只标的 + 模拟数据）
python main.py --batch-demo

# 历史回测 demo（内置标的 + 模拟数据，全程无网验证回测机制）
python main.py --backtest-demo
python main.py --backtest-demo --years 2018 2024 --no-chart   # 指定回测区间、不生图

# 历史回测实盘（读 代码,名称 清单，需联网）
python main.py --backtest stocks.txt

# 自定义基本面年份范围与输出目录
python main.py --demo --years 2020 2024 --out-dir output

# 启动 Web 仪表盘（交互式，浏览器内分析）
streamlit run app.py
```

### CLI 参数

| 参数 | 说明 |
|------|------|
| `symbol` | 股票代码或名称（如 000001 或 平安银行，名称支持模糊搜索） |
| `-n / --name` | 股票名称（用于标题） |
| `--demo` | 离线模拟数据模式 |
| `--report` | 生成自包含 HTML 投资报告 |
| `--no-chart` | 不生成图表文件 |
| `--out-dir DIR` | 输出目录（图表/报告存于其下 charts/、reports/） |
| `--batch FILE` | 批量选股：读取 `代码,名称` 文本文件逐只打分 |
| `--batch-demo` | 批量选股 demo：内置标的 + 模拟数据 |
| `--backtest FILE` | 历史回测：读取 `代码,名称` 文本文件验证信号历史有效性（实盘联网） |
| `--backtest-demo` | 历史回测 demo：内置标的 + seeded 模拟数据，全程无网验证回测机制 |
| `--years START END` | 基本面年份范围（默认 2021 2025）；回测模式下复用为回测区间 |

## 配置

修改 [config.py](config.py) 可自定义全部行为：标的、日期范围、行业画像（`INDUSTRY_PROFILES` / `SW_TO_BUCKET`）、DCF 参数与三情景、敏感性网格、筛选阈值（`ROE_THRESHOLD` / `DIV_THRESHOLD` / `MIN_COVERAGE_YEARS`）、评分权重与子权重、等级分档、输出目录。CLI 的 `--years` / `--out-dir` 等可在运行时覆盖。

## 输出

- 终端：各步骤分析结果 + 行业归属 + 综合评分 + 完整度标签 + 最终摘要表
- `charts/valuation_<代码>.png`：股价 vs 三情景内在价值走势（matplotlib）
- `charts/valuation_<代码>.html`：交互式估值图（Plotly，可选）
- `charts/sensitivity_<代码>.png`：DCF 敏感性热力图（增长率×WACC，含现价等值线）
- `charts/backtest_equity_backtest.{png,html}`：回测净值曲线（策略 vs 基准，`--backtest*`）
- `charts/backtest_drawdown_backtest.png`：回测水下回撤图（`--backtest*`）
- `charts/backtest_grade_returns_backtest.png`：各等级平均前向收益柱状图（`--backtest*`，验单调性）
- `reports/report_<代码>_<日期>.html`：自包含 HTML 投资报告（`--report`）

## 测试

```bash
pip install pytest
python -m pytest -q
```

覆盖：综合评分（等级/重归一化/完整度折让/ROE 水平调制/OCF 中位数/资产负债率近 1 年）、DCF（三情景单调/wacc≤永续 guard/最小二乘 CAGR/capex 兜底/破产清算口径）、分位数 numpy/scipy 语义、筛选阈值与覆盖年数逻辑、行业分桶映射与画像完整性、年报优先取数与股息率行业化口径、**回测 PIT 截断与披露滞后 / analyze_as_of 时点一致性与 fin 窗口前移 / run_backtest 结构与换仓成本 / compute_metrics 数学（总收益·最大回撤·常数 Sharpe=None·同曲线 Beta≈1）**。

## 依赖

Python 3.9+ · akshare · pandas · numpy · matplotlib · scipy（分位数，未装自动回退 numpy）· plotly（交互图与仪表盘）· streamlit（Web 仪表盘）· pytest（测试）

## 已知限定

- **总股本取数**：优先级为行业信息（EM f84）> 日频 `outstanding_share` > 财务摘要；三源全失败返回 `None` 并跳过 DCF 估值（不输出每股价值），实盘优先依赖行业信息源。demo 模式下所有标的的总股本固定为 197.56 亿（平安银行口径），非真实股本。
- **破产清算 D&A 取数**：折旧摊销从现金流量表取（合并列优先，否则折旧+摊销分列求和）。D&A 不可得时按 FCF×0.5 估算清算口径（不再回退归母净利润），自然满足 `破产清算 ≤ 保守`，事后钳位为 inert 安全网。sina 现金流量表若以行（非列）返回 D&A 致提取为空即走此估算，属可接受降级。离线 demo 已含 D&A 字段验证主路径。
- **合理估值上限锚定**：EPS 取最新归母净利润/总股本；5 年 PE 中位数取自个股历史 PE 末 5 个日历年。两者均取不到时上限退化为中性 DCF（不做封顶）。
- **市场情绪分位**：当前 ERP 与"历史市场 PE + 国债历史"按日期对齐得到的历史 ERP 序列比分位（取代旧合成 mock 分布，避免分位恒定无意义）。数据源为乐咕 `stock_market_pe_lg`（akshare 1.17.85 源码确认返回 `[date, close, pe]` 全历史）+ `bond_china_yield`。两者取数失败时回退合成分布 + 默认 PE（demo 亦走此回退路径，仅验证逻辑）。在线路径本环境无网未 runtime 验证，需联网复验。个股 PE/PB 自身分位用近 5 年滚动窗口（`INDIVIDUAL_PERCENTILE_WINDOW_YEARS`），窗口不足回退全历史。
- **行业分桶**：行业归属依赖 `stock_individual_info_em`（实盘联网）；接口漂移/失败时回退"其他"桶（== 全局口径，零回归）。`SW_TO_BUCKET` 覆盖申万常见一级行业及子行业名兜底，未命中→"其他"。
- **demo 数据**：`--demo` 与 `--batch-demo` 使用按标的派生种子的模拟数据，仅用于验证逻辑，**非真实行情**。行业归属按标的差异化（demo 清单覆盖各桶），但财务/现金流形态仍为银行股近似（000001 口径），已知简化。
- **AkShare 版本**：实测 1.17.85；`stock_a_indicator_lg` / `stock_market_pe_lg` 等接口漂移时各函数会降级到 demo/默认值。
- **数据缓存**：实盘股票列表（24h）、个股 PE/PB 历史（12h，按 symbol 分文件）、市场历史 PE（24h）、国债收益率历史（24h）、行业归属+总股本（720h 月级，按 symbol 分文件）已落盘到 `.cache/`（pickle；取数失败/None 不落盘，避免把瞬时失败固化成空缓存）。离线环境无法验证 TTL 命中/过期行为，缓存正确性留待联网验证。
- **历史回测（三段限定，非严格历史回测）**：
  - **准 PIT**：AkShare 财务/现金流接口返回全历史且常含**重述后**数据，并非严格时点可得。回测按"截止 as-of 日 T"显式截断所有输入序列（`truncate_to_date`），财报另按"报告期 + 披露滞后 120d"过滤（`filter_reports_by_pub_lag`），但无法消除重述偏差——仅为准 PIT 口径，不得宣称"严格历史可得"。
  - **幸存者偏差**：实盘回测标的清单来自当前在市标的（`fetch_stock_list` 即如此），已退市标的不在样本内，系统性高估策略表现。`--backtest-demo` 用模拟数据无此问题但非真实行情。
  - **简化成本**：仅计双边交易费率（`BACKTEST_TXN_COST=0.0005` 单边，换仓 round-trip 2×），未计滑点 / 印花税 / 停牌流动性冲击 / 涨跌停无法成交，净值偏乐观。
  - `fetch_benchmark_daily`（沪深 300）实盘路径本环境无网未 runtime 验证，需联网复验；离线走 `generate_benchmark_daily` 确定性模拟基准。

> 本工具仅供学习与研究参考，不构成投资建议。
