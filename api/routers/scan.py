"""
触发选股扫描并返回结构化结果。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.app import get_ctx
from api.schemas import CatchupResult, CatchupStep, ScanParams, ScanResult, ScanStockDetail
from strategy.core import Context

router = APIRouter(prefix="/scan", tags=["选股"])


@router.post("", response_model=ScanResult, summary="触发一次选股扫描")
def run_scan(params: ScanParams | None = None):
    from scan import scan

    p = params or ScanParams()
    raw = scan(
        notify=False,
        min_score=p.min_score,
        score_only=p.score_only,
    )

    details = [ScanStockDetail(**d) for d in raw.get("details", [])]

    return ScanResult(
        qualified=raw.get("qualified", []),
        details=details,
        market=raw.get("market", {}),
    )


@router.post(
    "/catchup",
    response_model=CatchupResult,
    summary="一键补跑完整早盘流程",
    description="补跑盘前统计→选股→竞价卖出检测→买入信号，适用于错过 09:25~09:28 定时窗口的情况。",
)
def run_catchup(ctx: Context = Depends(get_ctx)):
    from scan import morning_catchup

    raw = morning_catchup(ctx=ctx, notify=True)

    details = [ScanStockDetail(**d) for d in raw.get("details", [])]
    steps = [CatchupStep(**s) for s in raw.get("steps", [])]

    return CatchupResult(
        steps=steps,
        market=raw.get("market", {}),
        candidates=raw.get("candidates", []),
        qualified=raw.get("qualified", []),
        details=details,
        buy_signals=raw.get("buy_signals", []),
    )

