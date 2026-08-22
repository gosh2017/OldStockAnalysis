# 提示词 D — 历史回测验证模块（新增）

> 目标受众：编码助手（Claude Code 等）。本文件自包含，可直接作为任务指令执行。

## 角色与目标

你正在为一个基于 AkShare 的 A 股长线价值投资量化分析系统（Python，模块化：`config.py` / `analysis/*` / `data/*` / `utils/*` / `visualization/*` / `tests/*`）**新增历史回测验证模块**。系统核心产出是"基本面筛选 → DCF 估值 → 市场情绪 → 综合评分（0–100 / A–D）+ 操作建议（大幅买入/分批建仓/持有或减仓）"。本次任务是验证这些信号在历史上是否真的有效——即"A/B 级标的是否真的跑赢 D 级与基准""按建议操作能否跑赢买入持有"。

**回测是现有分析层的消费者，不是重写。** 新增 `analysis/backtest.py`（回测引擎 + 时点分析 + 业绩度量）、`data/pit.py`（时点数据截断）、`visualization/backtest_charts.py`（回测图表），在 `config.py` / `main.py` / `data/fetcher.py` / `data/demo_data.py` / `tests/` 增量接线。**不动现有 step1–4 / scoring 的算法逻辑与权重**——D2 仅以"数据注入"方式复用它们，不改其内部任何一行。

项目背景与硬约束：
- **离线优先**：本机环境无网络，所有验证须以 `--backtest-demo`（seeded 模拟数据，确定性可复现）跑通；`scipy` / `plotly` 未装须软导入 + numpy 兜底（沿用 `utils/stats.py` 既有分位数范式）。
- **时点完整性（PIT）**：AkShare 的财务/现金流接口返回全历史且常含**重述后**数据，并非严格 PIT。回测须显式按"截止 as-of 日 T"截断所有输入序列，并在 README / CHANGELOG / 已知限定里**诚实标注"准 PIT"口径与重述/幸存者偏差限定**，不得宣称"严格历史可得"。
- **代码风格**：中文 docstring、纯函数、配置集中在 `config.py`、数值用 `float()` 包裹、`utils/helpers.py` 既有列名模糊匹配工具优先复用。

## 任务清单（按重要性排序）

### D1. 时点数据截断层 `data/pit.py`

- **现状**：`data/fetcher.py` 的 `fetch_daily_data` 已接受 `start_date/end_date`，但 `fetch_financial_abstract` / `fetch_cashflow_detail` / `fetch_dividend` / `fetch_stock_indicator` / `fetch_market_pe_history` / `fetch_bond_yield_history` 均返回全历史且无 as-of 参数；`main()` 直接消费"截至今天"的数据。若直接在历史日 T 调 `main()`，会用今天才有的（且可能重述后的）财务数据 → 未来函数。
- **改法**：
  1. 新增 `data/pit.py`，提供：
     - `truncate_to_date(df, date_col, as_of, inclusive=True)`：通用按日期列截断到 ≤ as_of（日期列支持 `日期`/`报告期` 等中文列名模糊匹配，复用 `utils/helpers.py` 既有工具；`as_of` 统一转 `pd.Timestamp`）。
     - `filter_reports_by_pub_lag(fin_df, as_of, lag_days=120)`：财务/现金流按"报告期 + 披露滞后"过滤——仅保留报告期末月 ≤ (as_of − lag_days) 的行（年报次年 4–5 月才披露，`lag_days=120`≈4 个月保守口径）；docstring 说明"按披露时点而非日历年末取数，避免把未披露年报当已知"。
     - `as_of_bundle(symbol, as_of, live_cache, *, demo=False)`：组合产出回测一次调用的全部截断数据（daily / fin / cashflow / dividend / stock_indicator / market_pe_history / bond_yield_history / industry_info），供 D2 直接消费。live 模式从 `live_cache`（D6 的全量预取缓存）取数后截断；demo 模式从 `generate_all_demo_data` 派生并截断。
  2. `stock_indicator`（PE/PB）与 `market_pe_history` / `bond_yield_history` 同样截断到 ≤ as_of——情绪分位须在"截止 T 的历史窗口"上计算，不得偷看 T 之后。
  3. `industry_info`（行业桶 + 总股本）月级稳定，**不截断**直接透传（实盘取最新即可，demo 透传）；docstring 注明此简化。
- **验收**：`tests/test_pit.py` 新增——构造跨 2020–2025 的日线 + 财务（含 2024 年报披露滞后场景），断言 `as_of=2024-06-30` 时日线末行 ≤ 2024-06-30、财务不含 2024 年报（报告期末 2024-12-31 > 2024-06-30 − 120d）；`truncate_to_date` 对空 df 安全返回空 df。

### D2. 时点分析适配器 `analysis/backtest.py::analyze_as_of`

- **现状**：`main.py:main(ctx)` 自带取数 + 打印 + 图表，无法在回测循环里高效复用（每只标的每个调仓日重取数 + 重打印）。需要一条"数据注入 + 静默"的精简编排路径。
- **改法**：
  1. 在 `analysis/backtest.py` 新增 `analyze_as_of(ctx_as_of, bundle) -> dict`：接收 D1 的 `as_of_bundle` 截断数据与一个 `end_date=as_of` 的 `StockContext`（`fin_end` 取 D1 过滤后实际可用的最近年报年，`fin_start = fin_end − 4`），**直接调用** `fundamental_screening` / `dcf_valuation` / `market_sentiment` / `investment_advice` / `compute_score`（顺序与 `main()` 一致），静默（抑制 `sep` 打印——给步骤函数传 `quiet=True`，无该参数的用 `redirect_stdout` 兜底），返回 `{"score", "grade", "recommendation", "latest_price", "screened", "as_of", "ctx"}`。
  2. **不复用 `main()`**：`main()` 内联取数与打印，回测要避免每日重取数与大量打印。`analyze_as_of` 是对现有纯函数的薄编排，**不改 step1–4 / scoring 内部任何一行**。`investment_advice` / `compute_score` 读 `daily_df["收盘"].iloc[-1]` / `["日期"].iloc[-1]`（见 `step4_advice.py:35-36`）——bundle 的 daily 已截断到 ≤ as_of，故 `iloc[-1]` 即"截至 T 的最新价/日"，天然 PIT 正确。
  3. `market_pe` 末值（`estimate_dividend_yield` 用）按截断后的 `market_pe_history` 末值取，与 `main()` 口径一致。
- **验收**：`tests/test_backtest.py`——用 demo bundle 在两个不同 `as_of` 调用，断言 `fin_end` 随 as_of 前移而变化、`latest_price` 为截断日线末值、返回 dict 含全部键；与 `main(ctx, quiet=True)` 在同 ctx + 同（未截断）数据下，等级/建议字段口径一致（一致性回归用例）。

### D3. 回测引擎 `analysis/backtest.py::run_backtest`

- **现状**：无回测能力，无法验证信号有效性。
- **改法**：
  1. `run_backtest(symbols, *, start, end, freq="Q", hold_days=None, top_n=10, min_grade="B", weight="equal", txn_cost=0.001, benchmark="000300", demo=False) -> BacktestResult`：
     - 调仓日序列：按 `freq`（M/Q/Y）生成 `[start, end]` 内的调仓日（`dateutil` 不可用时手写月末/季末）。
     - 每个调仓日 T：对每只标的调 `analyze_as_of`（D2）得 score/grade/recommendation/price；按 `min_grade` 与 `top_n` 选股（先筛 `grade ≥ min_grade` 再按 score 降序取 `top_n`；不足全取，空仓则该期持有现金）。
     - 组合权重：`equal` 等权 / `score` 按 score 归一加权；换仓时按 `txn_cost` 扣双边成本。
     - 持有期收益：用**未截断的原始全量日线**（非 bundle 截断版）计算 T→T+hold 的真实前向收益（`hold_days=None` 即持有至下一调仓日；用未来日线对齐），逐期记录组合收益与每只持仓个股收益。
     - 空仓期收益记 0；标的在持有期退市/停牌：按可交易日对齐，缺失记 0 并在 `positions` 标 `delisted`。
  2. `BacktestResult`（dataclass）：`equity_curve`(pd.Series 日期→净值)、`positions`(list[dict] 每期持仓与收益)、`trades`、`benchmark_curve`、`grade_forward_returns`(dict 等级→前向收益序列)、`metrics`(dict)。
  3. `grade_forward_returns`：每个调仓日把**全部**标的（不限是否入选）按 grade 分桶，记录各自 hold 期前向收益；跨期汇总得"各等级平均前向收益"——这是验证"A 是否跑赢 D"的核心证据。
- **验收**：`tests/test_backtest.py`——demo 标的清单 + `freq="Y"` 跑 3 个调仓日，断言 `equity_curve` 长度合理、`grade_forward_returns` 各等级键齐全（无 A 级时该键为空序列）、`txn_cost>0` 时净值 ≤ `txn_cost=0` 时净值；持仓个股收益与 `positions` 一致。

### D4. 业绩度量层 `analysis/backtest.py::compute_metrics`

- **现状**：无业绩度量。
- **改法**：`compute_metrics(equity_curve, benchmark_curve, risk_free, *, periods_per_year=252) -> dict`，纯 numpy 实现（scipy 不可用时无硬依赖）：
  - 总收益、`CAGR = (V_end/V_start)**(years) − 1`、年化波动率 `std(daily_ret) * sqrt(252)`、最大回撤 `max((cummax − cum) / cummax)`、`Sharpe = (年化收益 − risk_free) / 年化波动`（`risk_free` 取国债历史末值年化，缺省 0）、胜率（持有期为正收益的期数占比）、`Alpha ≈ 策略 CAGR − 基准 CAGR`、`Beta = cov(策略, 基准) / var(基准)`。
  - 基准曲线由 D5 的 `fetch_benchmark_daily` 提供，对齐到策略调仓日序列。
  - 全部数值 `float()` 包裹；空序列/常数序列回退 `None` 并在 dict 标注（不抛异常）。
- **验收**：`tests/test_backtest.py`——构造已知 `equity_curve`（如 `[1, 1.1, 1.05, 1.2]`）断言总收益≈20%、最大回撤≈(1.1→1.05)/1.1≈4.5%；常数序列 `Sharpe=None` 不爆；benchmark 同曲线时 `Beta≈1`。

### D5. 基准数据与可视化

- **现状**：无基准指数拉取；无回测图表。
- **改法**：
  1. `data/fetcher.py` 新增 `fetch_benchmark_daily(symbol="000300", start, end)`（沪深 300，akshare `stock_zh_index_daily` / `index_zh_a_hist` 多接口 fallback），返回与 `fetch_daily_data` 同构的 `日期/收盘` 表；`data/demo_data.py` 新增 `generate_benchmark_daily(seed, start, end)` 生成确定性模拟基准（与 demo 标的日线同种子流派，确保 `--backtest-demo` 可复现）。
  2. `visualization/backtest_charts.py`：
     - `plot_equity_curve(result, ctx)`：策略 vs 基准净值曲线（matplotlib 优先；plotly 软导入出交互版）。
     - `plot_drawdown(result, ctx)`：水下图（回撤序列）。
     - `plot_grade_forward_returns(result, ctx)`：各等级平均前向收益柱状图（验证 A/B/C/D 单调性）。
  3. matplotlib/plotly 软导入 + `--no-chart` 与既有图表同口径；中文标题/标签；输出到 `charts/backtest_*.{png,html}`。
- **验收**：`--backtest-demo --no-chart` 跑通不报缺依赖；`--backtest-demo` 生成 3 张图；`fetch_benchmark_daily` demo 路径返回非空且首尾日期落在 `[start, end]`。

### D6. 配置、demo 数据时序化、CLI 接线

- **现状**：`config.py` 无回测参数；`demo_data` 的财务/现金流是单期快照，不支持"按 as_of 截断后仍有多期年报"；`main.py` 无 `--backtest*` 入口。
- **改法**：
  1. `config.py` 新增（集中配置，带注释）：`BACKTEST_REBALANCE_FREQ="Q"`、`BACKTEST_HOLD_PERIOD=None`（None=至下期）、`BACKTEST_TOP_N=10`、`BACKTEST_MIN_GRADE="B"`、`BACKTEST_WEIGHT="equal"`、`BACKTEST_TXN_COST=0.001`（双边各 0.1%）、`BACKTEST_BENCHMARK="000300"`、`BACKTEST_LOOKBACK_YEARS=10`、`BACKTEST_PUB_LAG_DAYS=120`、`BACKTEST_RISK_FREE=None`（None=取国债历史末值年化）。
  2. `data/demo_data.py`：把财务/现金流/分红/PE-PB 的 demo 生成改为**跨 10 年多报告期序列**（按标的种子派生逐年报告期行，ROE/净利/FCF 带温和趋势与噪声），使 D1 截断到任意 as_of 都能取到"该时点已知的多期财务"。保留向后兼容（`main --demo` 仍取最近若干年，口径不变）。
  3. `main.py`：新增 `--backtest FILE`（实盘，读 `代码,名称` 清单）与 `--backtest-demo`（内置 `BATCH_DEMO_LIST` + 模拟数据）；调用 `run_backtest` → `compute_metrics` → 图表 → 打印业绩表 + 各等级前向收益表 + 结论（"A 级平均前向收益 X% vs D 级 Y%，信号{有效/无效}"）。`--years` 复用为回测区间；`--out-dir` 复用。
- **验收**：`python main.py --backtest-demo` 全流程跑通，打印业绩表 + 等级前向收益表 + 生成图表；`--backtest-demo --no-chart` 不生成图；`python main.py --demo` 与 `--batch-demo` 零回归（demo_data 时序化后取数口径不变）。

## 实施约束

1. **零回归**：`python main.py --demo` / `--batch-demo` 跑通且口径不变；`python -m pytest -q` 全绿（含新增回测用例）。demo_data 时序化须保证 `main --demo` 取数口径与现状一致（最近若干年财务快照）。
2. **风格一致**：中文 docstring、纯函数、配置集中在 `config.py`、数值 `float()` 包裹、`utils/stats.py` / `utils/helpers.py` 既有工具优先复用。
3. **离线可验证**：`scipy`/`plotly` 软导入 + numpy 兜底（沿用 `utils/stats.py` 范式）；`--backtest-demo` 全程无网络。`fetch_benchmark_daily` 实盘路径本环境无网，需联网复验（在 CHANGELOG/已知限定注明）。
4. **不动**：`step1_fundamental.py` / `step2_dcf.py` / `step3_sentiment.py` / `step4_advice.py` / `scoring.py` 的算法与权重；`main()` 现有单标的/批量路径行为（仅新增 `--backtest*` 分支）。
5. **诚实标注限定**：README / CHANGELOG / 已知限定新增"准 PIT（AkShare 财务可能重述）+ 幸存者偏差（仅含当前在市标的）+ 简化成本（未计滑点/税/停牌流动性）"三段说明，不得宣称严格历史回测。

## 验收清单

- [ ] `data/pit.py`：`truncate_to_date` / `filter_reports_by_pub_lag` / `as_of_bundle`，`test_pit.py` 覆盖截断与披露滞后
- [ ] `analyze_as_of` 复用 step1–4 + scoring，不改其内部；与 `main()` 口径一致性回归
- [ ] `run_backtest` 调仓/选股/持有/换仓成本/退市兜底，`BacktestResult` 字段齐全
- [ ] `grade_forward_returns` 各等级前向收益，验证 A/B/C/D 单调性证据
- [ ] `compute_metrics` 纯 numpy：总收益/CAGR/波动/最大回撤/Sharpe/胜率/Alpha/Beta，空序列不爆
- [ ] `fetch_benchmark_daily` + demo 生成器；`backtest_charts` 净值/回撤/等级柱状图
- [ ] `config.py` 回测参数集中配置；`demo_data` 财务多期时序化且 `--demo` 口径不回归
- [ ] `main.py` `--backtest`/`--backtest-demo` 入口，打印业绩表 + 等级表 + 结论
- [ ] `pytest -q` 全绿；`--demo`/`--batch-demo`/`--backtest-demo` 跑通零回归
- [ ] README/CHANGELOG 同步回测模块 + 准 PIT/幸存者/成本三段限定
