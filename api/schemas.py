"""
API 请求/响应 Pydantic 模型。
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


# ======================================================================
# 通用
# ======================================================================

class ApiResult(BaseModel):
    """统一响应包装。"""
    ok: bool = True
    message: str = ""


# ======================================================================
# 认证
# ======================================================================

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, examples=["admin"])
    password: str = Field(..., min_length=1, examples=["admin123"])


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, examples=["trader01"])
    password: str = Field(..., min_length=6, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ======================================================================
# 持仓
# ======================================================================

class PositionItem(BaseModel):
    code: str
    total_amount: int
    closeable_amount: int
    avg_cost: float
    price: float
    value: float
    profit_pct: float
    init_time: dt.datetime | None = None


class PortfolioOverview(BaseModel):
    starting_cash: float
    available_cash: float
    total_value: float
    positions_value: float
    positions: list[PositionItem]


# ======================================================================
# 模拟买入 / 卖出
# ======================================================================

class BuyRequest(BaseModel):
    code: str = Field(..., examples=["600519.SH"])
    price: float = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    reason: str = ""


class SellRequest(BaseModel):
    code: str = Field(..., examples=["600519.SH"])
    price: float = Field(..., gt=0)
    quantity: int | None = Field(None, ge=1, description="为 null 时卖出全部可卖数量")
    reason: str = ""


class TradeResult(ApiResult):
    available_cash: float | None = None
    position: PositionItem | None = None


# ======================================================================
# 交易记录 / 信号
# ======================================================================

class SignalItem(BaseModel):
    type: str
    stock: str
    price: float
    reason: str
    time: dt.datetime


class TradeRecordItem(BaseModel):
    code: str
    action: str
    price: float
    quantity: int
    amount: float
    reason: str
    time: dt.datetime


# ======================================================================
# 选股
# ======================================================================

class ScanParams(BaseModel):
    min_score: int | None = None
    score_only: bool = False


class ScanStockDetail(BaseModel):
    stock: str
    name: str
    pattern: str
    total_score: float
    f1_limit_up: float
    f2_technical: float
    f3_volume_ma: float
    f4_mainline: float
    f5_sentiment: float
    f6_main_force: float


class ScanResult(BaseModel):
    qualified: list[str]
    details: list[ScanStockDetail]
    market: dict


# ======================================================================
# 市场 / 调度
# ======================================================================

class MarketStats(BaseModel):
    date: str = ""
    trend: str = ""
    change_rate: float = 0.0
    volatility: float = 0.0
    volume_ratio: float = 0.0


class StrategyPriority(BaseModel):
    trend: str = ""
    priority: list[str] = []


class SchedulerJob(BaseModel):
    id: str
    name: str
    next_run_time: str | None = None


class SchedulerStatus(BaseModel):
    running: bool
    job_count: int
    jobs: list[SchedulerJob]
