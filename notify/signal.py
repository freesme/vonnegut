"""
信号生成与格式化。
所有买卖操作最终汇聚于此，生成结构化信号并推送通知。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from utils.logger import log


@dataclass
class Signal:
    type: str           # "BUY" / "SELL"
    stock: str
    price: float
    reason: str
    time: dt.datetime
    extra: dict | None = None


_signal_log: list[Signal] = []


def emit_signal(
    signal_type: str,
    stock: str,
    price: float,
    reason: str,
    ctx=None,
):
    """生成并推送一条交易信号。"""
    now = ctx.current_dt if ctx else dt.datetime.now()
    sig = Signal(type=signal_type, stock=stock, price=price, reason=reason, time=now)
    _signal_log.append(sig)

    icon = "🟢" if signal_type == "BUY" else "🔴"
    msg = f"{icon} [{signal_type}] {stock} @ {price:.2f} | {reason} | {now.strftime('%H:%M:%S')}"
    log.info(msg)

    from notify.push import send
    send(msg)

    return sig


def emit_message(text: str, ctx=None):
    """发送纯文本通知（非交易信号）。"""
    log.info(f"[消息] {text}")
    from notify.push import send
    send(text)


def get_today_signals() -> list[Signal]:
    today = dt.date.today()
    return [s for s in _signal_log if s.time.date() == today]


def clear_signals():
    _signal_log.clear()
