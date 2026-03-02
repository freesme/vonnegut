"""
AKShare 数据源实现。
免费无需 token，适合历史K线、估值、概念等非实时数据。
"""
from __future__ import annotations

import datetime as dt

import akshare as ak
import pandas as pd

from data import cache
from data.provider import DataProvider, RealtimeQuote, SecurityInfo
from utils.code_convert import ts_to_ak, ak_to_ts, ts_exchange
from utils.logger import log


def _date_str(d) -> str:
    if isinstance(d, str):
        return d.replace("-", "")
    return d.strftime("%Y%m%d")


def _safe_float(val, default: float = 0.0) -> float:
    if val is None:
        return default
    if isinstance(val, str):
        v = val.strip().replace(",", "")
        if v in ("", "-", "--"):
            return default
        val = v
    try:
        if pd.isna(val):
            return default
    except Exception:
        pass
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


class AKShareProvider(DataProvider):

    # ------------------------------------------------------------------
    # 历史行情
    # ------------------------------------------------------------------
    def get_price(self, stocks, end_date, count, fields, frequency="daily", skip_paused=True):
        single = isinstance(stocks, str)
        stock_list = [stocks] if single else list(stocks)

        if not single and count == 1 and len(stock_list) > 100:
            return self._get_price_bulk_single_date(stock_list, end_date, fields)

        frames = []
        for code in stock_list:
            df = self._fetch_daily(code, end_date, count)
            if df is not None and not df.empty:
                avail = [f for f in fields if f in df.columns]
                sub = df[avail].tail(count).copy()
                sub["stock"] = code
                frames.append(sub)
        if not frames:
            return pd.DataFrame()
        result = pd.concat(frames)
        if single:
            result.drop(columns=["stock"], inplace=True, errors="ignore")
        return result

    def _get_price_bulk_single_date(
        self, stock_list: list[str], end_date, fields: list[str],
    ) -> pd.DataFrame:
        """批量获取单日数据的快速路径。"""
        end_d = end_date if isinstance(end_date, dt.date) else dt.datetime.strptime(str(end_date), "%Y-%m-%d").date()
        date_str = end_d.strftime("%Y-%m-%d")

        bulk = cache.get_cached_daily_bulk(date_str)
        if bulk.empty:
            log.info(f"  {date_str} 无缓存，回退逐只获取")
            frames = []
            for code in stock_list:
                df = self._fetch_daily(code, end_date, 1)
                if df is None or df.empty:
                    continue
                avail = [f for f in fields if f in df.columns]
                sub = df[avail].tail(1).copy()
                sub["stock"] = code
                frames.append(sub)
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames).reset_index(drop=True)
        wanted = set(stock_list)
        df = bulk[bulk["code"].isin(wanted)].copy()
        df.rename(columns={"code": "stock"}, inplace=True)
        avail = [f for f in fields if f in df.columns]
        keep = ["stock"] + avail
        return df[keep].reset_index(drop=True)

    def _fetch_daily(self, code: str, end_date, count: int) -> pd.DataFrame | None:
        from utils.trade_calendar import get_trade_days

        end_d = end_date if isinstance(end_date, dt.date) else dt.datetime.strptime(str(end_date), "%Y-%m-%d").date()
        trade_days = get_trade_days(end_d, count + 5)
        if not trade_days:
            return None
        start_str = trade_days[0].strftime("%Y-%m-%d")
        end_str = end_d.strftime("%Y-%m-%d")

        cached = cache.get_cached_daily(code, start_str, end_str)
        if cached is not None and len(cached) >= count:
            return cached

        symbol = ts_to_ak(code)
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_str.replace("-", ""),
                end_date=end_str.replace("-", ""),
                adjust="qfq",
            )
        except Exception as e:
            log.error(f"AKShare 日K线获取失败 {code}: {e}")
            return cached

        if df is None or df.empty:
            return cached

        col_map = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
                    "收盘": "close", "成交量": "volume", "成交额": "amount"}
        df.rename(columns=col_map, inplace=True)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
        df.sort_index(inplace=True)

        cache.save_daily(code, df)
        return df.tail(count)

    def get_minute_price(self, stock, end_dt, count, frequency="30m", fields=None):
        freq_map = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "60m": "60"}
        ak_period = freq_map.get(frequency, "30")
        symbol = ts_to_ak(stock)
        try:
            df = ak.stock_zh_a_hist_min_em(symbol=symbol, period=ak_period, adjust="qfq")
            if df is not None and not df.empty:
                col_map = {"时间": "time", "开盘": "open", "最高": "high", "最低": "low",
                            "收盘": "close", "成交量": "volume", "成交额": "amount"}
                df.rename(columns=col_map, inplace=True)
                if "time" in df.columns:
                    df["time"] = pd.to_datetime(df["time"])
                    df.set_index("time", inplace=True)
                if fields:
                    df = df[[f for f in fields if f in df.columns]]
                return df.tail(count)
        except Exception as e:
            log.warning(f"AKShare 分钟线获取失败 {stock}: {e}")
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 实时行情
    # ------------------------------------------------------------------
    def get_realtime_quotes(self, stocks):
        result = {}
        try:
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return result
            col_map = {
                "代码": "code", "名称": "name", "最新价": "last_price",
                "今开": "day_open", "昨收": "pre_close",
                "最高": "high", "最低": "low",
                "成交量": "volume", "成交额": "amount",
                "涨停": "high_limit", "跌停": "low_limit",
            }
            df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}, inplace=True)
            if "code" not in df.columns:
                return result

            wanted_syms = {ts_to_ak(s) for s in stocks}
            for _, row in df.iterrows():
                sym = str(row["code"]).zfill(6)
                if sym not in wanted_syms:
                    continue
                ts_code = ak_to_ts(sym)
                raw_name = row.get("name", "")
                name = "" if pd.isna(raw_name) else str(raw_name)
                last_p = _safe_float(row.get("last_price", 0))
                result[ts_code] = RealtimeQuote(
                    code=ts_code,
                    last_price=last_p,
                    day_open=_safe_float(row.get("day_open", 0)),
                    high_limit=_safe_float(row.get("high_limit", 0)),
                    low_limit=_safe_float(row.get("low_limit", 0)),
                    volume=_safe_float(row.get("volume", 0)),
                    amount=_safe_float(row.get("amount", 0)),
                    paused=(last_p <= 0),
                    pre_close=_safe_float(row.get("pre_close", 0)),
                    high=_safe_float(row.get("high", 0)),
                    low=_safe_float(row.get("low", 0)),
                    name=name,
                )
        except Exception as e:
            log.error(f"AKShare 实时行情获取失败: {e}")
        return result

    # ------------------------------------------------------------------
    # 估值
    # ------------------------------------------------------------------
    def get_valuation(self, stocks, date, fields):
        log.warning("AKShare 估值数据需逐只获取，建议使用 Tushare 数据源")
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 资金流向
    # ------------------------------------------------------------------
    def get_money_flow(self, stocks, start_date, end_date):
        frames = []
        for code in stocks:
            symbol = ts_to_ak(code)
            try:
                df = ak.stock_individual_fund_flow(stock=symbol, market=self._market(code))
                if df is not None and not df.empty:
                    df["stock"] = code
                    frames.append(df)
            except Exception:
                pass
        return pd.concat(frames) if frames else pd.DataFrame()

    @staticmethod
    def _market(code: str) -> str:
        return "sh" if ts_exchange(code) == "SH" else "sz"

    # ------------------------------------------------------------------
    # 集合竞价（AKShare 暂无标准接口，返回空）
    # ------------------------------------------------------------------
    def get_call_auction(self, stocks, date, fields=None):
        log.warning("AKShare 不支持集合竞价数据")
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 证券信息
    # ------------------------------------------------------------------
    def get_security_info(self, stock):
        cached = cache.get_cached_security_info(stock)
        if cached:
            return SecurityInfo(
                code=stock,
                display_name=cached["display_name"],
                start_date=dt.datetime.strptime(cached["start_date"], "%Y-%m-%d").date() if cached["start_date"] else None,
                concepts=cached.get("concepts", []),
            )
        try:
            df = ak.stock_info_a_code_name()
            if df is not None and not df.empty:
                sym = ts_to_ak(stock)
                match = df[df["code"] == sym]
                if not match.empty:
                    name = match.iloc[0].get("name", stock)
                    info = {"display_name": name, "start_date": "", "end_date": "", "concepts": []}
                    cache.save_security_info(stock, info)
                    return SecurityInfo(code=stock, display_name=name)
        except Exception as e:
            log.error(f"AKShare 证券信息获取失败 {stock}: {e}")
        return SecurityInfo(code=stock, display_name=stock)

    # ------------------------------------------------------------------
    # 概念
    # ------------------------------------------------------------------
    def get_concept(self, stocks, date):
        result = {s: [] for s in stocks}
        try:
            for code in stocks:
                symbol = ts_to_ak(code)
                df = ak.stock_board_concept_name_em()
                if df is not None and not df.empty:
                    result[code] = df["板块名称"].tolist()[:20]
        except Exception as e:
            log.error(f"AKShare 概念获取失败: {e}")
        return result

    # ------------------------------------------------------------------
    # 全部证券
    # ------------------------------------------------------------------
    def get_all_securities(self, date):
        try:
            df = ak.stock_info_a_code_name()
            if df is not None and not df.empty:
                codes = []
                items = []
                for _, row in df.iterrows():
                    sym = str(row["code"]).zfill(6)
                    ts_code = ak_to_ts(sym)
                    codes.append(ts_code)
                    items.append({
                        "code": ts_code,
                        "display_name": row.get("name", ""),
                        "start_date": "",
                        "end_date": "",
                        "concepts": [],
                    })
                cache.batch_save_security_info(items)
                log.info(f"已批量缓存 {len(items)} 只证券名称")
                return codes
        except Exception as e:
            log.error(f"AKShare 证券列表获取失败: {e}")
        return []
