"""
交易日历工具，替代聚宽 get_trade_days / get_all_trade_days。
首次调用时从 Tushare 或 AKShare 拉取交易日历并缓存到 SQLite。
"""
import datetime as dt
import sqlite3
from pathlib import Path

import config

_DB = config.DB_PATH
_TABLE = "trade_calendar"
_calendar: list[dt.date] | None = None


def _ensure_table(conn: sqlite3.Connection):
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_TABLE} (trade_date TEXT PRIMARY KEY)"
    )


def _load_from_db() -> list[dt.date]:
    if not Path(_DB).exists():
        return []
    conn = sqlite3.connect(str(_DB))
    _ensure_table(conn)
    rows = conn.execute(
        f"SELECT trade_date FROM {_TABLE} ORDER BY trade_date"
    ).fetchall()
    conn.close()
    return [dt.datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows]


def _save_to_db(dates: list[dt.date]):
    conn = sqlite3.connect(str(_DB))
    _ensure_table(conn)
    conn.executemany(
        f"INSERT OR IGNORE INTO {_TABLE} (trade_date) VALUES (?)",
        [(d.strftime("%Y-%m-%d"),) for d in dates],
    )
    conn.commit()
    conn.close()


def _fetch_remote() -> list[dt.date]:
    """从 AKShare 拉取 A 股交易日历。"""
    import akshare as ak
    import pandas as pd

    df = ak.tool_trade_date_hist_sina()
    dates = sorted(pd.Timestamp(d).date() for d in df["trade_date"])
    return dates


def refresh_calendar():
    """从远程刷新交易日历并写入本地缓存。"""
    import pandas as pd  # noqa: F811
    dates = _fetch_remote()
    _save_to_db(dates)
    global _calendar
    _calendar = dates


def _get_calendar() -> list[dt.date]:
    global _calendar
    if _calendar is None:
        _calendar = _load_from_db()
        if not _calendar:
            refresh_calendar()
    return _calendar


# ------------------------------------------------------------------
# 公开 API，与聚宽 get_trade_days 语义对齐
# ------------------------------------------------------------------

def get_trade_days(end_date: dt.date | str, count: int) -> list[dt.date]:
    """返回 end_date 及之前的 count 个交易日（含 end_date）。"""
    if isinstance(end_date, str):
        end_date = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
    cal = _get_calendar()
    idx = _bisect_right(cal, end_date)
    start = max(idx - count, 0)
    return cal[start:idx]


def get_all_trade_days() -> list[dt.date]:
    return list(_get_calendar())


def get_previous_trade_day(date: dt.date | str) -> dt.date:
    """给定日期的前一个交易日。"""
    if isinstance(date, str):
        date = dt.datetime.strptime(date, "%Y-%m-%d").date()
    cal = _get_calendar()
    idx = _bisect_left(cal, date)
    return cal[max(idx - 1, 0)]


def is_trade_day(date: dt.date | str) -> bool:
    if isinstance(date, str):
        date = dt.datetime.strptime(date, "%Y-%m-%d").date()
    cal = _get_calendar()
    idx = _bisect_left(cal, date)
    return idx < len(cal) and cal[idx] == date


def get_next_trade_day(date: dt.date | str) -> dt.date:
    if isinstance(date, str):
        date = dt.datetime.strptime(date, "%Y-%m-%d").date()
    cal = _get_calendar()
    idx = _bisect_right(cal, date)
    return cal[min(idx, len(cal) - 1)]


# ------------------------------------------------------------------
# bisect helpers (avoid importing bisect for minimal footprint)
# ------------------------------------------------------------------
def _bisect_left(a, x):
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _bisect_right(a, x):
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo
