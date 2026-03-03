"""
PostgreSQL 本地缓存层。
历史日K线、估值、证券信息等只拉一次，后续读缓存。
"""
from __future__ import annotations

import datetime as dt
import json
import threading

import pandas as pd
import psycopg2
import psycopg2.extras

import config

_lock = threading.Lock()


def _conn():
    return psycopg2.connect(config.DATABASE_URL)


def _ensure_tables():
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_price (
            code TEXT, trade_date TEXT,
            open DOUBLE PRECISION, high DOUBLE PRECISION,
            low DOUBLE PRECISION, close DOUBLE PRECISION,
            volume DOUBLE PRECISION, amount DOUBLE PRECISION,
            high_limit DOUBLE PRECISION, low_limit DOUBLE PRECISION,
            pre_close DOUBLE PRECISION,
            PRIMARY KEY (code, trade_date)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS valuation (
            code TEXT, trade_date TEXT,
            pe_ratio DOUBLE PRECISION, pb_ratio DOUBLE PRECISION,
            ps_ratio DOUBLE PRECISION,
            market_cap DOUBLE PRECISION,
            circulating_market_cap DOUBLE PRECISION,
            turnover_ratio DOUBLE PRECISION,
            PRIMARY KEY (code, trade_date)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS security_info (
            code TEXT PRIMARY KEY,
            display_name TEXT,
            start_date TEXT, end_date TEXT,
            concepts TEXT
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


_ensure_tables()


# ------------------------------------------------------------------
# 日K线缓存
# ------------------------------------------------------------------
def get_cached_daily(
    code: str, start_date: str, end_date: str, fields: list[str] | None = None
) -> pd.DataFrame | None:
    with _lock:
        conn = _conn()
        if fields:
            cols = "trade_date, " + ", ".join(f for f in fields if f != "trade_date")
        else:
            cols = "*"
        sql = (
            f"SELECT {cols} FROM daily_price "
            f"WHERE code=%s AND trade_date>=%s AND trade_date<=%s ORDER BY trade_date"
        )
        df = pd.read_sql_query(sql, conn, params=(code, start_date, end_date))
        conn.close()
    if df.empty:
        return None
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df.set_index("trade_date", inplace=True)
    return df


def save_daily(code: str, df: pd.DataFrame):
    """df.index 应为 datetime，columns 包含 OHLCV 等字段。"""
    if df.empty:
        return
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        for idx, row in df.iterrows():
            trade_date = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
            cur.execute(
                """INSERT INTO daily_price
                   (code, trade_date, open, high, low, close, volume, amount,
                    high_limit, low_limit, pre_close)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (code, trade_date) DO UPDATE SET
                    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                    close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount,
                    high_limit=EXCLUDED.high_limit, low_limit=EXCLUDED.low_limit,
                    pre_close=EXCLUDED.pre_close""",
                (
                    code, trade_date,
                    row.get("open"), row.get("high"), row.get("low"), row.get("close"),
                    row.get("volume"), row.get("amount"),
                    row.get("high_limit"), row.get("low_limit"),
                    row.get("pre_close"),
                ),
            )
        conn.commit()
        cur.close()
        conn.close()


def batch_save_daily(rows: list[tuple]):
    """批量写入日K线。rows 为 (code, trade_date, open, high, low, close, volume, amount, high_limit, low_limit, pre_close) 元组列表。"""
    if not rows:
        return
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO daily_price
               (code, trade_date, open, high, low, close, volume, amount,
                high_limit, low_limit, pre_close)
               VALUES %s
               ON CONFLICT (code, trade_date) DO UPDATE SET
                open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount,
                high_limit=EXCLUDED.high_limit, low_limit=EXCLUDED.low_limit,
                pre_close=EXCLUDED.pre_close""",
            rows,
        )
        conn.commit()
        cur.close()
        conn.close()


def has_daily_date(trade_date: str, min_count: int = 500) -> bool:
    """
    检查某日期是否已完成全市场日线预加载。

    少量个股缓存（如指数查询写入的 1~2 条）不应视为已预加载，
    因此要求记录数 >= min_count 才判定为缓存命中。
    A 股全市场约 5000 只，min_count=500 足够区分。
    """
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM daily_price WHERE trade_date=%s",
            (trade_date,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
    return row is not None and row[0] >= min_count


def get_cached_daily_bulk(trade_date: str) -> pd.DataFrame:
    """一次查询返回某日全市场日线，columns 含 code + OHLCV + 涨跌停。"""
    with _lock:
        conn = _conn()
        df = pd.read_sql_query(
            "SELECT * FROM daily_price WHERE trade_date=%s",
            conn,
            params=(trade_date,),
        )
        conn.close()
    return df


# ------------------------------------------------------------------
# 估值缓存
# ------------------------------------------------------------------
def get_cached_valuation(code: str, date: str) -> dict | None:
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM valuation WHERE code=%s AND trade_date=%s", (code, date)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
    if row is None:
        return None
    cols = ["code", "trade_date", "pe_ratio", "pb_ratio", "ps_ratio",
            "market_cap", "circulating_market_cap", "turnover_ratio"]
    return dict(zip(cols, row))


def save_valuation(code: str, date: str, data: dict):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO valuation
               (code, trade_date, pe_ratio, pb_ratio, ps_ratio,
                market_cap, circulating_market_cap, turnover_ratio)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (code, trade_date) DO UPDATE SET
                pe_ratio=EXCLUDED.pe_ratio, pb_ratio=EXCLUDED.pb_ratio,
                ps_ratio=EXCLUDED.ps_ratio, market_cap=EXCLUDED.market_cap,
                circulating_market_cap=EXCLUDED.circulating_market_cap,
                turnover_ratio=EXCLUDED.turnover_ratio""",
            (
                code, date,
                data.get("pe_ratio"), data.get("pb_ratio"), data.get("ps_ratio"),
                data.get("market_cap"), data.get("circulating_market_cap"),
                data.get("turnover_ratio"),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()


# ------------------------------------------------------------------
# 证券信息缓存
# ------------------------------------------------------------------
def get_cached_security_info(code: str) -> dict | None:
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM security_info WHERE code=%s", (code,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
    if row is None:
        return None
    return {
        "code": row[0],
        "display_name": row[1],
        "start_date": row[2],
        "end_date": row[3],
        "concepts": json.loads(row[4]) if row[4] else [],
    }


def save_security_info(code: str, info: dict):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO security_info
               (code, display_name, start_date, end_date, concepts)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (code) DO UPDATE SET
                display_name=EXCLUDED.display_name, start_date=EXCLUDED.start_date,
                end_date=EXCLUDED.end_date, concepts=EXCLUDED.concepts""",
            (
                code,
                info.get("display_name", ""),
                info.get("start_date", ""),
                info.get("end_date", ""),
                json.dumps(info.get("concepts", []), ensure_ascii=False),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()


def batch_save_security_info(items: list[dict]):
    """批量写入证券信息。每条 dict 需包含 code, display_name, start_date 等字段。"""
    if not items:
        return
    rows = [
        (
            it["code"],
            it.get("display_name", ""),
            it.get("start_date", ""),
            it.get("end_date", ""),
            json.dumps(it.get("concepts", []), ensure_ascii=False),
        )
        for it in items
    ]
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO security_info
               (code, display_name, start_date, end_date, concepts)
               VALUES %s
               ON CONFLICT (code) DO UPDATE SET
                display_name=EXCLUDED.display_name, start_date=EXCLUDED.start_date,
                end_date=EXCLUDED.end_date, concepts=EXCLUDED.concepts""",
            rows,
        )
        conn.commit()
        cur.close()
        conn.close()


def get_all_cached_security_start_dates() -> dict[str, str]:
    """返回 {code: start_date} 映射，用于批量新股过滤。"""
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT code, start_date FROM security_info WHERE start_date != ''"
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
    return {row[0]: row[1] for row in rows}
