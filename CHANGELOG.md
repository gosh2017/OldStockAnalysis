# 变更记录（CHANGELOG）

本文件记录量化价值投资分析系统的显著变更。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号对应 git 提交哈希前缀。仅记录面向使用者的行为与口径变化，不含纯重构。

## [未发布]

### 文档
- config.py 模块顶部新增 README「行业分桶」导航注释，串联 INDUSTRY_BUCKETS / SW_TO_BUCKET / INDUSTRY_PROFILES 与 `tests/test_industry.py`。
- README 同步行业分桶重构：新增「行业分桶」小节、更新 DCF/评分口径、重写「已知限定」（总股本不再 197.56 亿硬兜底）。

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
