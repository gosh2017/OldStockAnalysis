# -*- coding: utf-8 -*-
"""
磁盘缓存 — 把相对稳定的实盘数据（A 股代码-名称列表、个股 PE/PB 等）
落盘到 .cache/，避免每次启动 streamlit / 关闭 Demo 后都全量联网拉取。

语义：
  - 缓存文件不存在或超过 ttl_hours → 调 builder() 重新获取并落盘
  - 缓存有效 → 直接读盘（pickle），不联网
  - builder 返回 None / 空 DataFrame → 视为失败，不落盘（避免缓存失败态）
  - 任何读写异常都静默降级回 builder（缓存不应阻断主流程）

pickle 足以胜任中小体量 DataFrame（股票列表 ~5500 行、PE/PB ~1000 期），
且与 pandas DataFrame 零成本互转，无需引入额外依赖。
"""
from __future__ import annotations

import os
import pickle
import time
from typing import Callable, TypeVar

import pandas as pd

from config import CACHE_DIR

T = TypeVar("T")


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, name)


def _is_empty(value) -> bool:
    """判定 builder 结果是否为"失败态"（不应落盘）。"""
    if value is None:
        return True
    if isinstance(value, pd.DataFrame) and value.empty:
        return True
    return False


def disk_cache(name: str, ttl_hours: float, builder: Callable[[], T],
               force_refresh: bool = False) -> T:
    """
    带 TTL 的磁盘缓存包装器。

    :param name: 缓存文件名（置于 CACHE_DIR 下），如 "stock_list.pkl"
    :param ttl_hours: 有效期（小时）；过期则重新拉取
    :param builder: 缓存未命中时调用的取数函数，返回 DataFrame 或 None
    :param force_refresh: True 则强制重新拉取并覆盖缓存（用于"刷新"按钮）
    :return: builder 的返回值（命中缓存时为反序列化结果）
    """
    path = _cache_path(name)

    if not force_refresh and os.path.exists(path):
        try:
            mtime = os.path.getmtime(path)
            if time.time() - mtime < ttl_hours * 3600:
                with open(path, "rb") as f:
                    print(f"  [CACHE] 命中本地缓存 {name}（{(time.time() - mtime) / 3600:.1f}h 前生成）")
                    return pickle.load(f)
        except Exception as e:
            print(f"  [CACHE] 读取缓存 {name} 失败，回退到实时拉取: {e}")

    value = builder()
    if _is_empty(value):
        # 失败态不落盘，避免把瞬时失败固化成"未来 ttl 内都返回空"
        return value

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(value, f)
        print(f"  [CACHE] 已写入本地缓存 {name}")
    except Exception as e:
        print(f"  [CACHE] 写入缓存 {name} 失败（不影响本次结果）: {e}")

    return value


def clear_cache(name: str) -> None:
    """删除指定缓存文件（若存在）。"""
    path = _cache_path(name)
    try:
        if os.path.exists(path):
            os.remove(path)
            print(f"  [CACHE] 已清除 {name}")
    except Exception as e:
        print(f"  [CACHE] 清除 {name} 失败: {e}")
