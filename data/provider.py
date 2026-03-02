"""
统一数据接口抽象层。
策略代码只依赖此接口，底层可切换 Tushare / AKShare / 东方财富。
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd


@dataclass
class SecurityInfo:
    code: str
    display_name: str
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    concepts: list[str] = field(default_factory=list)


@dataclass
class RealtimeQuote:
    """单只股票的实时快照，替代聚宽 get_current_data()[stock]。"""
    code: str
    last_price: float
    day_open: float
    high_limit: float
    low_limit: float
    volume: float       # 当日累计成交量（手）
    amount: float       # 当日累计成交额（元）
    paused: bool = False
    pre_close: float = 0.0
    high: float = 0.0
    low: float = 0.0
    name: str = ""
    time: dt.datetime | None = None


class DataProvider(ABC):
    """所有数据源必须实现此接口。"""

    # ------------------------------------------------------------------
    # 历史行情
    # ------------------------------------------------------------------
    @abstractmethod
    def get_price(
        self,
        stocks: str | list[str],
        end_date: dt.date | str,
        count: int,
        fields: list[str],
        frequency: str = "daily",
        skip_paused: bool = True,
    ) -> pd.DataFrame:
        """
        获取历史价格数据。
        返回 DataFrame，columns = fields，index = datetime。
        单只股票返回扁平 DF；多只返回带 stock 列的长表。
        """

    @abstractmethod
    def get_minute_price(
        self,
        stock: str,
        end_dt: dt.datetime,
        count: int,
        frequency: str = "30m",
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """盘中分钟级 K 线。"""

    # ------------------------------------------------------------------
    # 实时行情
    # ------------------------------------------------------------------
    @abstractmethod
    def get_realtime_quotes(self, stocks: list[str]) -> dict[str, RealtimeQuote]:
        """批量获取实时快照，返回 {code: RealtimeQuote}。"""

    # ------------------------------------------------------------------
    # 估值 / 基本面
    # ------------------------------------------------------------------
    @abstractmethod
    def get_valuation(
        self,
        stocks: str | list[str],
        date: dt.date | str,
        fields: list[str],
    ) -> pd.DataFrame:
        """pe_ratio, pb_ratio, market_cap, circulating_market_cap, turnover_ratio …"""

    # ------------------------------------------------------------------
    # 资金流向
    # ------------------------------------------------------------------
    @abstractmethod
    def get_money_flow(
        self,
        stocks: list[str],
        start_date: dt.date | str,
        end_date: dt.date | str,
    ) -> pd.DataFrame:
        ...

    # ------------------------------------------------------------------
    # 集合竞价
    # ------------------------------------------------------------------
    @abstractmethod
    def get_call_auction(
        self,
        stocks: str | list[str],
        date: dt.date | str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        ...

    # ------------------------------------------------------------------
    # 证券信息 / 概念
    # ------------------------------------------------------------------
    @abstractmethod
    def get_security_info(self, stock: str) -> SecurityInfo:
        ...

    @abstractmethod
    def get_concept(self, stocks: list[str], date: dt.date | str) -> dict:
        """返回 {stock: [concept_name, ...]}"""

    @abstractmethod
    def get_all_securities(self, date: dt.date | str) -> list[str]:
        """返回全部 A 股代码列表（Tushare 格式）。"""

    # ------------------------------------------------------------------
    # 批量预加载（可选实现）
    # ------------------------------------------------------------------
    def prefetch_daily_date(self, trade_date) -> None:
        """预加载某日全市场日线+涨跌停价到缓存。默认空操作，子类可覆盖。"""

    # ------------------------------------------------------------------
    # Tick 数据（可选实现）
    # ------------------------------------------------------------------
    def subscribe_tick(
        self,
        stocks: list[str],
        callback: Callable[[str, dict], None],
    ):
        """订阅 tick 推送，默认不支持。"""
        raise NotImplementedError("此数据源不支持 tick 订阅")

    def unsubscribe_tick(self):
        pass
