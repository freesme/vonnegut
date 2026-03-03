"""
盘中选股 CLI 入口。
随时手动调用，执行完整选股流程并输出结果。

用法:
    python scan.py                  # 默认：执行选股 + 评分 + 排序
    python scan.py --score-only     # 仅对已有候选池重新评分
    python scan.py --notify         # 选股后推送通知
    python scan.py --min-score 16   # 自定义最低评分
"""
from __future__ import annotations

import argparse
import time

import config
from data import create_provider
from portfolio.tracker import PortfolioTracker
from strategy.core import (
    Context, state, record_morning_stats, update_strategy_priority,
)
from strategy.stock_select import get_stock_list
from strategy.scoring import filter_stocks_by_score, calculate_buy_score, get_money_flow_map
from strategy.buy import (
    _screen_lblt, _screen_rzq, _screen_gk, _screen_dk, _screen_fxsbdk,
    _sort_by_priority,
)
from utils.logger import log, setup_logging

_RED = "\033[91m"
_GREEN = "\033[92m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def _dw(s: str) -> int:
    """终端显示宽度（CJK 字符算 2）。"""
    return sum(2 if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f'
               or '\uff00' <= c <= '\uffef' else 1 for c in s)


def _rpad(s: str, w: int) -> str:
    s = _trim_display(s, w)
    return s + " " * max(0, w - _dw(s))


def _lpad(s: str, w: int) -> str:
    s = _trim_display(s, w)
    return " " * max(0, w - _dw(s)) + s


def _trim_display(s: str, w: int) -> str:
    """按显示宽度裁剪字符串，防止超长字段破坏对齐。"""
    if _dw(s) <= w:
        return s
    out = []
    used = 0
    for ch in s:
        ch_w = _dw(ch)
        if used + ch_w > w:
            break
        out.append(ch)
        used += ch_w
    return "".join(out)


def _with_color(text: str, color: str) -> str:
    if not color:
        return text
    return f"{color}{text}{_RESET}"


def _pool_tag(code: str) -> str:
    """根据选股池分类返回标签。"""
    if code in state.lblt:
        return "连板"
    if code in state.reversal:
        return "弱转强"
    if code in state.gap_up:
        return "一进二"
    if code in state.gap_down:
        return "首板低开"
    if code in state.fxsbdk:
        return "反向首板"
    return "-"


def _log_quotes_table(quotes: dict):
    """打印带分类标签的行情表格。"""
    if not quotes:
        return
    hdr = (
        f"  {_rpad('代码', 12)} {_rpad('名称', 10)} {_rpad('分类', 10)} "
        f"{_lpad('现价', 8)} {_lpad('涨幅', 8)} {_lpad('开盘', 8)} "
        f"{_lpad('最高', 8)} {_lpad('最低', 8)} {_lpad('昨收', 8)} "
        f"{_lpad('涨停', 8)} {_lpad('成交量', 14)}"
    )
    log.info(hdr)
    log.info("  " + "-" * 114)
    for code, q in quotes.items():
        chg = ((q.last_price / q.pre_close - 1) * 100) if q.pre_close > 0 else 0
        color = _RED if chg > 0 else _GREEN if chg < 0 else ""
        name = _rpad(getattr(q, "name", code), 10)
        tag = _rpad(_pool_tag(code), 10)
        p_last = _lpad(f"{q.last_price:.2f}", 8)
        p_chg = _lpad(f"{chg:+.2f}%", 8)
        p_open = _lpad(f"{q.day_open:.2f}", 8)
        p_high = _lpad(f"{q.high:.2f}", 8)
        p_low = _lpad(f"{q.low:.2f}", 8)
        p_pre = _lpad(f"{q.pre_close:.2f}", 8)
        p_limit = _lpad(f"{q.high_limit:.2f}", 8)
        p_vol = _lpad(f"{q.volume:,.0f}", 14)
        p_last = _with_color(p_last, color)
        p_chg = _with_color(p_chg, color)
        log.info(
            f"  {code:<12} {name} {tag} "
            f"{p_last} {p_chg} {p_open} {p_high} {p_low} {p_pre} {p_limit} {p_vol}"
        )


def scan(
    notify: bool = False,
    min_score: int | None = None,
    score_only: bool = False,
):
    """
    执行完整的盘中选股流程。

    Returns:
        dict: {
            "qualified": [...],       # 最终候选股列表
            "details": [{stock, score, pattern, ...}, ...],  # 每只股票的详情
            "market": {...},          # 市场统计
        }
    """
    setup_logging(config.LOG_DIR)
    dp = create_provider()
    portfolio = PortfolioTracker()
    ctx = Context(dp=dp, portfolio=portfolio)
    ctx.update_time()

    log.info("=" * 50)
    log.info(f"盘中选股扫描 - {ctx.current_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 50)

    timings: dict[str, float] = {}

    # Step 1: 盘前统计（获取市场趋势）
    t0 = time.time()
    log.info("▶ 阶段 1/6: 盘前统计（市场趋势判断）")
    record_morning_stats(ctx)
    market_stats = state.trade_stats.get("market_stats", {})
    trend = market_stats.get("trend", "unknown")
    timings["盘前统计"] = time.time() - t0
    log.info(f"  市场趋势: {trend} | 策略优先级: {' > '.join(state.priority_config)} "
             f"({timings['盘前统计']:.1f}s)")

    if score_only and state.qualified_stocks:
        log.info(f"仅评分模式：对已有 {len(state.qualified_stocks)} 只候选股重新评分")
        candidates = state.qualified_stocks
    else:
        # Step 2: 选股池生成
        t0 = time.time()
        log.info("▶ 阶段 2/6: 生成选股池（全 A 筛选 → 涨停/跌停分类）")
        get_stock_list(ctx)
        timings["选股池"] = time.time() - t0

        if state.is_empty:
            log.warning("大盘异常，触发空仓信号，无候选股")
            return {"qualified": [], "details": [], "market": market_stats}

        log.info(f"  选股池就绪 ({timings['选股池']:.1f}s): "
                 f"连板{len(state.lblt)}, 弱转强{len(state.reversal)}, "
                 f"一进二{len(state.gap_up)}, 首板低开{len(state.gap_down)}, "
                 f"反向首板{len(state.fxsbdk)}")

        # Step 3: 模式筛选
        t0 = time.time()
        log.info("▶ 阶段 3/6: 模式筛选（竞价条件验证）")
        all_codes = list(set(
            state.lblt + state.gap_up + state.gap_down + state.reversal + state.fxsbdk
        ))
        log.info(f"  获取 {len(all_codes)} 只实时行情...")
        quotes = dp.get_realtime_quotes(all_codes) if all_codes else {}
        _log_quotes_table(quotes)
        date_str = ctx.previous_date.strftime("%Y-%m-%d") if ctx.previous_date else ""

        log.info("  筛选连板龙头...")
        lblt = _screen_lblt(ctx, quotes, date_str)
        log.info("  筛选弱转强...")
        rzq = _screen_rzq(ctx, quotes, date_str)
        log.info("  筛选一进二...")
        gk = _screen_gk(ctx, quotes, date_str)
        log.info("  筛选首板低开...")
        dk = _screen_dk(ctx, quotes, date_str)
        log.info("  筛选反向首板低开...")
        fxsbdk = _screen_fxsbdk(ctx, quotes, date_str)

        candidates = list(dict.fromkeys(lblt + rzq + gk + dk + fxsbdk))
        state.lblt_stocks = lblt
        state.rzq_stocks = rzq
        state.gk_stocks = gk
        state.dk_stocks = dk
        state.fxsbdk_stocks = fxsbdk
        timings["模式筛选"] = time.time() - t0

        log.info(f"  模式筛选完成 ({timings['模式筛选']:.1f}s): "
                 f"连板{len(lblt)}, 弱转强{len(rzq)}, "
                 f"一进二{len(gk)}, 首板低开{len(dk)}, 反向首板{len(fxsbdk)}")
        log.info(f"  候选股合计: {len(candidates)}")

    if not candidates:
        log.info("无候选股")
        return {"qualified": [], "details": [], "market": market_stats}

    # Step 4: 评分
    t0 = time.time()
    log.info(f"▶ 阶段 4/6: 6因子评分（{len(candidates)} 只候选股）")
    for c in candidates:
        info = dp.get_security_info(c)
        pat = _get_pattern(c)
        log.info(f"  候选: {info.display_name}({c}) [{pat}]")
    threshold = min_score or state.min_score
    qualified = filter_stocks_by_score(candidates, ctx, min_score=threshold)
    state.qualified_stocks = qualified
    timings["评分"] = time.time() - t0
    log.info(f"  评分完成 ({timings['评分']:.1f}s): {len(qualified)} 只通过")

    # Step 5: 优先级排序
    log.info("▶ 阶段 5/6: 优先级排序")
    if not score_only:
        sorted_stocks = _sort_by_priority(
            qualified,
            state.lblt_stocks, state.rzq_stocks,
            state.gk_stocks, state.dk_stocks, state.fxsbdk_stocks,
        )
    else:
        sorted_stocks = qualified
    state.qualified_stocks = sorted_stocks

    # Step 6: 组装详情
    log.info("▶ 阶段 6/6: 组装结果")
    details = []
    for s in sorted_stocks:
        cached = state.score_cache.get(s, {})
        info = dp.get_security_info(s)
        pattern = _get_pattern(s)
        details.append({
            "stock": s,
            "name": info.display_name,
            "pattern": pattern,
            "total_score": cached.get("total_score", 0),
            "f1_limit_up": cached.get("factor1_limit_up", 0),
            "f2_technical": cached.get("factor2_technical", 0),
            "f3_volume_ma": cached.get("factor3_volume_ma", 0),
            "f4_mainline": cached.get("factor4_mainline", 0),
            "f5_sentiment": cached.get("factor5_sentiment", 0),
            "f6_main_force": cached.get("factor6_main_force", 0),
        })

    # 计算买卖点
    trade_points: dict[str, dict] = {}
    for d in details:
        tp = _calc_trade_points(d["stock"], d["pattern"], dp, ctx)
        if tp is not None:
            trade_points[d["stock"]] = tp

    # 耗时汇总
    log.info("-" * 40)
    for name, elapsed in timings.items():
        log.info(f"  {name}: {elapsed:.1f}s")
    log.info(f"  合计: {sum(timings.values()):.1f}s")

    # Step 7: 输出结果
    _print_results(sorted_stocks, details, market_stats)
    _print_trade_details(details, trade_points)

    # Step 8: 可选推送
    if notify and sorted_stocks:
        from notify.signal import emit_message
        lines = [f"盘中选股 {ctx.current_dt.strftime('%H:%M')}"]
        lines.append(f"趋势: {trend} | 优先级: {'>'.join(state.priority_config)}")
        for d in details:
            lines.append(f"  {d['name']}({d['stock']}) {d['pattern']} {d['total_score']:.0f}分")
        emit_message("\n".join(lines), ctx)

    return {"qualified": sorted_stocks, "details": details, "market": market_stats}


def _get_pattern(stock: str) -> str:
    if stock in state.lblt_stocks:
        return "连板龙头"
    if stock in state.rzq_stocks:
        return "弱转强"
    if stock in state.gk_stocks:
        return "一进二"
    if stock in state.dk_stocks:
        return "首板低开"
    if stock in state.fxsbdk_stocks:
        return "反向首板低开"
    return "-"


# ======================================================================
# 买卖点计算
# ======================================================================

_BUY_RANGES: dict[str, tuple[float | None, float | None, str]] = {
    "连板龙头":    (None, None, "排板涨停价"),
    "弱转强":     (0.98, 1.09, "开盘比 0.98~1.09"),
    "一进二":     (1.00, 1.06, "开盘比 1.00~1.06"),
    "首板低开":    (0.955, 0.97, "开盘比 0.955~0.97"),
    "反向首板低开": (1.04, 1.10, "开盘比 1.04~1.10"),
}


def _calc_trade_points(
    stock: str, pattern: str, dp, ctx: Context,
) -> dict | None:
    """
    根据选股模式和历史/实时行情，计算建议买入区间与分层卖出价位。

    优先使用实时行情中的 pre_close / high_limit；非盘中时降级为
    缓存中最近交易日收盘价，涨停价按主板 10%、创业板 20% 估算。
    """
    range_info = _BUY_RANGES.get(pattern)
    if range_info is None:
        return None

    pre_close: float | None = None
    high_limit: float | None = None
    last_price: float | None = None

    try:
        quotes = dp.get_realtime_quotes([stock])
        q = quotes.get(stock)
        if q and q.pre_close > 0:
            pre_close = q.pre_close
            if q.high_limit > 0:
                high_limit = q.high_limit
            if q.last_price > 0:
                last_price = q.last_price
    except Exception:
        pass

    if pre_close is None:
        try:
            df = dp.get_price(
                stock, ctx.previous_date, 1,
                ["close", "high_limit", "low_limit"],
            )
            if df is not None and not df.empty:
                pre_close = float(df["close"].iloc[-1])
                if "high_limit" in df.columns:
                    hl = df["high_limit"].iloc[-1]
                    if hl is not None and hl > 0:
                        high_limit = float(hl)
        except Exception:
            pass

    if pre_close is None or pre_close <= 0:
        return None

    if high_limit is None or high_limit <= 0:
        pct = 0.20 if stock.split(".")[0][:2] == "30" else 0.10
        high_limit = round(pre_close * (1 + pct), 2)

    # -- 买入区间 --
    lo_ratio, hi_ratio, desc = range_info
    if lo_ratio is None:
        buy_lo = buy_hi = high_limit
    else:
        buy_lo = round(pre_close * lo_ratio, 2)
        buy_hi = round(pre_close * hi_ratio, 2)
        if pattern == "一进二" and buy_hi > 47:
            buy_hi = 47.00

    buy_mid = (buy_lo + buy_hi) / 2

    # -- 卖出价位 --
    stop_morning = pre_close
    stop_fixed = round(buy_mid * 0.95, 2)
    stop_retreat = round(high_limit - buy_mid * 0.15, 2)

    ma5: float | None = None
    try:
        hist = dp.get_price(stock, ctx.previous_date, 4, ["close"])
        if hist is not None and not hist.empty and len(hist) >= 4:
            ma5 = round((float(hist["close"].sum()) + buy_mid) / 5, 2)
    except Exception:
        pass

    return {
        "pre_close": pre_close,
        "high_limit": high_limit,
        "last_price": last_price,
        "buy_lo": buy_lo,
        "buy_hi": buy_hi,
        "buy_desc": desc,
        "stop_morning": stop_morning,
        "stop_fixed": stop_fixed,
        "stop_retreat": stop_retreat,
        "ma5": ma5,
    }


# ======================================================================
# 输出格式化
# ======================================================================

def _print_results(stocks: list[str], details: list[dict], market: dict):
    print()
    print("=" * 75)
    print(f"  选股结果  |  趋势: {market.get('trend', '?')}  |  "
          f"波动率: {market.get('volatility', 0):.2f}%  |  "
          f"量能比: {market.get('volume_ratio', 0):.2f}")
    print("=" * 75)

    if not details:
        print("  (无符合条件的股票)")
        print("=" * 75)
        return

    print(f"  {'序号':>2}  {'代码':<12} {'名称':<8} {'模式':<8} "
          f"{'总分':>4} {'涨停':>3} {'技术':>3} {'量MA':>3} {'主线':>3} {'情绪':>3} {'资金':>3}")
    print("-" * 75)

    for i, d in enumerate(details, 1):
        print(f"  {i:>2}.  {d['stock']:<12} {d['name']:<8} {d['pattern']:<8} "
              f"{d['total_score']:>4.0f} "
              f"{d['f1_limit_up']:>3.0f} {d['f2_technical']:>3.0f} "
              f"{d['f3_volume_ma']:>3.0f} {d['f4_mainline']:>3.0f} "
              f"{d['f5_sentiment']:>3.0f} {d['f6_main_force']:>3.0f}")

    print("=" * 75)
    print(f"  共 {len(details)} 只  |  最低评分: {config.MIN_SCORE}  |  "
          f"最大持仓: {config.POSITION_LIMIT}")
    print()


def _print_trade_details(
    details: list[dict], trade_points: dict[str, dict],
):
    """在汇总表格下方，逐只输出买卖点详细分析块。"""
    if not details or not trade_points:
        return

    box_w = 63
    print("=" * box_w)
    print("  买卖点参考")
    print("=" * box_w)

    for i, d in enumerate(details, 1):
        tp = trade_points.get(d["stock"])
        if tp is None:
            continue

        # 构造现价/涨跌幅（带颜色），用纯文本版计算框宽度
        price_plain = ""
        price_colored = ""
        lp = tp.get("last_price")
        pc = tp["pre_close"]
        if lp and pc:
            chg = (lp / pc - 1) * 100
            color = _RED if chg > 0 else _GREEN if chg < 0 else ""
            price_plain = f"  {lp:.2f} ({chg:+.2f}%)"
            price_colored = (
                f"  {color}{lp:.2f}{_RESET}"
                f" ({color}{chg:+.2f}%{_RESET})"
            )

        base = (
            f" {i}. {d['stock']} {d['name']} "
            f"[{d['pattern']}] 总分: {d['total_score']:.0f}"
        )
        header_plain = base + price_plain
        header_colored = base + price_colored
        pad = max(1, box_w - _dw(header_plain) - 2)
        print(f"┌─{header_colored}{'─' * pad}┐")

        if tp["buy_lo"] == tp["buy_hi"]:
            print(f"│  买入价:    {tp['buy_lo']:>8.2f}  ({tp['buy_desc']})")
        else:
            print(
                f"│  买入区间:  "
                f"{tp['buy_lo']:.2f} ~ {tp['buy_hi']:.2f}  "
                f"({tp['buy_desc']})"
            )

        print("│  ── 卖出条件 ──")
        print(f"│  早盘止损:  {tp['stop_morning']:>8.2f}  (低于昨收即卖)")
        print(f"│  固定止损:  {tp['stop_fixed']:>8.2f}  (亏损 ≥5%)")
        print(f"│  涨停回撤:  {tp['stop_retreat']:>8.2f}  (距涨停回撤 ≥15%)")
        if tp["ma5"] is not None:
            print(f"│  MA5均线:   {tp['ma5']:>8.2f}  (跌破即卖)")

        print(f"└{'─' * box_w}┘")

    print()


# ======================================================================
# 供其他模块调用的快捷函数
# ======================================================================

def quick_scan(min_score: int | None = None) -> list[dict]:
    """
    快捷调用入口，返回选股详情列表。
    可在 Python 交互环境中使用:
        from scan import quick_scan
        results = quick_scan()
    """
    result = scan(notify=False, min_score=min_score)
    return result.get("details", [])


# ======================================================================
# 一键补跑早盘流程
# ======================================================================

def morning_catchup(
    ctx: Context | None = None,
    notify: bool = False,
) -> dict:
    """
    补跑完整早盘流程（盘前统计 → 选股 → 竞价卖出检测 → 买入信号）。

    如果错过 09:25~09:28 的定时任务窗口，可随时手动调用。
    会依次执行 scheduler 中四个早盘任务的核心逻辑。

    Args:
        ctx: 如通过 API 调用可传入已有 Context，否则自动创建。
        notify: 是否推送结果通知。

    Returns:
        dict: {
            "steps": [{"name": ..., "status": ..., "elapsed": ...}, ...],
            "market": {...},
            "candidates": [...],
            "qualified": [...],
            "details": [...],
            "buy_signals": [...],
        }
    """
    setup_logging(config.LOG_DIR)

    if ctx is None:
        dp = create_provider()
        portfolio = PortfolioTracker()
        ctx = Context(dp=dp, portfolio=portfolio)
    ctx.update_time()

    from strategy.core import record_morning_stats as _record_morning
    from strategy.stock_select import get_stock_list as _get_stock_list
    from strategy.sell_rules import sell_limit_down as _sell_limit_down
    from strategy.buy import buy as _buy

    log.info("=" * 60)
    log.info("一键补跑早盘流程")
    log.info(f"当前时间: {ctx.current_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    steps = []
    result: dict = {
        "steps": steps,
        "market": {},
        "candidates": [],
        "qualified": [],
        "details": [],
        "buy_signals": [],
    }

    # Step 1: 盘前统计
    t0 = time.time()
    log.info("▶ [1/4] 盘前统计（市场趋势 + 策略优先级）")
    try:
        _record_morning(ctx)
        market_stats = state.trade_stats.get("market_stats", {})
        result["market"] = market_stats
        trend = market_stats.get("trend", "unknown")
        log.info(f"  趋势: {trend} | 优先级: {' > '.join(state.priority_config)}")
        steps.append({"name": "盘前统计", "status": "ok", "elapsed": round(time.time() - t0, 1)})
    except Exception as e:
        log.error(f"  盘前统计失败: {e}")
        steps.append({"name": "盘前统计", "status": f"error: {e}", "elapsed": round(time.time() - t0, 1)})

    # Step 2: 选股池生成
    t0 = time.time()
    log.info("▶ [2/4] 选股池生成（全 A 筛选 → 涨停/跌停分类）")
    try:
        _get_stock_list(ctx)
        total = len(state.lblt) + len(state.reversal) + len(state.gap_up) + len(state.gap_down) + len(state.fxsbdk)
        result["candidates"] = list(set(
            state.lblt + state.reversal + state.gap_up + state.gap_down + state.fxsbdk
        ))
        log.info(f"  选股池 {total} 只: 连板{len(state.lblt)}, 弱转强{len(state.reversal)}, "
                 f"一进二{len(state.gap_up)}, 首板低开{len(state.gap_down)}, 反向首板{len(state.fxsbdk)}")
        steps.append({"name": "选股池生成", "status": "ok", "elapsed": round(time.time() - t0, 1)})
    except Exception as e:
        log.error(f"  选股池生成失败: {e}")
        steps.append({"name": "选股池生成", "status": f"error: {e}", "elapsed": round(time.time() - t0, 1)})

    # Step 3: 竞价卖出检测（针对已持仓）
    t0 = time.time()
    log.info("▶ [3/4] 竞价卖出检测（持仓股）")
    try:
        _sell_limit_down(ctx)
        steps.append({"name": "竞价卖出检测", "status": "ok", "elapsed": round(time.time() - t0, 1)})
    except Exception as e:
        log.error(f"  竞价卖出检测失败: {e}")
        steps.append({"name": "竞价卖出检测", "status": f"error: {e}", "elapsed": round(time.time() - t0, 1)})

    # Step 4: 买入信号生成
    t0 = time.time()
    log.info("▶ [4/4] 买入信号生成")
    try:
        _buy(ctx, force_morning=True)
        result["qualified"] = list(state.qualified_stocks) if state.qualified_stocks else []

        # 采集买入信号
        from notify.signal import get_today_signals
        buy_signals = [
            {"stock": s.stock, "price": s.price, "reason": s.reason,
             "time": s.time.isoformat() if hasattr(s.time, "isoformat") else str(s.time)}
            for s in get_today_signals() if s.type.upper() == "BUY"
        ]
        result["buy_signals"] = buy_signals
        steps.append({"name": "买入信号", "status": "ok", "elapsed": round(time.time() - t0, 1)})
    except Exception as e:
        log.error(f"  买入信号生成失败: {e}")
        steps.append({"name": "买入信号", "status": f"error: {e}", "elapsed": round(time.time() - t0, 1)})

    # 组装详情
    for s in result["qualified"]:
        cached = state.score_cache.get(s, {})
        info = ctx.dp.get_security_info(s)
        pattern = _get_pattern(s)
        result["details"].append({
            "stock": s,
            "name": info.display_name,
            "pattern": pattern,
            "total_score": cached.get("total_score", 0),
            "f1_limit_up": cached.get("factor1_limit_up", 0),
            "f2_technical": cached.get("factor2_technical", 0),
            "f3_volume_ma": cached.get("factor3_volume_ma", 0),
            "f4_mainline": cached.get("factor4_mainline", 0),
            "f5_sentiment": cached.get("factor5_sentiment", 0),
            "f6_main_force": cached.get("factor6_main_force", 0),
        })

    # 耗时汇总
    total_elapsed = sum(s["elapsed"] for s in steps)
    log.info("-" * 40)
    for s in steps:
        status_icon = "✓" if s["status"] == "ok" else "✗"
        log.info(f"  {status_icon} {s['name']}: {s['elapsed']:.1f}s")
    log.info(f"  合计: {total_elapsed:.1f}s")

    # 输出结果表格（复用已有输出函数）
    if result["details"]:
        _print_results(result["qualified"], result["details"], result.get("market", {}))

    # 可选推送
    if notify and result["qualified"]:
        from notify.signal import emit_message
        lines = [f"早盘补跑 {ctx.current_dt.strftime('%H:%M')}"]
        trend = result.get("market", {}).get("trend", "?")
        lines.append(f"趋势: {trend} | 候选: {len(result['qualified'])} 只")
        for d in result["details"]:
            lines.append(f"  {d['name']}({d['stock']}) {d['pattern']} {d['total_score']:.0f}分")
        if result["buy_signals"]:
            lines.append(f"买入信号: {len(result['buy_signals'])} 条")
        emit_message("\n".join(lines), ctx)

    return result


# ======================================================================
# CLI 入口
# ======================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="盘中选股扫描")
    parser.add_argument("--notify", action="store_true", help="选股后推送通知")
    parser.add_argument("--score-only", action="store_true", help="仅对已有候选池重新评分")
    parser.add_argument("--min-score", type=int, default=None, help="自定义最低评分阈值")
    parser.add_argument("--catchup", action="store_true",
                        help="一键补跑完整早盘流程（盘前统计→选股→卖出检测→买入信号）")
    args = parser.parse_args()

    start = time.time()
    if args.catchup:
        morning_catchup(notify=args.notify)
    else:
        scan(notify=args.notify, min_score=args.min_score, score_only=args.score_only)
    elapsed = time.time() - start
    print(f"耗时: {elapsed:.1f}s")

