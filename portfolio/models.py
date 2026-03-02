"""
持仓与账户数据模型。
替代聚宽 context.portfolio.positions[stock] 的属性访问。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class Position:
    code: str
    total_amount: int = 0
    closeable_amount: int = 0
    avg_cost: float = 0.0
    price: float = 0.0        # 最新价
    value: float = 0.0        # 持仓市值
    init_time: dt.datetime | None = None

    @property
    def profit_pct(self) -> float:
        if self.avg_cost <= 0:
            return 0.0
        return (self.price - self.avg_cost) / self.avg_cost


@dataclass
class TradeRecord:
    code: str
    action: str               # "BUY" / "SELL"
    price: float
    quantity: int
    amount: float
    reason: str = ""
    time: dt.datetime = field(default_factory=dt.datetime.now)
