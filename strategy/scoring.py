"""
6 因子评分系统。
替代原 calculate_buy_score_optimized / filter_stocks_by_score_optimized 等。
"""
from __future__ import annotations

import datetime as dt
import time
import traceback

import numpy as np
import pandas as pd

from strategy.core import Context, state
from utils.logger import log


# ======================================================================
# 单因子计算
# ======================================================================

def calculate_rsi(prices, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def calculate_limit_up_score(stock: str, ctx: Context, hist_data=None) -> float:
    """Factor1: 涨停因子 0~5 分。"""
    dp = ctx.dp
    score = 0.0
    try:
        if hist_data is None:
            hist_data = dp.get_price(stock, ctx.previous_date, 10, ["close", "volume", "high_limit"])
        if hist_data.empty or len(hist_data) < 2:
            return 0
        last = hist_data.iloc[-1]
        if last.get("close") == last.get("high_limit"):
            score += 3
        elif last.get("close", 0) >= last.get("high_limit", 1) * 0.98:
            score += 2
        elif last.get("close", 0) >= last.get("high_limit", 1) * 0.95:
            score += 1
        if len(hist_data) >= 2:
            prev_vol = hist_data["volume"].iloc[-2]
            curr_vol = hist_data["volume"].iloc[-1]
            if prev_vol > 0 and curr_vol > prev_vol * 1.5:
                score += 2
            elif prev_vol > 0 and curr_vol > prev_vol * 1.2:
                score += 1
    except Exception:
        pass
    return min(score, 5)


def calculate_technical_score(stock: str, ctx: Context, hist_data=None) -> float:
    """Factor2: 技术因子 0~10 分。"""
    dp = ctx.dp
    score = 0.0
    try:
        if hist_data is None:
            hist_data = dp.get_price(stock, ctx.previous_date, 30, ["close", "volume", "high_limit"])
        if hist_data.empty or len(hist_data) < 10:
            return 0

        closes = hist_data["close"].values
        hl = hist_data.get("high_limit")

        # 近10日涨停数
        if hl is not None and not hl.empty:
            hl_count = sum(1 for c, h in zip(closes[-10:], hl.values[-10:]) if c == h)
            if hl_count >= 5:
                score += 5
            elif hl_count >= 4:
                score += 3
            elif hl_count >= 1:
                score += 2

        # MA 多头排列
        if len(closes) >= 20:
            ma5 = np.mean(closes[-5:])
            ma10 = np.mean(closes[-10:])
            ma20 = np.mean(closes[-20:])
            if ma5 > ma10 > ma20:
                score += 2

        # RSI
        rsi = calculate_rsi(closes)
        if 30 <= rsi <= 70:
            score += 2

        # 价格位置（20日高低区间）
        if len(closes) >= 20:
            high20 = max(closes[-20:])
            low20 = min(closes[-20:])
            if high20 > low20:
                pos = (closes[-1] - low20) / (high20 - low20)
                if pos >= 0.5:
                    score += 1
    except Exception:
        pass
    return min(score, 10)


def calculate_volume_ma_score(stock: str, ctx: Context, hist_data=None) -> float:
    """Factor3: 放量MA因子 0~5 分。"""
    dp = ctx.dp
    score = 0.0
    try:
        if hist_data is None:
            hist_data = dp.get_price(stock, ctx.previous_date, 30, ["close", "volume"])
        if hist_data.empty or len(hist_data) < 10:
            return 0

        closes = hist_data["close"].values
        volumes = hist_data["volume"].values

        # MA5 上穿 MA10
        if len(closes) >= 10:
            ma5 = np.mean(closes[-5:])
            ma10 = np.mean(closes[-10:])
            ma5_prev = np.mean(closes[-6:-1])
            ma10_prev = np.mean(closes[-11:-1])
            if ma5 > ma10 and ma5_prev <= ma10_prev:
                score += 3
            elif closes[-1] > ma5:
                score += 1

        # 放量确认
        if len(volumes) >= 10:
            recent_avg = np.mean(volumes[-5:])
            hist_avg = np.mean(volumes[-10:-5])
            if hist_avg > 0 and recent_avg > hist_avg * 1.5:
                score += 2
    except Exception:
        pass
    return min(score, 5)


def calculate_mainline_score(stock: str, ctx: Context) -> float:
    """Factor4: 主线因子 0~5 分（基于热门概念匹配）。"""
    try:
        hot_set = set()
        for item in state.hot_concepts_cache:
            if isinstance(item, str):
                hot_set.add(item)
            elif isinstance(item, dict):
                hot_set.add(item.get("name", ""))

        if not hot_set:
            return 0

        dp = ctx.dp
        info = dp.get_security_info(stock)
        if not info.concepts:
            return 0

        matched = [c for c in info.concepts if c in hot_set]
        return min(len(set(matched)) * 2, 5)
    except Exception:
        return 0


def calculate_sentiment_score(stock: str, ctx: Context) -> float:
    """Factor5: 情绪因子 0~5 分。"""
    dp = ctx.dp
    score = 0.0
    try:
        market_stats = state.trade_stats.get("market_stats", {})
        trend = market_stats.get("trend", "")

        if trend in ("strong_up", "up"):
            score += 2
        elif trend == "flat":
            score += 1

        vol_ratio = market_stats.get("volume_ratio", 1)
        if vol_ratio >= 1.5:
            score += 2
        elif vol_ratio >= 1.2:
            score += 1

        # 个股相对强度
        index_data = dp.get_price("000001.SH", ctx.previous_date, 5, ["close"])
        stock_data = dp.get_price(stock, ctx.previous_date, 5, ["close"])
        if not index_data.empty and not stock_data.empty and len(index_data) >= 2 and len(stock_data) >= 2:
            idx_ret = (index_data["close"].iloc[-1] / index_data["close"].iloc[-2] - 1)
            stk_ret = (stock_data["close"].iloc[-1] / stock_data["close"].iloc[-2] - 1)
            if stk_ret - idx_ret > 0.02:
                score += 1
    except Exception:
        pass
    return min(score, 5)


def calculate_main_force_flow_score(stock: str, fund_flow_list, close_prices) -> float:
    """Factor6: 主力资金因子 0~10 分。"""
    try:
        if not fund_flow_list or not close_prices:
            return 0

        total_inflow = sum(f.get("net_mf_amount", 0) for f in fund_flow_list)
        total_amount = sum(f.get("buy_elg_amount", 0) + f.get("sell_elg_amount", 0) for f in fund_flow_list)

        ratio_score = 0
        if total_amount > 0:
            ratio = total_inflow / total_amount
            if ratio > 0.3:
                ratio_score = 20
            elif ratio > 0.1:
                ratio_score = 12
            elif ratio > 0:
                ratio_score = 6

        abs_score = 0
        if total_inflow > 1e8:
            abs_score = 10
        elif total_inflow > 5e7:
            abs_score = 6
        elif total_inflow > 0:
            abs_score = 3

        pattern_score = 0
        if len(fund_flow_list) >= 3:
            recent = [f.get("net_mf_amount", 0) for f in fund_flow_list[-3:]]
            if all(r > 0 for r in recent):
                pattern_score = 15
            elif sum(1 for r in recent if r > 0) >= 2:
                pattern_score = 8

        weighted = ratio_score * 0.4 + abs_score * 0.2 + pattern_score * 0.4
        return min(weighted, 10)
    except Exception:
        return 0


# ======================================================================
# 资金流数据获取
# ======================================================================

def get_money_flow_map(ctx: Context, stocks: list[str]) -> dict:
    """批量获取资金流数据，返回 {stock: [records]}。"""
    dp = ctx.dp
    result = {}
    try:
        from utils.trade_calendar import get_trade_days
        trade_days = get_trade_days(ctx.previous_date, 5)
        if not trade_days:
            return result
        df = dp.get_money_flow(stocks, trade_days[0], ctx.previous_date)
        if df.empty:
            return result
        if "stock" in df.columns:
            for stock in stocks:
                sub = df[df["stock"] == stock]
                result[stock] = sub.to_dict("records")
        else:
            result[stocks[0]] = df.to_dict("records") if len(stocks) == 1 else {}
    except Exception as e:
        log.error(f"资金流数据获取失败: {e}")
    return result


# ======================================================================
# 综合评分与筛选
# ======================================================================

def calculate_buy_score(stock: str, ctx: Context, money_flow_map: dict) -> dict:
    """计算单只股票的 6 因子综合评分。"""
    dp = ctx.dp
    hist_data = dp.get_price(stock, ctx.previous_date, 30, ["close", "volume", "high_limit"])
    close_prices = hist_data["close"].tolist() if not hist_data.empty else []
    fund_flow_list = money_flow_map.get(stock, [])

    f1 = calculate_limit_up_score(stock, ctx, hist_data)
    f2 = calculate_technical_score(stock, ctx, hist_data)
    f3 = calculate_volume_ma_score(stock, ctx, hist_data)
    f4 = calculate_mainline_score(stock, ctx)
    f5 = calculate_sentiment_score(stock, ctx)
    f6 = calculate_main_force_flow_score(stock, fund_flow_list, close_prices)

    total = f1 + f2 + f3 + f4 + f5 + f6
    return {
        "stock": stock,
        "total_score": total,
        "factor1_limit_up": f1,
        "factor2_technical": f2,
        "factor3_volume_ma": f3,
        "factor4_mainline": f4,
        "factor5_sentiment": f5,
        "factor6_main_force": f6,
    }


def filter_stocks_by_score(
    stocks: list[str],
    ctx: Context,
    min_score: int | None = None,
    max_stocks: int = 100,
) -> list[str]:
    """
    对候选股评分并过滤，返回通过的股票列表。
    同时更新 state.score_cache。
    """
    if min_score is None:
        min_score = state.min_score

    total_count = min(len(stocks), max_stocks)
    log.info(f"开始评分: {total_count} 只候选股, 阈值={min_score}")

    t0 = time.time()
    log.info("  获取资金流数据...")
    money_flow_map = get_money_flow_map(ctx, stocks)
    log.info(f"  资金流数据就绪 ({time.time() - t0:.1f}s)")

    passed = []

    for idx, stock in enumerate(stocks[:max_stocks], 1):
        try:
            if idx % 10 == 1 or idx == total_count:
                log.info(f"  评分进度: {idx}/{total_count} "
                         f"(已通过 {len(passed)} 只, 耗时 {time.time() - t0:.1f}s)")

            scores = calculate_buy_score(stock, ctx, money_flow_map)
            state.score_cache[stock] = scores

            total = scores["total_score"]
            if total < min_score:
                continue

            f4 = scores["factor4_mainline"]
            f6 = scores["factor6_main_force"]
            f1 = scores["factor1_limit_up"]

            if stock in state.lblt:
                if f4 <= 0 and not (f6 > 0 or f1 >= 5):
                    continue
            elif stock in state.gap_up:
                if not (20 <= total <= 37):
                    continue
            elif stock in (state.reversal + state.gap_down + state.fxsbdk):
                if total < 20:
                    continue

            passed.append(stock)
            info = ctx.dp.get_security_info(stock)
            log.info(f"  ✓ [{idx}/{total_count}] {info.display_name}({stock}) "
                     f"总分={total:.1f} F1={f1:.1f} F2={scores['factor2_technical']:.1f} "
                     f"F3={scores['factor3_volume_ma']:.1f} F4={f4:.1f} "
                     f"F5={scores['factor5_sentiment']:.1f} F6={f6:.1f}")
        except Exception as e:
            log.error(f"  ✗ [{idx}/{total_count}] 评分失败 {stock}: {e}")
            log.debug(traceback.format_exc())

    elapsed = time.time() - t0
    log.info(f"评分完成: {len(stocks)} → {len(passed)} 只通过 "
             f"(阈值={min_score}, 耗时 {elapsed:.1f}s)")
    return passed
