"""
FastAPI 应用工厂 + 共享状态依赖注入。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, Request

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler

    from portfolio.tracker import PortfolioTracker
    from strategy.core import Context


def create_app(
    ctx: "Context",
    scheduler: "BackgroundScheduler | None" = None,
) -> FastAPI:
    app = FastAPI(
        title="Hot Stock API",
        version="1.0.0",
        description="A 股涨停板策略本地信号系统 API",
    )

    app.state.ctx = ctx
    app.state.scheduler = scheduler

    from api.auth import router as auth_router, verify_token
    from api.routers.market import router as market_router
    from api.routers.portfolio import router as portfolio_router
    from api.routers.scan import router as scan_router
    from api.routers.trades import router as trades_router

    # 认证路由（公开，不加鉴权）
    app.include_router(auth_router, prefix="/api")

    # 业务路由（全部要求 JWT 鉴权）
    protected = [Depends(verify_token)]
    app.include_router(portfolio_router, prefix="/api", dependencies=protected)
    app.include_router(trades_router, prefix="/api", dependencies=protected)
    app.include_router(market_router, prefix="/api", dependencies=protected)
    app.include_router(scan_router, prefix="/api", dependencies=protected)

    return app


# ------------------------------------------------------------------
# 依赖注入辅助
# ------------------------------------------------------------------

def get_ctx(request: Request) -> "Context":
    return request.app.state.ctx


def get_portfolio(request: Request) -> "PortfolioTracker":
    return request.app.state.ctx.portfolio


def get_scheduler(request: Request) -> "BackgroundScheduler | None":
    return request.app.state.scheduler
