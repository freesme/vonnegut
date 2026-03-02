"""
APScheduler 调度器，替代聚宽 run_daily。
交易日内按时间表触发策略函数。
"""
from __future__ import annotations

import datetime as dt

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from strategy.core import Context
from utils.logger import log
from utils.trade_calendar import is_trade_day


def _trade_day_guard(func, ctx: Context):
    """仅在交易日执行的装饰器。"""
    def wrapper():
        if not is_trade_day(dt.date.today()):
            return
        ctx.update_time()
        try:
            func(ctx)
        except Exception as e:
            log.error(f"任务 {func.__name__} 执行失败: {e}")
            import traceback
            log.debug(traceback.format_exc())
    wrapper.__name__ = func.__name__
    return wrapper


def build_scheduler(ctx: Context) -> BackgroundScheduler:
    """根据 config.SCHEDULE 构建完整调度器。"""
    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    schedule = config.SCHEDULE

    from strategy.core import record_morning_stats, record_closing_stats, log_daily_trades
    from strategy.stock_select import get_stock_list
    from strategy.sell_rules import sell_limit_down, sell_limit_per5min, sell2
    from strategy.buy import buy

    # 固定时间点任务
    _add_time_job(sched, record_morning_stats, schedule["record_morning_stats"], ctx)
    _add_time_job(sched, get_stock_list, schedule["get_stock_list"], ctx)
    _add_time_job(sched, sell_limit_down, schedule["sell_limit_down"], ctx)
    _add_time_job(sched, buy, schedule["buy_morning"], ctx)
    _add_time_job(sched, buy, schedule["buy_afternoon"], ctx)
    _add_time_job(sched, record_closing_stats, schedule["record_closing_stats"], ctx)
    _add_time_job(sched, log_daily_trades, schedule["log_daily_trades"], ctx)

    # sell2 多时间点
    for t in schedule["sell2_times"]:
        _add_time_job(sched, sell2, t, ctx)

    # sell_limit_per5min 每5分钟（上午 + 下午）
    for period_key in ("sell_per5min_am", "sell_per5min_pm"):
        period = schedule[period_key]
        start_parts = period["start"].split(":")
        end_parts = period["end"].split(":")
        start_h, start_m = int(start_parts[0]), int(start_parts[1])
        end_h, end_m = int(end_parts[0]), int(end_parts[1])
        interval = period["interval"]

        h, m = start_h, start_m
        while (h, m) <= (end_h, end_m):
            _add_time_job(sched, sell_limit_per5min, f"{h:02d}:{m:02d}", ctx)
            m += interval
            if m >= 60:
                h += 1
                m -= 60

    # T+1 每日解锁
    def _daily_portfolio_update():
        if not is_trade_day(dt.date.today()):
            return
        ctx.portfolio.daily_update()
        log.info("T+1 持仓解锁完成")

    sched.add_job(
        _daily_portfolio_update,
        CronTrigger(hour=9, minute=20, timezone="Asia/Shanghai"),
        id="daily_portfolio_update",
    )

    log.info(f"调度器已构建，共 {len(sched.get_jobs())} 个任务")
    return sched


def _add_time_job(sched: BackgroundScheduler, func, time_str: str, ctx: Context):
    parts = time_str.split(":")
    hour = int(parts[0])
    minute = int(parts[1])
    second = int(parts[2]) if len(parts) > 2 else 0

    sched.add_job(
        _trade_day_guard(func, ctx),
        CronTrigger(hour=hour, minute=minute, second=second, timezone="Asia/Shanghai"),
        id=f"{func.__name__}_{time_str}",
        replace_existing=True,
    )
