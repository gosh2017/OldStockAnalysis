# 量化价值投资分析系统

基于 **AkShare** 的 A 股长线价值投资量化分析工具，按照四步逻辑对目标股票进行全面评估。

## 项目结构

```
OldStockAnalysis/
├── main.py                  # 主入口，编排各分析步骤
├── config.py                # 全局配置参数（标的、日期、DCF 参数等）
├── utils/
│   ├── __init__.py
│   └── helpers.py           # 通用工具：重试封装、列名查找、股息率估算等
├── data/
│   ├── __init__.py
│   └── fetcher.py           # 数据获取层：6 个 AkShare 数据拉取函数
├── analysis/
│   ├── __init__.py
│   ├── step1_fundamental.py # 第一步：基本面筛选（ROE / 股息率 / 利润质量）
│   ├── step2_dcf.py         # 第二步：DCF 三情景估值
│   ├── step3_sentiment.py   # 第三步：市场情绪（股债性价比）
│   └── step4_advice.py      # 第四步：综合投资建议
├── visualization/
│   ├── __init__.py
│   └── charts.py            # 估值走势图（matplotlib + plotly）
├── requirements.txt         # 依赖清单
├── README.md
└── charts/                  # 输出图表目录
```

## 功能概述

| 步骤 | 模块 | 内容 |
|------|------|------|
| Step 1 | `analysis/step1_fundamental.py` | ROE / 股息率 / 资产负债率 / 利润质量，判断是否符合长线价值投资标准 |
| Step 2 | `analysis/step2_dcf.py` | 自由现金流折现（DCF），输出保守 / 中性 / 乐观三情景内在价值 |
| Step 3 | `analysis/step3_sentiment.py` | 全市场 PE 中位数 + 10 年期国债收益率 → 股债性价比及历史分位数 |
| Step 4 | `analysis/step4_advice.py` | 结合估值和情绪，给出具体操作建议 |

## 使用方法

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 运行分析（默认分析 000001 平安银行）
python main.py

# 3. 指定其他股票
python main.py 600519 -n 贵州茅台
```

## 配置

修改 [config.py](config.py) 中的参数可自定义分析行为：

| 参数 | 说明 |
|------|------|
| `STOCK_CODE` | 股票代码 |
| `STOCK_NAME` | 股票名称 |
| `FIN_START` / `FIN_END` | 基本面分析年份范围 |
| `SCENARIOS` | DCF 三情景参数（增长率 / 永续增长 / WACC） |

## 输出

- 终端打印：各步骤详细分析结果 + 最终摘要
- `charts/valuation_000001.png`：股价 vs 内在价值走势图（matplotlib）
- `charts/valuation_000001.html`：交互式估值图（Plotly，可选）

## 依赖

Python 3.9+ / akshare / pandas / numpy / matplotlib / plotly / scipy
