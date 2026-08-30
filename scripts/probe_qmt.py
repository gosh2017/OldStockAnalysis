# -*- coding: utf-8 -*-
"""QMT / xtquant 数据接口校准探针。

把 fetcher.py 取数接口迁/叠加 QMT 前，坐实 xtquant 到底能取到什么、字段口径
如何。逐项对照 config.HKYUU_FINANCE_FIELDS 的 11 个科目（归母净利润/净利润/
经营现金流/capex/折旧/摊销/ROE_加权/资产负债率/归母权益/每股净资产/总股本），
输出「QMT 有无 + 命中路径 + 样本值 + 量级对照」表，并 dump K线/财务/
instrument_detail 的原始 schema 供人工确认——因为 xtquant 各版本 schema 不一，
本探针不假设字段名，靠中文科目关键词模糊定位。

判定基线（000001 平安银行 2024 年报，与 probe_hikyuu_finance.py 同基准）：
  - 归母净利润  ~3.9e10（390 亿） → 元
  - 经营现金流  ~7e10             → 元
  - 归母权益    ~5.2e11           → 元
  - 总股本      ~1.94e10（194 亿股）→ 股
  - 每股净资产  ~23              → 元/股
  - ROE / 资产负债率 8.3 / 91.0  → %(0-100)
  - capex      ~2.4e9（24 亿）   → 元
  - 折旧+摊销  ~2.4e9（24 亿）   → 元
QMT 取出的值若与上述量级一致 → 单位确认；差 1e4/1e8 → 单位待定需换算。

前置（任一不满足即报错退出，本探针不联网）：
  1. xtquant 已可导入——通常需把 QMT 安装目录下的 xtquant 加入路径：
       完整版： <QMT>/bin.x64/Lib/site-packages
       mini QMT：<QMT>/userdata_mini/Lib/site-packages
     设环境变量 XTQUANT_PATH 指向含 xtquant 的 site-packages 目录，
     或 pip install xtquant（非官方 wheel）。
  2. QMT 客户端（君弘君智模拟交易系统）已登录运行、本地数据服务在线
     （xtdata.connect 默认连 127.0.0.1:58612；PROBE_QMT_IP/PORT 可覆盖）。

用法：python scripts/probe_qmt.py
      可选 env: XTQUANT_PATH / PROBE_QMT_IP / PROBE_QMT_PORT
                 PROBE_QMT_STOCKS（逗号分隔，默认 000001.SZ,600519.SH,300750.SZ）
"""
from __future__ import annotations

import os
import sys
import io
import time
import math
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _utf8_stdout() -> None:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# xtquant 软导入
# ---------------------------------------------------------------------------

def _import_xtquant():
    """软导入 xtquant；失败给路径指引。返回 (xtdata, None) 或 (None, errmsg)。"""
    try:
        import xtquant  # noqa: F401
        from xtquant import xtdata
        return xtdata, None
    except Exception:
        pass
    p = os.environ.get("XTQUANT_PATH")
    if p and p not in sys.path:
        sys.path.insert(0, p)
        try:
            from xtquant import xtdata
            return xtdata, None
        except Exception as e:
            return None, f"XTQUANT_PATH={p} 仍导入失败: {type(e).__name__}: {e}"
    return None, ("未找到 xtquant。设环境变量 XTQUANT_PATH 指向含 xtquant 的 "
                  "site-packages 目录（如 <QMT>\\bin.x64\\Lib\\site-packages 或 "
                  "<QMT>\\userdata_mini\\Lib\\site-packages），或 pip install xtquant。")


# ---------------------------------------------------------------------------
# 通用工具：单位推断 / 值格式化 / 未知结构 dump / 叶子收集
# ---------------------------------------------------------------------------

def _pct_unit(magnitude: float) -> str:
    m = abs(magnitude)
    if m >= 1e8:
        return "元"
    if m >= 1e4:
        return "万元?"
    if m >= 1:
        return "亿元?"
    return "?"


def _fmt_val(v) -> str:
    if v is None:
        return "None"
    try:
        f = float(v)
    except Exception:
        return repr(v)[:120]
    if math.isnan(f) or math.isinf(f):
        return repr(v)
    if f == 0:
        return "0"
    a = abs(f)
    if 0.001 <= a < 1e6:
        return f"{f:.4f}"
    return f"{f:.4e}"


def _dump(obj, indent=0, max_depth=5, max_items=3):
    """递归 dump 未知结构：dict 打印 keys+值类型，list 打印前 N 项样本。"""
    pad = "  " * indent
    if indent > max_depth:
        print(f"{pad}... (depth>{max_depth})")
        return
    if isinstance(obj, dict):
        if not obj:
            print(f"{pad}{{}} (空 dict)")
            return
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                print(f"{pad}{repr(k)}: {type(v).__name__} (len={len(v)})")
                _dump(v, indent + 1, max_depth, max_items)
            else:
                print(f"{pad}{repr(k)}: {_fmt_val(v)}")
    elif isinstance(obj, list):
        if not obj:
            print(f"{pad}[] (空 list)")
            return
        print(f"{pad}[list len={len(obj)}, 前 {min(max_items, len(obj))} 项:]")
        for i, item in enumerate(obj[:max_items]):
            print(f"{pad}[{i}]:")
            _dump(item, indent + 1, max_depth, max_items)
    else:
        print(f"{pad}{_fmt_val(obj)}  ({type(obj).__name__})")


def _walk_leaves(obj, out, prefix=""):
    """把嵌套 dict/list 里所有标量叶子收集为 (路径, 值)，供中文科目关键词匹配。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk_leaves(v, out, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk_leaves(v, out, f"{prefix}[{i}]")
    else:
        out.append((prefix, obj))


# 11 个科目在 xtquant 财务返回里可能出现的中文关键词（OR 任一命中即记）。
# hikyuu 是 "表名_科目"，xtquant 多为裸中文科目，故用多个短词覆盖不同措辞。
FIELD_KEYWORDS = {
    "归母净利润": ["归属于母公司", "归母净利润", "母公司所有者的净利润"],
    "净利润":     ["净利润"],
    "经营现金流": ["经营活动产生的现金流量净额", "经营活动现金流量净额"],
    "capex":      ["购建固定资产", "购建固定资产、无形资产"],
    "折旧":       ["固定资产折旧", "油气资产折耗", "折旧"],
    "摊销":       ["无形资产摊销", "摊销"],
    "ROE_加权":   ["加权净资产收益率", "净资产收益率", "ROE"],
    "资产负债率": ["资产负债率"],
    "归母权益":   ["归属于母公司股东权益", "归属于母公司所有者权益",
                   "归属于母公司所有者权益合计"],
    "每股净资产": ["每股净资产"],
    "总股本":     ["总股本", "总股本数", "股本总数"],
}

# 000001 平安银行 2024 年报已知量级（来自 probe_hikyuu_finance.py 实测基线）。
BASELINE = {
    "归母净利润": 3.9e10, "净利润": 3.9e10, "经营现金流": 7e10,
    "capex": 2.4e9, "折旧": 1.2e9, "摊销": 1.2e9,
    "ROE_加权": 8.3, "资产负债率": 91.0, "归母权益": 5.2e11,
    "每股净资产": 23.0, "总股本": 1.94e10,
}


def _pick_latest_annual(quote_for_code):
    """从 get_financial_data 单 code 返回里挑最近年报（报告期含 '12'）的 inner 结构。"""
    if isinstance(quote_for_code, dict):
        keys = list(quote_for_code.keys())
    elif isinstance(quote_for_code, list):
        # list 形态：取末元素（通常末元素即最近报告期），date 未知置 None
        return (quote_for_code[-1] if quote_for_code else None), None
    else:
        return None, None
    annual = [k for k in keys if "12" in str(k)[4:7]]
    latest = sorted(annual, reverse=True)[0] if annual else (sorted(keys, reverse=True)[0] if keys else None)
    return (quote_for_code.get(latest) if latest else None), latest


def _match_fields(leaf_pairs):
    """对 11 科目做关键词 OR 匹配，返回 {label: (hit_path, hit_val)}（仅首个命中）。"""
    out = {}
    for label, kws in FIELD_KEYWORDS.items():
        hit = None
        for path, val in leaf_pairs:
            ps = str(path)
            if any(kw in ps for kw in kws):
                hit = (path, val)
                break
        out[label] = hit
    return out


def _order_of_mag(x):
    if x is None:
        return None
    try:
        f = abs(float(x))
    except Exception:
        return None
    if f == 0:
        return None
    return 10 ** math.floor(math.log10(f))


# ---------------------------------------------------------------------------
# 探针分段
# ---------------------------------------------------------------------------

def probe_connect(xtdata):
    print("\n[probe] === xtquant 连接 ===")
    ip = os.environ.get("PROBE_QMT_IP", "127.0.0.1")
    port = int(os.environ.get("PROBE_QMT_PORT", "58612"))
    try:
        ret = xtdata.connect(ip, port)
        print(f"[probe] connect({ip},{port}) -> {ret!r} (0 通常表示成功)")
    except Exception as e:
        print(f"[probe][FATAL] connect 抛异常: {type(e).__name__}: {e}")
        print("[probe]   请确认：QMT 客户端（君弘君智）已登录运行；已启用 mini QMT / "
              "Python 数据接口；端口未被改（默认 58612）。")
        return False
    # 探测 server status（不同版本函数名不一）
    for fn in ("get_server_status", "get_status"):
        if hasattr(xtdata, fn):
            try:
                st = getattr(xtdata, fn)()
                print(f"[probe] {fn}() -> {st!r}")
            except Exception as e:
                print(f"[probe] {fn}() 失败: {e}")
            break
    return True


def probe_kdata(xtdata, stocks):
    print("\n[probe] === K 线（前复权日K）===")
    today = dt.date.today()
    start = (today - dt.timedelta(days=400)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    targets = list(stocks) + ["000300.SH", "000001.SH", "399001.SZ"]
    for code in targets:
        try:
            try:
                xtdata.download_history_data(code, "1d", start, end)
            except Exception as e:
                print(f"[probe]   {code}: download_history_data 跳过/失败 ({e})")
            k = xtdata.get_market_data_ex(
                [], [code], period="1d", start_time=start, end_time=end,
                count=-1, dividend_type="front", fill_data=True)
            df = k.get(code) if isinstance(k, dict) else None
            if df is None or (hasattr(df, "empty") and df.empty):
                print(f"[probe]   {code}: 无K线数据（可能未登录/未订阅）")
                continue
            cols = list(df.columns)
            idx0, idx1 = df.index[0], df.index[-1]
            def _d(x):
                try:
                    return str(dt.date.fromtimestamp(int(x) / 1000)) if int(x) > 1e12 else str(int(x))
                except Exception:
                    return str(x)
            print(f"[probe]   {code}: shape={df.shape} cols={cols}")
            print(f"[probe]     首行 {df.iloc[0].to_dict()}")
            print(f"[probe]     index 首末={_d(idx0)} ~ {_d(idx1)}（列含中文? {'成交额' in ''.join(map(str, cols))}）")
        except Exception as e:
            print(f"[probe]   {code} K线 ERR: {type(e).__name__}: {e}")


def probe_finance(xtdata, stocks):
    print("\n[probe] === 财务数据 schema dump + 关键词匹配 ===")
    try:
        from config import HKYUU_FINANCE_FIELDS
    except Exception as e:
        print(f"[probe][FATAL] 读 config 失败: {e}")
        return
    sample_code = stocks[0] if stocks else "000001.SZ"
    print(f"[probe] --- 样本 {sample_code} get_financial_data 原始结构（最多深度5）---")
    fin = None
    try:
        fin = xtdata.get_financial_data([sample_code])
    except Exception as e:
        print(f"[probe] get_financial_data ERR: {type(e).__name__}: {e}")
    if fin is None:
        print("[probe] get_financial_data 返回 None——xtquant 可能不提供财务历史，或需登录。")
        return
    code_fin = fin.get(sample_code, fin) if isinstance(fin, dict) else fin
    print(f"[probe] 顶层类型={type(code_fin).__name__}")
    _dump(code_fin, max_depth=5)

    inner, latest_date = _pick_latest_annual(code_fin)
    print(f"[probe] 取最近年报报告期 = {latest_date}")
    if inner is None:
        print("[probe]   未能从结构中取出报告期数据，关键词匹配跳过。")
        return
    leaves = []
    _walk_leaves(inner, leaves)
    hits = _match_fields(leaves)
    print(f"[probe] --- 11 科目命中（{sample_code} 最近年报）---")
    for label in FIELD_KEYWORDS:
        hku_name = HKYUU_FINANCE_FIELDS.get(label, "?")
        h = hits.get(label)
        if h is None:
            print(f"[probe]   {label:8s} ❌ 未命中  (hku: {hku_name})")
        else:
            path, val = h
            print(f"[probe]   {label:8s} ✅ 路径={path}  值={_fmt_val(val)}  (hku: {hku_name})")
    _coverage_table(sample_code, hits)


def _coverage_table(sample_code, hits):
    """覆盖度汇总：逐科目 ✅/⚠️/❌ + QMT值 + 量级 + 与基线对照判定。"""
    print(f"\n[probe] ===== 覆盖度汇总（{sample_code}，对照平安银行2024年报基线）=====")
    print(f"[probe] {'科目':8s} {'判定':4s} {'QMT样本值':>16s} {'量级':>10s} "
          f"{'基线':>10s} {'口径判定':16s}")
    for label in FIELD_KEYWORDS:
        h = hits.get(label)
        base = BASELINE.get(label)
        if h is None:
            print(f"[probe] {label:8s} {'❌':4s} {'-':>16s} {'-':>10s} "
                  f"{_fmt_val(base):>10s} {'QMT未提供':16s}")
            continue
        _, val = h
        try:
            fv = float(val)
        except Exception:
            print(f"[probe] {label:8s} {'⚠️':4s} {_fmt_val(val):>16s} {'?':>10s} "
                  f"{_fmt_val(base):>10s} {'非数值/空':16s}")
            continue
        om = _order_of_mag(fv)
        bom = _order_of_mag(base)
        if fv == 0:
            verdict = "样本为0?"
        elif bom and om and abs(math.log10(om / bom)) < 0.6:
            verdict = "量级一致✓"
        elif bom and om:
            ratio = math.log10(om / bom) if bom else 0
            verdict = f"差~10^{ratio:.0f}倍?"
        else:
            verdict = "量级待定?"
        print(f"[probe] {label:8s} {'✅':4s} {_fmt_val(fv):>16s} "
              f"{_fmt_val(om):>10s} {_fmt_val(base):>10s} {verdict:16s}")
    print("[probe] 说明：量级一致→QMT单位与hikyuu同（元/股/%）；差10^4倍→可能万元；")
    print("[probe]       差10^8倍→可能亿元；❌未命中→该科目需用hku/ak补或自算派生指标。")


def probe_detail(xtdata, stocks):
    print("\n[probe] === instrument_detail（行业/总股本/PE-PB快照）===")
    for code in stocks:
        try:
            d = xtdata.get_instrument_detail(code)
            if not d:
                print(f"[probe]   {code}: detail 返回空")
                continue
            print(f"[probe]   {code}: detail keys={list(d.keys()) if isinstance(d, dict) else type(d)}")
            if isinstance(d, dict):
                for k in ("InstrumentName", "name", "InstrumentID", "ExchangeID",
                          "IndexCode", "IndexName", "ListingDate", "ListingBoard",
                          "TotalVolume", "总股本", "FlowVolume", "Industry", "行业"):
                    if k in d:
                        print(f"[probe]     {k} = {d[k]}")
                # 探测实时快照里有无 PE/PB（非历史序列，仅当前值）
                pe_pb = {k: d[k] for k in d if "PE" in str(k).upper() or "PB" in str(k).upper()}
                if pe_pb:
                    print(f"[probe]     PE/PB 相关字段(仅快照非历史): {pe_pb}")
        except Exception as e:
            print(f"[probe]   {code} detail ERR: {type(e).__name__}: {e}")


def probe_dividend(xtdata, stocks):
    print("\n[probe] === 分红送配接口探测 ===")
    for code in stocks:
        for fn in ("get_divid_factors", "get_divid_factor", "get_stock_dividend"):
            if hasattr(xtdata, fn):
                try:
                    r = getattr(xtdata, fn)(code)
                    if r:
                        print(f"[probe]   {code} {fn}() 返回类型={type(r).__name__} len={len(r) if hasattr(r,'__len__') else '?'}")
                        _dump(r if not hasattr(r, "items") else (list(r.items())[:2]), max_depth=3)
                        break
                except Exception as e:
                    print(f"[probe]   {code} {fn} ERR: {e}")
        else:
            print(f"[probe]   {code}: xtdata 无分红函数（get_divid_factors 等），"
                  f"fetch_dividend 仍需 hku/ak。")


def main() -> None:
    _utf8_stdout()
    print(f"[probe] ===== QMT 校准探针 start {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    xtdata, err = _import_xtquant()
    if xtdata is None:
        print(f"[probe][FATAL] {err}")
        return
    print(f"[probe] xtquant 导入成功，xtdata 模块={xtdata}")
    stocks_env = os.environ.get("PROBE_QMT_STOCKS", "000001.SZ,600519.SH,300750.SZ")
    stocks = [s.strip() for s in stocks_env.split(",") if s.strip()]
    if not probe_connect(xtdata):
        return
    probe_kdata(xtdata, stocks)
    probe_finance(xtdata, stocks)
    probe_detail(xtdata, stocks)
    probe_dividend(xtdata, stocks)
    print(f"\n[probe] ===== all done {time.strftime('%H:%M:%S')} =====")
    print("[probe] 把上面「覆盖度汇总」表 + 「财务schema dump」发给 Claude，"
          "据此判定哪些 fetch_* 可切 QMT 源、字段单位是否需换算。")


if __name__ == "__main__":
    main()
