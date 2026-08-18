交接摘要：OldStockAnalysis 合理性优化（12 项，P4 收尾阶段）
1. 项目目标
OldStockAnalysis 是 A 股量化价值投资系统（基本面筛选 → DCF 估值 → 市场情绪 → 综合建议 + 评分）。分析结果"不够合理"的根因是缺少行业维度：所有股票共用一套 DCF 参数（WACC 9.5%/永续 1.5%）、一套筛选阈值（ROE>15%/股息>2% 全部达标）、一套评分权重（资产负债率权重=0），导致跨行业不可比——银行 ROE ~12% 结构性 fail 15% 线、total_shares 兜底 197.56e8 只对 000001 成立、周期股用单年 EPS 失真。

目标：按"行业分桶基础设施 → DCF 合理化 → 筛选/评分可比性 → 情绪/可信度"四阶段落地 12 项优化，每阶段以 py -3.14 -m pytest -q 全绿为关卡。计划文件：C:\Users\zwsoft\.claude\plans\crispy-honking-tower.md。

2. 已做决策（通过 AskUserQuestion 绑定，仍有效）
范围：全部 12 项。
行业数据源：通过 akshare stock_individual_info_em 真实获取，磁盘缓存（INDUSTRY_INFO_TTL_HOURS=720），失败回退"其他"桶，映射到申万一级 6 桶。
筛选规则：从"5 年全部通过"改为"中位数达标 + ≥ MIN_PASSING_YEARS 年达标 + 覆盖 ≥ MIN_COVERAGE_YEARS"（MIN_PASSING_YEARS=3, MIN_COVERAGE_YEARS=4），同步改受影响测试。
item 8 取舍：采用最小破坏方案——PB-ROE 锚只在 scoring 估值子分新增独立交叉项，不动 neutral==ceiling 等价（避免牵连 step2_dcf/advice/app/report + 4 条 ceiling 测试重写）。
3. 当前状态
已完成且已验证（pytest 63 passed，P3b 关卡）
P1 行业基础设施：config（INDUSTRY_BUCKETS/SW_TO_BUCKET/INDUSTRY_PROFILES）、fetcher（fetch_industry_info+map_to_industry_bucket）、demo_data（generate_industry_info+_DEMO_INDUSTRY）、conftest（industry_info/bucket fixture）、main 接入。tests/test_industry.py。
P2 DCF 重构（items 1-4）：scenarios_for(bucket)、derive_explicit_growth(CAGR+clip+None 兜底)、dcf_valuation(..., industry_info=None)、总股本 None 守卫（删 197.56e8 兜底）、FCF 加权均值含负值+has_negative_fcf 信号、EPS normalized/shiller。tests/test_dcf.py（15 测试）。
P3a 筛选+股息来源（items 7,9）：中数判定、estimate_dividend_yield 返回 (value, source)、结果表新增"分红来源"列。tests/test_screening.py（7 测试）。
P3b 评分行业化+独立锚（items 5,6,8）：ROE 按桶基准归一+CV 稳定性、SCORE_QUALITY_W_BY_BUCKET 覆盖（金融桶 ocf/debt=0、非金融桶 debt=0.20）、金融桶跳过 ocf、pb_roe 子分、step3 暴露 current_pe/current_pb/erp_source。tests/test_scoring.py+test_sentiment.py 增补。
已完成并验证通过（P4，pytest 69 passed + demo 运行时验证）
item 12（10 年窗口）：config.py:179 SENTIMENT_HISTORY_DAYS=365*10；data/demo_data.py generate_market_pe_history/generate_bond_yield_history 起始 2020-01-01→2016-01-01。
item 10（erp_source 可信度标注）：analysis/step3_sentiment.py _historical_erp_series 返回签名从 list|None 改为 tuple[list|None, bool]（第二值 used_real_bond 标识是否用了真实国债历史）。market_sentiment 据此设 erp_source：real（PE+国债均真实）/ real_partial（仅 PE 真实、国债标量兜底）/ synthetic（无 PE 历史→generate_historical_erp）。降级路径打印 [WARN] 不静默。
item 11（完整性置信度）：analysis/scoring.py 新增 _completeness(screening, dcf, sentiment) → (completeness 0-100, completeness_tag∈{"高","中","低"}, ≥80/≥50/<50)。加权：覆盖 30% + DCF 数据 30% + 股息来源 real 占比 20% + erp_source 20%（real=100/real_partial=60/synthetic=20/缺失=0）。compute_score 返回 dict 新增 completeness/completeness_tag；score_summary 末尾追加 [完整度X]。
P4 测试：tests/test_sentiment.py 5 个既有测试加 erp_source 断言 + 新增 test_history_window_10y/test_erp_source_real/test_erp_source_real_partial/test_erp_source_synthetic；tests/test_scoring.py 新增 test_completeness_tag/test_completeness_signal_isolation。
4. 遗留问题
P4 已全部验证通过：py -3.14 -m pytest -q → 69 passed（63 基线 + P4 新增 sentiment 4 + scoring 2）。本会话分类器间歇限流，但最终全绿，无失败。
demo 运行时已验证：py -3.14 main.py --demo --no-chart → 情绪段 [INFO] 历史分位基于真实序列（127 期 ERP，2016 起 10 年窗）+ 评分段「数据完整度: 高（100/100）」渲染正常；py -3.14 main.py --batch-demo → 排名表「完整度」列渲染正常。app.py/report.py/main.py 均通过 py_compile。
batch-demo 完整度差异未体现：5 只均 高(100)。因 demo 种子数据对所有标的均匀完整（同一 market_pe_history/bond_yield_history 形态 + 全 real 分红 → erp_source=real → 100）。完整度差异只在实盘模式（数据覆盖各异）才会出现。属 demo 数据特性，非缺陷。streamlit run app.py 未单独跑（非交互；复用同一已验证 main() 管线，caption 编辑已 py_compile 通过）。
app/report 完整度展示已落地：main.py 单股评分行 + run_batch 排名列 + app.py caption + report.py HTML badge 均显式展示 completeness_tag。item 11「app/report 可选加 caption」完成。
5. 已尝试但失败的方法
Bash 分类器（"glm-5.2"）受限：本会话及先前会话多次出现 glm-5.2 is temporarily unavailable (rate-limited)，导致 pytest/main.py --demo 等 Bash 调用间歇性无法执行。应对：只读操作（Read/Grep/Glob）不受影响，继续推进代码与测试编辑；pytest 关卡推迟到分类器恢复后重试。无代码层面失败——所有编辑均成功 apply。
（先前会话）item 8 曾考虑"打破 neutral==ceiling 等价"方案，因牵连面过大被否，改用最小破坏方案（scoring 新增 pb_roe）——已采纳并落地。
6. 下一步计划
P4 全部完成并验证：12 项「合理性优化」全部落地，pytest 69 passed，demo 运行时验证通过。无遗留阻塞项。
可选后续（非必需）：
  - 实盘模式端到端验证（需联网 + AkShare）：py -3.14 main.py 000001 --no-chart 看 erp_source=real、完整度随真实数据覆盖变化；streamlit run app.py 实盘交互。
  - batch-demo 完整度差异仅在实盘模式体现（demo 种子数据均匀完整）。
  - 提交：用户未要求提交，遵循"只在用户要求时提交"。如需提交，建议一次性提交 P4 收尾（main.py/app.py/report.py 展示层 + HANDOFF.md 状态更新）。
风险点（读码推断应通过，但未实测）
_completeness 全数据路径算出 84（恰好 ≥80 → "高"），边界略紧但成立。
degraded 路径（base_fcf None + erp synthetic + 全 real 分红）= 100×0.3 + 0 + 100×0.2 + 20×0.2 = 54 → "中"，< 84 且 ≠ "高"，断言成立。
_historical_erp_series 返回元组后，market_sentiment 内唯一调用点已解包为 (historical_erp, used_real_bond)，分支逻辑三态完备。
简言之：12 项「合理性优化」全部落地并验证（pytest 69 passed + demo 运行时验证通过）。P1-P3b 63 passed，P4 新增 6 测试全绿。展示层（main.py/app.py/report.py）完整度标签已贯通。无遗留阻塞项。可选后续：实盘模式端到端验证、提交（待用户要求）。