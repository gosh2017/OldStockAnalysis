# 变更记录（CHANGELOG）

本文件记录量化价值投资分析系统的显著变更。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号对应 git 提交哈希前缀。仅记录面向使用者的行为与口径变化，不含纯重构。

## [未发布]

> 本次合并三组"估值 / 评分 / 情绪口径"合理性优化，对应 `prompts/01_dcf_valuation.md`（A）、`prompts/02_scoring.md`（B）、`prompts/03_sentiment_fundamental.md`（C）。条目以 `(A1)`/`(B2)` 等标注回溯至提示词。

### 新增
- **(A1) 行业化 capex 兜底**：`INDUSTRY_PROFILES` 各桶新增 `capex_ratio`（周期 0.45 / 其他 0.20 / 消费 0.15 / 成长 0.25 / 银行·非银 0.10），capex 缺失年按 OCF × 该比例估算维持性支出，替代固定 0.20（消除重资产股 FCF 虚高、轻资产股虚低）。`dcf_valuation` 返回 dict 新增 `capex_estimated` 布尔标记兜底年份。
- **(B1) 完整度温和折让**：综合评分在类内重归一后乘 `score × (floor + weight × completeness/100)`，配置 `SCORE_COMPLETENESS_PENALTY = {"floor": 0.70, "weight": 0.30}`（完整度 100→×1.0、0→×0.70），解决"数据稀疏标的因类内重归一而虚高"；终端打印 `[INFO] 完整度折让: ×{factor}` 透明可见。
- **(C1) 个股 PE/PB 滚动窗口分位**：个股自身 PE/PB 历史分位改用近 5 年滚动窗口（`INDIVIDUAL_PERCENTILE_WINDOW_YEARS`），与市场 ERP 窗口思路一致，避免估值中枢长期漂移致全历史分位失真（如银行 PE 20→5，当前 5 倍在全历史为极低分位 → "极度便宜"误判）；窗口不足 2 期回退全历史。
- **(C2) 股息率去硬编码**：`INDUSTRY_PROFILES` 各桶新增 `payout_ratio`（成长 0.15 / 周期 0.25 / 银行·非银·其他 0.30 / 消费 0.40）；`estimate_dividend_yield` 签名新增可选 `bucket` / `market_pe`，分红率按桶取、隐含市值改用"净利润 × 市场 PE"（PE 缺失回退 20），向后兼容旧调用。
- **(C3) 年报优先取数**：新增共享函数 `pick_annual_row`（`utils/helpers.py`）——优先取报告期月份==12 的最大日期行，无年报取年内最大日期行，返回 `(row, is_annual)`；`step1`/`step2` 取数改用之，季报年透明标注（`[!] {年} 年仅季报`）；`step2` 返回 dict 新增 `non_annual_years` 接口（预留完整度降权，暂未接评分）。

### 变更
- **(A2) 显性增长率最小二乘拟合**：`derive_explicit_growth` 由首末两点 CAGR 改为对 `log(净利润)` 序列最小二乘线性回归（`np.polyfit`），周期股首末落峰/谷时不再失真；保留 ≥3 个正值点、首末非正回退行业永续的逻辑，clip 区间 `DCF_GROWTH_CAGR_CLIP` 不变。
- **(A4) wacc≤永续防御性 guard**：主 DCF 三情景循环新增 `wacc ≤ 永续增长` 守卫，命中则该情景 `intrinsic_value` 置 None 并跳过，不产生负/无穷内在价值（现行画像安全，仅配置被改/永续抬升时触发）。
- **(A5) 破产清算 D&A 口径**：D&A 不可得时由"回退归母净利润"改为"按 FCF×0.5 估算清算口径"，与 `all_liq` 为空时一致，自然满足 `破产清算 ≤ 保守`；事后钳位降为 inert 安全网。
- **(B2) ROE 稳定性水平调制**：稳定性分乘 `min(1.0, roe_mean / roe_benchmark)`，"稳定地差"（低 ROE、变异系数小）不再虚高；均值≈0 回退分支同步乘调制。
- **(B3) 资产负债率用近 1 年值**：子分由历史 `max` 改用 `iloc[-1]`（table 已按年排序，末行即最新年），反映当前杠杆而非历史峰值。
- **(B4) OCF 质量用中位数**：经营现金流-净利润比由均值改用中位数，抗亏损年极值；比例 ≥1 → 100 的 clip 不变。

### 修复
- **DCF 敏感性图现价标注**：`plot_sensitivity_heatmap`（matplotlib）与 `sensitivity_figure`（plotly / Streamlit）现价落在网格值域 `[vmin, vmax]` 外时不再丢失现价。等值线（"内在价值=现价"分界）仅当现价落在值域内绘制；超界时改由 colorbar 端点标记（matplotlib，夹至端点 + 方向注）或图角标注（plotly）呈现现价位置，并据"全参数下内在价值 ＜ / ＞ 现价"标注「全面高估 / 全面低估」，标题同步说明买入区 / 高估 / 低估语义（老登股现价常超出 DCF 内在价值区间，此前等值线画不出致现价从图上消失）。

### 文档
- config.py 模块顶部新增 README「行业分桶」导航注释，串联 INDUSTRY_BUCKETS / SW_TO_BUCKET / INDUSTRY_PROFILES 与 `tests/test_industry.py`。
- README 同步行业分桶重构：新增「行业分桶」小节、更新 DCF/评分口径、重写「已知限定」（总股本不再 197.56 亿硬兜底）。
- README / CHANGELOG 同步提示词 A（DCF 估值）/ B（综合评分）/ C（情绪与基本面口径）三组合理性优化，条目以 `(A1)` 等回溯 `prompts/`。
- 新增 `tests/test_helpers.py`（`pick_annual_row` / `estimate_dividend_yield` 行业化口径）；`test_dcf.py` / `test_scoring.py` / `test_sentiment.py` / `test_industry.py` 增 A1–A5 / B1–B4 / C1–C3 用例，`pytest -q` 99 项全绿，`--demo` / `--batch-demo` 跑通零回归。

## [ae7baf6] - 2026-08-18

### 新增
- **行业分桶**：申万一级行业 → 6 桶（银行/非银金融/消费/周期/成长/其他），各桶差异化 DCF 参数（WACC/中性永续）、ROE 基准、EPS 算法（周期桶 shiller 10 年平滑，其余 normalized 5 年）与评分权重（金融桶跳过资产负债率与经营现金流口径）。
- **CAGR 推导显性增长率**：`derive_explicit_growth` 由 ≥3 年归母净利 CAGR 推导中性显性增长率，裁剪至 `[-5%, 12%]`（`DCF_GROWTH_CAGR_CLIP`）。
- **PB-ROE 独立锚**（评分 item 8）：公允 PB = ROE 均值 / 15，作为估值子分的第三支点，不依赖 DCF 可估值。
- **数据完整度置信度**（评分 item 11）：汇总覆盖度 / DCF 数据可得性 / 股息真实来源占比 / ERP 来源可信度，输出 高/中/低 标签，独立于综合评分。
- **10 年情绪窗口**：市场 ERP 分位改用乐咕真实历史市场 PE + 国债历史序列按日期对齐计算，弃用合成 mock 分布（`SENTIMENT_HISTORY_DAYS = 365*10`）。
- **测试**：新增 `tests/test_industry.py`（144 行），覆盖 `map_to_industry_bucket` 映射、demo 行业数据差异化、`INDUSTRY_PROFILES` 6 桶与字段完整性、周期桶 shiller 口径、`SW_TO_BUCKET` 一级行业覆盖。

### 变更
- DCF 折现率 WACC 由「固定 9.5%」改为随行业桶变化（成长 8.5% / 消费 9.0% / 其他·银行·非银 9.5% / 周期 10.0%），三情景参数由 `scenarios_for(bucket)` 按桶构造。
- 综合评分质量子分 ROE 水平按桶 `roe_benchmark` 归一，稳定性改用变异系数 CV（无量纲、跨行业可比）。

### 修复
- **总股本取数**：优先级改为行业信息（EM f84）> 日频 `outstanding_share` > 财务摘要；三源全失败返回 `None` 并跳过 DCF，不再用 197.56 亿硬兜底（消除对非 000001 标的每股估值的错估）。

## [10bdbd1] - 2026-08-16

### 修复
- 市场情绪弃用不可靠的东财 spot 快照，改用乐咕真实历史市场 PE + 国债历史序列计算 ERP 历史分位。

### 新增
- Streamlit 仪表盘「批量排名」tab 支持在线模式（逐只联网打分），不再仅限 Demo。

## [2c8f47a] - 2026-08-16

### 新增
- 综合评分系统（0–100 / A–D）：质量 40% + 估值 35% + 情绪 25%，缺失数据类内重归一化。
- 自包含 HTML 投资报告（`--report`）：估值图 base64 内嵌，单文件可分享。
- Streamlit Web 仪表盘（`streamlit run app.py`）：交互式估值图 / 敏感性热力图 / 评分明细。
- 磁盘缓存：股票列表、个股 PE/PB、市场 PE、国债、行业归属按各自 TTL 落盘 `.cache/`（pickle，失败不落盘）。
- 批量选股：`--batch`（实盘）与 `--batch-demo`（内置标的）逐只打分排名。
- 名称模糊搜索：直接输入名称（茅台/平安）解析为代码，支持片段与近似匹配。

---

> 历史更早的提交（`0ca121c` 及以前）以"Improve./fix:"为主，未纳入本 CHANGELOG 起点。
