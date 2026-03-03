"""
信号生成与格式化。
所有买卖操作最终汇聚于此，生成结构化信号并推送通知。
信号持久化到 PostgreSQL signals 表。

状态说明:
  CANDIDATE — 通过评分的候选信号
  EXECUTED  — 已执行买入/卖出
  SKIPPED   — 因涨停/仓位/资金等原因跳过
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import psycopg2

import config
from utils.logger import log


@dataclass
class Signal:
    type: str           # "BUY" / "SELL"
    stock: str
    price: float
    reason: str
    time: dt.datetime
    status: str = "EXECUTED"   # CANDIDATE / EXECUTED / SKIPPED
    extra: dict | None = None


# ------------------------------------------------------------------
# 数据库持久化
# ------------------------------------------------------------------

def _conn():
    return psycopg2.connect(config.DATABASE_URL)


def _ensure_signals_table():
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id SERIAL PRIMARY KEY,
            type TEXT NOT NULL,
            stock TEXT NOT NULL,
            price NUMERIC(12, 4) NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'EXECUTED',
            signal_time TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 兼容升级：已有表添加 status 列
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'signals' AND column_name = 'status'
            ) THEN
                ALTER TABLE signals ADD COLUMN status TEXT NOT NULL DEFAULT 'EXECUTED';
            END IF;
        END $$
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_signals_date
        ON signals ((signal_time::date))
    """)
    conn.commit()
    cur.close()
    conn.close()


try:
    _ensure_signals_table()
except Exception as e:
    log.warning(f"signals 表初始化失败（数据库可能未就绪）: {e}")


def _save_signal(sig: Signal):
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO signals (type, stock, price, reason, status, signal_time) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sig.type, sig.stock, sig.price, sig.reason, sig.status, sig.time),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log.error(f"信号持久化失败: {e}")


# ------------------------------------------------------------------
# 公开接口
# ------------------------------------------------------------------

def emit_signal(
    signal_type: str,
    stock: str,
    price: float,
    reason: str,
    ctx=None,
    status: str = "EXECUTED",
):
    """生成并推送一条交易信号。"""
    now = ctx.current_dt if ctx else dt.datetime.now()
    sig = Signal(
        type=signal_type, stock=stock, price=price,
        reason=reason, time=now, status=status,
    )
    _save_signal(sig)

    if status == "EXECUTED":
        icon = "🟢" if signal_type == "BUY" else "🔴"
        msg = f"{icon} [{signal_type}] {stock} @ {price:.2f} | {reason} | {now.strftime('%H:%M:%S')}"
        log.info(msg)
        from notify.push import send
        send(msg)
    else:
        log.info(f"[{status}] {signal_type} {stock} @ {price:.2f} | {reason}")

    return sig


def emit_message(text: str, ctx=None):
    """发送纯文本通知（非交易信号）。"""
    log.info(f"[消息] {text}")
    from notify.push import send
    send(text)


def get_today_signals(status: str | None = None) -> list[Signal]:
    """从数据库读取今日信号。可按 status 过滤。"""
    try:
        conn = _conn()
        cur = conn.cursor()
        if status:
            cur.execute(
                "SELECT type, stock, price, reason, status, signal_time "
                "FROM signals WHERE signal_time::date = %s AND status = %s "
                "ORDER BY signal_time",
                (dt.date.today(), status),
            )
        else:
            cur.execute(
                "SELECT type, stock, price, reason, status, signal_time "
                "FROM signals WHERE signal_time::date = %s "
                "ORDER BY signal_time",
                (dt.date.today(),),
            )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            Signal(
                type=r[0], stock=r[1], price=float(r[2]),
                reason=r[3], status=r[4], time=r[5],
            )
            for r in rows
        ]
    except Exception as e:
        log.error(f"读取今日信号失败: {e}")
        return []


def clear_signals():
    """清空今日信号。"""
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM signals WHERE signal_time::date = %s",
            (dt.date.today(),),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log.error(f"清空信号失败: {e}")
