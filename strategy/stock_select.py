"""
选股模块：5类模式的股票池生成。
替代原 get_stock_list / prepare_stock_list / get_hl_stock 等函数。
"""
from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from strategy.core import Context, state, transform_date, should_empty_position
from utils.logger import log


def _elapsed(t0: float) -> str:
    return f"{time.time() - t0:.1f}s"


def _chunked(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ======================================================================
# 基础过滤
# ======================================================================

def filter_kcbj_stock(stock_list: list[str]) -> list[str]:
    """保留主板和创业板，过滤科创板、北交所。"""
    return [s for s in stock_list if s.split(".")[0][:2] in ("60", "00", "30")]


def filter_new_stock(stock_list: list[str], date: dt.date, dp, days: int = 50) -> list[str]:
    """过滤上市不足 N 天的新股。优先批量读缓存，仅缓存缺失时才逐只查询。"""
    from data import cache as _cache
    start_dates = _cache.get_all_cached_security_start_dates()
    result = []
    miss_count = 0
    unknown_start_count = 0
    for s in stock_list:
        sd_str = start_dates.get(s)
        if sd_str:
            try:
                sd = dt.datetime.strptime(sd_str, "%Y-%m-%d").date()
                if (date - sd).days > days:
                    result.append(s)
                continue
            except ValueError:
                pass
        info = dp.get_security_info(s)
        miss_count += 1
        if info.start_date is None:
            # 无法确认上市日期时，避免误删老股，放行并记录。
            unknown_start_count += 1
            result.append(s)
            continue
        if (date - info.start_date).days > days:
            result.append(s)
    if miss_count > 0:
        log.info(f"  新股过滤: {miss_count} 只未命中缓存，已逐只查询")
    if unknown_start_count > 0:
        log.warning(f"  新股过滤: {unknown_start_count} 只缺少上市日期，已放行")
    return result


def filter_st_paused(stock_list: list[str], quotes: dict) -> list[str]:
    """过滤 ST、停牌、退市股。"""
    result = []
    missing_quote = 0
    for s in stock_list:
        q = quotes.get(s)
        if q is None:
            # 实时行情偶发缺失时不做误删，保留该股票。
            missing_quote += 1
            result.append(s)
            continue
        if q.paused:
            continue
        name = str(getattr(q, "name", "") or "").upper()
        if "ST" in name or "退" in name:
            continue
        result.append(s)
    if missing_quote > 0:
        log.warning(f"  ST/停牌过滤: {missing_quote} 只缺少实时行情，已保留")
    return result


def prepare_stock_list(ctx: Context) -> list[str]:
    """每日初始股票池：全 A → 过滤科创北交 → 过滤新股 → 过滤 ST/停牌。"""
    dp = ctx.dp
    date = ctx.previous_date

    t0 = time.time()
    all_stocks = dp.get_all_securities(date)
    log.info(f"  [1/4] 获取全 A 股列表: {len(all_stocks)} 只 ({_elapsed(t0)})")

    stocks = filter_kcbj_stock(all_stocks)
    log.info(f"  [2/4] 过滤科创/北交后: {len(stocks)} 只")

    t1 = time.time()
    stocks = filter_new_stock(stocks, date, dp)
    log.info(f"  [3/4] 过滤新股后: {len(stocks)} 只 ({_elapsed(t1)})")

    # 分批获取实时行情并过滤 ST/停牌，避免单次请求过大导致失败。
    t2 = time.time()
    quotes = {}
    for batch in _chunked(stocks, 300):
        try:
            quotes.update(dp.get_realtime_quotes(batch))
        except Exception as e:
            log.warning(f"  获取实时行情失败（批量 {len(batch)} 只）: {e}")
    stocks = filter_st_paused(stocks, quotes)
    log.info(f"  [4/4] 过滤 ST/停牌后: {len(stocks)} 只 ({_elapsed(t2)})")

    return stocks


# ======================================================================
# 涨停/跌停筛选
# ======================================================================

def get_hl_stock(stock_list: list[str], date, dp) -> list[str]:
    """筛选某日收盘涨停的股票。"""
    df = dp.get_price(stock_list, date, 1, ["close", "high_limit"])
    if df.empty:
        return []
    df = df.dropna()
    if "stock" in df.columns:
        return df[df["close"] == df["high_limit"]]["stock"].tolist()
    return df[df["close"] == df["high_limit"]].index.tolist() if "close" in df.columns else []


def get_ever_hl_stock(stock_list: list[str], date, dp) -> list[str]:
    """筛选某日盘中曾涨停（最高价触及涨停价）的股票。"""
    df = dp.get_price(stock_list, date, 1, ["high", "high_limit"])
    if df.empty:
        return []
    df = df.dropna()
    if "stock" in df.columns:
        return df[df["high"] == df["high_limit"]]["stock"].tolist()
    return df[df["high"] == df["high_limit"]].index.tolist() if "high" in df.columns else []


def get_ever_hl_not_closed(stock_list: list[str], date, dp) -> list[str]:
    """筛选曾涨停但收盘未封住的股票（弱转强候选）。"""
    df = dp.get_price(stock_list, date, 1, ["close", "high", "high_limit"])
    if df.empty:
        return []
    df = df.dropna()
    if "stock" in df.columns:
        mask = (df["high"] == df["high_limit"]) & (df["close"] != df["high_limit"])
        return df[mask]["stock"].tolist()
    if "high" in df.columns:
        mask = (df["high"] == df["high_limit"]) & (df["close"] != df["high_limit"])
        return df[mask].index.tolist()
    return []


def get_ll_stock(stock_list: list[str], date, dp) -> list[str]:
    """筛选某日收盘跌停的股票。"""
    df = dp.get_price(stock_list, date, 1, ["close", "low_limit"])
    if df.empty:
        return []
    df = df.dropna()
    if "stock" in df.columns:
        return df[df["close"] == df["low_limit"]]["stock"].tolist()
    return df[df["close"] == df["low_limit"]].index.tolist() if "close" in df.columns else []


# ======================================================================
# 连板/涨停计数
# ======================================================================

def get_hl_count_df(hl_list: list[str], date, watch_days: int, dp) -> pd.DataFrame:
    """计算每只股票在 watch_days 内的涨停数和一字涨停数。"""
    df = dp.get_price(hl_list, date, watch_days, ["close", "high_limit", "low"])
    if df.empty:
        return pd.DataFrame(columns=["count", "extreme_count"])

    hl_counts, extreme_counts = [], []
    for stock in hl_list:
        if "stock" in df.columns:
            sub = df[df["stock"] == stock]
        else:
            sub = df
        hl_days = len(sub[sub["close"] == sub["high_limit"]])
        extreme_days = len(sub[sub["low"] == sub["high_limit"]])
        hl_counts.append(hl_days)
        extreme_counts.append(extreme_days)

    return pd.DataFrame(
        index=hl_list,
        data={"count": hl_counts, "extreme_count": extreme_counts},
    )


def get_continue_count_df(hl_list: list[str], date, watch_days: int, dp) -> pd.DataFrame:
    """
    计算连板数（从最近一天往前的最大连续涨停天数）。
    只拉一次数据，在内存中计算。
    """
    t0 = time.time()
    df = dp.get_price(hl_list, date, watch_days, ["close", "high_limit", "low"])
    log.info(f"    连板数据拉取: {len(hl_list)} 只 × {watch_days} 天 ({_elapsed(t0)})")
    if df.empty:
        return pd.DataFrame(columns=["count", "extreme_count"])

    results = {}
    for stock in hl_list:
        if "stock" in df.columns:
            sub = df[df["stock"] == stock].sort_index()
        else:
            sub = df.sort_index()
        if sub.empty or "close" not in sub.columns or "high_limit" not in sub.columns:
            continue

        consecutive = 0
        extreme = 0
        for i in range(len(sub) - 1, -1, -1):
            row = sub.iloc[i]
            if pd.notna(row["close"]) and pd.notna(row["high_limit"]) and row["close"] == row["high_limit"]:
                consecutive += 1
                if pd.notna(row.get("low")) and row["low"] == row["high_limit"]:
                    extreme += 1
            else:
                break

        if consecutive >= 2:
            results[stock] = {"count": consecutive, "extreme_count": extreme}

    if not results:
        return pd.DataFrame(columns=["count", "extreme_count"])

    result_df = pd.DataFrame.from_dict(results, orient="index")
    log.info(f"    连板计算完成: {len(results)} 只 ≥2连板 ({_elapsed(t0)})")
    return result_df.sort_values("count", ascending=False)


def get_relative_position_df(stock_list: list[str], date, watch_days: int, dp) -> pd.DataFrame:
    """计算股票在 watch_days 内的相对位置（0=最低, 1=最高）。"""
    df = dp.get_price(stock_list, date, watch_days, ["high", "low", "close"])
    if df.empty:
        return pd.DataFrame()
    results = {}
    for stock in stock_list:
        if "stock" in df.columns:
            sub = df[df["stock"] == stock]
        else:
            sub = df
        if sub.empty:
            continue
        high_max = sub["high"].max()
        low_min = sub["low"].min()
        last_close = sub["close"].iloc[-1]
        if high_max == low_min:
            results[stock] = 0.5
        else:
            results[stock] = (last_close - low_min) / (high_max - low_min)
    return pd.DataFrame.from_dict(results, orient="index", columns=["relative_position"])


# ======================================================================
# 左压判断
# ======================================================================

def rise_low_volume(stock: str, ctx: Context) -> bool:
    """判断股票上涨时是否未放量（左压）。"""
    dp = ctx.dp
    hist = dp.get_price(stock, ctx.previous_date, 106, ["high", "volume"])
    if hist.empty or len(hist) < 10:
        return False
    highs = hist["high"].values[:102]
    volumes = hist["volume"].values
    prev_high = highs[-1]
    zyts_0 = 100
    for i, h in enumerate(highs[-3::-1], 2):
        if h >= prev_high:
            zyts_0 = i - 1
            break
    zyts = zyts_0 + 5
    if volumes[-1] <= max(volumes[-zyts:-1]) * 0.9:
        return True
    return False


# ======================================================================
# 主选股函数
# ======================================================================

def get_stock_list(ctx: Context):
    """
    每日选股主入口 (09:28)。
    生成 5 类股票池到 state，并处理空仓逻辑。
    """
    t_start = time.time()
    dp = ctx.dp
    date = ctx.previous_date

    log.info("── 选股步骤 1/9: 判断大盘异常 ──")
    t0 = time.time()
    if should_empty_position(ctx):
        state.is_empty = True
        log.warning(f"大盘异常空仓 ({_elapsed(t0)})")
        if ctx.portfolio:
            from notify.signal import emit_signal
            for stock in list(ctx.portfolio.positions.keys()):
                emit_signal("SELL", stock, 0, "大盘异常空仓", ctx)
        return
    state.is_empty = False
    log.info(f"  大盘正常 ({_elapsed(t0)})")

    log.info("── 选股步骤 2/9: 生成初始股票池 ──")
    t0 = time.time()
    initial_list = prepare_stock_list(ctx)
    log.info(f"  初始池: {len(initial_list)} 只 ({_elapsed(t0)})")

    log.info("── 选股步骤 3/9: 预加载全市场日线（批量缓存） ──")
    t0 = time.time()
    from utils.trade_calendar import get_trade_days as _get_trade_days
    trade_days = _get_trade_days(date, 3)
    if len(trade_days) < 3:
        log.error(f"交易日历不足 3 天 (got {len(trade_days)}), 无法选股")
        return
    date_2, date_1, date = trade_days[0], trade_days[1], trade_days[2]
    for td in (date, date_1, date_2):
        dp.prefetch_daily_date(td)
    log.info(f"  预加载完成 ({_elapsed(t0)})")

    log.info("── 选股步骤 4/9: 筛选昨日涨停股 ──")
    t0 = time.time()
    hl0 = get_hl_stock(initial_list, date, dp)
    log.info(f"  昨日涨停: {len(hl0)} 只 ({_elapsed(t0)})")

    log.info("── 选股步骤 5/9: 筛选前日/前前日曾涨停股 ──")
    t0 = time.time()
    ever_hl1 = get_ever_hl_stock(initial_list, date_1, dp)
    ever_hl2 = get_ever_hl_stock(initial_list, date_2, dp)
    hl1_closed = get_hl_stock(initial_list, date_1, dp)
    log.info(f"  前日曾涨停: {len(ever_hl1)}, 前前日曾涨停: {len(ever_hl2)}, "
             f"前日收盘涨停: {len(hl1_closed)} ({_elapsed(t0)})")

    log.info("── 选股步骤 6/9: 筛选曾涨停未封股 ──")
    t0 = time.time()
    ever_hl0_not_closed = get_ever_hl_not_closed(initial_list, date, dp)
    log.info(f"  曾涨停未封: {len(ever_hl0_not_closed)} 只 ({_elapsed(t0)})")

    log.info("── 选股步骤 7/9: 筛选昨日跌停股 ──")
    t0 = time.time()
    ll0 = get_ll_stock(initial_list, date, dp)
    log.info(f"  昨日跌停: {len(ll0)} 只 ({_elapsed(t0)})")

    log.info("── 选股步骤 8/9: 分类汇总 ──")
    ever_hl_remove = set(ever_hl1 + ever_hl2)
    state.gap_up = [s for s in hl0 if s not in ever_hl_remove]
    state.gap_down = [s for s in hl0 if s not in set(ever_hl1)]
    state.reversal = [s for s in ever_hl0_not_closed if s not in set(hl1_closed)]
    state.fxsbdk = ll0
    log.info(f"  一进二候选: {len(state.gap_up)}, 首板低开候选: {len(state.gap_down)}, "
             f"弱转强候选: {len(state.reversal)}, 反向首板候选: {len(state.fxsbdk)}")

    log.info("── 选股步骤 9/9: 计算连板龙头 ──")
    t0 = time.time()
    if hl0:
        ccd = get_continue_count_df(hl0, date, 10, dp)
        state.lblt = ccd.index.tolist() if not ccd.empty else []
        log.info(f"  连板龙头: {len(state.lblt)} 只 ({_elapsed(t0)})")
    else:
        state.lblt = []
        log.info(f"  无涨停股，跳过连板计算 ({_elapsed(t0)})")

    log.info(f"选股池生成完成 (总耗时 {_elapsed(t_start)}): "
             f"连板{len(state.lblt)}, 弱转强{len(state.reversal)}, "
             f"一进二{len(state.gap_up)}, 首板低开{len(state.gap_down)}, "
             f"反向首板{len(state.fxsbdk)}")
