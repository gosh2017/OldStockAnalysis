# -*- coding: utf-8 -*-
"""Hikyuu 财报 / 权息 / 国债 校准探针（数据查询接口迁移的 §7 交付物）。

迁移单股历史数据接口到 Hikyuu 本地库前/后运行，坐实字段单位与口径，避免
×1e4 量级错误污染 DCF/PE。两部分：

  1. DB 自省（sqlite，只读，零网络）：表清单、HistoryFinance/Field 行数、
     关键字段 id↔name 对照、zh_bond10 首末日期与末值、block 表 category。
  2. 运行时 dump（hikyuu）：样本股 get_history_finance 报告期数 + 各字段最近
     年报值 + 量级单位推断（元/万元/亿元/股/%）+ weight bonus/total_count +
     kdata 末收盘。

单位推断基线（000001 平安银行 2024 年报，探针实测坐实）：
  - 归母净利润  ~3.9e10（390 亿） → 元
  - 经营现金流  ~7e10            → 元
  - 归属母公司股东权益 ~5.2e11   → 元
  - 总股本      ~1.94e10（194 亿股）→ 股
  - 每股净资产  ~23              → 元/股
  - ROE / 资产负债率 8.3 / 91.0  → %(0-100)
  - capex      ~2.4e9（24 亿）   → 元
  - 折旧+摊销  ~2.4e9（24 亿）   → 元
  - weight.total_count ~1.94e6（万股）→ ×1e4 → 股
  - weight.bonus  ~2-5          → 每 10 股红利（元/10股）
  - zh_bond10.value 末值 ~18140 → /1e6 → 小数 0.018（1.8%）

用法： python scripts/probe_hikyuu_finance.py
      （akshare 交叉对照段需联网；离线时该段自动跳过并打印说明）
"""
from __future__ import annotations

import os
import sqlite3
import sys
import io
import time

# 作为脚本运行（python scripts/probe_hikyuu_finance.py）时 sys.path[0] 是脚本目录，
# 不含项目根；插入根目录以便 from config import（与 pytest/conftest 环境对齐）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _utf8_stdout() -> None:
    """Windows 控制台按 GBK 解码会乱码；强制 UTF-8 stdout。"""
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass


def _pct_unit(magnitude: float) -> str:
    """按量级推断金额字段单位：1e8-1e12 → 元；1e4-1e8 → 万元；1-1e4 → 亿元；
    1e6-1e11 且字段为总股本 → 股。仅作启发式提示，最终以已知基本面对照。"""
    m = abs(magnitude)
    if m >= 1e8:
        return "元"
    if m >= 1e4:
        return "万元?"
    if m >= 1:
        return "亿元?"
    return "?"


def probe_db() -> None:
    """DB 自省：表清单、行数、字段 id↔name、zh_bond10、block category。"""
    from config import HIKYUU_DB_PATH, HKYUU_FINANCE_FIELDS
    print(f"\n[probe] === DB 自省（{HIKYUU_DB_PATH}）===")
    if not os.path.exists(HIKYUU_DB_PATH):
        print(f"[probe][FATAL] {HIKYUU_DB_PATH} 不存在——先跑 "
              f"python scripts/run_hikyuu_import.py")
        return
    conn = sqlite3.connect(HIKYUU_DB_PATH)
    try:
        cur = conn.cursor()
        tabs = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        print(f"[probe] 表: {tabs}")
        for t in ("HistoryFinance", "HistoryFinanceField", "zh_bond10", "block", "Stock"):
            try:
                n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"[probe]   {t:20s} rows={n}")
            except Exception as e:
                print(f"[probe]   {t:20s} ERR {e}")

        print("[probe] 关键字段 id↔name（DB id；运行时数组下标 = id-1）：")
        for label, name in HKYUU_FINANCE_FIELDS.items():
            r = cur.execute("SELECT id FROM HistoryFinanceField WHERE name=?",
                            (name,)).fetchone()
            fid = r[0] if r else "??(未找到)"
            print(f"[probe]   {label:8s} db_id={fid:>4}  arr_ix={int(fid)-1 if isinstance(fid,int) else '?':>4}"
                  f"  name={name}")

        n = cur.execute("SELECT COUNT(*) FROM zh_bond10").fetchone()[0]
        r0 = cur.execute("SELECT date,value FROM zh_bond10 ORDER BY date LIMIT 1").fetchone()
        r1 = cur.execute("SELECT date,value FROM zh_bond10 ORDER BY date DESC LIMIT 1").fetchone()
        print(f"[probe] zh_bond10: rows={n} 首={r0} 末={r1}")
        if r1:
            print(f"[probe]   末值小数 = value/1e6 = {r1[1]/1_000_000:.5f} "
                  f"({r1[1]/1_000_000*100:.3f}%)"
                  f"   ← 注意：/10000 会得 {r1[1]/10000}（错，=181%）")

        cats = [r[0] for r in cur.execute("SELECT DISTINCT category FROM block")]
        n_ind = cur.execute("SELECT COUNT(*) FROM block WHERE category='行业板块'").fetchone()[0]
        print(f"[probe] block category: {cats}  行业板块 membership rows={n_ind}")
        # 行业板块是否含样本股（get_belong_to_block_list 依赖 BlockIndex，仅含指数板块）
        print("[probe]   行业板块 membership 覆盖样本股？")
        for code in ("SZ000001", "SH600519", "SZ300750"):
            rows = cur.execute(
                "SELECT name FROM block WHERE category='行业板块' AND market_code=?",
                (code,)).fetchall()
            print(f"[probe]     {code}: {[r[0] for r in rows] or '（空——行业板块未导入/未覆盖）'}")
    finally:
        conn.close()


def probe_runtime() -> None:
    """运行时 dump：样本股财报字段值 + 量级单位推断 + weight + kdata。"""
    print(f"\n[probe] === 运行时 dump（hikyuu）===")
    try:
        import hikyuu as hku
    except ImportError:
        print("[probe][FATAL] 未安装 hikyuu（pip install hikyuu）")
        return
    from config import HKYUU_FINANCE_FIELDS
    t0 = time.time()
    try:
        hku.load_hikyuu(load_history_finance=True, load_weight=True, start_spot=False)
    except Exception as e:
        print(f"[probe][FATAL] load_hikyuu 失败: {type(e).__name__}: {e}")
        return
    print(f"[probe] load_hikyuu(finance+weight) {time.time()-t0:.1f}s  len(sm)={len(hku.sm)}")
    sm = hku.sm

    samples = [("sz000001", "平安银行"), ("sh600519", "贵州茅台"), ("sz300750", "宁德时代")]
    key_labels = ["归母净利润", "经营现金流", "归母权益", "总股本",
                  "每股净资产", "ROE_加权", "资产负债率", "capex", "折旧", "摊销"]
    for sym, expect in samples:
        try:
            s = sm[sym]
            if not s.valid:
                print(f"[probe]   {sym}: invalid")
                continue
            print(f"\n[probe] === {sym} {s.name}（期望 {expect}）===")
            kd = s.get_kdata(hku.Query(-1))
            print(f"[probe]   kdata 末收盘 = {float(kd[-1].close):.2f}  "
                  f"{str(kd[-1].datetime)[:10] if len(kd) else 'n/a'}")
            wl = s.get_weight()
            if len(wl):
                w = wl[-1]
                print(f"[probe]   weight: total_count={w.total_count:.0f}万股 ×1e4 = "
                      f"{w.total_count*1e4:.3e}股；bonus={w.bonus}（每10股红利）")
            hf = s.get_history_finance()
            print(f"[probe]   get_history_finance() = {len(hf) if hf else 0} 期")
            if not hf:
                continue
            # 取最近年报（报告期月份==12 的最后一条），无则最后一条
            rec = None
            for r in reversed(hf):
                try:
                    if str(r[0])[5:7] == "12":
                        rec = r
                        break
                except Exception:
                    pass
            if rec is None:
                rec = hf[-1]
            print(f"[probe]   取最近年报 rec[0]={str(rec[0])[:10]}")
            values = rec[2]
            for label in key_labels:
                name = HKYUU_FINANCE_FIELDS.get(label)
                if not name:
                    continue
                try:
                    ix = sm.get_history_finance_field_index(name)
                    v = float(values[ix])
                except Exception as e:
                    print(f"[probe]     {label:8s} 取数失败: {e}")
                    continue
                if label in ("ROE_加权", "资产负债率"):
                    print(f"[probe]     {label:8s} arr_ix={ix:>3} = {v:.3f}（%）")
                elif label in ("总股本",):
                    print(f"[probe]     {label:8s} arr_ix={ix:>3} = {v:.3e}（股）")
                elif label == "每股净资产":
                    print(f"[probe]     {label:8s} arr_ix={ix:>3} = {v:.3f}（元/股）")
                else:
                    print(f"[probe]     {label:8s} arr_ix={ix:>3} = {v:.4e}"
                          f"（量级推断 {_pct_unit(v)}）")
        except Exception as e:
            print(f"[probe]   {sym} ERR: {type(e).__name__}: {e}")


def probe_akshare_crosscheck() -> None:
    """akshare 交叉对照（需联网；离线时跳过并说明）。

    用途：以 akshare 同期值为权威基准，确认 HistoryFinance 金额字段单位。
    本环境 akshare 东财端点频繁断连（见 memory），离线时该段自动跳过——
    此时以「量级推断 + 已知基本面」为准（000001 2024 年报见模块 docstring）。
    """
    print(f"\n[probe] === akshare 交叉对照（需联网）===")
    try:
        import akshare as ak
    except ImportError:
        print("[probe] 未安装 akshare，跳过交叉对照。")
        return
    for sym in ("000001", "600519"):
        try:
            df = ak.stock_financial_abstract(symbol=sym)
            if df is None or df.empty:
                print(f"[probe] {sym}: akshare 返回空（可能断连），跳过")
                continue
            # 权威基准：取最近年报的 归母净利润 / 经营现金流，与 Hikyuu 同字段值对照
            date_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
            latest = sorted(date_cols)[-1] if date_cols else None
            if latest is None:
                print(f"[probe] {sym}: 无日期列，跳过")
                continue
            np_row = df[df["指标"].astype(str).str.contains("净利润", na=False)]
            np_val = np_row.iloc[0][latest] if not np_row.empty else None
            print(f"[probe] {sym}: akshare 最近年报({latest}) 净利润 = {np_val}"
                  f"  ← 与 Hikyuu 同字段值对照，量级一致即单位确认（元）")
        except Exception as e:
            print(f"[probe] {sym}: akshare 交叉对照失败（{type(e).__name__}: {e}）——"
                  f"离线/断连属预期，以量级推断为准")
    print("[probe] 交叉对照段结束（联网环境下可用于单位复核；离线跳过）")


def main() -> None:
    _utf8_stdout()
    print(f"[probe] ===== 校准探针 start {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    probe_db()
    probe_runtime()
    probe_akshare_crosscheck()
    print(f"\n[probe] ===== all done {time.strftime('%H:%M:%S')} =====")
    print("[probe] 单位若与 module docstring 基线不符，须更新 config.HKYUU_FINANCE_FIELDS "
          "注释与 data/hikyuu_backend.py 的换算常量后重跑 pytest。")


if __name__ == "__main__":
    main()
