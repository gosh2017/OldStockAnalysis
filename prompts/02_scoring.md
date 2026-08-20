# 提示词 B — 综合评分层合理性优化

> 目标受众：编码助手（Claude Code 等）。本文件自包含，可直接作为任务指令执行。

## 角色与目标

你正在改进一个基于 AkShare 的 A 股长线价值投资量化分析系统（Python，模块化）。本次任务**只动综合评分层**（`analysis/scoring.py`、`config.py` 的评分权重、`tests/test_scoring.py`），目的是修正 4 处影响"分数可比性与可信度"的偏差。**不要改动 DCF、情绪、建议模块的算法逻辑。**

项目背景：总分 = 质量×0.40 + 估值×0.35 + 情绪×0.25；任一子指标缺失时**类内丢弃权重并重新归一化**；另有独立的"完整度置信度"（高/中/低标签）。代码风格：中文 docstring、纯函数、配置集中在 `config.py`。

## 任务清单（按重要性排序）

### B1. 评分对完整度做温和折让，解决"数据稀疏却高分"

- **现状**：`analysis/scoring.py:36-49`（`_category_score` 类内重归一）+ `scoring.py:285-294`（顶层重归一）+ `scoring.py:184-243`（`_completeness` 只算标签）。结果：一家只剩 2 个子指标能算的标的，剩余指标权重膨胀，仍可得 A，完整度仅挂"低"标签，不反映到分数。
- **改法**：在 `compute_score` 最终 `score = _clip(score)` 之前，做温和折让：
  `score = score * (0.70 + 0.30 * completeness / 100)`。
  - 即完整度 100 → 不折让（×1.0），完整度 0 → ×0.70。
  - 在 `config.py` 新增 `SCORE_COMPLETENESS_PENALTY = {"floor": 0.70, "weight": 0.30}` 集中配置，docstring 说明"避免数据稀疏标的因重归一而虚高"。
  - 打印层（`main.py` 综合评分块）追加一行 `[INFO] 完整度折让: ×{factor:.2f}`，透明可见。
- **验收**：`test_scoring.py` 新增用例——构造"质量分 90 但完整度 20"的输入，断言折让后分数 < 90×0.40 的对应预期；完整度 100 时分数不变。

### B2. ROE 稳定性 CV 对"稳定地差"的公司虚高 → 加水平调制

- **现状**：`analysis/scoring.py:89-94`，`roe_stability = 100 - (std/mean)*100`。ROE 长期 2%±0.5% 的公司 CV 小 → 稳定性分高，但它"稳定地差"，与 ROE 水平分独立加权后叠加虚高。
- **改法**：稳定性分乘水平调制因子 `modulation = min(1.0, roe_mean / roe_benchmark)`，即
  `subs["roe_stability"] = _clip((100 - (std/mean)*100) * min(1.0, mean/roe_benchmark))`。
  - ROE 达基准 → 调制 1.0（不衰减）；ROE 仅基准一半 → 调制 0.5。
  - 均值≈0 的回退分支同步乘调制。
- **验收**：`test_scoring.py` 用例——ROE=2%±0.5%（benchmark=15%）的稳定性分明显低于 ROE=15%±3% 者，断言单调关系合理。

### B3. 资产负债率改用近 1 年值，不再用历史 max

- **现状**：`analysis/scoring.py:117`，`debt_s.max()`。某年因并购负债率冲到 80% 后回落 40%，max 仍判低分，过于严苛且不反映当前杠杆。
- **改法**：改用 `debt_s.iloc[-1]`（最近一年，table 已按年份排序，末行即最新年）。映射函数不变（`100 - max(0, val-50)*2`）。在注释里说明"反映当前杠杆而非历史峰值"。
- **验收**：`test_scoring.py` 用例——`[60, 80, 40]`（末值 40）应得分高于 `[40, 40, 40]`？不，应断言末值 40 → 高分、且不因历史峰值 80 被压低。

### B4. OCF 质量改用中位数，不再用均值

- **现状**：`analysis/scoring.py:110`，`ocf_s.mean()*100`。亏损年净利润为负时 OCF/净利润 比值异常巨大，均值被拉偏。
- **改法**：改 `ocf_s.median()`。比例 ≥1 → 100 的 clip 上限不变。注释说明"中位数抗亏损年极值"。
- **验收**：`test_scoring.py` 用例——`[0.9, 1.1, 25.0]`（含一异常高值）中位数 1.1 → 100 分（clip），均值法会被 25 拉高但已 clip，重点断言中位数路径不被单点主导、且与原均值法在正常序列下结果接近。

## 实施约束

1. **零回归**：`python main.py --demo` 综合评分块正常打印；`--batch-demo` 排名仍产出。等级分档 `GRADE_BANDS` 不变。
2. **风格一致**：中文 docstring、纯函数、配置集中在 `config.py`；新增配置项有注释。
3. **测试**：更新 `tests/test_scoring.py` 覆盖 B1-B4；`python -m pytest -q` 全绿。
4. **不动**：`step2_dcf.py` / `step3_sentiment.py` / `step4_advice.py` / `step1_fundamental.py` 本任务不改。B1 用到的 `completeness` 计算逻辑（`_completeness`）保持现口径，不改其加权。

## 验收清单

- [ ] `config.SCORE_COMPLETENESS_PENALTY` 集中配置，`compute_score` 实施折让
- [ ] 完整度折让在终端打印可见
- [ ] ROE 稳定性分含水平调制因子
- [ ] 资产负债率用近 1 年值
- [ ] OCF 质量用中位数
- [ ] `test_scoring.py` 新增 4 组用例，`pytest -q` 全绿
- [ ] `--demo` / `--batch-demo` 跑通
