# -*- coding: utf-8 -*-
"""一次性导入东财行业板块到 Hikyuu 本地库（鲁棒版）。

背景：Hikyuu 包的 download_block.py:339-343 把行业/概念/地域三个下载函数
【硬编码注释掉】了，默认导入只下指数板块（download_all_zsbk_info，走
sina/csindex）。故导入后 stock.db 的 block 表全是「指数板块」，
get_block_list('行业板块') 为空 → 批量筛选行业列全 None → 桶全「其他」。

旧版本脚本直接调 hikyuu 的 down_em_all_hybk_info()，但该函数有两个致命缺陷：
  1. 把间歇性 RemoteDisconnected 当致命 IP 封禁（raise + "拖动划窗解锁"），
     单请求失败即整轮 abort——而实测东财 push2 端点正是**间歇性可达**
     （同一请求秒级内时好时坏，与 UA 无关）。
  2. @timeout(600s) 对 496 板块（每板块 sleep 1–3s + 分页）太短，必超时截断。

本版弃用 down_em_all_hybk_info，自管「取板块名 + 取成份 + 落盘」全流程，
**逐请求**重试 + 指数退避（专治间歇性 RemoteDisconnected），无全局 timeout
悬崖，断点续传（复用 hikyuu 的 5 天缓存判据）。落盘格式与 hikyuu 完全一致：
  ~/.hikyuu/downloads/block/行业板块/{code}_{name}.txt
每行一个经 modifiy_code（600519→SH600519）的代码——这样
em_import_block_to_sqlite 的 read_block_from_path 会自动以
category=目录名「行业板块」、block name=下划线后部分入库。

入库仍调 em_import_block_to_sqlite(conn)（复用 hikyuu 入库逻辑；指数板块
.txt 已缓存，其内部 download_block_info() 秒过不重下，read_block_from_path
按磁盘上存在的 category 增量入库——delete+insert 仅针对存在的 category，
不破坏指数板块）。导入后查询期走本地、零 HTTP。

用法： python scripts/import_hikyuu_industry_blocks.py
"""
import os
import sys
import time
import math
import random
import traceback

import requests

# 作为脚本运行（python scripts/import_hikyuu_industry_blocks.py）时 sys.path[0]
# 是脚本目录，不含项目根；插入根目录以便 probe_calibrate 的 from config import。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


STOCK_DB = r"c:\stock\stock.db"

# 东财 push2 端点（与 hikyuu zh_block_em.py 一致）
_NAMES_URL = "https://19.push2.eastmoney.com/api/qt/clist/get"
_CONS_URL = "http://30.push2.eastmoney.com/api/qt/clist/get"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://quote.eastmoney.com/",
}
# 逐请求重试：8 次，退避 2/4/6/8/10/12/14/16s（专治间歇性 RemoteDisconnected）
_MAX_ATTEMPTS = 8
_REQ_TIMEOUT = 15
_CACHE_SEC = 5 * 24 * 60 * 60  # 与 hikyuu is_file_can_download 默认一致
# 显式禁用代理：requests 默认读 Windows 系统代理（IE/注册表），本机该代理对
# EM push2 返回 ProxyError（即便无 HTTP_PROXY 环境变量）→ 板块名拉取 8 次重试全败。
# 禁用后直连可达（实测 total=496 个行业板块）。akshare 的东财端点是另一种故障
# （RemoteDisconnected），与此处代理无关。
_NO_PROXY = {"http": None, "https": None}


def _get_json_with_retry(url, params, label):
    """单次分页请求，带逐请求重试 + 指数退避。

    间歇性 RemoteDisconnected / ConnectionError / Timeout 在秒级内自愈，
    故对【同一请求】重试 N 次而非整函数级 abort。返回解析后的 json 或 None。
    """
    last_err = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            r = requests.get(url, params=params, headers=_HEADERS,
                             timeout=_REQ_TIMEOUT, proxies=_NO_PROXY)
            return r.json()
        except Exception as e:  # noqa: BLE001  间歇性故障要吞下重试
            last_err = e
            kind = type(e).__name__
            if attempt < _MAX_ATTEMPTS:
                backoff = 2 * attempt  # 2/4/6/…s
                print(f"[industry]   {label} 请求失败({kind})，"
                      f"{backoff}s 后重试({attempt}/{_MAX_ATTEMPTS})",
                      flush=True)
                time.sleep(backoff)
    print(f"[industry]   {label} {_MAX_ATTEMPTS} 次重试全失败: "
          f"{type(last_err).__name__}: {last_err}", flush=True)
    return None


def _fetch_all_pages(url, base_params, label, row_key):
    """分页拉取并合并 diff，逐请求重试。row_key: 'f12,f14' 或 'f12'。"""
    params = dict(base_params)
    j = _get_json_with_retry(url, params, f"{label} p1")
    if j is None or not j.get("data") or j["data"].get("diff") is None:
        return None
    rows = list(j["data"]["diff"])
    total = j["data"]["total"]
    pages = math.ceil(total / 100)
    for pg in range(2, pages + 1):
        params["pn"] = pg
        j = _get_json_with_retry(url, params, f"{label} p{pg}")
        if j is None or not j.get("data") or j["data"].get("diff") is None:
            # 单页拉空不致命，保留已得行，继续
            continue
        rows.extend(j["data"]["diff"])
        time.sleep(random.uniform(1, 3))
    return rows


def fetch_industry_block_names():
    """取全部行业板块 (code, name) 列表（约 496 个）。"""
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f3",
        "fs": "m:90 t:2 f:!50", "fields": "f12,f14",
        "_": "1626075887768",
    }
    rows = _fetch_all_pages(_NAMES_URL, params, "板块名", "f12,f14")
    if not rows:
        return []
    # 去重（EM 偶有重复条目，如 BK1015 能源金属出现两次）
    seen = set()
    blks = []
    for v in rows:
        code, name = v.get("f12"), v.get("f14")
        if code and name and code not in seen:
            seen.add(code)
            blks.append((code, name))
    return blks


def fetch_block_constituent_codes(blk_code):
    """取指定行业板块的成份代码列表（原始 6 位码，未 modifiy_code）。"""
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f3",
        "fs": f"b:{blk_code} f:!50", "fields": "f12",
        "_": "1626081702127",
    }
    rows = _fetch_all_pages(_CONS_URL, params, f"{blk_code}", "f12")
    if not rows:
        return None
    return [v["f12"] for v in rows if v.get("f12")]


def download_industry_blocks():
    """下载东财行业板块成份 .txt（每板块 5 天缓存，重跑续传）。

    逐板块：5 天缓存命中则跳过；否则拉成份（逐请求重试），modifiy_code 后
    落盘。单板块最终失败仅跳过该板块，不阻断其余 495 个——这是相对旧版
    down_em_all_hybk_info（首块即 abort）的核心修复。
    """
    from hikyuu.data.download_block import (
        _BLOCK_SAVE_PATH, is_file_can_download, save_block,
    )
    from hikyuu.data.common import modifiy_code

    save_path = os.path.join(_BLOCK_SAVE_PATH, "行业板块")
    os.makedirs(save_path, exist_ok=True)

    print("[industry] 拉取行业板块名列表...", flush=True)
    blks = fetch_industry_block_names()
    if not blks:
        print("[industry][FATAL] 板块名列表为空（端点全 8 次重试失败？）",
              flush=True)
        return False
    print(f"[industry] 共 {len(blks)} 个行业板块，开始逐个下载成份"
          f"（5 天缓存跳过，{time.strftime('%H:%M:%S')}）", flush=True)

    ok = skip = fail = 0
    t0 = time.time()
    for i, (code, name) in enumerate(blks, 1):
        filename = os.path.join(save_path, f"{code}_{name}.txt")
        if not is_file_can_download(filename, _CACHE_SEC):
            skip += 1
            continue
        try:
            stk_codes = fetch_block_constituent_codes(code)
            if stk_codes is None:
                fail += 1
                print(f"[industry] {i}/{len(blks)} {name}({code}) 拉取失败，"
                      f"跳过", flush=True)
                time.sleep(2)
                continue
            stk_codes = [modifiy_code(c) for c in stk_codes]
            stk_codes = [c for c in stk_codes if c is not None]
            save_block(stk_codes, filename)
            ok += 1
            if i % 20 == 0 or i == len(blks):
                dt = time.time() - t0
                print(f"[industry] {i}/{len(blks)} 完成，{name}={len(stk_codes)}只 "
                      f"（已下载{ok} 跳过{skip} 失败{fail}，{dt:.0f}s）",
                      flush=True)
        except Exception as e:  # noqa: BLE001  单板块失败不阻断其余
            fail += 1
            print(f"[industry] {i}/{len(blks)} {name}({code}) 异常: "
                  f"{type(e).__name__}: {e}，跳过", flush=True)
            traceback.print_exc()
        time.sleep(random.uniform(1, 3))

    dt = time.time() - t0
    print(f"[industry] 下载轮次结束：下载{ok} 跳过(缓存){skip} 失败{fail}，"
          f"耗时 {dt:.0f}s ({dt/60:.1f} min)", flush=True)
    return fail == 0


def import_to_sqlite():
    """read_block_from_path 读全部 category（含新行业板块）→ 入 stock.db。"""
    import sqlite3
    from hikyuu.data.em_block_to_sqlite import em_import_block_to_sqlite

    if not os.path.exists(STOCK_DB):
        print(f"[industry][FATAL] {STOCK_DB} 不存在，先跑 run_hikyuu_import.py",
              flush=True)
        return 0
    conn = sqlite3.connect(STOCK_DB)
    try:
        n = em_import_block_to_sqlite(conn)
        print(f"[industry] 入库 block 表 {n} 行（含指数+行业）", flush=True)
        return n
    except Exception as e:  # noqa: BLE001
        print(f"[industry] 入库抛异常: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return 0
    finally:
        conn.close()


def probe_calibrate():
    """重载 Hikyuu，dump 行业 category / 板块名 / 样本股归属 + 桶覆盖，供 config 校准。"""
    import hikyuu as hku
    from config import (HIKYUU_INDUSTRY_CATEGORY, HIKYUU_INDUSTRY_TO_BUCKET,
                        SW_TO_BUCKET)
    from data.fetcher import map_to_industry_bucket

    hku.load_hikyuu(load_history_finance=False, load_weight=True, start_spot=False)
    sm = hku.sm
    print(f"[probe] len(sm) = {len(sm)}", flush=True)

    cats = [str(c) for c in sm.get_category_list()]
    print(f"[probe] get_category_list = {cats}", flush=True)

    for cat in ("行业板块", "概念板块", "地域板块"):
        try:
            bl = sm.get_block_list(cat)
            names = sorted(str(b.name) for b in bl)
            print(f"[probe] get_block_list({cat!r}) = {len(names)} 个",
                  flush=True)
            if cat == HIKYUU_INDUSTRY_CATEGORY and names:
                # 把全部行业板块名按 map_to_industry_bucket 分桶，统计覆盖率
                from collections import Counter
                cnt = Counter(map_to_industry_bucket(n) for n in names)
                uncovered = [n for n in names
                             if map_to_industry_bucket(n) == "其他"]
                print(f"[probe]   行业板块→桶分布: {dict(cnt)}", flush=True)
                print(f"[probe]   落「其他」的板块名({len(uncovered)}): "
                      f"{uncovered}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[probe] get_block_list({cat!r}) ERR: {type(e).__name__}: {e}",
                  flush=True)

    samples = [("sh600519", "贵州茅台"), ("sh600000", "浦发银行"),
               ("sh601398", "工商银行"), ("sz000001", "平安银行"),
               ("sz300750", "宁德时代"), ("sh688981", "中芯国际")]
    print("[probe] 样本股行业归属（category='行业板块'）:", flush=True)
    for sym, expect in samples:
        try:
            s = sm[sym]
            if not s.valid:
                print(f"[probe]   {sym} {expect}: invalid", flush=True)
                continue
            bis = s.get_belong_to_block_list(category=HIKYUU_INDUSTRY_CATEGORY)
            inds = [str(b.name) for b in bis] if bis else []
            bucket = (map_to_industry_bucket(inds[0]) if inds else "（空→其他）")
            print(f"[probe]   {sym} {s.name}（期望 {expect}）: {inds} → 桶 {bucket}",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[probe]   {sym} ERR: {type(e).__name__}: {e}", flush=True)


def main():
    print(f"[industry] ===== start {time.strftime('%Y-%m-%d %H:%M:%S')} =====",
          flush=True)
    print(f"[industry] cwd = {os.getcwd()}", flush=True)
    print(f"[industry] py  = {sys.version.split()[0]}", flush=True)
    print(f"[industry] db  = {STOCK_DB}", flush=True)
    print(f"[industry] 源  = EM push2 (19/30.push2.eastmoney.com)，仅导入期 HTTP",
          flush=True)

    ok = download_industry_blocks()
    if not ok:
        print("[industry][WARN] 部分板块下载失败（见上），已下载的仍会入库，"
              "缺失板块对应股票行业列空 → 桶「其他」。可重跑续传（5 天缓存跳过）",
              flush=True)

    n = import_to_sqlite()
    print(f"[industry] 入库行数 = {n}", flush=True)

    print("[industry] ===== 校准探针 =====", flush=True)
    try:
        probe_calibrate()
    except Exception:  # noqa: BLE001
        print("[industry] 探针抛异常:", flush=True)
        traceback.print_exc()

    print(f"[industry] ===== all done {time.strftime('%H:%M:%S')} =====",
          flush=True)


if __name__ == "__main__":
    main()
