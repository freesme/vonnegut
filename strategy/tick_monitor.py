"""
Tick 级别实时卖出监控。
独立线程，盘中持续监控持仓股 tick 数据，触发即时卖出信号。

功能：
  1. 跌停保护（不发卖出信号）
  2. 急跌止损（1分钟内跌幅 > 阈值）
  3. 放量大跌（实时量比 + 跌幅）
  4. 涨停打开（封板后资金撤单）
"""
from __future__ import annotations

import collections
import datetime as dt
import threading
import time
from typing import TYPE_CHECKING

import config
from strategy.core import Context, state, is_at_limit_down
from utils.logger import log

if TYPE_CHECKING:
    from data.provider import DataProvider


class TickMonitor:
    """盘中 tick 级别卖出监控器。"""

    def __init__(self, ctx: Context):
        self._ctx = ctx
        self._running = False
        self._thread: threading.Thread | None = None
        # 每只股票最近 N 个 tick 的价格历史，用于急跌检测
        self._price_history: dict[str, collections.deque] = {}
        # 涨停封板状态跟踪
        self._was_at_limit_up: dict[str, bool] = {}

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        log.info("Tick 监控线程已启动")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Tick 监控线程已停止")

    def _run_loop(self):
        while self._running:
            try:
                now = dt.datetime.now()
                t = now.time()
                # 仅在交易时间运行
                if not self._in_trading_hours(t):
                    time.sleep(10)
                    continue

                portfolio = self._ctx.portfolio
                if not portfolio or not portfolio.positions:
                    time.sleep(config.TICK_POLL_INTERVAL)
                    continue

                dp = self._ctx.dp
                stocks = list(portfolio.positions.keys())
                quotes = dp.get_realtime_quotes(stocks)
                self._ctx.update_time()

                for stock in stocks:
                    q = quotes.get(stock)
                    if q is None or q.paused:
                        continue

                    pos = portfolio.positions.get(stock)
                    if pos is None or pos.closeable_amount <= 0:
                        continue

                    self._process_tick(stock, q, pos)

            except Exception as e:
                log.error(f"Tick 监控异常: {e}")

            time.sleep(config.TICK_POLL_INTERVAL)

    def _process_tick(self, stock: str, q, pos):
        """处理单个 tick，检查各种卖出条件。"""
        # 跌停保护
        if q.last_price <= q.low_limit:
            return

        # 更新价格历史
        if stock not in self._price_history:
            self._price_history[stock] = collections.deque(maxlen=20)
        self._price_history[stock].append({
            "price": q.last_price,
            "time": dt.datetime.now(),
            "volume": q.volume,
        })

        # ---- 检查1: 急跌止损 ----
        if self._check_rapid_drop(stock, q):
            self._emit_sell_signal(stock, q.last_price, "急跌止损(tick级)")
            return

        # ---- 检查2: 放量大跌 ----
        if self._check_volume_drop(stock, q, pos):
            self._emit_sell_signal(stock, q.last_price, "放量大跌(tick级)")
            return

        # ---- 检查3: 涨停打开 ----
        if self._check_limit_up_broken(stock, q):
            self._emit_sell_signal(stock, q.last_price, "涨停打开(tick级)")
            return

    def _check_rapid_drop(self, stock: str, q) -> bool:
        """1分钟内跌幅超过阈值。"""
        history = self._price_history.get(stock)
        if not history or len(history) < 2:
            return False

        now = dt.datetime.now()
        one_min_ago = now - dt.timedelta(minutes=1)

        # 找到1分钟前的价格
        older_price = None
        for tick in history:
            if tick["time"] <= one_min_ago:
                older_price = tick["price"]
                break

        if older_price is None or older_price <= 0:
            return False

        drop = (older_price - q.last_price) / older_price
        return drop >= config.TICK_RAPID_DROP_PCT

    def _check_volume_drop(self, stock: str, q, pos) -> bool:
        """放量大跌：跌幅 + 量比。"""
        if q.pre_close <= 0:
            return False
        drop = (q.pre_close - q.last_price) / q.pre_close
        if drop < config.TICK_VOLUME_DROP_PCT:
            return False

        # 简单量比估算：当前量 vs 平均
        dp = self._ctx.dp
        try:
            vol_data = dp.get_price(stock, self._ctx.previous_date, 5, ["volume"])
            if vol_data.empty:
                return False
            avg_vol = vol_data["volume"].mean()
            if avg_vol <= 0:
                return False

            now_t = dt.datetime.now().time()
            if now_t <= dt.time(11, 30):
                elapsed = (now_t.hour - 9) * 60 + now_t.minute - 30
            elif now_t < dt.time(13, 0):
                elapsed = 120
            else:
                elapsed = 120 + (now_t.hour - 13) * 60 + now_t.minute
            elapsed = max(elapsed, 1)

            estimated = q.volume * (240.0 / elapsed)
            return estimated / avg_vol >= config.TICK_VOLUME_RATIO
        except Exception:
            return False

    def _check_limit_up_broken(self, stock: str, q) -> bool:
        """涨停打开检测：之前在涨停，现在不在了。"""
        at_limit = q.last_price >= q.high_limit
        was_at = self._was_at_limit_up.get(stock, False)

        if at_limit:
            self._was_at_limit_up[stock] = True
            return False

        if was_at and not at_limit:
            self._was_at_limit_up[stock] = False
            drop_from_limit = (q.high_limit - q.last_price) / q.high_limit
            # 从涨停回落超过 2% 才触发
            if drop_from_limit >= 0.02:
                return True

        return False

    def _emit_sell_signal(self, stock: str, price: float, reason: str):
        from notify.signal import emit_signal
        log.warning(f"[Tick卖出] {stock} @ {price:.2f} | {reason}")
        emit_signal("SELL", stock, price, reason, self._ctx)

    @staticmethod
    def _in_trading_hours(t: dt.time) -> bool:
        morning = dt.time(9, 30) <= t <= dt.time(11, 30)
        afternoon = dt.time(13, 0) <= t <= dt.time(15, 0)
        return morning or afternoon
