# 数据查询接口迁移至 Hikyuu 本地库 — 实施计划

> 目标：将项目的**数据查询类接口**（历史/静态数据）从 AkShare 实时 HTTP 切换到 Hikyuu 本地库（pytdx 一次性导入 → HDF5 kdata + SQLite stock.db），**实时数据除外**。根因：东财/乐咕/申万等 HTTP 端点频繁连不上（见已落地的 `fetch_stock_screening_data` 迁移与若干 memory 记录）；Hikyuu 查询全走本地、零运行时 HTTP。

> 调研基线：Hikyuu 2.7.2（本地 `G:\Python3.13\Lib\site-packages\hikyuu\`），源码核实见 `cpp\core313.pyi`、`data\sqlite_upgrade\0014.sql`、`data\pytdx_finance_to_sqlite.py`、`gui\data\UsePytdxImportToH5Thread.py`。

---

## 1. 范围与边界

### 1.1 迁移（历史/静态数据 → Hikyuu）
| 接口 | 当前源 | Hikyuu 能力 | 处置 |
|---|---|---|---|
| `fetch_daily_data` | ak `stock_zh_a_daily`/`stock_zh_a_hist` | `stock.get_kdata(Query, recover=FORWARD)` | **迁移** |
| `fetch_benchmark_daily` | ak `stock_zh_index_daily`/`index_zh_a_hist` | `sm['sh000300'].get_kdata` | **迁移** |
| `fetch_financial_abstract` | ak `stock_financial_abstract` | `HistoryFinance` 三表（581 字段） | **迁移**（需开 finance 导入） |
| `fetch_cashflow_detail` | ak `stock_financial_report_sina` | `HistoryFinance` 现金流量表字段 | **迁移**（需开 finance 导入） |
| `fetch_dividend` | ak `stock_history_dividend_detail`/`stock_dividend_cninfo` | `stock.get_weight()`（`bonus`） | **迁移** |
| `fetch_bond_yield_history` / `fetch_bond_yield_10y` | ak `bond_china_yield` | `zh_bond10` SQLite 表（1990 起） | **迁移**（运行时本地；导入期仍依赖 akshare） |
| `fetch_stock_indicator` | ak `stock_a_indicator_lg`/`stock_zh_valuation_baidu` | 无内置；从 kdata+finance+weight 自算 | **迁移**（PB 易、PE_TTM 复杂，分两步） |
| `fetch_industry_info` | ak `stock_individual_info_em` | `get_belong_to_block_list` + `get_weight` | **迁移**（与批量筛选同源，统一行业口径） |
| `fetch_stock_list` | ak 交易所 xls + 新浪 spot | `sm` 迭代（code+name） | **迁移** |
| `fetch_stock_screening_data` | — | 已迁移（`_fetch_stock_screening_data_hikyuu`） | **已完成** |

### 1.2 不迁移（实时数据 — 用户明确排除）
| 接口 | 原因 |
|---|---|
| `fetch_market_overview` | 全市场实时 spot 快照（东财 `stock_zh_a_spot_em`），属实时数据。且 `main` 实盘路径已置 `market_df=None`（被 `fetch_market_pe_history` 取代），实际未在主链路调用。**保留 AkShare，不动。** |

### 1.3 需决策的特例
- **`fetch_market_pe_history`（乐咕市场历史 PE）**：Hikyuu **无原生市场 PE 序列**。乐咕 `stock_market_pe_lg` 稳定可取全历史（见 `market-sentiment-datasource` memory），并非当前断连痛点。两条路：
  - **A（推荐）**：保留乐咕 AkShare 作为单一稳定非实时源，计划中明确标注其为「唯一非实时 HTTP 残留」+ 给出 Hikyuu 自算路径作为后续增强项。
  - **B（严格遵循「除实时外全迁」）**：从 Hikyuu 上证指数成份股自算 `Σ市值 / ΣTTM净利润` 历史序列。成本高（~1600 股 × 日期 × TTM）、幸存者/成份变动偏差大、与乐咕权威序列难对齐。
  - **见 §10 决策点 1。** 本计划默认采用 A，并在 §6.8 给出 B 的设计草稿供择期实施。

---

## 2. Hikyuu 能力对照（源码核实）

### 2.1 初始化与加载
- `hku.load_hikyuu(load_history_finance, load_weight, start_spot)` —— `__init__.py:219`。
  - `load_history_finance=True` 才会把 `HistoryFinance` 载入内存（**当前各调用点为 False，必须改 True**）。
  - `load_weight=True` 才有 `get_weight()` 的 total_count/bonus（当前已 True）。
- `sm = hku.sm`（StockManager）；`Query = hku.Query`。

### 2.2 Query / KData
- `Query(start=0, end=None, ktype='DAY', recover_type=Query.NO_RECOVER)`（`core313.pyi:4215`）。
  - 索引查询：`Query(-1)` 末根；`Query(-150)` 最近 150 根；`Query(0,10)` 前 10 根；`[start,end)` 半开。
  - 日期查询：`Query(Datetime(YYYYMMDD0000), Datetime(YYYYMMDD0000), ktype=Query.DAY, recover_type=...)`。
  - `recover_type`：`Query.NO_RECOVER` / `FORWARD`(前复权) / `BACKWARD`(后复权) / `EQUAL_FORWARD`(等比前复权)。
- `stock.get_kdata(Query)` → KData；逐根 `KRecord` 有 `.datetime/.open/.high/.low/.close/.volume/.amount`。
- **复权**：现 akshare 用 `qfq`（前复权）→ Hikyuu `Query.FORWARD`。两者同为前复权但方法略异（ acceptable，文档标注）。
- **指数代码**：Hikyuu 用 `sh000300`/`sh000001`/`sz399001`/`sz399006`（**不是** `000300`/`999999`）。

### 2.3 财报 HistoryFinance（关键）
- 表：`HistoryFinance(id, file_date, report_date INT(YYYYMMDD), values BLOB)` + `HistoryFinanceField(id, name)`，581 字段（`0014.sql`）。
- 运行时 API：
  - `stock.get_history_finance() -> list`（`core313.pyi:5143`）
  - `sm.get_history_finance_all_fields() -> list` / `sm.get_history_finance_field_index(name) -> int` / `sm.get_history_finance_field_name(index) -> str`（`core313.pyi:5547-5562`）
  - `FINANCE(name|ix, [kdata])` 指标（`core313.pyi:9355`），按 KData 日期对齐 —— 单股日序列派生 PE/PB 时优先。
- **所需字段 id**：
  | 字段 | id | 用途 |
  |---|---|---|
  | 利润表_归属于母公司所有者的净利润 | 96 | DCF 净利 / PE_TTM |
  | 利润表_净利润 | 95 | 兜底 |
  | 现金流量表_经营活动产生的现金流量净额 | 107 | DCF OCF |
  | 现金流量表_购建固定资产、无形资产和其他长期资产支付的现金 | 114 | DCF capex |
  | 折旧摊销 | 136 / 137 | 破产清算 D&A（合并列优先，否则 136+137） |
  | 盈利能力_加权净资产收益率(每股指标) | 281 | step1 ROE（兜底 id 6/197） |
  | 资本结构_资产负债率 | 210 | step1 资产负债率 |
  | 资产负债表_归属于母公司股东权益 | 271 | step1 权益 / PB |
  | 每股净资产 | 4 | PB 自算（便捷） |
  | 股本股东_总股本 | 238 | 总股本交叉校验 |
- **单位坑（致命）**：HistoryFinance 金额字段单位不一致——部分字段名带「万元」（如 id 502/283/308-310），多数（如 id 95 净利润）无后缀但为 gpcw 原始 float。blob 0-indexed（field id N 在 array index N-1）。**优先用 `FINANCE(name)` 按名取，避免 off-by-one；金额字段必须逐字段探测单位后归一到「元」（与现 akshare 契约一致，下游按 1e8 转亿元）。** 见 §7 校准探针。
- **前置开关（两道）**：① 导入 `[finance] enable=True`（`UsePytdxImportToH5Thread.py:95` → `ImportHistoryFinanceTask`）；② 运行时 `load_history_finance=True`。**当前两道均关 → `FINANCE()`/`get_history_finance()` 返回空。**

### 2.4 分红 / 权息
- `stock.get_weight(start, end) -> StockWeightList`（`core313.pyi:5245`）。每条 `StockWeight`：
  - `datetime`（权息日期，`Datetime`）、`bonus`（**每 10 股红利**，元/10股 → 每股 = bonus/10）、`count_as_gift`(送股)、`increasement`(转增)、`count_for_sell`(配股)、`price_for_sell`(配股价)、`total_count`(**万股**)、`free_count`(万股)、`suogu`。
  - 也有 `StockWeightList.to_df()` / 模块级 `weights_to_df()`。
- 现有 `_hku_total_count` 已正确用 `total_count`（万股）；分红用 `bonus`（每10股，与 akshare `派息` 列口径一致，`_normalize_div_per_share` 对「派息」列按 /10 处理）。

### 2.5 国债
- `zh_bond10(date, value)` SQLite 表（`zh_bond10_to_sqlite.py` → `get_china_bond10_rate` → `ak.bond_zh_us_rate`）。`value` 为基点 int（rate×10000）。1990-12-19 起。
- 导入**无条件运行**（`UsePytdxImportToH5Thread.py:103`），但依赖 akshare；离线/失败时 `@hku_catch` 静默空表。
- 运行时：直接读 SQLite 表（`SELECT date,value FROM zh_bond10 ORDER BY date`）→ `/10000` 归一小数，**运行时零 HTTP**。或用 `ZHBOND10(kdata, default_val)` 指标（按 KData 日期对齐，`core313.pyi:14176`；`default_val=0.4` 是占位，须自传）。

### 2.6 PE/PB 自算
- 无内置 `hku.PE()/PB()`。原料齐备：`close`(kdata) + `FINANCE("利润表_归属于母公司所有者的净利润")` + `total_count`(weight,×1e4) + `FINANCE("每股净资产")`/`FINANCE("资产负债表_归属于母公司股东权益")`。
- `PB = close / 每股净资产`（每股净资产 id 4，按最近报告期 PIT 取值，简单）。
- `PE_TTM = close / (TTM_EPS)`，TTM 需由季报累加最近 4 个单季度净利润构造（复杂，见 §6.7）。

---

## 3. 前置条件（必须先做）

### 3.1 数据导入配置变更（`~/.hikyuu/importdata-gui.ini`）
当前配置（见 `scripts/run_hikyuu_import.py` 注释）：
```
day=True  (day_start_date=2024-01-01)   # 仅近 2 年 —— 筛选够用，日线/回测不够！
weight=True
block=True
fund/min/min5/finance=False             # finance 关闭 —— 财报迁移必需开启
```
**改为**：
```
[day]   enable=True, day_start_date=20100101   # 覆盖 START_DATE(2016) + 回测 10 年回溯(2016) + 余量
[weight] enable=True
[block] enable=True
[finance] enable=True                         # 新开：导入 HistoryFinance 三表
# fund/min/min5 仍 False（不需要）
```
- **day 回溯到 2010**：`fetch_daily_data` 默认 `START_DATE=20160101`；回测 `BACKTEST_LOOKBACK_YEARS=10`（~2016 起）。留余量到 2010 避免边界缺数。**这是一次性重导**，HDF5 体积与导入耗时显著增加（小时级），但之后运行时全本地。
- **finance 导入**：`ImportHistoryFinanceTask` 下载 TDX gpcw → 填 `HistoryFinance`+`HistoryFinanceField`。TDX 财报历史 ~10+ 年，覆盖 `FIN_START=2021` 与回测窗。

### 3.2 运行时加载标志统一
所有 `load_hikyuu(...)` 调用点改为：
```python
hku.load_hikyuu(load_history_finance=True, load_weight=True, start_spot=False)
```
当前 `_fetch_stock_screening_data_hikyuu` 与 `run_hikyuu_import.py` 验证段用的是 `load_history_finance=False`。迁移后单股路径（daily/finance/dividend/indicator/industry）均需 `load_history_finance=True`。**集中到访问层 §4 一次调用、进程级缓存，避免重复 load。**

### 3.3 新增共享访问层 `data/hikyuu_backend.py`
（详见 §4）承载：惰性单次 `load_hikyuu`、symbol→Stock 解析（含 bj 前缀）、kdata→DataFrame、finance 字段读取、单位归一、weight 读取、bond sqlite 直读。

### 3.4 requirements.txt
`hikyuu>=2.8` 已在「可选依赖」。维持可选（未装/未导入时各 fetcher 降级到 akshare fallback，保 `--demo` 离线不变）。可在注释补一句「单股历史数据迁移后推荐安装并完成一次性导入」。

---

## 4. 共享 Hikyuu 访问层设计（`data/hikyuu_backend.py`）

迁移涉及 ~9 个 fetcher，避免在每个里重复 `import hikyuu`/`load_hikyuu`/前缀映射/单位换算。新增 `data/hikyuu_backend.py`：

```python
# data/hikyuu_backend.py
"""Hikyuu 本地库统一访问层。惰性单次 load_hikyuu（进程级缓存），
symbol→Stock 解析、kdata→DataFrame、finance 字段读取、单位归一、
weight/bond 读取。未安装/未导入时各函数返回 None/空，由调用方降级到 akshare。"""
import pandas as pd
from config import HIKYUU_DB_PATH  # 新增：r"c:\stock\stock.db"

_HKU = None  # 缓存已 load 的 hikyuu 模块与 sm/Query/Datetime

def _hku():
    """惰性 load_hikyuu（load_history_finance=True, load_weight=True）。
    失败（未装/未导入/StockManager 空）返回 None。进程内只 load 一次。"""
    global _HKU
    if _HKU is not None:
        return _HKU
    try:
        import hikyuu as hku
        hku.load_hikyuu(load_history_finance=True, load_weight=True, start_spot=False)
        if not len(hku.sm):
            return None
        _HKU = hku
        return _HKU
    except Exception:
        return None

def hku_stock(symbol):
    """6 位代码 → hku.Stock（sh/sz/bj 前缀）。无效/不在库 → 返回 null stock（.valid=False）。"""
    hku = _hku()
    if hku is None:
        return None
    s = str(symbol).zfill(6)
    if s.startswith("6"):      prefix = "sh"
    elif s.startswith(("0","3")): prefix = "sz"
    elif s.startswith(("43","83","87","92")): prefix = "bj"   # 北交所
    else: prefix = "sz"
    try:
        return hku.sm[f"{prefix}{s}"]
    except Exception:
        return None

def hku_index_stock(symbol):
    """指数代码 → Stock。000300→sh000300, 999999/000001(上证综指)→sh000001, 399001→sz399001。
    传入 6 位裸码时按 6→sh、0/3→sz 加前缀（指数段：000xxx=沪、399xxx=深）。"""
    hku = _hku()
    if hku is None: return None
    s = str(symbol).zfill(6)
    prefix = "sh" if s.startswith("0") else ("sz" if s.startswith("3") else "sh")
    try: return hku.sm[f"{prefix}{s}"]
    except Exception: return None

def kdata_to_df(kdata, cols=("日期","开盘","收盘","最高","最低","成交量","成交额")):
    """KData → DataFrame（中文列名，与 _normalize_daily_df 契约一致）。空 KData → 空 df。"""
    if kdata is None or not len(kdata):
        return pd.DataFrame()
    rows = [{"日期": r.datetime, "开盘": r.open, "收盘": r.close, "最高": r.high,
             "最低": r.low, "成交量": r.volume, "成交额": r.amount} for r in kdata]
    return pd.DataFrame(rows)

def fetch_kdata_df(symbol, start, end, *, index=False, recover="FORWARD"):
    """日线 [start,end] → DataFrame。index=True 用 hku_index_stock；recover 选 NO_RECOVER(指数)/FORWARD(个股前复权)。"""
    hku = _hku()
    if hku is None: return pd.DataFrame()
    st = hku_index_stock(symbol) if index else hku_stock(symbol)
    if st is None or not st.valid: return pd.DataFrame()
    rt = hku.Query.NO_RECOVER if recover == "NO_RECOVER" else hku.Query.FORWARD
    q = hku.Query(hku.Datetime(f"{start}0000"), hku.Datetime(f"{end}0000"),
                  ktype=hku.Query.DAY, recover_type=rt)
    return kdata_to_df(st.get_kdata(q))

# finance 字段读取 / 单位归一 / weight / bond —— 见 §6 各接口
```

设计要点：
- **进程级单次 load**：`_hku()` 缓存，避免每个 fetcher 重复 load_hikyuu（昂贵）。
- **null stock 防御**：`st.valid` 复用筛选路径已验证的判据。
- **bj 前缀**：现 `_prefix_symbol` 对北交所返回裸码（漏），访问层补 `bj`。
- **契约复用**：`kdata_to_df` 产出与 `_normalize_daily_df` 同构列名，可直接复用下游归一/消费链。

---

## 5. 通用迁移模式（akshare fallback 保留）

每个被迁移的 fetcher 采用统一结构（以 `fetch_daily_data` 为模板）：

```python
def fetch_daily_data(symbol=STOCK_CODE, start_date=None, end_date=None):
    start, end = start_date or START_DATE, end_date or END_DATE
    # 1) 优先 Hikyuu 本地库
    df = hku_fetch_kdata_df(symbol, start, end, index=False, recover="FORWARD")
    if df is not None and not df.empty:
        print(f"  [OK] 使用 Hikyuu 本地库获取日频数据")
        return _normalize_daily_df(df)          # 复用既有归一
    # 2) 降级 akshare（hikyuu 未装/未导入/标的不在库/退市新股）
    print("  [INFO] Hikyuu 不可用，降级 AkShare …")
    return _fetch_daily_data_ak(symbol, start, end)   # 原 strategies 逻辑移入私有函数
```

- **降级触发**：hikyuu 未装 / `sm` 空 / 该标的不在库（新股/退市未导入） / KData 空。保留 akshare 私有实现作为 fallback，**不丢 robustness**。
- **`--demo` 不变**：demo 走 `generate_all_demo_data`，完全不触 hikyuu/akshare（`main` 的 `if ctx.demo` 分支）。访问层 `_hku()` 仅在 live 分支调用。
- **缓存**：现 `fetch_bond_yield_history`/`fetch_market_pe_history`/`fetch_stock_indicator`/`fetch_industry_info`/`fetch_stock_list` 已带 `disk_cache`。迁移后**保留** disk_cache（hikyuu 本地查询虽快，但 finance/bond 全量读仍可观；24h TTL 合理）。日线/财报无缓存（每次按需查本地）。

---

## 6. 逐接口迁移方案

> 每条给：Hikyuu API、**返回契约（必须保持的列名/单位，下游 find_col_in/直接索引依赖）**、单位/fallback/gotcha。

### 6.1 `fetch_daily_data` → Hikyuu KData（前复权）
- **API**：`fetch_kdata_df(symbol, start, end, index=False, recover="FORWARD")`（§4）。
- **契约**：`_normalize_daily_df` 期望列 日期/开盘/收盘/最高/最低（+成交量/成交额）。`kdata_to_df` 已产出同名中文列。下游：`main` 取 `收盘` 末值；step1/step2 用 `日期`/`收盘`；backtest 用 `日期`/`收盘`；`_get_total_shares` 兜底取 `outstanding_share`/`总股本` 列（hikyuu kdata 无股本列，此兜底失效——但行业信息/weight 已能取总股本，不影响，见 §6.8）。
- **复权**：`Query.FORWARD`（前复权）对齐 akshare `qfq`。文档标注「同向前复权，方法略异」。
- **bj**：`hku_stock` 补 bj 前缀。
- **fallback**：原 `strategies`（stock_zh_a_daily/hist）移入 `_fetch_daily_data_ak`。

### 6.2 `fetch_benchmark_daily` → Hikyuu 指数 KData
- **API**：`fetch_kdata_df(symbol, start, end, index=True, recover="NO_RECOVER")`（指数不复权）。
- **代码映射**：`000300`→`sh000300`；`999999`/`000001`(上证综指)→`sh000001`；`399001`→`sz399001`。`hku_index_stock` 处理。
- **契约**：仅 `[日期, 收盘]`（`fetch_benchmark_daily` 末段已 `keep=["日期","收盘"]`）。复用 `_normalize_daily_df` 后切片。
- **fallback**：原 `stock_zh_index_daily`/`index_zh_a_hist` 移入私有 akshare 函数。

### 6.3 `fetch_financial_abstract` → Hikyuu HistoryFinance
- **API**：`stock.get_history_finance()` 取该股全部报告期记录；用 `sm.get_history_finance_field_index(name)` 按 name 定位字段 id；按 `report_date` 升序构造长格式。
- **契约**（`_transform_financial_abstract` 的 `indicator_map` 键，下游 `find_col_in` 必须命中子串）：
  | 输出列 | Hikyuu 字段 id | 单位（归一到） |
  |---|---|---|
  | `报告期` | report_date(int→Timestamp) | datetime |
  | `加权净资产收益率(%)` | 281（兜底 6/197） | %（0-100，与 akshare 一致） |
  | `资产负债率(%)` | 210 | % |
  | `经营活动产生的现金流量净额` | 107 | **元**（akshare 为元） |
  | `归属于上市公司股东的净利润` | 96 | **元** |
  | `归属母公司股东权益` | 271 | **元** |
  | `总股本` | 238 | **股**（dcf `_get_total_shares` 直接用，需与 industry_info 同口径） |
- **单位坑**：id 107/96/271 等金额字段需探测是否为「万元」（部分字段是）。若为万元则 `×1e4` 归一到元。ROE/资产负债率（210/281）已是 %，不换算。**用 §7 校准探针对平安银行(000001) 逐字段对照 akshare `stock_financial_abstract` 数值定单位。**
- **报告期粒度**：HistoryFinance 含季报（Q1/中报/Q3/年报）。`step1`/`step2` 用 `pick_annual_row` 选年报优先 —— 保留该逻辑，无需改下游。
- **fallback**：akshare `stock_financial_abstract`（原 `_transform_financial_abstract` 链路）保留。

### 6.4 `fetch_cashflow_detail` → Hikyuu HistoryFinance 现金流量表
- **API**：同 6.3，取现金流量表字段。
- **契约**（`step2_dcf` 的 `find_col_in` 子串）：
  | 输出列（须含子串） | Hikyuu 字段 id | 单位（元） |
  |---|---|---|
  | `购建固定资产、无形资产和其他长期资产支付的现金`（含「购建固定资产」） | 114 | 元 |
  | `折旧与摊销`（含「折旧与摊销」） | 136+137 合并（若无合并列） | 元 |
- step2_dcf 对 capex 用 `abs(float(row[capex_col]))`、D&A 用 `da_col` 或 `dep_col+am_col`。**输出列名直接复用 akshare 原名**（「购建固定资产、无形资产和其他长期资产支付的现金」「折旧与摊销」）→ 下游零改动。
- **单位**：id 114 探测万元/元，归一到元（与 akshare sina 现金流量表元口径一致）。
- **fallback**：akshare `stock_financial_report_sina`（现金流量表）保留。

### 6.5 `fetch_dividend` → Hikyuu get_weight
- **API**：`stock.get_weight()` → StockWeightList；取 `datetime`、`bonus`（>0 的现金分红记录）。
- **契约**（`estimate_dividend_yield` 的 `_find_div_per_share_col` + 公告年份匹配）：
  | 输出列 | Hikyuu 字段 | 说明 |
  |---|---|---|
  | `公告日期` | `weight.datetime`（权息日） | 权息日≈除权日，通常在 year+1 中；匹配 `公告年份==year+1` 仍成立（中报分红 6-8 月） |
  | `派息` | `weight.bonus` | bonus 为**每 10 股红利**，与 akshare「派息」列口径一致 → `_normalize_div_per_share` 对「派息」列 /10 正确 |
- **语义偏移**：akshare「公告日期」是分红方案公告日；hikyuu `datetime` 是权息/除权日（晚于公告 ~1-2 月）。`estimate_dividend_yield` 仅用「年份」匹配（公告年份==year+1），权息日仍在 year+1 → 匹配成立。文档标注此语义差异（股息率估算本就近似，可接受）。
- **送股/转增**：股息率只需现金（bonus），送转不影响。可顺手导出 `送股`=count_as_gift、`转增`=increasement 以兼容 demo 列结构。
- **fallback**：akshare `stock_history_dividend_detail`/`stock_dividend_cninfo` 保留。

### 6.6 `fetch_bond_yield_history` / `fetch_bond_yield_10y` → Hikyuu zh_bond10
- **API**：直读 SQLite（`HIKYUU_DB_PATH`）：
  ```python
  import sqlite3
  conn = sqlite3.connect(HIKYUU_DB_PATH)
  df = pd.read_sql("SELECT date, value FROM zh_bond10 ORDER BY date", conn); conn.close()
  out["日期"] = pd.to_datetime(df["date"]); out["国债收益率"] = df["value"]/10000  # bps→小数
  ```
- **契约**：`[日期, 国债收益率]` 小数制（如 0.023），升序、去重。`fetch_bond_yield_10y` 取末值。`step3._historical_erp_series`、`pit.as_of_bundle`、`backtest` risk_free 均按此契约消费。✓
- **优势**：1990 起（vs akshare 2020 起）→ ERP 历史窗口国债真实覆盖大增，`bond_real_coverage` 提升、`erp_source` 从 `real_partial` 升 `real`。
- **导入期 caveat**：`zh_bond10` 导入依赖 `ak.bond_zh_us_rate`（一次性，运行时不再触网）。离线环境导入失败则表空 → fallback akshare `bond_china_yield`。
- **fallback**：akshare `bond_china_yield`（原 `_fetch_bond_yield_history_live`）保留。
- **配置**：`config.py` 新增 `HIKYUU_DB_PATH = r"c:\stock\stock.db"`。

### 6.7 `fetch_stock_indicator` → Hikyuu 自算 PE/PB（最复杂，分两步）
- **契约**（step3 + dcf `find_col_in`）：`[日期, 市盈率PE, 市净率PB]`，日频。
- **PB（先做，简单）**：逐交易日 `PB = close_d / nav_latest_report`，`nav_latest_report` = 截至 d 最近的 `FINANCE("每股净资产")`(id 4) PIT 取值（`FINANCE(kdata, "每股净资产")` 指标按 KData 日期对齐，天然 PIT）。
- **PE_TTM（后做，复杂）**：`PE = close_d / EPS_TTM`，`EPS_TTM = TTM净利润 / total_shares`。TTM 净利润 = 最近 4 个单季度归母净利润之和（用单季度字段 id 230-237 或累计字段差分）。需按 d 做 PIT（d 时点已知的最末报告期起算的 4Q 和）。复用 backtest 已有的 PIT 披露滞后逻辑（`filter_reports_by_pub_lag`，120d）。
- **total_shares**：`weight[-1].total_count × 1e4`（动态：用 d 时点最近 weight 记录，或 `ZONGGUBEN(kdata)` 指标按日对齐）。
- **字段名**：用 `FINANCE(name)` 按名取，避免 off-by-one。
- **单位**：净利润/权益探测万元/元归一；close 元；shares 股 → EPS 元/股 → PE 无量纲。
- **两步交付**：第一步只迁 PB（便宜、稳）；PE_TTM 作为第二步（依赖 TTM 构造正确性，需单测覆盖季报拼接）。PE 缺失期间该日 PE 置 NaN（step3 已 `pe>0 & <1000` 过滤 NaN）。
- **fallback**：akshare `stock_a_indicator_lg`/`stock_zh_valuation_baidu`（原 `_fetch_stock_indicator_live`）保留。PE_TTM 未就绪前可先只迁 PB、PE 仍走 akshare。
- **缓存**：保留 `disk_cache(f"indicator_{symbol}.pkl", 12h)`。

### 6.8 `fetch_industry_info` → Hikyuu 板块 + 股本
- **API**：
  - 行业：`stock.get_belong_to_block_list(category=HIKYUU_INDUSTRY_CATEGORY)`（="行业板块"）→ `bl[0].name`（复用 `_hku_industry_name`）。
  - 总股本：`stock.get_weight()[-1].total_count`（**万股 → ×1e4 归一到「股」**）。
- **契约**：`{industry, bucket, total_shares, source}`。`total_shares` 单位**必须为「股」**（dcf `_get_total_shares` 直接 `float(ts)` 当股用，对比 akshare EM f84 为股）。现 `_hku_total_count` 返回万股 → 此处 **×1e4**。`source` 标 `"hku"`。
- **统一行业口径（收益）**：单股行业从 akshare 申万(EM) 切到 Hikyuu 东财板块后，`map_to_industry_bucket` 走 `HIKYUU_INDUSTRY_TO_BUCKET` + `INDUSTRY_KEYWORDS`（已校准 99.4% 命中，见 config 注释），**与批量筛选同源**，消除单股/批量行业口径分歧。`SW_TO_BUCKET` 退为 demo 路径专用（`_DEMO_INDUSTRY` 用申万名）。
- **fallback**：akshare `stock_individual_info_em`（原 `_fetch_industry_info_live`）保留；hku 不可用时 `source="fallback"`。

### 6.9 `fetch_stock_list` → Hikyuu sm 迭代
- **API**：复用 `_fetch_stock_screening_data_hikyuu` 已验证的 `sm` 迭代 + `_is_a_share` 过滤，但只取 `[代码, 名称]`：
  ```python
  stocks = [s for s in sm if _is_a_share(s)]
  return pd.DataFrame({"代码": [str(s.code) for s in stocks],
                       "名称": [str(s.name) for s in stocks]})
  ```
- **契约**：`[代码, 名称]`，`search_stocks`/`resolve_symbol`/`_lookup_stock_name` 直接消费。✓
- **缓存**：保留 `disk_cache("stock_list.pkl", 24h)`（虽本地，迭代 ~5400 只仍非零成本）。
- **fallback**：akshare 交易所 xls + 新浪 spot（原 `_fetch_stock_list_live`）保留。

### 6.10 `fetch_market_pe_history` — 见 §1.3 / §10 决策点
默认保留乐咕 AkShare（A 方案）。如择期 B 方案自算，设计草稿：
- 取 `sm.get_block("指数板块","沪深300")`（或上证）成份股；对每历史日 d：`Σ(close_d × total_shares_d) / Σ(TTM净利润_d)`。TTM 同 §6.7。日期轴用某指数 KData 的交易日。成本高、需重度缓存（按月采样即可，市场 PE 月频足够）。**标注为独立后续任务，不阻塞本迁移主干。**

---

## 7. 校准探针（`scripts/probe_hikyuu_finance.py`，新增）

类比已有 `import_hikyuu_industry_blocks.py:probe_calibrate`，迁移前必跑，坐实字段单位/口径：

- 对样本股（000001 平安银行 / 600519 茅台 / 300750 宁德时代）：
  - dump `get_history_finance()` 报告期数、`get_history_finance_field_index(name)` 对 §2.3 各字段 id；
  - 逐字段取最近年报值，**与 akshare `stock_financial_abstract`/`stock_financial_report_sina` 同期值对照**，判定单位（元 vs 万元）→ 写入 `config.py` 的字段单位映射表（如 `HKYUU_FINANCE_UNIT = {"净利润": 1.0, "经营现金流": 1.0, ...}` 或 `×1e4`）。
  - dump `get_weight()` 最近 5 条（bonus/datetime/total_count），对照 akshare 分红记录，确认 bonus=每10股、total_count=万股。
  - dump `zh_bond10` 行数、首末日期、末值，对照 akshare `bond_china_yield` 末值，确认 `value/10000` == 小数收益率。
- 探针结果固化进 `config.py` 注释 + `data/hikyuu_backend.py` 的单位换算常量。**未跑探针前不得相信金额字段单位。**

---

## 8. 实施阶段与顺序

> 由低风险→高风险、由无依赖→有依赖推进。每阶段可独立合并、`--demo` 全程不破。

### 阶段 0：前置（阻塞后续）
0.1 改 `~/.hikyuu/importdata-gui.ini`：`day_start_date=20100101`、`[finance] enable=True`。  
0.2 重跑 `python scripts/run_hikyuu_import.py`（全量重导，含 day 10 年 + finance；小时级）。  
0.3 跑 `scripts/probe_hikyuu_finance.py`（§7）→ 固化单位映射。  
0.4 新增 `data/hikyuu_backend.py`（§4）+ `config.py` 增 `HIKYUU_DB_PATH`。  

### 阶段 1：低风险（纯 kdata/sm，无 finance 依赖）
1.1 `fetch_daily_data` → hku（fallback ak）。  
1.2 `fetch_benchmark_daily` → hku 指数。  
1.3 `fetch_stock_list` → sm 迭代。  
1.4 `fetch_industry_info` → 板块+weight（总股本 ×1e4）。  
（每条改后跑 `--demo` 验零回归 + 单测 `test_helpers`/`test_industry`。）

### 阶段 2：分红 + 国债
2.1 `fetch_dividend` → get_weight(bonus)。  
2.2 `fetch_bond_yield_history`/`fetch_bond_yield_10y` → zh_bond10 sqlite。  
（重点验：股息率 `real` 来源占比不降、`erp_source` 从 `real_partial`→`real`。）

### 阶段 3：财报（finance 依赖，单位敏感）
3.1 `fetch_financial_abstract` → HistoryFinance（ROE/资产负债率/OCF/净利润/权益/总股本）。  
3.2 `fetch_cashflow_detail` → HistoryFinance（capex id114 / D&A 136+137）。  
（必跑探针单位对照；验 DCF base_fcf/total_shares/破产清算值与 akshare 口径一致 ±单位。）

### 阶段 4：个股估值（最复杂）
4.1 `fetch_stock_indicator` PB → hku 自算（每股净资产 id4）。  
4.2 `fetch_stock_indicator` PE_TTM → hku 自算（TTM 构造 + 单测）。  
（PE 未就绪前 PE 列仍走 akshare，PB 先迁。）

### 阶段 5：收尾
5.1 `fetch_market_pe_history` 决策落地（A 保留 / B 自算，§10）。  
5.2 清理：评估能否删 akshare fallback（建议保留至少 1-2 版本作降级；akshare 仍为 `fetch_market_overview` 依赖，不宜从 requirements 移除）。  
5.3 更新 README「已知限定」与「数据源」段、CHANGELOG。

---

## 9. 配置变更清单

| 文件 | 变更 |
|---|---|
| `~/.hikyuu/importdata-gui.ini` | `day_start_date=20100101`；`[finance] enable=True` |
| `config.py` | 新增 `HIKYUU_DB_PATH = r"c:\stock\stock.db"`；新增 finance 字段单位映射常量（探针产出）；`fetch_industry_info` source 标 `hku` |
| `data/hikyuu_backend.py` | 新增（§4 访问层） |
| `data/fetcher.py` | 各 fetcher 改「hku 优先 + ak fallback」（§5/§6）；原 akshare 实现移私有 `_ak` 函数；`_prefix_symbol` 的 bj 段补全（或由 `hku_stock` 取代） |
| `data/__init__.py` | 导出 `hku_fetch_kdata_df` 等（按需） |
| `scripts/run_hikyuu_import.py` | 注释更新 scope（day 2010 + finance）；验证段 `load_history_finance=True` |
| `scripts/probe_hikyuu_finance.py` | 新增（§7） |
| `requirements.txt` | `hikyuu>=2.8` 注释补「单股历史数据迁移后推荐安装并完成一次性导入」 |
| `README.md` / `CHANGELOG.md` | 数据源与已知限定段更新 |

---

## 10. 风险与未决项

1. **决策点 1 — `fetch_market_pe_history`**：Hikyuu 无原生市场 PE。默认 A（保留乐咕 akshare，标注为唯一非实时 HTTP 残留 + Hikyuu 自算 B 作为后续增强）。**需用户拍板是否接受 A，或要求 B。** B 成本高且与乐咕权威序列难严格对齐。
2. **单位风险（最高）**：HistoryFinance 金额字段单位不一致（元/万元混存）。**必须跑 §7 探针逐字段定单位**，否则 DCF/PE 全线失真（×1e4 量级错误）。ROE/资产负债率（% 字段）不受影响。
3. **day 重导成本**：`day_start_date` 从 2024 回溯到 2010 → HDF5 体积与导入时间显著增加（一次性，小时级）。回测/日线必须，无绕过。
4. **PE_TTM 构造正确性**：季报累加 4Q 易错（报告期口径、单季度 vs 累计字段差分、PIT 披露滞后）。需专项单测（构造已知样本验 TTM）。PE 未就绪前保留 akshare。
5. **复权方法差异**：hku `FORWARD` vs akshare `qfq` 同为前复权但实现略异，日线数值可能有厘级差异，影响回测净值/前向收益的精确复现。文档标注；如需严格复现可保留 akshare 作日线源（但与「迁移」目标相悖，仅作 fallback）。
6. **行业口径切换**：单股行业从申万(EM)→东财板块。`HIKYUU_INDUSTRY_TO_BUCKET`+关键词已校准 99.4%，但个别标的桶归属可能变（如综合类落「其他」）。`test_industry` 需复核。
7. **退市/新股/北交所**：未导入本地库的标的，hku 返回 null stock → fallback akshare。BJ 前缀补全（§4）。
8. **国债序列切换**：`zh_bond10`(中美国债源) vs `bond_china_yield`(10年国债) 末值可能略异；1990 起更长历史是净收益。探针对照末值确认。
9. **`load_history_finance=True` 内存/耗时**：载入全量三表内存占用增加；进程级单次 load（§4）缓解。如过重可考虑按 symbol 惰性查 sqlite 而非全量 load。

---

## 11. 不迁移项（明确边界）

- **`fetch_market_overview`**：实时全市场 spot 快照，用户明确排除实时数据。且 `main` 实盘路径已 `market_df=None`（被 `fetch_market_pe_history` 取代）。**保留 AkShare 不动。**
- **`search_stocks` / `map_to_industry_bucket`**：纯函数，无取数，不涉及。
- **demo 数据链路**（`data/demo_data.py` 全部 `generate_*`）：`--demo`/`--batch-demo`/`--backtest-demo` 离线路径，本就不触网，不改。

---

## 附：下游契约速查（迁移须保持的列名/单位，find_col_in/直接索引依赖）

| fetcher | 必须输出列 | 关键单位 | 下游消费者 |
|---|---|---|---|
| daily_data | 日期/开盘/收盘/最高/最低/(成交量/成交额) | 元 | main/step1/step2/backtest/charts |
| benchmark_daily | 日期/收盘 | 元(指数点) | backtest 基准曲线 |
| financial_abstract | 报告期/加权净资产收益率(%)/资产负债率(%)/经营活动产生的现金流量净额/归属于上市公司股东的净利润/归属母公司股东权益/总股本 | OCF/净利/权益=元；ROE/资产负债率=%；总股本=股 | step1/step2 |
| cashflow_detail | 报告期/购建固定资产…/折旧与摊销 | 元 | step2(DCF capex + D&A) |
| dividend | 公告日期/派息 | 派息=每10股元 | step1 estimate_dividend_yield |
| stock_indicator | 日期/市盈率PE/市净率PB | 无量纲 | step3/dcf step2 |
| industry_info | dict{industry,bucket,total_shares,source} | total_shares=股 | step2/scoring/main |
| stock_list | 代码/名称 | — | search_stocks/resolve_symbol |
| bond_yield_history | 日期/国债收益率 | 小数(0.023) | step3/backtest risk_free/pit |
| market_pe_history | 日期/市盈率 | 倍 | step3 ERP 历史分位 |
