"""
触发选股扫描并返回结构化结果。
"""
from __future__ import annotations

from fastapi import APIRouter

from api.schemas import ScanParams, ScanResult, ScanStockDetail

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
