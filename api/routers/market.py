"""
市场统计 + 策略优先级 + 调度器状态。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from api.app import get_scheduler
from api.schemas import (
    MarketStats,
    SchedulerJob,
    SchedulerStatus,
    StrategyPriority,
)
from strategy.core import state

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler

router = APIRouter(prefix="/market", tags=["市场"])


@router.get("/stats", response_model=MarketStats, summary="市场趋势统计")
def market_stats():
    ms = state.trade_stats.get("market_stats", {})
    return MarketStats(
        date=ms.get("date", ""),
        trend=ms.get("trend", ""),
        change_rate=ms.get("change_rate", 0.0),
        volatility=ms.get("volatility", 0.0),
        volume_ratio=ms.get("volume_ratio", 0.0),
    )


@router.get("/strategy", response_model=StrategyPriority, summary="策略优先级")
def strategy_priority():
    sp = state.trade_stats.get("strategy_priority", {})
    return StrategyPriority(
        trend=sp.get("trend", ""),
        priority=sp.get("priority", state.priority_config),
    )


@router.get("/scheduler", response_model=SchedulerStatus, summary="调度器状态")
def scheduler_status(
    sched: "BackgroundScheduler | None" = Depends(get_scheduler),
):
    if sched is None:
        return SchedulerStatus(running=False, job_count=0, jobs=[])

    jobs = sched.get_jobs()
    return SchedulerStatus(
        running=sched.running,
        job_count=len(jobs),
        jobs=[
            SchedulerJob(
                id=j.id,
                name=j.name,
                next_run_time=j.next_run_time.isoformat() if j.next_run_time else None,
            )
            for j in jobs
        ],
    )
