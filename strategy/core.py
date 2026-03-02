"""
策略核心模块：GlobalState 单例、Context 上下文、通用工具函数。
替代聚宽 g 对象、context 对象及零散的辅助函数。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import config
from utils.logger import log

if TYPE_CHECKING:
    from data.provider import DataProvider, RealtimeQuote
    from portfolio.tracker import PortfolioTracker


# ======================================================================
# GlobalState — 替代聚宽 g 对象
# ======================================================================
class GlobalState:
    """策略运行期间的全局可变状态。模块级单例。"""

    def __init__(self):
        self.is_empty: bool = False
        self.position_limit: int = config.POSITION_LIMIT
        self.min_score: int = config.MIN_SCORE
        self.concept_num: int = config.CONCEPT_NUM
        self.cache_max_days: int = config.CACHE_MAX_DAYS

        # 选股池
        self.emo_count: list = []
        self.gap_up: list[str] = []
        self.gap_down: list[str] = []
        self.reversal: list[str] = []
        self.fxsbdk: list[str] = []
        self.lblt: list[str] = []

        # 筛选结果
        self.qualified_stocks: list[str] = []
        self.lblt_stocks: list[str] = []
        self.rzq_stocks: list[str] = []
        self.gk_stocks: list[str] = []
        self.dk_stocks: list[str] = []
        self.fxsbdk_stocks: list[str] = []

        # 评分缓存
        self.score_cache: dict = {}
        self.priority_config: list[str] = []

        # 热门概念
        self.hot_concepts_cache: list = []
        self.hot_concepts_data_cache: dict = {}
        self.hot_concepts_api_called: dict = {}

        # 交易统计
        self.trade_stats: dict = {
            "daily_returns": [],
            "position_stats": {},
            "market_stats": {},
            "trade_details": [],
        }
        self.last_trade_info: dict | None = None
        self.today_trades: list[dict] = []
        self.trade_records: dict = {}
        self.today_buy_list: list[str] = []
        self.volume_data_cache: dict = {}

    def clear_daily(self):
        """每日开盘前重置日内状态。"""
        self.today_trades = []
        self.today_buy_list = []
        self.volume_data_cache = {}
        self.score_cache = {}


# 模块级单例
state = GlobalState()


# ======================================================================
# Context — 替代聚宽 context 对象
# ======================================================================
@dataclass
class Context:
    """策略上下文，由调度器在每次回调前更新。"""
    current_dt: dt.datetime = field(default_factory=dt.datetime.now)
    previous_date: dt.date | None = None
    portfolio: "PortfolioTracker | None" = None
    dp: "DataProvider | None" = None  # 当前数据源

    def update_time(self):
        self.current_dt = dt.datetime.now()
        from utils.trade_calendar import get_previous_trade_day
        self.previous_date = get_previous_trade_day(self.current_dt.date())


# ======================================================================
# 通用工具函数
# ======================================================================

def transform_date(date, date_type: str):
    """日期格式转换。date_type: 'str' / 'dt' / 'd'。"""
    if isinstance(date, str):
        str_date = date
        dt_date = dt.datetime.strptime(date, "%Y-%m-%d")
        d_date = dt_date.date()
    elif isinstance(date, dt.datetime):
        str_date = date.strftime("%Y-%m-%d")
        dt_date = date
        d_date = dt_date.date()
    elif isinstance(date, dt.date):
        str_date = date.strftime("%Y-%m-%d")
        dt_date = dt.datetime.strptime(str_date, "%Y-%m-%d")
        d_date = date
    else:
        raise TypeError(f"不支持的日期类型: {type(date)}")
    return {"str": str_date, "dt": dt_date, "d": d_date}[date_type]


def get_trading_time_status(ctx: Context) -> tuple[bool, bool, bool]:
    """返回 (is_morning, is_afternoon, is_trading_time)。"""
    now = ctx.current_dt
    t = now.time()
    morning_start = dt.time(9, 25)
    morning_end = dt.time(11, 35)
    afternoon_start = dt.time(12, 55)
    afternoon_end = dt.time(15, 5)

    is_morning = morning_start <= t <= morning_end
    is_afternoon = afternoon_start <= t <= afternoon_end
    is_trading = is_morning or is_afternoon
    return is_morning, is_afternoon, is_trading


def get_realtime_data(ctx: Context, stocks: list[str] | None = None) -> dict[str, "RealtimeQuote"]:
    """获取实时行情快照，替代 get_current_data()。"""
    if ctx.dp is None:
        return {}
    target = stocks or list(ctx.portfolio.positions.keys()) if ctx.portfolio else []
    if not target:
        return {}
    return ctx.dp.get_realtime_quotes(target)


def is_at_limit_up(stock: str, quotes: dict[str, "RealtimeQuote"]) -> bool:
    q = quotes.get(stock)
    if q is None:
        return False
    return q.last_price >= q.high_limit or q.day_open >= q.high_limit


def is_at_limit_down(stock: str, quotes: dict[str, "RealtimeQuote"]) -> bool:
    q = quotes.get(stock)
    if q is None:
        return False
    return q.last_price <= q.low_limit


def get_buy_reason(stock: str) -> str:
    if stock in state.lblt:
        return "连板龙头"
    if stock in state.reversal:
        return "弱转强"
    if stock in state.gap_up:
        return "一进二"
    if stock in state.gap_down:
        return "首板低开"
    if stock in state.fxsbdk:
        return "反向首板低开"
    return ""


def update_strategy_priority(trend: str):
    """根据市场趋势更新策略优先级。"""
    priority_map = {
        "down": ["lb", "fxsbdk", "yje", "rzq", "dk"],
        "strong_up": ["lb", "rzq", "yje", "fxsbdk", "dk"],
        "flat": ["lb", "rzq", "yje", "fxsbdk", "dk"],
        "up": ["yje", "lb", "rzq", "fxsbdk", "dk"],
    }
    state.priority_config = priority_map.get(trend, ["lb", "rzq", "yje", "dk", "fxsbdk"])
    state.trade_stats["strategy_priority"] = {"trend": trend, "priority": state.priority_config}
    log.info(f"根据市场趋势 [{trend}] 更新策略优先级: {' > '.join(state.priority_config)}")


def record_sell_trade(ctx: Context, stock: str, reason: str, details: dict, quotes: dict, date: str):
    """记录卖出交易。"""
    from data.provider import RealtimeQuote
    q: RealtimeQuote | None = quotes.get(stock)
    trade = {
        "action": "卖出",
        "stock": stock,
        "reason": reason,
        "price": q.last_price if q else 0,
        "time": ctx.current_dt.strftime("%H:%M:%S"),
        "date": date,
        "details": details,
    }
    state.today_trades.append(trade)
    state.last_trade_info = trade
    log.info(f"[卖出] {stock} | {reason} | {details}")


def record_buy_trade(ctx: Context, stock: str, reason: str, price: float, quantity: int, date: str):
    """记录买入交易。"""
    trade = {
        "action": "买入",
        "stock": stock,
        "reason": reason,
        "price": price,
        "quantity": quantity,
        "time": ctx.current_dt.strftime("%H:%M:%S"),
        "date": date,
    }
    state.today_trades.append(trade)
    state.today_buy_list.append(stock)
    log.info(f"[买入] {stock} | {reason} | 价格:{price:.2f} 数量:{quantity}")


def record_morning_stats(ctx: Context):
    """盘前统计：大盘趋势、波动率、量能比。"""
    try:
        dp = ctx.dp
        if dp is None:
            return
        state.clear_daily()
        log.info(f"====== {ctx.current_dt.strftime('%Y-%m-%d')} 盘前数据 ======")

        index_code = "000001.SH"
        index_data = dp.get_price(index_code, ctx.previous_date, 5, ["close", "volume"])
        if index_data.empty or len(index_data) < 2:
            return

        current_close = index_data["close"].iloc[-1]
        prev_close = index_data["close"].iloc[-2]
        change_rate = (current_close - prev_close) / prev_close * 100

        volatility = index_data["close"].pct_change().std() * 100
        current_volume = index_data["volume"].iloc[-1]
        avg_volume = index_data["volume"].iloc[:-1].mean()
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1

        if len(index_data) >= 4:
            close_3d_ago = index_data["close"].iloc[-4]
            change_rate_3d = (current_close - close_3d_ago) / close_3d_ago * 100
        else:
            change_rate_3d = change_rate

        if change_rate_3d > 2:
            trend = "strong_up"
        elif change_rate_3d > 0.5:
            trend = "up"
        elif change_rate_3d > -1.5:
            trend = "flat"
        else:
            trend = "down"

        log.info(f"市场: 趋势={trend}, 波动率={volatility:.2f}%, 量能比={volume_ratio:.2f}")

        state.trade_stats["market_stats"] = {
            "date": ctx.current_dt.strftime("%Y-%m-%d"),
            "trend": trend,
            "change_rate": change_rate,
            "volatility": volatility,
            "volume_ratio": volume_ratio,
        }
        update_strategy_priority(trend)

    except Exception as e:
        log.error(f"盘前统计失败: {e}")


def record_closing_stats(ctx: Context):
    """盘后统计：账户、持仓、收益。"""
    try:
        portfolio = ctx.portfolio
        if portfolio is None:
            return
        log.info("====== 盘后统计 ======")
        log.info(f"总资产: {portfolio.total_value:.2f}")
        log.info(f"可用资金: {portfolio.available_cash:.2f}")
        log.info(f"持仓数: {len(portfolio.positions)}")
        for code, pos in portfolio.positions.items():
            log.info(f"  {code}: 数量={pos.total_amount}, 成本={pos.avg_cost:.2f}, 市值={pos.value:.2f}")
    except Exception as e:
        log.error(f"盘后统计失败: {e}")


def log_daily_trades(ctx: Context):
    """记录每日交易日志。"""
    if not state.today_trades:
        log.info("今日无交易")
        return
    log.info(f"==== 今日交易总结: {len(state.today_trades)} 笔 ====")
    buys = [t for t in state.today_trades if t["action"] == "买入"]
    sells = [t for t in state.today_trades if t["action"] == "卖出"]
    log.info(f"买入: {len(buys)} 笔, 卖出: {len(sells)} 笔")
    for t in state.today_trades:
        log.info(f"  [{t['action']}] {t['stock']} {t.get('reason','')} @ {t.get('price','')}")


def should_empty_position(ctx: Context) -> bool:
    """判断是否应全部清仓（大盘连续2日量能异常）。"""
    try:
        dp = ctx.dp
        if dp is None:
            return False
        index_code = "000300.SH"
        vol_data = dp.get_price(index_code, ctx.previous_date, 7, ["volume"])
        if vol_data.empty or len(vol_data) < 7:
            return False
        avg_vol = vol_data["volume"].iloc[:-2].mean()
        if avg_vol <= 0:
            return False
        recent_2 = vol_data["volume"].iloc[-2:]
        abnormal_days = sum(
            1 for v in recent_2 if v > avg_vol * 2.5 or v < avg_vol * 0.4
        )
        if abnormal_days >= 2:
            log.warning("大盘连续2日量能异常，触发空仓")
            return True
        return False
    except Exception as e:
        log.error(f"空仓判断失败: {e}")
        return False
