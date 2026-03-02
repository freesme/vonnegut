"""
本地持仓跟踪器。
手动确认买卖后更新持仓状态，持久化到 JSON。
替代聚宽 context.portfolio。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import config
from portfolio.models import Position, TradeRecord
from utils.logger import log


class PortfolioTracker:
    """本地持仓/资金管理，数据持久化到 JSON 文件。"""

    def __init__(self, initial_cash: float | None = None, path: Path | str | None = None):
        self._path = Path(path) if path else config.PORTFOLIO_PATH
        self.starting_cash: float = initial_cash or config.INITIAL_CASH
        self.available_cash: float = self.starting_cash
        self.positions: dict[str, Position] = {}
        self.trades: list[TradeRecord] = []
        self._load()

    # ------------------------------------------------------------------
    # 属性（兼容原 context.portfolio 用法）
    # ------------------------------------------------------------------
    @property
    def total_value(self) -> float:
        pos_value = sum(p.value for p in self.positions.values())
        return self.available_cash + pos_value

    @property
    def positions_value(self) -> float:
        return sum(p.value for p in self.positions.values())

    # ------------------------------------------------------------------
    # 手动确认买入
    # ------------------------------------------------------------------
    def confirm_buy(self, code: str, price: float, quantity: int, reason: str = ""):
        amount = price * quantity
        if amount > self.available_cash:
            log.warning(f"资金不足: 需{amount:.2f}, 可用{self.available_cash:.2f}")
            return False

        self.available_cash -= amount
        if code in self.positions:
            pos = self.positions[code]
            total_cost = pos.avg_cost * pos.total_amount + amount
            pos.total_amount += quantity
            pos.avg_cost = total_cost / pos.total_amount
            pos.price = price
            pos.value = pos.total_amount * price
        else:
            self.positions[code] = Position(
                code=code,
                total_amount=quantity,
                closeable_amount=0,  # T+1，当天不可卖
                avg_cost=price,
                price=price,
                value=amount,
                init_time=dt.datetime.now(),
            )

        self.trades.append(TradeRecord(
            code=code, action="BUY", price=price,
            quantity=quantity, amount=amount, reason=reason,
        ))
        log.info(f"[确认买入] {code} {quantity}股 @ {price:.2f}, 剩余资金: {self.available_cash:.2f}")
        self._save()
        return True

    # ------------------------------------------------------------------
    # 手动确认卖出
    # ------------------------------------------------------------------
    def confirm_sell(self, code: str, price: float, quantity: int | None = None, reason: str = ""):
        if code not in self.positions:
            log.warning(f"无持仓: {code}")
            return False

        pos = self.positions[code]
        max_closeable = min(pos.closeable_amount, pos.total_amount)
        if max_closeable <= 0:
            log.warning(f"可卖数量不足(T+1限制): {code}, 可卖{pos.closeable_amount}股")
            return False

        qty = max_closeable if quantity is None else min(quantity, max_closeable)
        if qty <= 0:
            log.warning(f"卖出数量无效: {code}, 请求{quantity}, 可卖{max_closeable}")
            return False
        amount = price * qty

        self.available_cash += amount
        pos.total_amount -= qty
        pos.closeable_amount = max(0, pos.closeable_amount - qty)
        pos.price = price
        pos.value = pos.total_amount * price

        self.trades.append(TradeRecord(
            code=code, action="SELL", price=price,
            quantity=qty, amount=amount, reason=reason,
        ))

        profit = (price - pos.avg_cost) / pos.avg_cost * 100 if pos.avg_cost > 0 else 0
        log.info(f"[确认卖出] {code} {qty}股 @ {price:.2f}, 盈亏: {profit:.2f}%")

        if pos.total_amount <= 0:
            del self.positions[code]

        self._save()
        return True

    # ------------------------------------------------------------------
    # 每日开盘更新可卖数量（T+1 解锁）
    # ------------------------------------------------------------------
    def daily_update(self):
        """每日开盘调用：T+1 解锁 closeable_amount。"""
        today = dt.date.today()
        for pos in self.positions.values():
            if pos.init_time and pos.init_time.date() < today:
                pos.closeable_amount = pos.total_amount
        self._save()

    # ------------------------------------------------------------------
    # 更新持仓市价
    # ------------------------------------------------------------------
    def update_prices(self, quotes: dict):
        """用实时行情更新持仓价格和市值。"""
        for code, pos in self.positions.items():
            q = quotes.get(code)
            if q:
                pos.price = q.last_price
                pos.value = q.last_price * pos.total_amount

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _save(self):
        data = {
            "starting_cash": self.starting_cash,
            "available_cash": self.available_cash,
            "positions": {
                code: {
                    "total_amount": p.total_amount,
                    "closeable_amount": p.closeable_amount,
                    "avg_cost": p.avg_cost,
                    "price": p.price,
                    "value": p.value,
                    "init_time": p.init_time.isoformat() if p.init_time else None,
                }
                for code, p in self.positions.items()
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self):
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.starting_cash = data.get("starting_cash", self.starting_cash)
            self.available_cash = data.get("available_cash", self.available_cash)
            for code, pdata in data.get("positions", {}).items():
                self.positions[code] = Position(
                    code=code,
                    total_amount=pdata.get("total_amount", 0),
                    closeable_amount=pdata.get("closeable_amount", 0),
                    avg_cost=pdata.get("avg_cost", 0),
                    price=pdata.get("price", 0),
                    value=pdata.get("value", 0),
                    init_time=dt.datetime.fromisoformat(pdata["init_time"]) if pdata.get("init_time") else None,
                )
        except Exception as e:
            log.error(f"加载持仓数据失败: {e}")
