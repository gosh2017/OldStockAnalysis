# 变更记录（CHANGELOG）

本文件记录量化价值投资分析系统的显著变更。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号对应 git 提交哈希前缀。仅记录面向使用者的行为与口径变化，不含纯重构。

## [未发布]

> 将**历史/静态数据查询接口**从 AkShare 实时 HTTP 迁移至 **Hikyuu 本地库**（`code_plan/hikyuu-data-migration.md` 落地）。pytdx 一次性导入后查询全走本地（HDF5 kdata + SQLite stock.db），根因：东财/乐咕/申万 HTTP 端点频繁连不上、`try_fetch` 静默返 None。实时数据（全市场 spot）与市场历史 PE（乐咕）仍走 AkShare；各接口保留 AkShare fallback。本机本地库已就绪（DB 1.2GB，finance 已导入），hku 路径经 live smoke 实测、`--demo`/pytest 零回归。

### 新增（数据源迁移）
- **共享访问层 `data/hikyuu_backend.py`**：惰性单次 `load_hikyuu(load_history_finance=True, load_weight=True, start_spot=False)`（进程级缓存）、symbol→Stock 解析（含 **bj** 北交所前缀，补 `_prefix_symbol` 漏项）、KData→DataFrame（中文列名，对齐 `_normalize_daily_df` 契约）、HistoryFinance 按名读取（避免 id off-by-one）、weight（分红 bonus / 总股本）、zh_bond10 直读、PB 自算（`收盘 / FINANCE(每股净资产)` PIT 对齐）。未装/未导入时各函数返 None/空，调用方降级 AkShare。
- **9 个 fetcher 迁移**（hku 优先 + ak fallback，原 akshare 体移私有 `_ak` 函数）：`fetch_daily_data`（FORWARD 前复权）、`fetch_benchmark_daily`（指数 NO_RECOVER）、`fetch_stock_list`（sm 迭代）、`fetch_industry_info`（板块 + weight 总股本）、`fetch_dividend`（get_weight bonus，每10股）、`fetch_bond_yield_history`/`_10y`（zh_bond10）、`fetch_financial_abstract`（HistoryFinance 三表）、`fetch_cashflow_detail`（capex + 折旧+摊销合并列）、`fetch_stock_indicator`（PB 自算 hku、PE 留 akshare）。`_fetch_stock_screening_data_hikyuu` 改用共享 `_hku()` + backend 助手（去重 load/枚举）。
- **校准探针 `scripts/probe_hikyuu_finance.py`**（§7）：DB 自省（表/字段 id↔name/zh_bond10/block）+ 运行时 dump 样本股（000001/600519/300750）财报字段值+量级单位推断（元/万元/股/%）+ weight + kdata 末收盘；akshare 交叉对照段需联网（离线跳过）。坐实字段单位，避免 ×1e4 量级错误污染 DCF/PE。
- **测试 `tests/test_hikyuu_backend.py`**（19 例）：backend 各函数 + 迁移后 fetcher 的契约（列名/单位/量级）+ hku 不可用降级；`pytest.importorskip("hikyuu")` + DB 不存在 skip，不构成硬依赖。`pytest -q` **190 项全绿**（171 + 19），`--demo`/`--backtest-demo` 零回归。

### 变更（数据源迁移·对计划文档的校正）
- **stock.db 路径**：实测位于 `G:/QTrading/StockData/stock.db`（`~/.hikyuu/importdata-gui.ini` 的 `[hdf5] dir`），非计划假设的 `c:\stock`。`config.HIKYUU_DB_PATH` 据此校正。
- **zh_bond10 单位**：`value` 为「小数×1e6」（末值 18140 ≈ 1.814%），归一小数须 **÷1e6**——计划文档 `/10000` 得 1.814（=181%）是错的，探针捕获并修正。
- **财报字段 id**：计划 §2.3 的 id（96/95/107/114/281/210/271/4/238）为 DB `id` 列（正确）；运行时 `get_history_finance_field_index(name)` 返回 id-1（0 基数组下标）。`get_history_finance()` 返回 `[(report_date, file_date, values[581])]`。金额字段经量级核实为元（归母净利 ~4e10、权益 ~5e12、capex ~2e9），无需缩放。
- **行业板块前置**：Hikyuu 行业归属需先跑 `scripts/import_hikyuu_industry_blocks.py` 导入东财行业板块（block 表默认仅指数板块）；未导入时 `fetch_industry_info` 降级 AkShare（仍取行业+总股本），总股本可由 Hikyuu `weight` 提供（不丢）。顺带修 `import_hikyuu_industry_blocks.py` 的 `probe_calibrate` 路径 bug（`from config import` 因 sys.path 缺项目根而静默失败）。
- **批量筛选行业 fallback（新浪）**：Hikyuu 行业板块当前只导入 19/496（EM push2 间歇性 ProxyError/ConnectionError，已修代理 bug 但端点仍不稳）→ 批量筛选的行业列覆盖率仅 ~18%（银行等大类缺失）。新增 `_fetch_sina_industry_map`（新浪 `stock_sector_spot` + `stock_sector_detail`，~49 行业，端点稳定）作为批量筛选的行业兜底：hku 行业覆盖 < 80% 时自动触发，覆盖可提升至 ~54%（2917/5447），64 个行业（含银行/金融、采掘、供水供气、船舶、飞机等），金融股（47 只）正确归「非银金融」桶（is_financial=True 口径，避免 90% 负债率被错误打分）。申万 `index_component_sw` 批量调用被服务端阻断/返回残缺响应（akshare 的 KeyError 包装被 try_fetch 视为确定性错误不重试），故新浪作为更可靠的兜底源。新浪行业覆盖不到的股票行业留空（~46%），待 EM 恢复后 `import_hikyuu_industry_blocks.py` 补全本地板块即可自动不触发 fallback。`config.HIKYUU_INDUSTRY_TO_BUCKET` 新增新浪行业板块映射（金融→非银金融、采掘/供水供气→周期、家具/物资外贸→消费、船舶/飞机→成长 等）。

### 修复（批量筛选行业兜底）
- **「申万行业映射」长期失效根因：行业代码未去 `.SI` 后缀**。`sw_index_first_info` 返回 `801780.SI`，而成份端点 `swsresearch.com/.../component_stocks/` 只认裸码——带后缀时返回 `results=[]`，akshare 随后在列选择处抛 `KeyError("证券代码 ... not in index")`。**确定性空响应，重试无效**（此前被误判为"服务端连拉易阻断"，故上条改走新浪兜底）。`_fetch_sw_industry_map` 截后缀后 31 个行业 42/79/104… 只全部正常返回。
- **兜底链改为三级（精度优先）**：Hikyuu 本地东财板块 →（覆盖 <98%）**申万一级**（31 行业，**银行 42 / 非银金融 79 分列**，覆盖沪深全 A ~5200 只）→（仍 <95%）新浪（仅补残留，主要是北交所）。原先新浪直接兜底会把银行/证券/保险合并成一个「金融行业」→ 桶误判为「非银金融」，银行股打分口径整体错（`is_financial` 同口径但行业桶不同）。拆分 `_fill_industry_from_sw` / `_fill_industry_from_sina` / `_apply_industry_map`（就地改 df，只填 NaN、同步重算桶）。
- `config.SW_TO_BUCKET` 补申万 2021 版名精确项「纺织服饰→消费」「环保→周期」，使申万 31 行业名走精确表而非关键词兜底。
- **测试**：新增 `tests/test_industry_fallback.py`（12 例，全离线 mock）：后缀剥离/裸码不误截/空码跳过/重叠取末 + 兜底链顺序（申万优先、新浪仅补残留、申万全覆盖则跳过新浪、本地满覆盖则不联网、申万失败转新浪）+ `_apply_industry_map` 只填 NaN 并重算桶 + 申万 31 行业名分桶 + 银行桶来源守护。`pytest -q` **202 项全绿**。
- **实测效果**（`force_refresh=True` 重建）：行业覆盖 2917→**5193/5447**（53.5%→95.3%），**「银行」42 只回归**（含工行/建行/招行/浦发/平安），桶「其他」2613→291。
- **修复（批量筛选市值缺失）**：上条「待办①」已解。① `scripts/run_hikyuu_import.py` 校验判据由 `len(sm)>1000`（基于 Stock 元数据表，导全即 ~7801，恒为真→「日线仅 44%」被静默吞掉）改为实测 A 股 `get_count(DAY)>0` 覆盖率（`_a_share_kdata_coverage`，口径同 `hku_is_a_share`），<95% 重试（env `HKU_IMPORT_COV_THRESHOLD`/`HKU_IMPORT_MAX_ATTEMPTS`，默认 0.95/3）。② 用「day-only + `day_start_date=2024-01-01`」重导：市值口径只需最新收盘、无需 35 年全量历史，每股 fetch ~8500 bar→~650 bar 快约 14×，12min 即把 kdata 覆盖 44%→**99.8%**（5532/5541），**批量筛选市值有效 5510/5527=99.7%**（仅 17 只停牌/退市 pytdx 无近期数据仍缺）。upsert 不删旧数据，已导全量历史不丢；缺漏股的深度历史（1990 起）可后续单独补跑。
- **待办**：行业名现为多源混合（本地 19 个东财板块 + 申万 31 + 新浪 49），出现「环保/环保行业」「纺织服饰/纺织服装」等近义重复名；桶映射与筛选均正确，仅下拉列表观感冗余，待 EM 恢复后补全本地板块（496 板块）即统一为东财口径。

> 以下为**历史回测验证模块**（提示词 D，对应 `prompts/04_backtest.md`），验证综合评分信号在历史上是否有效（"A/B 级是否跑赢 D 级与基准"）。回测是现有分析层的"消费者"，以数据注入方式复用 step1–4 / scoring，**不改其算法与权重**。条目以 `(D1)`–`(D6)` 标注回溯至提示词。

### 新增（回测模块）
- **(D1) 时点数据截断层 `data/pit.py`**：`truncate_to_date`（按日期列模糊匹配截断到 ≤ as_of）、`filter_reports_by_pub_lag`（财报按"报告期 + 披露滞后 120d"过滤，避免把未披露年报当已知 → 未来函数）、`as_of_bundle`（组合产出回测一次调用的全部截断数据）。保证每个调仓日 T 看到的数据不晚于 T。
- **(D2) 时点分析适配器 `analysis/backtest.py::analyze_as_of`**：接收截断 bundle + end_date=as_of 的 StockContext，按 main() 同序调用 step1–4 + compute_score，`redirect_stdout` 静默；fin 窗口从 bundle 派生（fin_end=最近可用年报年、fin_start=fin_end−4，与 main 的 5 年窗口一致）。不改 step/scoring 内部；与 main() 等级/建议一致性回归用例守护。
- **(D3) 回测引擎 `run_backtest`**：调仓日序列（M/Q/Y，纯 pandas 无 dateutil 依赖）→ 每标的 analyze_as_of → 选股（grade≥min_grade 且 score 降序 top_n）→ 等权/得分归一权重 → 日频 mark-to-market 净值（换仓扣双边成本、空仓期记 0、退市/停牌缺失记 0 并标 `delisted`）。`BacktestResult` 含 equity_curve/positions/trades/benchmark_curve/grade_forward_returns/metrics/rebalance_dates。`grade_forward_returns` 把**全部**标的按等级分桶记 hold 期前向收益，验证 A/B/C/D 单调性证据。
- **(D4) 业绩度量 `compute_metrics`**：纯 numpy 实现总收益/CAGR/年化波动/最大回撤/Sharpe/胜率/Alpha/Beta；空/常数序列回退 None 不抛（vol=0→Sharpe=None、var(bench)=0→Beta=None），scipy 不可用无硬依赖。
- **(D5) 基准与可视化**：`fetch_benchmark_daily`（沪深 300，`stock_zh_index_daily`/`index_zh_a_hist` fallback）+ `generate_benchmark_daily`（确定性模拟基准）；`visualization/backtest_charts.py` 三图——净值曲线（策略 vs 基准）/ 水下回撤图 / 各等级平均前向收益柱状（验单调性）。matplotlib 优先 + plotly 软导入，`--no-chart` 同口径，输出 `charts/backtest_*.{png,html}`。
- **(D6) 配置 / demo 时序化 / CLI 接线**：`config.py` 新增 `BACKTEST_*` 参数集中配置（频率/持有期/top_n/最低等级/权重/交易成本/基准/回溯年/披露滞后/无风险利率）；`generate_all_demo_data(backtest=True)` 生成跨 ~2011–今宽跨度多报告期序列（按标的派生 quality 因子缩放 ROE/净利/OCF/PE-PB 制造 A/B/C/D 截面分散），默认分支逐字节不变（quality=1.0 → 零回归）；`main.py` 新增 `--backtest FILE`（实盘）与 `--backtest-demo`（内置标的 + 模拟数据，全程无网），打印业绩度量表 + 等级前向收益表 + 信号有效性结论（"A 级平均前向收益 X% vs D 级 Y%，信号{有效/无效}"）。`--years`/`--out-dir`/`--no-chart` 复用。
- **测试**：新增 `tests/test_pit.py`（11 例）+ `tests/test_backtest.py`（12 例），覆盖截断/披露滞后/analyze_as_of fin 窗口前移与一致性/run_backtest 结构与成本/compute_metrics 数学（总收益/最大回撤/常数 Sharpe=None/同曲线 Beta≈1）。`pytest -q` 122 项全绿，`--demo`/`--batch-demo`/`--backtest-demo` 跑通零回归。

### 文档（回测模块）
- README 同步回测模块：新增「历史回测验证」小节、CLI 参数表补 `--backtest` / `--backtest-demo`、项目结构补 `data/pit.py` / `analysis/backtest.py` / `visualization/backtest_charts.py`、功能概述表补回测行。
- README / CHANGELOG 新增**三段限定**：准 PIT（AkShare 财务可能重述）+ 幸存者偏差（仅含当前在市标的）+ 简化成本（未计滑点/税/停牌流动性），不得宣称严格历史回测；`fetch_benchmark_daily` 实盘路径本环境无网，需联网复验。

### 新增（进度反馈）
- **统一进度回调 `on_progress(done, total, desc)`**：为批量处理（`main.py::run_batch`）与历史回测（`analysis/backtest.py::run_backtest` / `_prefetch_live`）新增可选 `on_progress` 关键字参数，向后兼容（缺省 `None` 即不报进度），分析逻辑不耦合任何具体 UI。
- **CLI 终端进度条 `utils/progress.py`**：tqdm 可用时渲染标准终端进度条（ETA / 速率 / 自适应宽度），未安装时降级为向 stderr 的周期性百分比打印（每 ~10% 或描述变化时刷新，不刷屏）；与 `utils/stats.py` 对 scipy 的处理同款——可选依赖 + 自动回退，不构成硬依赖。`main.py` 的 `run_batch` / `run_backtest_flow` 调用均包入 `with Progress() as prog:`。
- **回测两阶段进度**：回测总工作量在预取前不可知（调仓日数依赖预取结果），故分两阶段独立呈现——"预取数据 k/n"（`total=len(symbols)`）随后"逐期回测 i/期数 × 标的数"（`total=len(rebal_eff)·len(symbols)`）；`Progress` 在 `total` 跨阶段变化时自动关闭旧条、新建新条，避免百分比跨阶段跳变。计数对齐：逐期循环对每个标的槽位（含退市 `continue` 跳过）都触发回调，使 `done`/`total` 始终同步。
- **Streamlit 仪表盘进度条**：`app.py` 的 `run_batch_silent` / `run_backtest_silent` 透传 `on_progress`；批量排名与历史回测两标签页按钮处理改用 `st.progress` 实时渲染（取代原 `st.spinner` 的"无进度等待"），成功时置 100%、异常时清空条并报错。
- **依赖**：`requirements.txt` 新增 `tqdm>=4.60`（运行依赖，标注未安装时自动回退）。

---

> 以下为**估值 / 评分 / 情绪口径**三组合理性优化，对应 `prompts/01_dcf_valuation.md`（A）、`prompts/02_scoring.md`（B）、`prompts/03_sentiment_fundamental.md`（C）。条目以 `(A1)`/`(B2)` 等标注回溯至提示词。

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

### 新增（仪表盘）
- **输入清单跨启动持久化**：Streamlit 仪表盘「批量排名 / 历史回测」两个标的输入框的文本现缓存到 `.cache/dashboard_inputs.json`，下次启动 streamlit 自动恢复上次输入，免去重复录入。侧边栏「➕ 批量排名 / ➕ 历史回测」与「❌ 移除」现直接改写对应输入框文本（此前侧边栏追加与输入框脱节：首次渲染后追加不回填输入框），输入框成为唯一数据源；编辑（on_change）、追加、移除三处均即时落盘，失败静默降级不阻断主流程。新增 `tests/test_dashboard_inputs_cache.py`（6 例，AppTest 模拟跨会话恢复 / 追加 / 移除 / 注释行保留 / 无缓存回退默认清单）。
- **批量筛选标签页**：仪表盘新增「批量筛选」标签页（`单股分析 / 批量筛选 / 批量排名 / 历史回测`），按**市值区间**（下限 / 上限，亿元，0 或留空 = 不限）与**行业**（多选，留空 = 不限）筛选全 A 股，勾选结果后一键加入「批量排名」或「历史回测」清单。数据层 `fetch_stock_screening_data` 改调 `_fetch_stock_screening_data_hikyuu`（**数据源从 akshare 实时 HTTP 切到 Hikyuu 本地库**：旧链路 `stock_zh_a_spot_em` + `sw_index_first_info` + 31 次 `index_component_sw` 走东财/乐咕/申万端点频繁连不上，`try_fetch` 静默返 None 致表加载失败；Hikyuu 一次性 pytdx 导入后查询全走本地 HDF5 kdata + SQLite 股本/板块，**查询期零 HTTP、零 akshare**）。口径：总市值 = 总股本（`total_count`，万股）× 1e4 × 最近收盘（`get_kdata(Query(-1))[-1].close`，日级 EOD）；枚举迭代 `hku.sm` 按 `_is_a_share` 过滤得沪深京全 A 股 ~5500 只（含科创板/创业板/北交所；预定义板块 `get_block("A","沪深")` 仅 3193 漏创业板/科创板故不用）；`load_weight=True` 必需（否则 `get_weight()` 空、总股本不可得）。日级磁盘缓存（`STOCK_SCREENING_TTL_HOURS`）、`on_progress(done,total,desc)` 回调（与 run_batch / run_backtest 同款）；hikyuu 未装 / 本地库未导入（`load_hikyuu` 抛 HKUException）→ 返回 None（app 层 st.error 提示切 Demo 或先跑 `scripts/run_hikyuu_import.py`）；总股本/收盘取不到的标的总市值置 NaN（市值筛排除）。旧 akshare 取数体 `_fetch_stock_screening_data_live` + `_fetch_sw_industry_map` 保留为休眠代码备手动切换，本函数不再调用（用户明确「别用 akshare 了」）。Demo 路径 `generate_stock_screening_data` 确定性生成 30 行（50 亿 ~ 8000 亿元覆盖小 / 中 / 大盘）。勾选表格用 `st.data_editor` + CheckboxColumn，key 随筛选条件派生（改条件 → 重建表格清空勾选；点「加入」时筛选不变 → key 稳定 → 勾选保留可读，避免行数变化后旧勾选错位）；追加复用 `_append_pairs_to_input`（按 `代码,名称` 去重、保留注释 / 空行，与侧边栏单只「➕」同口径）。**live 模式失败可见性（修复"点了加载筛选表却无结果"）**：`fetch_stock_screening_data` 在 akshare 返回空 / 超时时经 `try_fetch` 静默返回 None（不抛异常、`disk_cache` 亦不缓存空值），app 层旧逻辑仍 `progress(1.0, 完成)` 并回落到"点击加载"引导 → 既无表也无错（用户报告的根因）；现改为显式判定 None / 空态 → `st.error` 给出可操作提示（切 Demo 离线预览 / 点「🔄 刷新」重试），不再误报"完成"；刷新失败保留上次成功旧表 + 顶部 `st.warning`（避免一次刷新失败清空已加载结果）；spot_em 全市场拉取阶段补充进度文案，避免数十秒拉取期进度条卡在通用提示被误以为假死。**行业分类降级为「其他」**：坐实 Hikyuu 本地库 `block` 表仅含指数板块（行业/概念/地域下载在包源码 `download_block.py:339-343` 被硬编码注释、且行业下载走被封的 EM push2），无本地行业来源 → 行业列暂全 `None`、桶全「其他」，行业多选筛选项实质关闭（市值区间筛选完整可用）；`map_to_industry_bucket` 已扩为「申万次表 `SW_TO_BUCKET` 优先 → Hikyuu 次表 `HIKYUU_INDUSTRY_TO_BUCKET` → 其他」三级查表（申万优先保单股 `fetch_industry_info` 路径零回归），待后续接入稳定本地行业源即可无改下游地补齐。**实盘 Hikyuu 路径已烟测验证（5549 只 / 12s / 市值正确：工行 2.79 万亿、茅台 1.62 万亿）。**新增 `tests/test_dashboard_screening.py`（12 例：AppTest Demo 加载 / 市值过滤 / 行业多选 / 加入按钮空选提示 + live 失败三路径回归（fetch 返回 None → `st.error` 可操作提示 / fetch 抛异常 → 携原始信息 / 刷新失败保留旧表 + warning）+ 筛选表形状与过滤掩码单元测试 + Hikyuu 次表映射（申万优先 → Hikyuu 次表 → 其他三级查表）与「hikyuu 未装 / 本地库未导入 → 返 None」两例降级用例）；`requirements.txt` 新增 `hikyuu>=2.8`（可选依赖，未装 / 未导入时自动降级到 None，不构成硬依赖）；`pytest -q` 167 项全绿（164 + 新增 3），`--demo` 跑通零回归。

### 修复（仪表盘·批量筛选）
- **加入后侧边栏 / 输入框即时同步**：「批量筛选」结果经「➕ 加入批量排名 / 加入历史回测」追加后，侧边栏「已添加标的」展开器与对应标的输入框此前仍显旧值，须再次交互才刷新。根因是追加发生在「批量筛选」标签页（侧边栏之后渲染），同一帧侧边栏已先读过旧值、读不到当帧追加结果。修复：append 净增 > 0 后置反馈标志并 `st.rerun()`——重跑帧侧边栏先于标签页渲染、读到新清单；`st.rerun()` 不携带前端 `widget_states`，故既不清空程序化写入（`_new_session_state` 保留追加结果）也不清逐行补丁，text_area 输入框同帧取到新值（`value_changed=True` → 前端采纳），无输入框失同步回归；成功提示经标志在重跑帧重放（rerun 会冲掉当帧 `st.success`）。空选 / 所选均已在清单（净增 0）不 rerun、直接提示。与侧边栏「❌ 移除」的既有 `st.rerun()` 同款。
- **批量筛选结果一键全选 / 取消全选**：结果表新增「☑️ 全选」「☐ 取消全选」按钮，免逐行勾选即可整批加入清单；全选 / 取消除翻转标志外 bump `data_editor` key 版本号以丢弃前端逐行编辑补丁（否则光改数据列盖不掉用户手动取消的某行）。
- **测试**：`tests/test_dashboard_screening.py` 新增 `test_select_all_add_syncs_sidebar_targets`（全选 → 加入批量排名 / 历史回测后，侧边栏两展开器标签由「（5 行）」同步至「（30 行）」+ 成功提示经 rerun 重放）；`pytest -q` 171 项全绿。

### 变更（DCF 折现率）
- **WACC 改为利率联动（Rf + β × ERP）**：DCF 折现率由「每桶静态常数」改为动态算式 `WACC = risk_free + β × ERP`。`risk_free` 取实时 10Y 国债收益率（main/backtest 已传 `bond_yield`，PIT 正确），`ERP = EQUITY_RISK_PREMIUM` = 0.06（A 股长期股权风险溢价中位数，独立于 step3 情绪 ERP——后者 = 1/PE−Rf≈3% 是「便宜-vs-债券」指标、非资本成本输入），`β` 取各桶 `INDUSTRY_PROFILES[bucket]["beta"]`（成长 1.0333 / 消费 1.1167 / 其他·银行·非银 1.2000 / 周期 1.2833）。β 反解校准使 Rf = `RISK_FREE_REFERENCE` = 0.023（现行 10Y）时动态值 == 旧静态 wacc（零回归），Rf 漂移时 WACC 随 β 线性漂移；`risk_free` 不可得回退静态 wacc 兜底值。`scenarios_for(bucket, risk_free=None)` / `dcf_valuation(..., risk_free=None)` 新增可选参；返回 dict 新增 `wacc_basis`（mode/risk_free/beta/erp/wacc）+ 终端打印分解式 `[INFO] WACC 利率联动: Rf X% + β Y × ERP Z% = W%`。新增 `tests/test_dcf.py::test_wacc_dynamic_rate_linked` + `tests/test_industry.py::test_wacc_beta_calibration_invariant`；`pytest -q` 130 项全绿，`--demo` 逐字节零回归（仅多一行 INFO）。
- README 同步：行业分桶表新增 `β` 列、DCF 口径「折现率 WACC」改写为利率联动（公式 + β/ERP/Rf 来源 + 零回归校准说明）。

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
