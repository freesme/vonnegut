"""
持仓查询 + 模拟买入/卖出。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.app import get_portfolio
from api.schemas import (
    BuyRequest,
    PortfolioOverview,
    PositionItem,
    SellRequest,
    TradeResult,
)
from portfolio.tracker import PortfolioTracker

router = APIRouter(prefix="/portfolio", tags=["持仓"])


def _to_position_item(code: str, pos) -> PositionItem:
    return PositionItem(
        code=code,
        total_amount=pos.total_amount,
        closeable_amount=pos.closeable_amount,
        avg_cost=pos.avg_cost,
        price=pos.price,
        value=pos.value,
        profit_pct=pos.profit_pct,
        init_time=pos.init_time,
    )


@router.get("", response_model=PortfolioOverview, summary="账户总览")
def get_overview(pt: PortfolioTracker = Depends(get_portfolio)):
    return PortfolioOverview(
        starting_cash=pt.starting_cash,
        available_cash=pt.available_cash,
        total_value=pt.total_value,
        positions_value=pt.positions_value,
        positions=[_to_position_item(c, p) for c, p in pt.positions.items()],
    )


@router.get("/positions", response_model=list[PositionItem], summary="持仓明细")
def list_positions(pt: PortfolioTracker = Depends(get_portfolio)):
    return [_to_position_item(c, p) for c, p in pt.positions.items()]


@router.get(
    "/positions/{code}",
    response_model=PositionItem,
    summary="单只持仓详情",
)
def get_position(code: str, pt: PortfolioTracker = Depends(get_portfolio)):
    pos = pt.positions.get(code)
    if pos is None:
        raise HTTPException(404, detail=f"无持仓: {code}")
    return _to_position_item(code, pos)


@router.post("/buy", response_model=TradeResult, summary="模拟买入")
def simulate_buy(req: BuyRequest, pt: PortfolioTracker = Depends(get_portfolio)):
    success = pt.confirm_buy(
        code=req.code,
        price=req.price,
        quantity=req.quantity,
        reason=req.reason or "API 模拟买入",
    )
    if not success:
        return TradeResult(ok=False, message="买入失败: 资金不足或参数无效")

    pos = pt.positions.get(req.code)
    return TradeResult(
        ok=True,
        message=f"买入成功: {req.code} {req.quantity}股 @ {req.price:.2f}",
        available_cash=pt.available_cash,
        position=_to_position_item(req.code, pos) if pos else None,
    )


@router.post("/sell", response_model=TradeResult, summary="模拟卖出")
def simulate_sell(req: SellRequest, pt: PortfolioTracker = Depends(get_portfolio)):
    success = pt.confirm_sell(
        code=req.code,
        price=req.price,
        quantity=req.quantity,
        reason=req.reason or "API 模拟卖出",
    )
    if not success:
        return TradeResult(ok=False, message=f"卖出失败: 无持仓或可卖数量不足 ({req.code})")

    pos = pt.positions.get(req.code)
    return TradeResult(
        ok=True,
        message=f"卖出成功: {req.code} @ {req.price:.2f}",
        available_cash=pt.available_cash,
        position=_to_position_item(req.code, pos) if pos else None,
    )
