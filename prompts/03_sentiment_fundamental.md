# 提示词 C — 情绪与基本面口径优化

> 目标受众：编码助手（Claude Code 等）。本文件自包含，可直接作为任务指令执行。

## 角色与目标

你正在改进一个基于 AkShare 的 A 股长线价值投资量化分析系统（Python，模块化）。本次任务**只动市场情绪与基本面口径**（`analysis/step3_sentiment.py`、`analysis/step1_fundamental.py`、`analysis/step2_dcf.py` 的取数时点、`utils/helpers.py` 的股息率估算、`config.py` 对应配置、相关测试），目的是修正 3 处"口径不一致或硬编码"导致的合理性偏差。**不要改 DCF 估值公式与评分加权逻辑。**

项目背景：市场情绪用"股债性价比 ERP 近 10 年历史分位 + 个股自身 PE/PB 历史分位"；基本面筛选用 ROE/股息率/资产负债率/OCF 口径，阈值可配置。代码风格：中文 docstring、纯函数、配置集中。

## 任务清单（按重要性排序）

### C1. 个股 PE/PB 分位改用滚动 5 年窗口，与市场 ERP 口径统一

- **现状**：`analysis/step3_sentiment.py:187-201`，个股 PE/PB 分位用 `pe_s.tolist()` 全历史序列求分位。若股票估值中枢长期下移（如银行 PE 从 20→5），当前 5 倍在全历史里是极低分位 → "极度便宜"，但相对近年中枢未必便宜。而市场 ERP 已用近 10 年窗口（`config.SENTIMENT_HISTORY_DAYS`），两处口径不一致。
- **改法**：
  1. `config.py` 新增 `INDIVIDUAL_PERCENTILE_WINDOW_YEARS = 5`，注释说明"与市场 ERP 窗口思路一致，避免全历史分位因估值中枢漂移失真"。
  2. `step3_sentiment.py` 求 `pe_percentile`/`pb_percentile` 时，先按日期取最近 N 年（N=`INDIVIDUAL_PERCENTILE_WINDOW_YEARS`）子序列，再 `percentile_of_score`。当前值仍取末值。窗口内不足 2 期则回退全历史（保留原兜底语义，打印 `[!] 个股分位窗口不足，回退全历史`）。
  3. `current_pe`/`current_pb` 仍暴露给评分层 PB-ROE 锚，不变。
- **验收**：`test_sentiment.py` 新增用例——构造一条长 10 年、估值中枢逐年下移的 PE 序列，断言滚动 5 年分位 ≠ 全历史分位且更合理；窗口不足时回退全历史。

### C2. 股息率估算去硬编码：分红率按行业桶、隐含市值用市场 PE

- **现状**：`utils/helpers.py:269-289` 的 `estimate_dividend_yield`：
  - 方案 2 `roe * 0.30`（30% 分红率对银行合理，成长股 0%、周期股波动大失真）；
  - 方案 3 `implied_mv = np_val * 6`（PE=6 远低于 A 股中位数，估算股息率系统性偏高）。
- **改法**：
  1. `config.INDUSTRY_PROFILES` 每桶新增 `payout_ratio`（建议：银行 0.30、非银 0.30、消费 0.40、周期 0.25、成长 0.15、其他 0.30），注释说明"capex/分红率等行业结构性假设集中于此"。
  2. `estimate_dividend_yield` 签名新增可选参数 `bucket: str = "其他"` 与 `market_pe: float | None = None`；方案 2 改用 `profile["payout_ratio"]` 取代硬编码 0.30；方案 3 改用 `implied_mv = np_val / (market_pe or 20) * ... ` 即 `市值 = 净利润 × (1/PE) 倒数`——准确说是 `市值 = 净利润 × PE`，故 `implied_mv = np_val * (market_pe or 20)`，股息 = `np_val * payout_ratio`，股息率 = 股息/市值。请按"市值=净利润×市场PE"重写方案 3，PE 缺失回退 20。
  3. 调用方 `step1_fundamental.py:93-95` 传入 `bucket`（需从 `screening` 上游或 `industry_info` 取——本任务在 `fundamental_screening` 签名新增可选 `bucket: str = "其他"`，由 `main.py` 调用时传入 `industry_info.get("bucket")`）与 `market_pe`（由 `main.py` 传入 `market_pe_history` 末值，取不到传 None）。
- **验收**：`tests/` 新增/更新用例覆盖：成长桶 payout 0.15、消费桶 0.40；方案 3 用 market_pe=30 时隐含市值 = np×30。原"real 来源"主路径不变。

### C3. 同一年取 `iloc[-1]` 可能取到季报 → 优先取年报，否则标注

- **现状**：`analysis/step1_fundamental.py:78` 与 `analysis/step2_dcf.py:105`（及 `:135`）都用 `sort_values(date).iloc[-1]` 取年内最大日期行。若数据源该年只返回到三季报（年报次年 4-5 月才披露），会把三季报当全年用。`FIN_END=2025` 对今天安全，但逻辑本身有边界风险。
- **改法**：
  1. 抽一个共享小函数 `pick_annual_row(year_df, date_col)`（放 `utils/helpers.py`）：优先取报告期月份==12 的行（`dt.month == 12`）中日期最大者；无 12 月行则取年内最大日期行，并返回 `(row, is_annual: bool)`。
  2. `step1_fundamental.py:78` 与 `step2_dcf.py` 三处（`:105` 财务摘要、`:135` 现金流）改用该函数；`is_annual=False` 时该行结果在 `step1` 的 table 里"分红来源"列旁追加标记或在 `step2` 打印 `[!] {year} 年仅季报，数据待年报`。本任务**只标注不改值**（季报数据仍用，但透明标注）。
  3. 完整度置信度可选：`is_annual=False` 的年份在 `scoring._completeness` 的 coverage 口径里降权——本任务**不接线**，仅在 `step2` 返回 dict 新增 `non_annual_years: list` 留接口。
- **验收**：`tests/` 新增用例——`pick_annual_row` 对 `[20250930, 20251231]` 返回 12-31 行 `is_annual=True`；对 `[20250331, 20250630, 20250930]` 返回 09-30 行 `is_annual=False`。

## 实施约束

1. **零回归**：`python main.py --demo` 全流程跑通，情绪分位、股息率、基本面表正常打印；`--batch-demo` 排名正常。
2. **风格一致**：中文 docstring、纯函数、配置集中在 `config.py`、共享函数放 `utils/`；新增配置项有注释。
3. **测试**：更新 `tests/test_sentiment.py`、`tests/test_industry.py`、`tests/test_dcf.py`（或新增 `test_helpers.py`）覆盖上述改动；`python -m pytest -q` 全绿。
4. **不动**：`scoring.py` 加权逻辑、`step2_dcf.py` 的 DCF 公式（仅改取数时点）、`step4_advice.py`。

## 验收清单

- [ ] `config.INDIVIDUAL_PERCENTILE_WINDOW_YEARS` 配置，个股分位用滚动窗口、不足回退全历史
- [ ] `INDUSTRY_PROFILES` 各桶含 `payout_ratio`，`test_industry.py` 守护
- [ ] `estimate_dividend_yield` 用桶分红率 + 市场 PE，签名向后兼容
- [ ] `pick_annual_row` 共享函数落地，`step1`/`step2` 取数改用之，季报年透明标注
- [ ] `step2` 返回 dict 新增 `non_annual_years`
- [ ] 相关测试新增用例，`pytest -q` 全绿，`--demo`/`--batch-demo` 跑通
