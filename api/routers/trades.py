"""
今日信号 + 历史交易记录。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.app import get_portfolio
from api.schemas import SignalItem, TradeRecordItem
from portfolio.tracker import PortfolioTracker

router = APIRouter(prefix="/trades", tags=["交易"])


@router.get("/signals", response_model=list[SignalItem], summary="今日交易信号")
def today_signals(status: str | None = None):
    """查询今日信号。可选 status 过滤: CANDIDATE / EXECUTED / SKIPPED"""
    from notify.signal import get_today_signals

    return [
        SignalItem(
            type=s.type,
            stock=s.stock,
            price=s.price,
            reason=s.reason,
            status=s.status,
            time=s.time,
        )
        for s in get_today_signals(status=status)
    ]


@router.get("/history", response_model=list[TradeRecordItem], summary="历史交易记录")
def trade_history(pt: PortfolioTracker = Depends(get_portfolio)):
    return [
        TradeRecordItem(
            code=t.code,
            action=t.action,
            price=t.price,
            quantity=t.quantity,
            amount=t.amount,
            reason=t.reason,
            time=t.time,
        )
        for t in pt.trades
    ]
