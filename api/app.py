"""
FastAPI 应用工厂 + 共享状态依赖注入。

支持两种启动方式：
1. 嵌入模式：main.py 传入 ctx, scheduler → create_app(ctx, scheduler)
2. 独立模式：uvicorn --reload --factory → create_app() 自动构建 Context
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, Request

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler

    from portfolio.tracker import PortfolioTracker
    from strategy.core import Context


def create_app(
    ctx: "Context | None" = None,
    scheduler: "BackgroundScheduler | None" = None,
) -> FastAPI:
    app = FastAPI(
        title="Hot Stock API",
        version="1.0.0",
        description="A 股涨停板策略本地信号系统 API",
    )

    # 独立模式：自动构建 Context
    if ctx is None:
        import config
        from data import create_provider
        from portfolio.tracker import PortfolioTracker as PT
        from strategy.core import Context as Ctx
        from utils.logger import log, setup_logging

        setup_logging(config.LOG_DIR)
        dp = create_provider()
        portfolio = PT()
        ctx = Ctx(dp=dp, portfolio=portfolio)
        ctx.update_time()
        log.info("API 独立模式：已自动创建 Context")

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
