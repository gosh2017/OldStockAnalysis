# -*- coding: utf-8 -*-
"""
通用工具函数 — 被多个模块共享。
"""
import time
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from config import AKSHARE_TIMEOUT, AKSHARE_RETRIES, AKSHARE_HEADERS

# 配置 AkShare 全局会话
try:
    import akshare as _ak
    _ak.set_time_out(AKSHARE_TIMEOUT)
except Exception:
    pass

try:
    import akshare as _ak2
    _ak2.set_proxies(False)
except Exception:
    pass


def sep(title: str = "") -> None:
    """打印分隔线"""
    print(f"\n{'=' * 70}")
    if title:
        print(f"  {title}")
        print(f"{'=' * 70}")


def try_fetch(fn, retries: int | None = None, **kwargs) -> pd.DataFrame | None:
    """
    带重试的 AkShare 数据获取封装。
    指数退避重试：第 n 次失败后等待 2^n 秒再试。
    同时注入自定义 HTTP 头，降低被服务器拦截的概率。
    """
    max_retries = retries if retries is not None else AKSHARE_RETRIES

    # 注入全局 session headers（AkShare 底层用 requests.Session）
    _patch_akkshare_session()

    for attempt in range(1 + max_retries):
        try:
            df = fn(**kwargs)
            if df is None:
                continue
            if isinstance(df, pd.DataFrame) and df.empty:
                continue
            return df
        except Exception as e:
            if attempt < max_retries:
                wait = min(2 ** (attempt + 1), 16)  # 2s, 4s, 8s, 16s cap
                print(f"  [!] 第 {attempt + 1} 次请求失败: {e}")
                print(f"     等待 {wait}s 后重试...")
                time.sleep(wait)
            else:
                print(f"  [X] 请求最终失败（共 {1 + max_retries} 次尝试）: {e}")
    return None


# 用于缓存是否已 patch
_SESSION_PATCHED = False


def _patch_akkshare_session() -> None:
    """
    为 AkShare 全局 session 注入自定义请求头。
    某些网络环境下（公司防火墙/代理），默认无 User-Agent 会被拒绝连接。
    仅执行一次。
    """
    global _SESSION_PATCHED
    if _SESSION_PATCHED:
        return
    _SESSION_PATCHED = True

    try:
        import requests as _req
        # 1. 尝试 AkShare 内置 session
        try:
            session = _ak.session
            if hasattr(session, "headers"):
                session.headers.update(AKSHARE_HEADERS)
                print("  [调试] AkShare session headers 已配置")
        except Exception:
            pass

        # 2. 尝试东方财富接口专用 session（akshare 内部常使用）
        for module_attr in ["stock_zh_a_hist", "stock_financial_abstract"]:
            try:
                mod = getattr(_ak, module_attr, None)
                if mod and hasattr(mod, "__module__"):
                    pass  # 函数级，无法直接改 session
            except Exception:
                pass

        # 3. 通过猴子补丁覆盖 requests.get/post 以注入头
        _original_get = _req.get
        _original_post = _req.post

        def _wrapped_get(url, **kw):
            kw.setdefault("headers", {})
            for key, val in AKSHARE_HEADERS.items():
                kw["headers"].setdefault(key, val)
            kw.setdefault("timeout", AKSHARE_TIMEOUT)
            return _original_get(url, **kw)

        def _wrapped_post(url, **kw):
            kw.setdefault("headers", {})
            for key, val in AKSHARE_HEADERS.items():
                kw["headers"].setdefault(key, val)
            kw.setdefault("timeout", AKSHARE_TIMEOUT)
            return _original_post(url, **kw)

        _req.get = _wrapped_get
        _req.post = _wrapped_post

    except Exception:
        pass


def find_col_in(candidates: list, df: pd.DataFrame) -> str | None:
    """在 DataFrame 列中查找包含候选关键词的列名（大小写不敏感）"""
    for c in candidates:
        for col in df.columns:
            if c in str(col):
                return col
    return None


def estimate_dividend_yield(
    year: int, row, equity_col: str,
    dividend_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    roe_col: str, np_col: str,
) -> float:
    """
    估算某年的股息率。
    方案 1：从分红记录中提取实际分红金额 / 年末股价。
    方案 2：用 ROE × 30%（A 股银行典型分红比例）近似。
    方案 3：用净利润 × 30% / 隐含市值 近似。
    """
    # 方案 1：分红记录
    if dividend_df is not None and not dividend_df.empty:
        try:
            div_data = dividend_df.copy()
            date_col = None
            for c in div_data.columns:
                if "公告日期" in str(c) or "除权" in str(c) or "日期" in str(c):
                    date_col = c
                    break
            if date_col:
                div_data[date_col] = pd.to_datetime(div_data[date_col], errors="coerce")
                div_data["年份"] = div_data[date_col].dt.year
                year_div = div_data[div_data["年份"] == year]
                if not year_div.empty:
                    eps_div_col = None
                    for c in div_data.columns:
                        if "每股" in str(c) and ("分红" in str(c) or "派" in str(c)
                                                    or "息" in str(c) or "红利" in str(c)):
                            eps_div_col = c
                            break
                    if eps_div_col:
                        div_per_share = float(year_div[eps_div_col].iloc[0])
                        if not daily_df.empty and "日期" in daily_df.columns:
                            year_end = daily_df[daily_df["日期"] <= pd.Timestamp(f"{year}-12-31")]
                            if not year_end.empty:
                                price = float(year_end.iloc[-1]["收盘"])
                                return div_per_share / price * 100
        except Exception:
            pass

    # 方案 2：ROE × 30% 近似
    try:
        if roe_col and equity_col:
            roe_val = float(row[roe_col])
            if roe_val > 100:
                roe_val = roe_val / 100
            return roe_val * 0.30 / 100 * 100
    except Exception:
        pass

    # 方案 3：净利润 × 30% / 隐含市值 近似
    try:
        if np_col:
            np_val = float(row[np_col])
            implied_mv = np_val * 6
            dividend = np_val * 0.30
            return dividend / implied_mv * 100
    except Exception:
        pass

    return 0.0


def generate_historical_erp() -> list:
    """
    基于近 5 年 A 股市场经验数据生成股债性价比（ERP）的历史模拟分布，
    用于估算当前值的分位数位置。
    """
    np.random.seed(42)
    erp_list = []
    for _ in range(250):  # 约 5 年交易日
        pe = np.random.normal(22, 4)
        pe = max(15, min(35, pe))
        bond_r = np.random.normal(0.028, 0.004)
        bond_r = max(0.02, min(0.04, bond_r))
        erp_list.append((1 / pe) - bond_r)
    return erp_list
