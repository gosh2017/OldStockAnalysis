# -*- coding: utf-8 -*-
"""一次性 Hikyuu 数据导入（步骤 2）。

用 hikyuu.gui.importdata.HKUImportDataCMD 的非 GUI 入口跑全量导入，
数据源 pytdx（TDX 行情服务器，已验证可达 10 个），落 c:\\stock（HDF5 + SQLite）。

关键点（本会话实测坐实，见计划 glowing-herding-pie.md）：
- start_import_data() 同步调用 UsePytdxImportToH5Thread.run()（直接 .run()，
  非 .start()）。该 QThread 的 message=Signal(list) 在进度回调里 emit，
  必须先建 QApplication 否则 Signal.emit 崩。
- 导入子任务 ImportPytdxToH5 是纯 callable，经 multiprocessing.Process
  执行（Windows = spawn）。spawn 下子进程会重新 import 本脚本，故实际
  导入调用必须由 if __name__=='__main__' 守卫，否则无限递归 spawn。

导入范围（~/.hikyuu/importdata-gui.ini）：
  day=True   （day_start_date=1990-12-19，全历史——覆盖日线/回测 10 年回溯 + 余量）
  weight=True（总股本 total_count，单位万股）
  block=True （TDX 板块/行业分类）
  finance=True（HistoryFinance 三表 581 字段——单股财报/估值迁移后必需）
  fund/min/min5=True（可选；finance 迁移只需 day/weight/block/finance）
落盘于 [hdf5] dir = G:/QTrading/StockData（HDF5 + SQLite stock.db，~1.2GB）。
注：单股历史数据迁移后（data/hikyuu_backend.py + 各 fetcher），查询期全走本地、
零 HTTP；finance 导入是一次性联网（TDX gpcw + akshare bond_zh_us_rate）。

用法： python scripts/run_hikyuu_import.py
"""
import os
import sys
import time
import traceback


def _a_share_kdata_coverage(hku):
    """统计 A 股日线 kdata 覆盖率：(A股总数, 有日线只数, 缺失代码列表)。

    sm 计数只反映 Stock 元数据表（导全≈7802 即使日线 HDF5 没导），无法发现
    「日线仅 44%」的静默失败——导入跑完后 sm 仍是 7801，旧判据 len(sm)>1000
    恒真，于是 kdata 缺失被吞掉。故改用 get_count(DAY)>0 实测每只 A 股日线是否
    落盘 HDF5。口径与 data/hikyuu_backend.hku_is_a_share 一致（SH 60/68、
    SZ 00/30、BJ 43/83/87/92）。~5400 只实测约 3s，相对导入耗时可忽略。
    """
    total = have = 0
    miss = []
    for s in hku.sm:
        try:
            mkt = str(s.market).upper()
            code = str(s.code)
        except Exception:
            continue
        if len(code) != 6 or not code.isdigit():
            continue
        if mkt == "SH":
            if code[:2] not in ("60", "68"):
                continue
        elif mkt == "SZ":
            if code[:2] not in ("00", "30"):
                continue
        elif mkt == "BJ":
            if code[:2] not in ("43", "83", "87", "92"):
                continue
        else:
            continue
        total += 1
        try:
            cnt = s.get_count(hku.Query.DAY)
        except Exception:
            cnt = 0
        if cnt and cnt > 0:
            have += 1
        else:
            miss.append(code)
    return total, have, miss


def main():
    import multiprocessing
    # Windows spawn 冻结支持（无害；本脚本由 __main__ 守卫，子进程不会重入 main）
    multiprocessing.freeze_support()

    # Signal.emit 需 QApplication 存在（QThread 直接 .run() 时进度回调会 emit）
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        print(f"[import] QApplication ready: {app}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[import][WARN] 建 QApplication 失败: {type(e).__name__}: {e}",
              flush=True)
        print("[import][WARN] 继续尝试——start_import_data 在 emit 时可能崩",
              flush=True)
        app = None

    from hikyuu.gui.importdata import HKUImportDataCMD

    cmd = HKUImportDataCMD()

    # 包一层 on_message_from_thread，把每条进度消息打到 stdout（后台日志可见）
    _orig_on_msg = cmd.on_message_from_thread

    def _trace_on_msg(msg, _o=_orig_on_msg):
        try:
            print(f"[import] {msg}", flush=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            _o(msg)
        except Exception:  # noqa: BLE001  进度槽异常不阻断导入
            pass

    cmd.on_message_from_thread = _trace_on_msg

    print(f"[import] ===== start at {time.strftime('%Y-%m-%d %H:%M:%S')} =====",
          flush=True)
    print(f"[import] cwd   = {os.getcwd()}", flush=True)
    print(f"[import] py    = {sys.version.split()[0]}", flush=True)
    # 数据目录与导入范围实读 ini（~/.hikyuu/importdata-gui.ini），勿硬编码——历史
    # 版本写死 "c:\stock"+"no fund/min/min5/finance" 与真实 ini 不符，误导排障。
    import configparser
    _ini = os.path.expanduser("~/.hikyuu/importdata-gui.ini")
    _cfg = configparser.ConfigParser()
    _cfg.read(_ini, encoding="utf-8")

    def _b(sec, key):
        try:
            return _cfg.getboolean(sec, key)
        except Exception:
            return "?"

    _datadir = _cfg.get("hdf5", "dir", fallback="?")
    print(f"[import] ini   = {_ini}", flush=True)
    print(f"[import] data  = {_datadir} (HDF5 + SQLite stock.db)", flush=True)
    print(f"[import] scope = quotation[stock={_b('quotation','stock')},"
          f"fund={_b('quotation','fund')}] ktype[day={_b('ktype','day')},"
          f"min={_b('ktype','min')},min5={_b('ktype','min5')}]"
          f" weight={_b('weight','enable')} finance={_b('finance','enable')}"
          f" block={_b('block','enable')}", flush=True)
    print(f"[import] 判据 = A 股日线 kdata 覆盖率≥"
          f"{os.environ.get('HKU_IMPORT_COV_THRESHOLD', '0.95')}×100%"
          f"（实测 get_count(DAY)>0，非 len(sm)；sm 计数不反映 kdata 缺失）",
          flush=True)
    print(f"[import] 首步 search_best_tdx() 瞬时超时则重试（覆盖率达阈即停）",
          flush=True)

    # ---- 导入主循环：重试以抗 search_best_tdx 瞬时超时 ----
    # 同一 cmd 复用：start_import_data() 每次新建 UsePytdxImportToH5Thread，
    # 并重连 message→self.on_message_from_thread（即下方的 _trace_on_msg，
    # 闭包绑定的 _o 是该 cmd 的原方法，self 正确）。重导幂等（CREATE IF NOT
    # EXISTS + 按日期 upsert），部分导入后重试安全。
    import hikyuu as hku

    # 成功判据：A 股日线 kdata 覆盖率（非 sm 计数）。sm 计数基于 Stock 元数据表，
    # 元数据导全≈7802 即使日线 HDF5 没导也恒为 7801 → 旧判据 len(sm)>1000 恒真，
    # 「日线只到 44%」被静默吞掉（实测 2025-11 导入即如此：sm=7801 但仅 44% A 股
    # 有日线，批量筛选市值缺 56%）。改判 kdata 实测覆盖，< 阈值则重试——重导幂等、
    # 按日 upsert，已导股增量、缺漏股补全，重试覆盖率应单调升。
    COV_THRESHOLD = float(os.environ.get("HKU_IMPORT_COV_THRESHOLD", "0.95"))
    max_attempts = int(os.environ.get("HKU_IMPORT_MAX_ATTEMPTS", "3"))
    ok = False
    last_total = last_have = 0
    last_pct = 0.0
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            print(f"[import] 等 20s 后第 {attempt}/{max_attempts} 次重试"
                  f"（重导幂等，按日 upsert，已导股增量、缺漏股补全）...",
                  flush=True)
            time.sleep(20)
        print(f"[import] ===== attempt {attempt}/{max_attempts} start "
              f"{time.strftime('%H:%M:%S')} =====", flush=True)
        t0 = time.time()
        try:
            cmd.start_import_data()
        except Exception:
            print(f"[import][attempt {attempt}] start_import_data 抛异常:",
                  flush=True)
            traceback.print_exc()
            continue
        dt = time.time() - t0
        print(f"[import] ===== attempt {attempt} done in {dt:.0f}s "
              f"({dt/60:.1f} min) =====", flush=True)

        # 验证：实测 A 股日线 kdata 覆盖率（sm 计数不反映 kdata 缺失）
        print("[import] 验证：重新 load_hikyuu() 实测 A 股日线 kdata 覆盖率...",
              flush=True)
        try:
            hku.load_hikyuu(load_history_finance=True, load_weight=True,
                            start_spot=False)
            n_sm = len(hku.sm)
            total, have, miss = _a_share_kdata_coverage(hku)
            pct = (have * 100.0 / total) if total else 0.0
            last_total, last_have, last_pct = total, have, pct
            print(f"[import] len(sm)={n_sm}；A 股日线 kdata 覆盖 "
                  f"{have}/{total}（{pct:.1f}%）"
                  + (f"，缺 {len(miss)} 只" if miss else ""), flush=True)
            if miss:
                print(f"[import] 缺失样例(前20): {miss[:20]}", flush=True)
            if pct >= COV_THRESHOLD * 100:
                print(f"[import][OK] kdata 覆盖 {pct:.1f}% ≥ "
                      f"{COV_THRESHOLD * 100:.0f}% 阈值，导入完成", flush=True)
                ok = True
                break
            print(f"[import][WARN] kdata 覆盖 {pct:.1f}% < "
                  f"{COV_THRESHOLD * 100:.0f}% 阈值，重试以回填缺失股", flush=True)
        except Exception:
            print("[import][WARN] load_hikyuu 验证抛异常:", flush=True)
            traceback.print_exc()

    if not ok:
        miss_n = last_total - last_have
        print(f"[import][FATAL] {max_attempts} 次重试后 A 股日线 kdata 覆盖仅 "
              f"{last_pct:.1f}%（{last_have}/{last_total}，阈值 "
              f"{COV_THRESHOLD * 100:.0f}%），仍有约 {miss_n} 只无日线 → "
              f"批量筛选市值将缺失。可手动再跑或加 HKU_IMPORT_MAX_ATTEMPTS；"
              f"缺漏股可用 akshare spot 兜底市值。", flush=True)
        sys.exit(2)

    print(f"[import] ===== all done at {time.strftime('%H:%M:%S')} =====",
          flush=True)


if __name__ == "__main__":
    main()
