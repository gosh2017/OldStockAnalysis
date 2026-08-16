# 量化价值投资分析系统

基于 **AkShare** 的 A 股长线价值投资量化分析工具。按"基本面筛选 → DCF 估值 → 市场情绪 → 综合建议"四步逻辑评估标的，并在此基础上扩展了**综合评分、DCF 敏感性、个股自身估值分位、HTML 报告、批量选股**等能力。

## 项目结构

```
OldStockAnalysis/
├── main.py                  # 主入口：编排四步分析 + 评分 + 报告 + 批量
├── app.py                   # Streamlit Web 仪表盘（交互式）
├── config.py                # 全局配置 + StockContext 上下文（贯穿调用链）
├── conftest.py              # pytest 根级配置与 fixtures
├── requirements.txt
├── utils/
│   ├── helpers.py           # 重试、会话注入、列名模糊匹配、股息率估算、ERP 模拟
│   └── stats.py             # 分位数计算（scipy 优先，numpy 兜底）
├── data/
│   ├── fetcher.py           # AkShare 数据拉取（日线/财务/现金流/分红/市场PE/国债/个股PE-PB）
│   └── demo_data.py         # 离线模拟数据（seeded，按标的派生种子支持批量差异化）
├── analysis/
│   ├── step1_fundamental.py # 基本面筛选（可配置阈值 ROE/股息率/覆盖年数）
│   ├── step2_dcf.py         # DCF 三情景估值 + 敏感性网格
│   ├── step3_sentiment.py   # 股债性价比 + 个股 PE/PB 自身历史分位
│   ├── step4_advice.py      # 综合投资建议
│   └── scoring.py           # 综合评分系统（0–100 / A–D）
├── visualization/
│   ├── charts.py            # 估值走势图（matplotlib/plotly）+ 敏感性热力图
│   └── report.py            # 自包含 HTML 投资报告
├── tests/                   # pytest：评分/DCF 数学/分位数/筛选逻辑
├── charts/                  # 输出图表（gitignore）
└── reports/                 # 输出 HTML 报告（gitignore）
```

## 功能概述

| 模块 | 内容 |
|------|------|
| 第一步 基本面 | ROE / 股息率 / 资产负债率 / 经营现金流-净利润比；阈值与覆盖年数可配置 |
| 第二步 DCF | 自由现金流折现三情景估值（保守/中性/乐观）+ 增长率×WACC 敏感性网格 |
| 第三步 情绪 | 市场历史 PE（乐咕）+ 10Y 国债历史 → 股债性价比（ERP）历史分位；个股自身 PE/PB 历史分位 |
| 第四步 建议 | 价格 vs 三情景估值区间 → 操作建议（可被市场情绪微调）|
| 综合评分 | 质量(40%)+估值(35%)+情绪(25%) → 0–100 分 + A–D 等级 |
| 批量选股 | 多标的逐只打分，按评分降序排名 |
| Web 仪表盘 | `streamlit run app.py`：交互式输入标的、查看估值图/敏感性热力图/评分；批量排名支持 Demo 与在线（自定义标的逐只联网打分） |
| 名称模糊搜索 | 直接输入名称（平安银行/茅台/平安）自动解析为代码；代码或名称均可，支持片段与错字近似 |

### 综合评分方法论

总分 = 质量 × 0.40 + 估值 × 0.35 + 情绪 × 0.25（权重见 `config.SCORE_WEIGHTS`，可调）。

- **质量**：ROE 水平（20%→100 分）/ ROE 稳定性 / 股息率（4%→100 分，乘分红连续性）/ 经营现金流-净利润比（≥1→100）；资产负债率默认权重 0（行业差异大，如银行 ~90%）。
- **估值**：相对保守/中性估值的安全边际（+50% 上行→100 分，-50%→0 分）；DCF 无法估值时记 0。
- **情绪**：市场 ERP 历史分位（基于乐咕市场历史 PE + 国债历史序列；高=便宜=高分）+ 个股 PE/PB 分位（低分位=便宜=高分，取 100−分位）。

任一子指标数据缺失时，**在所属类别内丢弃其权重并重新归一化**，保证数据不全的标的仍能得到稳定可比的分数。等级：A≥80 / B 65–79 / C 50–64 / D<50。

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

# 自定义基本面年份范围与输出目录
python main.py --demo --years 2020 2024 --out-dir output

# 启动 Web 仪表盘（交互式，浏览器内分析）
streamlit run app.py
```

### CLI 参数

| 参数 | 说明 |
|------|------|
| `symbol` | 股票代码（默认 000001） |
| `-n / --name` | 股票名称（用于标题） |
| `--demo` | 离线模拟数据模式 |
| `--report` | 生成自包含 HTML 投资报告 |
| `--no-chart` | 不生成图表文件 |
| `--out-dir DIR` | 输出目录（图表/报告存于其下 charts/、reports/） |
| `--years START END` | 基本面年份范围（默认 2021 2025） |
| `--batch FILE` | 批量选股：读取 `代码,名称` 文本文件逐只打分 |
| `--batch-demo` | 批量选股 demo：内置标的 + 模拟数据 |

## 配置

修改 [config.py](config.py) 可自定义全部行为：标的、日期范围、DCF 参数与三情景、敏感性网格、筛选阈值（`ROE_THRESHOLD` / `DIV_THRESHOLD` / `MIN_COVERAGE_YEARS`）、评分权重与子权重、等级分档、输出目录。CLI 的 `--years` / `--out-dir` 等可在运行时覆盖。

## 输出

- 终端：各步骤分析结果 + 综合评分 + 最终摘要表
- `charts/valuation_<代码>.png`：股价 vs 三情景内在价值走势（matplotlib）
- `charts/valuation_<代码>.html`：交互式估值图（Plotly，可选）
- `charts/sensitivity_<代码>.png`：DCF 敏感性热力图（增长率×WACC，含现价等值线）
- `reports/report_<代码>_<日期>.html`：自包含 HTML 投资报告（`--report`）

## 测试

```bash
pip install pytest
python -m pytest -q
```

覆盖：综合评分等级/重归一化/边际单调、DCF 公式与三情景单调、分位数 numpy/scipy 语义、筛选阈值与覆盖年数逻辑。

## 依赖

Python 3.9+ · akshare · pandas · numpy · matplotlib · scipy（分位数，未装自动回退 numpy）· plotly（交互图与仪表盘）· streamlit（Web 仪表盘）· pytest（测试）

## 已知限定

- **总股本兜底**：`_get_total_shares` 最后兜底值 197.56 亿仅对 000001 平安银行成立；实盘应依赖日频 `outstanding_share` 或财务摘要中的总股本，分析其他标的时若前两条取不到则每股估值会有偏差。
- **市场情绪分位**：当前 ERP 与"历史市场 PE + 国债历史"对齐得到的历史 ERP 序列比分位（取代旧合成 mock 分布，避免分位恒定无意义）。数据源为乐咕 `stock_market_pe_lg`（akshare 1.17.85 源码确认返回 `[date, close, pe]` 全历史）+ `bond_china_yield`。两者取数失败时回退合成分布 + 默认 PE（demo 亦走此回退路径，仅验证逻辑）。在线路径本环境无网未 runtime 验证，需联网复验。
- **demo 数据**：`--demo` 与 `--batch-demo` 使用按标的派生种子的模拟数据，仅用于验证逻辑，**非真实行情**；非 000001 标的亦为 000001 形态（银行股典型），已知简化。
- **AkShare 版本**：实测 1.17.85；`stock_a_indicator_lg` / `stock_market_pe_lg` 等接口漂移时各函数会降级到 demo/默认值。
- **数据缓存**：实盘股票列表（24h）、个股 PE/PB 历史（12h，按 symbol 分文件）、市场历史 PE（24h）、国债收益率历史（24h）已落盘到 `.cache/`（pickle；取数失败/None 不落盘，避免把瞬时失败固化成空缓存）。离线环境无法验证 TTL 命中/过期行为，缓存正确性留待联网验证。

> 本工具仅供学习与研究参考，不构成投资建议。
