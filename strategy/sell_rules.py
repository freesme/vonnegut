"""
三层卖出规则模块。
Layer1: sell_limit_down (竞价卖出)
Layer2: sell_limit_per5min (每5分钟技术止损)
Layer3: sell2 (定时策略卖出，互斥)
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from strategy.core import (
    Context, state, transform_date, get_trading_time_status,
    is_at_limit_down, record_sell_trade,
)
from utils.logger import log


# ======================================================================
# 辅助函数
# ======================================================================

def check_volume_drop_signal(stock: str, ctx: Context, quotes: dict) -> bool:
    """检测放量大跌信号：跌幅≥6% 且 量比≥1.5。"""
    dp = ctx.dp
    try:
        q = quotes.get(stock)
        if q is None or q.pre_close <= 0:
            return False
        drop_pct = (q.pre_close - q.last_price) / q.pre_close
        if drop_pct < 0.06:
            return False

        vol_data = dp.get_price(stock, ctx.previous_date, 6, ["volume"])
        if vol_data.empty or len(vol_data) < 5:
            return False
        avg_vol_5 = vol_data["volume"].iloc[-5:].mean()
        if avg_vol_5 <= 0:
            return False

        # 估算当日成交量
        now = ctx.current_dt.time()
        if now <= dt.time(11, 30):
            elapsed = (now.hour - 9) * 60 + now.minute - 30
        elif now < dt.time(13, 0):
            elapsed = 120
        else:
            elapsed = 120 + (now.hour - 13) * 60 + now.minute
        elapsed = max(elapsed, 1)

        estimated_daily_vol = q.volume * (240.0 / elapsed)
        volume_ratio = estimated_daily_vol / avg_vol_5
        return volume_ratio >= 1.5
    except Exception:
        return False


def calculate_ths_indicators(stock: str, ctx: Context, period: int = 30, unit: str = "1d") -> dict:
    """简化版同花顺波段指标。返回 {'sell_signals': [...]}。"""
    dp = ctx.dp
    signals = {"sell_signals": []}
    try:
        if unit in ("5m", "15m", "30m"):
            hist = dp.get_minute_price(stock, ctx.current_dt, period, unit, ["close", "volume"])
        else:
            hist = dp.get_price(stock, ctx.previous_date, period, ["close", "volume"])
        if hist.empty or len(hist) < 10:
            return signals
        closes = hist["close"].values
        # 波段卖：短周期均线下穿长周期 + RSI 超买回落
        ma3 = np.mean(closes[-3:])
        ma7 = np.mean(closes[-7:])
        from strategy.scoring import calculate_rsi
        rsi = calculate_rsi(closes)
        if ma3 < ma7 and rsi > 65:
            signals["sell_signals"].append("波段卖")
    except Exception:
        pass
    return signals


# ======================================================================
# Layer1: 竞价卖出 (09:28)
# ======================================================================

def sell_limit_down(ctx: Context):
    """集合竞价阶段检测并发出卖出信号。"""
    dp = ctx.dp
    portfolio = ctx.portfolio
    if portfolio is None or not portfolio.positions:
        return

    quotes = dp.get_realtime_quotes(list(portfolio.positions.keys()))
    date = ctx.previous_date

    for stock, pos in list(portfolio.positions.items()):
        if pos.closeable_amount <= 0:
            continue
        q = quotes.get(stock)
        if q is None or q.paused:
            continue

        try:
            price_data = dp.get_price(stock, date, 6, ["open", "close", "high", "low", "volume", "high_limit"])
            if price_data.empty or len(price_data) < 6:
                continue
            prev = price_data.iloc[-1]
            avg_vol_5 = price_data["volume"].iloc[:-1].mean()
            prev_close = prev.get("close", 0)
            prev_hl = prev.get("high_limit", 0)
            today_open = q.day_open
            if pd.isna(today_open) or today_open == 0:
                continue

            val_df = dp.get_valuation(stock, date, ["pe_ratio", "circulating_market_cap"])
            if val_df.empty:
                continue
            pe_ratio = val_df["pe_ratio"].iloc[0] if "pe_ratio" in val_df.columns else 0
            market_cap = val_df["circulating_market_cap"].iloc[0] if "circulating_market_cap" in val_df.columns else 0

            # 策略1: 涨停低开快速卖出
            if prev_close == prev_hl and prev_close > 0:
                open_drop = (today_open - prev_close) / prev_close
                should_sell = False
                if -0.03 <= open_drop <= -0.01 and 50 <= market_cap <= 100:
                    should_sell = True
                elif pe_ratio and pe_ratio < 20 and -0.05 <= open_drop <= -0.01:
                    should_sell = True

                if should_sell:
                    details = {"开盘跌幅": f"{open_drop:.2%}", "市值": f"{market_cap:.0f}亿"}
                    record_sell_trade(ctx, stock, "涨停低开快速卖出", details, quotes, transform_date(date, "str"))
                    _emit_sell(ctx, stock, "涨停低开快速卖出")
                    continue

            # 策略2: 放量长上影开盘卖出
            if len(price_data) >= 2:
                yest = price_data.iloc[-1]
                y_open = yest.get("open", 0)
                y_close = yest.get("close", 0)
                y_high = yest.get("high", 0)
                y_low = yest.get("low", 0)
                y_vol = yest.get("volume", 0)

                if y_high > 0 and y_low > 0 and y_open > 0 and y_close > 0:
                    upper_shadow = y_high - max(y_open, y_close)
                    lower_shadow = min(y_open, y_close) - y_low
                    body = abs(y_close - y_open)
                    total_range = y_high - y_low

                    is_long_upper = (
                        upper_shadow > lower_shadow * 1.2
                        and upper_shadow > body * 1.5
                        and total_range > 0 and upper_shadow > total_range * 0.3
                    )
                    is_heavy_vol = avg_vol_5 > 0 and y_vol > avg_vol_5 * 1.5
                    open_gain = (today_open - prev_close) / prev_close if prev_close > 0 else 0

                    if is_long_upper and is_heavy_vol and open_gain < 0.02 and pos.closeable_amount > 0:
                        details = {"上影线": f"{upper_shadow:.2f}", "量比": f"{y_vol / avg_vol_5:.2f}"}
                        record_sell_trade(ctx, stock, "放量长上影开盘卖出", details, quotes, transform_date(date, "str"))
                        _emit_sell(ctx, stock, "放量长上影开盘卖出")

        except Exception as e:
            log.error(f"sell_limit_down {stock} 失败: {e}")


# ======================================================================
# Layer2: 每5分钟技术止损
# ======================================================================

def sell_limit_per5min(ctx: Context):
    """每5分钟检测持仓，基于技术指标卖出。"""
    portfolio = ctx.portfolio
    if portfolio is None or not portfolio.positions:
        return

    dp = ctx.dp
    quotes = dp.get_realtime_quotes(list(portfolio.positions.keys()))
    date_str = transform_date(ctx.previous_date, "str")

    for stock, pos in list(portfolio.positions.items()):
        if pos.closeable_amount <= 0:
            continue
        q = quotes.get(stock)
        if q is None or q.paused:
            continue

        try:
            ths = calculate_ths_indicators(stock, ctx, 30, "5m")
            has_sell = "波段卖" in ths["sell_signals"]
            has_vol_drop = check_volume_drop_signal(stock, ctx, quotes)

            if has_sell and has_vol_drop:
                details = {"信号": "波段卖+放量大跌"}
                record_sell_trade(ctx, stock, "紧急止损", details, quotes, date_str)
                _emit_sell(ctx, stock, "紧急止损-波段卖+放量大跌")
            elif has_sell and q.last_price > 0 and pos.avg_cost > 0:
                loss = (pos.avg_cost - q.last_price) / pos.avg_cost
                if loss >= 0.03:
                    details = {"信号": "波段卖", "亏损": f"{loss:.2%}"}
                    record_sell_trade(ctx, stock, "波段卖出", details, quotes, date_str)
                    _emit_sell(ctx, stock, "波段卖出")
            elif has_vol_drop:
                drop = (q.pre_close - q.last_price) / q.pre_close if q.pre_close > 0 else 0
                if drop >= 0.08:
                    details = {"信号": "放量大跌", "跌幅": f"{drop:.2%}"}
                    record_sell_trade(ctx, stock, "放量大跌止损", details, quotes, date_str)
                    _emit_sell(ctx, stock, "放量大跌止损")
        except Exception as e:
            log.error(f"sell_per5min {stock} 失败: {e}")


# ======================================================================
# Layer3: 定时策略卖出 (互斥)
# ======================================================================

def sell2(ctx: Context):
    """多策略卖出（互斥：一只股票只触发一个策略）。"""
    portfolio = ctx.portfolio
    if portfolio is None or not portfolio.positions:
        return

    dp = ctx.dp
    quotes = dp.get_realtime_quotes(list(portfolio.positions.keys()))
    date_str = transform_date(ctx.previous_date, "str")
    today = ctx.current_dt.date()
    is_morning, is_afternoon, _ = get_trading_time_status(ctx)

    for stock, pos in list(portfolio.positions.items()):
        try:
            if pos.closeable_amount <= 0:
                continue
            q = quotes.get(stock)
            if q is None or q.paused:
                continue
            if is_at_limit_down(stock, quotes):
                continue
            # T+1 保护
            if pos.init_time and pos.init_time.date() == today:
                continue

            current_price = q.last_price
            avg_cost = pos.avg_cost
            high_limit = q.high_limit
            sold = False

            # 上午策略
            if is_morning and not sold:
                # 1. 月初不涨停时间止损
                try:
                    hist = dp.get_price(stock, ctx.previous_date, 10, ["open"])
                    if not hist.empty and len(hist) == 10:
                        start_p = hist["open"].iloc[0]
                        end_p = hist["open"].iloc[-1]
                        if end_p / start_p > 1.8 and today.day == 1 and high_limit > current_price:
                            details = {"10日涨幅": f"{end_p / start_p - 1:.2%}"}
                            record_sell_trade(ctx, stock, "月初不涨停时间止损", details, quotes, date_str)
                            _emit_sell(ctx, stock, "月初不涨停时间止损")
                            sold = True
                except Exception:
                    pass

                # 2. 低于昨收
                if not sold:
                    try:
                        prev_df = dp.get_price(stock, ctx.previous_date, 1, ["close"])
                        if not prev_df.empty:
                            yest_close = prev_df["close"].iloc[-1]
                            if not pd.isna(yest_close) and current_price < yest_close:
                                details = {"昨收": f"{yest_close:.2f}", "现价": f"{current_price:.2f}"}
                                record_sell_trade(ctx, stock, "低于昨日收盘价", details, quotes, date_str)
                                _emit_sell(ctx, stock, "低于昨日收盘价")
                                sold = True
                    except Exception:
                        pass

            # 下午策略
            if is_afternoon and not sold:
                if avg_cost == 0:
                    continue
                loss_pct = (avg_cost - current_price) / avg_cost
                hl_retreat = (high_limit - current_price) / avg_cost if avg_cost > 0 else 0

                if loss_pct >= 0.05 or hl_retreat >= 0.15:
                    details = {"亏损": f"{loss_pct:.2%}", "涨停回撤": f"{hl_retreat:.2%}"}
                    record_sell_trade(ctx, stock, "止损卖出", details, quotes, date_str)
                    _emit_sell(ctx, stock, "止损卖出")
                    sold = True

                if not sold:
                    try:
                        close_data = dp.get_price(stock, ctx.previous_date, 4, ["close"])
                        if not close_data.empty:
                            ma5 = (close_data["close"].sum() + current_price) / 5
                            if current_price < ma5:
                                details = {"MA5": f"{ma5:.2f}", "现价": f"{current_price:.2f}"}
                                record_sell_trade(ctx, stock, "跌破MA5均线", details, quotes, date_str)
                                _emit_sell(ctx, stock, "跌破MA5均线")
                                sold = True
                    except Exception:
                        pass

            # 全时段: 量价顶背离
            if not sold:
                try:
                    kline = dp.get_minute_price(stock, ctx.current_dt, 24, "30m", ["high", "volume"])
                    if not kline.empty and len(kline) >= 24:
                        highs = kline["high"].values
                        volumes = kline["volume"].values
                        max_h, max_v = max(highs), max(volumes)
                        if max_v > 0:
                            last_h, last_v = highs[-1], volumes[-1]
                            drop_pct = (last_h - current_price) / last_h * 100
                            if (last_h >= max_h - 1e-6
                                    and last_v <= max_v * 0.5
                                    and current_price < high_limit
                                    and drop_pct > 3.0):
                                details = {"回撤": f"{drop_pct:.2f}%", "量比": f"{last_v / max_v:.2%}"}
                                record_sell_trade(ctx, stock, "量价顶背离", details, quotes, date_str)
                                _emit_sell(ctx, stock, "量价顶背离")
                except Exception:
                    pass

        except Exception as e:
            log.error(f"sell2 {stock} 失败: {e}")


# ======================================================================
# 信号发射（统一出口）
# ======================================================================

def _emit_sell(ctx: Context, stock: str, reason: str):
    """发出卖出信号。"""
    from notify.signal import emit_signal
    q_dict = ctx.dp.get_realtime_quotes([stock])
    q = q_dict.get(stock)
    price = q.last_price if q else 0
    emit_signal("SELL", stock, price, reason, ctx)
