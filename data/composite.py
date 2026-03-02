"""
组合数据源：历史数据走 Tushare，实时行情走东方财富。
自动在两个数据源间路由，对策略代码透明。
"""
from __future__ import annotations

import datetime as dt
from typing import Callable

import pandas as pd

from data.eastmoney_src import EastMoneyProvider
from data.provider import DataProvider, RealtimeQuote, SecurityInfo
from data.tushare_src import TushareProvider


class CompositeProvider(DataProvider):
    """Tushare(历史) + 东方财富(实时) 的组合。"""

    def __init__(self):
        self._ts = TushareProvider()
        self._em = EastMoneyProvider()

    def prefetch_daily_date(self, trade_date):
        return self._ts.prefetch_daily_date(trade_date)

    # 历史数据 → Tushare
    def get_price(self, stocks, end_date, count, fields, frequency="daily", skip_paused=True):
        return self._ts.get_price(stocks, end_date, count, fields, frequency, skip_paused)

    def get_minute_price(self, stock, end_dt, count, frequency="30m", fields=None):
        return self._em.get_minute_price(stock, end_dt, count, frequency, fields)

    # 实时 → 东方财富
    def get_realtime_quotes(self, stocks):
        return self._em.get_realtime_quotes(stocks)

    # 估值 → Tushare
    def get_valuation(self, stocks, date, fields):
        return self._ts.get_valuation(stocks, date, fields)

    # 资金流 → Tushare
    def get_money_flow(self, stocks, start_date, end_date):
        return self._ts.get_money_flow(stocks, start_date, end_date)

    # 竞价 → Tushare
    def get_call_auction(self, stocks, date, fields=None):
        return self._ts.get_call_auction(stocks, date, fields)

    # 证券信息 → Tushare
    def get_security_info(self, stock):
        return self._ts.get_security_info(stock)

    # 概念 → Tushare
    def get_concept(self, stocks, date):
        return self._ts.get_concept(stocks, date)

    # 全部证券 → Tushare
    def get_all_securities(self, date):
        return self._ts.get_all_securities(date)

    # tick → 东方财富
    def subscribe_tick(self, stocks, callback):
        return self._em.subscribe_tick(stocks, callback)

    def unsubscribe_tick(self):
        return self._em.unsubscribe_tick()
