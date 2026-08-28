# -*- coding: utf-8 -*-
"""进度报告：为 CLI 批量处理 / 历史回测提供终端进度条。

tqdm 可用时渲染标准终端进度条（ETA / 速率 / 自适应宽度）；未安装时降级为
向 stderr 的周期性百分比打印（每 ~10% 或描述变化时刷新，避免刷屏）。与
`utils/stats.py` 对 scipy 的处理同款：可选依赖 + 自动回退，不构成硬依赖。

仅服务于命令行（`main.py` / `analysis/backtest.py` 的 CLI 路径）；Streamlit
仪表盘另用 `st.progress` 构造同款 `(done, total, desc)` 回调，故本模块不依赖
Streamlit。两路径通过统一的 `on_progress(done, total, desc)` 回调解耦：CLI
传入 `Progress()` 实例，仪表盘传入闭包。
"""
from __future__ import annotations

import sys

try:
    from tqdm.auto import tqdm as _tqdm
    _HAS_TQDM = True
except Exception:  # pragma: no cover - 依赖缺失时的降级路径
    _HAS_TQDM = False


class Progress:
    """进度回调：``__call__(done, total, desc)`` 更新进度条。

    用法::

        with Progress() as prog:
            for i, sym in enumerate(items):
                ...  # 干活
                prog(i + 1, len(items), f"分析 {sym}")

    ``total`` 在调用间变化时（如从"预取数据"切到"逐期回测"阶段）自动关闭旧条、
    新建新条，使各阶段进度独立呈现而非跨阶段跳变。无 tqdm 时回退为 stderr
    百分比打印（同样支持跨阶段，仅按 desc 与百分比阈值刷新）。
    """

    def __init__(self, *, stream=None, leave: bool = True):
        self._bar = None
        self._stream = stream or sys.stderr
        self._leave = leave
        self._total = None
        self._last_pct = -1
        self._last_desc = None

    def __call__(self, done, total, desc=None):
        if total is None or total <= 0:
            total = done if done > 0 else 1
        if _HAS_TQDM:
            # 跨阶段（total 变化）：关旧条、稍后新建，避免百分比跳变
            if self._bar is not None and self._total != total:
                self._bar.close()
                self._bar = None
            if self._bar is None:
                self._bar = _tqdm(total=total, desc=desc or "",
                                  leave=self._leave, file=self._stream,
                                  dynamic_ncols=True)
                self._total = total
            if self._bar.total != total:
                self._bar.total = total
                self._bar.refresh()
            self._bar.n = done
            if desc is not None:
                self._bar.set_description(desc)
            self._bar.refresh()
        else:
            pct = int(done / total * 100) if total else 0
            if (pct >= self._last_pct + 10 or pct >= 100
                    or (desc is not None and desc != self._last_desc)):
                self._stream.write(
                    f"\r{(desc or ''):<24} {pct:3d}% ({done}/{total})")
                self._stream.flush()
                self._last_pct = pct
                self._last_desc = desc

    def close(self):
        if self._bar is not None:
            self._bar.close()
            self._bar = None
            self._total = None
        elif self._last_pct >= 0:
            self._stream.write("\n")
            self._stream.flush()
            self._last_pct = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
