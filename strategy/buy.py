"""
买入模块：选股筛选、评分排序、信号发射。
替代原 buy() 函数及 optimize_friday_trading_logic。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import config
from strategy.core import (
    Context, state, get_buy_reason, get_trading_time_status,
    is_at_limit_up, record_buy_trade,
)
from strategy.scoring import filter_stocks_by_score
from strategy.stock_select import (
    get_continue_count_df, get_relative_position_df, rise_low_volume,
)
from utils.logger import log


def buy(ctx: Context):
    """
    买入主入口，每日 09:28:10 和 14:50 调用。
    周一~四早盘执行买入，周五早盘仅筛选，周五下午执行买入。
    """
    if state.is_empty:
        return

    dp = ctx.dp
    portfolio = ctx.portfolio
    weekday = ctx.current_dt.weekday()
    is_morning, is_afternoon, _ = get_trading_time_status(ctx)

    # 周一~四 14:50 不买
    if weekday < 4 and is_afternoon:
        log.info(f"周{weekday + 1} 14:50，不执行买入")
        return

    date_str = ctx.previous_date.strftime("%Y-%m-%d") if ctx.previous_date else ""
    quotes = dp.get_realtime_quotes(
        state.lblt + state.gap_up + state.gap_down + state.reversal + state.fxsbdk
    )

    # ---- 早盘选股逻辑 ----
    if is_morning:
        lblt_stocks = _screen_lblt(ctx, quotes, date_str)
        rzq_stocks = _screen_rzq(ctx, quotes, date_str)
        gk_stocks = _screen_gk(ctx, quotes, date_str)
        dk_stocks = _screen_dk(ctx, quotes, date_str)
        fxsbdk_stocks = _screen_fxsbdk(ctx, quotes, date_str)

        all_candidates = list(dict.fromkeys(
            lblt_stocks + rzq_stocks + gk_stocks + dk_stocks + fxsbdk_stocks
        ))

        if not all_candidates:
            log.info("没有符合条件的股票")
            from notify.signal import emit_message
            emit_message("今日无目标个股", ctx)
            return

        qualified = filter_stocks_by_score(all_candidates, ctx)
        state.qualified_stocks = qualified

        # 按优先级排序
        sorted_stocks = _sort_by_priority(
            qualified, lblt_stocks, rzq_stocks, gk_stocks, dk_stocks, fxsbdk_stocks
        )
        state.qualified_stocks = sorted_stocks

        # 保存子列表到 state（周五也需要）
        state.lblt_stocks = lblt_stocks
        state.rzq_stocks = rzq_stocks
        state.gk_stocks = gk_stocks
        state.dk_stocks = dk_stocks
        state.fxsbdk_stocks = fxsbdk_stocks

        if weekday < 4:
            _print_selection(sorted_stocks, lblt_stocks, rzq_stocks, gk_stocks, dk_stocks, fxsbdk_stocks)

        # 周五早盘仅筛选
        if weekday == 4:
            log.info(f"周五早盘仅筛选: {sorted_stocks}")
            return

    # ---- 周五下午建仓 ----
    if weekday == 4 and is_afternoon:
        if not state.qualified_stocks:
            log.warning("周五14:50: 无候选股")
            return
        sorted_stocks = _optimize_friday(ctx, state.qualified_stocks)
        if not sorted_stocks:
            log.info("周五优化筛选后无符合条件的股票")
            from notify.signal import emit_message
            emit_message("周五下午无目标个股", ctx)
            return
    elif is_morning and weekday < 4:
        sorted_stocks = state.qualified_stocks
    else:
        return

    # ---- 执行买入 ----
    _execute_buy(ctx, sorted_stocks, quotes)


# ======================================================================
# 模式筛选子函数
# ======================================================================

def _screen_lblt(ctx: Context, quotes: dict, date_str: str) -> list[str]:
    """连板龙头筛选。"""
    if not state.lblt:
        return []
    dp = ctx.dp
    try:
        ccd = get_continue_count_df(state.lblt, ctx.previous_date, 20, dp)
        if ccd.empty:
            return []
        m_max = ccd["count"].max()
        leaders = ccd[ccd["count"] == m_max].index.tolist()
        # 排除一字板风险（近5日有2个以上连续一字板）
        result = []
        for s in leaders:
            if s in ccd.index:
                extreme = ccd.loc[s, "extreme_count"] if isinstance(ccd.loc[s], pd.Series) else ccd.loc[s, "extreme_count"].iloc[0]
                if extreme > 2:
                    continue
            result.append(s)
        return result
    except Exception as e:
        log.error(f"连板龙头筛选失败: {e}")
        return []


def _screen_rzq(ctx: Context, quotes: dict, date_str: str) -> list[str]:
    """弱转强筛选。"""
    result = []
    dp = ctx.dp
    for s in state.reversal:
        try:
            hist = dp.get_price(s, ctx.previous_date, 3, ["close", "open"])
            if hist.empty or len(hist) < 3:
                continue
            pct3 = (hist["close"].iloc[-1] / hist["close"].iloc[0] - 1)
            if pct3 > 0.28:
                continue
            q = quotes.get(s)
            if q is None or q.pre_close <= 0:
                continue
            ratio = q.day_open / q.pre_close if q.pre_close > 0 else 1
            if not (0.98 <= ratio <= 1.09):
                continue
            result.append(s)
        except Exception:
            continue
    return result


def _screen_gk(ctx: Context, quotes: dict, date_str: str) -> list[str]:
    """一进二筛选。"""
    result = []
    for s in state.gap_up:
        try:
            q = quotes.get(s)
            if q is None or q.pre_close <= 0:
                continue
            ratio = q.day_open / q.pre_close if q.pre_close > 0 else 1
            if not (1.00 <= ratio <= 1.06):
                continue
            if q.last_price > 47:
                continue
            result.append(s)
        except Exception:
            continue
    return result


def _screen_dk(ctx: Context, quotes: dict, date_str: str) -> list[str]:
    """首板低开筛选。"""
    dp = ctx.dp
    result = []
    rel_pos = get_relative_position_df(state.gap_down, ctx.previous_date, 20, dp)
    for s in state.gap_down:
        try:
            if s in rel_pos.index and rel_pos.loc[s, "relative_position"] > 0.5:
                continue
            q = quotes.get(s)
            if q is None or q.pre_close <= 0 or q.day_open <= 0:
                continue
            open_ratio = q.day_open / q.pre_close
            if not (0.955 <= open_ratio <= 0.97):
                continue
            prev_day = dp.get_price(s, ctx.previous_date, 1, ["amount"])
            if prev_day.empty:
                continue
            amount = prev_day["amount"].iloc[-1]
            if pd.isna(amount) or amount < 1e8:
                continue
            result.append(s)
        except Exception:
            continue
    return result


def _screen_fxsbdk(ctx: Context, quotes: dict, date_str: str) -> list[str]:
    """反向首板低开筛选。"""
    dp = ctx.dp
    result = []
    rel_pos = get_relative_position_df(state.fxsbdk, ctx.previous_date, 20, dp)
    for s in state.fxsbdk:
        try:
            if s in rel_pos.index and rel_pos.loc[s, "relative_position"] > 0.5:
                continue
            q = quotes.get(s)
            if q is None or q.pre_close <= 0 or q.day_open <= 0:
                continue
            open_ratio = q.day_open / q.pre_close
            if not (1.04 <= open_ratio < 1.10):
                continue
            result.append(s)
        except Exception:
            continue
    return result


# ======================================================================
# 排序与周五优化
# ======================================================================

def _sort_by_priority(qualified, lblt, rzq, gk, dk, fxsbdk) -> list[str]:
    """按 priority_config 和评分排序。"""
    pattern_map = {
        "lb": set(lblt), "rzq": set(rzq), "yje": set(gk),
        "dk": set(dk), "fxsbdk": set(fxsbdk),
    }
    priority_order = state.priority_config or ["lb", "rzq", "yje", "dk", "fxsbdk"]

    def _get_priority(stock):
        for i, pat in enumerate(priority_order):
            if stock in pattern_map.get(pat, set()):
                return len(priority_order) - i
        return 0

    info_list = []
    for s in qualified:
        score = state.score_cache.get(s, {}).get("total_score", 0)
        info_list.append({"stock": s, "priority": _get_priority(s), "score": score})

    info_list.sort(key=lambda x: (x["priority"], x["score"]), reverse=True)
    return [item["stock"] for item in info_list[:state.position_limit]]


def _optimize_friday(ctx: Context, candidates: list[str]) -> list[str]:
    """周五尾盘二次筛选。"""
    dp = ctx.dp
    market_stats = state.trade_stats.get("market_stats", {})
    trend = market_stats.get("trend", "")
    min_score = 18 if trend == "down" else 16
    result = []
    for s in candidates:
        cached = state.score_cache.get(s, {})
        total = cached.get("total_score", 0)
        if total < min_score:
            continue
        vol_ratio = market_stats.get("volume_ratio", 1)
        if vol_ratio < (1.2 if trend == "down" else 1.0):
            continue
        result.append(s)
    return result


# ======================================================================
# 执行买入
# ======================================================================

def _execute_buy(ctx: Context, sorted_stocks: list[str], quotes: dict):
    """仓位分配并发射买入信号。"""
    portfolio = ctx.portfolio
    if portfolio is None:
        return

    available = portfolio.available_cash
    held = len(portfolio.positions)
    available_positions = state.position_limit - held
    if available_positions <= 0:
        log.info(f"已达最大持仓限制 {state.position_limit}")
        return

    # 排除涨停
    candidates = [s for s in sorted_stocks if not is_at_limit_up(s, quotes)]
    buy_count = min(len(candidates), available_positions)
    if buy_count <= 0:
        log.info("无可买入股票或仓位已满")
        return

    per_stock = available / buy_count
    max_single = portfolio.total_value * config.MAX_SINGLE_POSITION
    value = min(per_stock, max_single)
    log.info(f"仓位分配: 可用{available:.0f}, 买{buy_count}只, 每只{value:.0f}(上限{max_single:.0f})")

    bought = 0
    from notify.signal import emit_signal
    for s in candidates[:buy_count]:
        q = quotes.get(s)
        if q is None:
            continue
        if available < q.last_price * 100:
            continue

        qty = int(value / q.last_price / 100) * 100
        if qty <= 0:
            continue

        reason = get_buy_reason(s)
        date_str = ctx.current_dt.strftime("%Y-%m-%d")
        record_buy_trade(ctx, s, reason, q.last_price, qty, date_str)
        emit_signal("BUY", s, q.last_price, f"{reason} 数量:{qty}", ctx)
        bought += 1

    if bought == 0:
        log.info("本次未买入任何股票")
        from notify.signal import emit_message
        emit_message("本次未买入任何股票", ctx)


def _print_selection(qualified, lblt, rzq, gk, dk, fxsbdk):
    log.info("———————————————————————————————————")
    log.info(f"连板龙头({len(lblt)}): {','.join(lblt)}")
    log.info(f"弱转强({len(rzq)}): {','.join(rzq)}")
    log.info(f"一进二({len(gk)}): {','.join(gk)}")
    log.info(f"首板低开({len(dk)}): {','.join(dk)}")
    log.info(f"反向首板低开({len(fxsbdk)}): {','.join(fxsbdk)}")
    log.info(f"最终选股({len(qualified)}): {','.join(qualified)}")
    log.info("———————————————————————————————————")
