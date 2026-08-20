# 提示词 A — DCF 估值层合理性优化

> 目标受众：编码助手（Claude Code 等）。本文件自包含，可直接作为任务指令执行。

## 角色与目标

你正在改进一个基于 AkShare 的 A 股长线价值投资量化分析系统（Python，模块化：`config.py` / `analysis/*` / `data/*` / `utils/*` / `tests/*`）。
本次任务**只动 DCF 估值层**（`analysis/step2_dcf.py` 及其在 `config.py` 的画像配置、`tests/test_dcf.py`），目的是修正 5 处影响"估值基数合理性"的偏差。**不要改动评分、情绪、建议模块。**

项目背景：DCF 采用"5 年显性期 + 永续"单阶段模型，基期 FCF = 近 5 年加权均值，参数随行业桶（`INDUSTRY_PROFILES`）差异化。代码风格为中文 docstring + 纯函数 + config 集中配置。

## 任务清单（按重要性排序）

### A1. capex 取不到时按行业差异化，不再用固定 0.20

- **现状**：`analysis/step2_dcf.py:168-170`，capex 缺失年用 `capex = abs(ocf) * 0.20` 兜底。重资产股（钢铁/公用事业/采掘 capex 常占 OCF 40-60%）FCF 虚高，轻资产股（软件/消费 <10%）FCF 虚低，污染整个 DCF 基数。
- **改法**：
  1. 在 `config.INDUSTRY_PROFILES` 每个桶新增字段 `capex_ratio`（建议取值：周期 0.45、其他 0.20、消费 0.15、成长 0.25、银行/非银 0.10），在 `config.py` 顶部注释说明含义（"capex 取不到时按 OCF 的该比例估算维持性支出"）。
  2. `step2_dcf.py` 兜底处改用 `profile["capex_ratio"]`，并在该年走兜底时记录一个标记（如 `fcf_estimated_years` 集合），供下游完整度置信度使用（本任务只记录、不接线，输出到 dcf 返回 dict 的 `capex_estimated` 布尔即可）。
- **验收**：`capex_ratio` 在 `INDUSTRY_PROFILES` 6 桶齐全；`test_industry.py` 增断言各桶含该字段；兜底路径单测覆盖"周期桶取不到 capex 时用 0.45"。

### A2. 显性增长率由两点 CAGR 改为最小二乘拟合

- **现状**：`analysis/step2_dcf.py:44-60` 的 `derive_explicit_growth` 用 `(last/first)**(1/n)-1`，只用首末两点，中间年份忽略。周期股首末落在峰/谷时严重失真。
- **改法**：对 `log(净利润)` 序列做最小二乘线性回归，斜率 `s` 即隐含增长率，`exp(s)-1` 为 CAGR。要求 ≥3 个正值点；首点或末点为非正时回退（保留现有 None 回退到行业永续的逻辑）。clip 区间 `DCF_GROWTH_CAGR_CLIP` 不变。用 `numpy.polyfit(np.log(profits), 1)` 或手写正规方程，不要引入新依赖。
- **验收**：`test_dcf.py` 新增用例：构造 `[10, 8, 12, 14, 16]`（首末两点法 CAGR≈12.6%，最小二乘≈12%），断言落在 clip 内且方向合理；原"不足 3 年返回 None"用例不变。

### A3. 估值安全边际映射改非线性，恢复极端值区分度

- **现状**：`analysis/scoring.py:152-156`（注：本任务**不动 scoring.py**，只在 dcf 层暴露更细的原始值；如需改映射请在**对应评分任务 B**中处理）。本任务在 `step2_dcf.py` 的返回 dict 中新增 `margin_neutral_pct` / `margin_conservative_pct`（相对当前价的原始百分比，不 clip），供后续评分层使用更平滑映射。
- **改法**：在 `dcf_valuation` 返回 dict 中增加上述两个字段（当前价由调用方传入，本函数无 price——故改为：在已有 `conservative`/`neutral`/`liquidation` 基础上保持不变，**本条 A3 降级为"仅记录待办"**，实际映射改造归入任务 B）。即：本文件**不实际改 A3 的映射**，只在 docstring 末尾留 TODO 注明"安全边际非线性映射见 prompt B"。
- **验收**：无代码改动要求，仅 docstring TODO。

### A4. 主 DCF 路径补 `wacc <= perpetual` 防御性 guard

- **现状**：`analysis/step2_dcf.py:253-254` `terminal_value = terminal_fcf / (wacc - perp_g)`。敏感性网格有 `if w <= perp: nan` 守卫，但主路径没有。当前画像配置安全（成长 8.5%/2.5%），但配置被改或行业永续抬升时会除零/得负 terminal value 爆炸。
- **改法**：在三情景循环内，`if wacc <= perp_g:` 跳过该情景（intrinsic_value 置 None 并打印 `[!] wacc≤永续，跳过该情景`），保证不产生负/无穷内在价值。下游 `investment_advice` 已对 None 有兜底，确认不回归。
- **验收**：`test_dcf.py` 新增用例：构造 `perp > wacc` 的 scenario_params，断言该情景返回 None 而非负数/异常。

### A5. 破产清算 D&A 不可得时不再回退归母净利润

- **现状**：`analysis/step2_dcf.py:206-213`，D&A 不可得时 `liquidation_fcf_values[year] = net_profit_values.get(year, 0)`。语义上破产清算应"移除非现金加回"，用净利润反而把非现金收益加回，常导致 `liquidation > conservative`，靠 `[step2_dcf.py:279-281]` 事后钳位补救。
- **改法**：D&A 不可得分支改为 `liquidation_fcf_values[year] = fcf_values.get(year, 0) * 0.5`（与 `all_liq` 为空时的 `base_fcf*0.5` 口径一致），并在打印里把回退说明从"回退归母净利润"改为"按 FCF×0.5 估算清算口径"。保留事后 `liquidation ≤ conservative` 钳位（现 inert，作安全网）。
- **验收**：`test_dcf.py` 断言 D&A 不可得时 `liquidation ≤ conservative` 自然成立（不依赖钳位）；docstring 同步更新口径说明。

## 实施约束

1. **零回归**：`python main.py --demo` 必须仍能跑通且输出阶梯（破产清算 ≤ 保守 ≤ 合理估值上限）保持单调；`--batch-demo` 排名正常返回。
2. **风格一致**：中文 docstring、纯函数、配置集中在 `config.py`、数值用 `float()` 包裹；新字段在 `dcf_valuation` 返回 dict 里补 docstring 说明。
3. **测试**：更新 `tests/test_dcf.py` 与 `tests/test_industry.py` 覆盖上述改动；`python -m pytest -q` 全绿。
4. **不动**：`scoring.py` / `step3_sentiment.py` / `step4_advice.py` / `step1_fundamental.py` 本任务不改（A3 映射归 B，CAGR/股息率归 C）。

## 验收清单

- [ ] `INDUSTRY_PROFILES` 6 桶均含 `capex_ratio`，`test_industry.py` 守护
- [ ] `derive_explicit_growth` 改最小二乘，新旧用例通过
- [ ] 主 DCF 对 `wacc≤perp` 返回 None 不爆
- [ ] D&A 不可得时破产清算口径改为 FCF×0.5
- [ ] `dcf_valuation` 返回 dict 新增 `capex_estimated` 字段并文档化
- [ ] `pytest -q` 全绿，`--demo` / `--batch-demo` 跑通
