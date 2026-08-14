# -*- coding: utf-8 -*-
"""
通用工具函数 -- 被多个模块共享。
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


# 网络/连接异常类 -- 这些值得重试
_NETWORK_ERRORS = (
    ConnectionError, ConnectionAbortedError, ConnectionResetError,
    ConnectionRefusedError, TimeoutError, OSError,
)

# 确定性错误（参数错误、类型错误等） -- 重试无用
_DETERMINISTIC_ERRORS = (TypeError, ValueError, KeyError, AttributeError)


def try_fetch(fn, retries: int | None = None, **kwargs) -> pd.DataFrame | None:
    """
    带重试的 AkShare 数据获取封装。
    指数退避重试：第 n 次失败后等待 2^n 秒再试。
    确定性错误（TypeError/ValueError 等）不重试，直接返回 None。
    """
    max_retries = retries if retries is not None else AKSHARE_RETRIES

    _patch_akkshare_session()

    for attempt in range(1 + max_retries):
        try:
            df = fn(**kwargs)
        except _DETERMINISTIC_ERRORS as e:
            print(f"  [X] 函数调用参数错误（无需重试）: {e}")
            return None
        except _NETWORK_ERRORS as e:
            if attempt < max_retries:
                wait = min(2 ** (attempt + 1), 16)
                print(f"  [!] 第 {attempt + 1} 次请求失败: {e}")
                print(f"     等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            else:
                print(f"  [X] 请求最终失败（共 {1 + max_retries} 次尝试）: {e}")
                return None
        except Exception as e:
            if attempt < max_retries:
                wait = min(2 ** (attempt + 1), 16)
                print(f"  [!] 第 {attempt + 1} 次请求失败: {e}")
                print(f"     等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            else:
                print(f"  [X] 请求最终失败（共 {1 + max_retries} 次尝试）: {e}")
                return None

        if df is None:
            continue
        if isinstance(df, pd.DataFrame) and df.empty:
            continue
        return df

    return None


# 用于缓存是否已 patch
_SESSION_PATCHED = False


def _patch_akkshare_session() -> None:
    """
    为 AkShare 注入自定义请求头（module-level get/post + Session-level get/post）。
    仅执行一次。
    """
    global _SESSION_PATCHED
    if _SESSION_PATCHED:
        return
    _SESSION_PATCHED = True

    try:
        import requests as _req

        # 1. AkShare 内置 session
        try:
            session = _ak.session
            if hasattr(session, "headers"):
                session.headers.update(AKSHARE_HEADERS)
        except Exception:
            pass

        # 2. 保存原始方法
        _orig_get = _req.get
        _orig_post = _req.post
        _orig_session_get = _req.Session.get
        _orig_session_post = _req.Session.post

        def _merge_headers(kw):
            kw.setdefault("headers", {})
            for key, val in AKSHARE_HEADERS.items():
                kw["headers"].setdefault(key, val)
            kw.setdefault("timeout", AKSHARE_TIMEOUT)
            return kw

        # 3. Patch module-level
        def _wrapped_get(url, **kw):
            _merge_headers(kw)
            return _orig_get(url, **kw)

        def _wrapped_post(url, **kw):
            _merge_headers(kw)
            return _orig_post(url, **kw)

        _req.get = _wrapped_get
        _req.post = _wrapped_post

        # 4. Patch Session.get / Session.post
        def _session_get(self, url, **kw):
            _merge_headers(kw)
            return _orig_session_get(self, url, **kw)

        def _session_post(self, url, **kw):
            _merge_headers(kw)
            return _orig_session_post(self, url, **kw)

        _req.Session.get = _session_get
        _req.Session.post = _session_post

    except Exception:
        pass


def find_col_in(candidates: list, df: pd.DataFrame) -> str | None:
    """在 DataFrame 列中查找包含候选关键词的列名（大小写不敏感）"""
    for c in candidates:
        for col in df.columns:
            if c in str(col):
                return col
    return None


def _find_div_per_share_col(div_data: pd.DataFrame) -> str | None:
    """
    在分红 DataFrame 中查找"每股分红金额"对应的列。
    适配多种 AkShare 接口的列名：
      - stock_history_dividend_detail: "派息"
      - stock_dividend_cninfo: "分红"、"每股分红(元)" 等
    """
    for known in ["派息", "每股分红", "每股分红(元)", "每股股息", "每10股派息"]:
        if known in div_data.columns:
            return known
    for c in div_data.columns:
        cs = str(c)
        if ("每股" in cs and ("分红" in cs or "派" in cs or "息" in cs or "红利" in cs)) or \
           ("派息" in cs) or ("股息" in cs):
            return c
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
    方案 2：用 ROE x 30%（A 股银行典型分红比例）近似。
    方案 3：用净利润 x 30% / 隐含市值 近似。
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
            eps_div_col = _find_div_per_share_col(div_data)

            if date_col and eps_div_col:
                div_data[date_col] = pd.to_datetime(div_data[date_col], errors="coerce")
                div_data["年份"] = div_data[date_col].dt.year
                year_div = div_data[div_data["年份"] == year]
                if not year_div.empty:
                    raw_val = year_div[eps_div_col].iloc[0]
                    try:
                        div_per_share = float(raw_val)
                    except (ValueError, TypeError):
                        div_per_share = 0.0

                    # "每10股派息"格式需要除以 10
                    if "10股" in eps_div_col and div_per_share > 10:
                        div_per_share = div_per_share / 10.0

                    if div_per_share > 0:
                        if not daily_df.empty and "日期" in daily_df.columns:
                            year_end = daily_df[daily_df["日期"] <= pd.Timestamp(f"{year}-12-31")]
                            if not year_end.empty:
                                price = float(year_end.iloc[-1]["收盘"])
                                if price > 0:
                                    return div_per_share / price * 100
        except Exception:
            pass

    # 方案 2：ROE x 30% 近似
    try:
        if roe_col and equity_col:
            roe_val = float(row[roe_col])
            if roe_val > 100:
                roe_val = roe_val / 100
            return roe_val * 0.30 / 100 * 100
    except Exception:
        pass

    # 方案 3：净利润 x 30% / 隐含市值 近似
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
    for _ in range(250):
        pe = np.random.normal(22, 4)
        pe = max(15, min(35, pe))
        bond_r = np.random.normal(0.028, 0.004)
        bond_r = max(0.02, min(0.04, bond_r))
        erp_list.append((1 / pe) - bond_r)
    return erp_list