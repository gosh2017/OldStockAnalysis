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

导入范围（已在 ~/.hikyuu/importdata-gui.ini 瘦身）：
  day=True  （day_start_date=2024-01-01，仅近 ~2 年，筛选只需最近收盘）
  weight=True（总股本 total_count，单位万股）
  block=True（TDX 板块/行业分类）
  fund/min/min5/finance=False（筛选不需要）

用法： python scripts/run_hikyuu_import.py
"""
import os
import sys
import time
import traceback


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
    print(f"[import] stock = c:\\stock (HDF5 + SQLite stock.db)", flush=True)
    print(f"[import] scope = day(near 2024-01-01) + weight + block; "
          f"no fund/min/min5/finance", flush=True)
    print(f"[import] TDX search_best_tdx() 是导入首步、单一故障点；"
          f"瞬时超时则重试（判据 len(sm)>1000）", flush=True)

    # ---- 导入主循环：重试以抗 search_best_tdx 瞬时超时 ----
    # 同一 cmd 复用：start_import_data() 每次新建 UsePytdxImportToH5Thread，
    # 并重连 message→self.on_message_from_thread（即下方的 _trace_on_msg，
    # 闭包绑定的 _o 是该 cmd 的原方法，self 正确）。重导幂等（CREATE IF NOT
    # EXISTS + 按日期 upsert），部分导入后重试安全。
    import hikyuu as hku
    ok = False
    for attempt in range(1, 4):
        if attempt > 1:
            print(f"[import] 等 20s 后第 {attempt} 次重试...", flush=True)
            time.sleep(20)
        print(f"[import] ===== attempt {attempt}/3 start "
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

        # 验证：重新 load_hikyuu 后查 sm 计数（≈5000 即全市场）
        print("[import] 验证：重新 load_hikyuu() 查 sm 计数...", flush=True)
        try:
            hku.load_hikyuu(load_history_finance=False, load_weight=False,
                            start_spot=False)
            n = len(hku.sm)
            print(f"[import] len(hku.sm) = {n}", flush=True)
            if n > 1000:
                print(f"[import][OK] 导入成功，sm 计数 {n}（≈5000 即全市场）",
                      flush=True)
                ok = True
                break
            print(f"[import][WARN] sm 计数仅 {n}，可能未完整，将重试", flush=True)
        except Exception:
            print("[import][WARN] load_hikyuu 验证抛异常:", flush=True)
            traceback.print_exc()

    if not ok:
        print("[import][FATAL] 3 次重试仍未完成导入——停下回报，不硬推。",
              flush=True)
        sys.exit(2)

    print(f"[import] ===== all done at {time.strftime('%H:%M:%S')} =====",
          flush=True)


if __name__ == "__main__":
    main()
