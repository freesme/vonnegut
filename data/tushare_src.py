"""
Tushare Pro 数据源实现。
主要负责：历史K线、估值、资金流、集合竞价、证券信息、概念。
"""
from __future__ import annotations

import datetime as dt
import time

import pandas as pd
import tushare as ts

import config
from data import cache
from data.provider import DataProvider, RealtimeQuote, SecurityInfo
from utils.code_convert import ts_to_ak
from utils.logger import log

_pro: ts.pro_api | None = None


def _api() -> ts.pro_api:
    global _pro
    if _pro is None:
        if not config.TUSHARE_TOKEN:
            raise RuntimeError("TUSHARE_TOKEN 未设置，请在环境变量或 config.py 中配置")
        ts.set_token(config.TUSHARE_TOKEN)
        _pro = ts.pro_api()
    return _pro


def _date_str(d) -> str:
    if isinstance(d, str):
        return d.replace("-", "")
    return d.strftime("%Y%m%d")


def _to_std_date(d) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    s = str(d)
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _is_index(code: str) -> bool:
    """判断是否为指数代码（如 000001.SH 上证指数、000300.SH 沪深300）。"""
    sym = code.split(".")[0]
    exchange = code.split(".")[-1] if "." in code else ""
    if exchange == "SH" and sym.startswith("000"):
        return True
    if exchange == "SZ" and sym.startswith("399"):
        return True
    return False


class TushareProvider(DataProvider):

    # ------------------------------------------------------------------
    # 批量预加载
    # ------------------------------------------------------------------
    def prefetch_daily_date(self, trade_date) -> None:
        """用 2 次 API 调用拉取某日全市场日线 + 涨跌停价并写入缓存。"""
        td_str = trade_date.strftime("%Y-%m-%d") if hasattr(trade_date, "strftime") else str(trade_date)
        td_ts = _date_str(trade_date)

        if cache.has_daily_date(td_str):
            log.info(f"  日线缓存已存在 {td_str}，跳过预加载")
            return

        api = _api()

        t0 = time.time()
        log.info(f"  预加载 {td_str} 全市场日线...")
        try:
            daily_df = api.daily(trade_date=td_ts)
        except Exception as e:
            log.error(f"  预加载日线失败: {e}")
            return
        if daily_df is None or daily_df.empty:
            log.warning(f"  {td_str} 无日线数据")
            return
        log.info(f"  日线: {len(daily_df)} 条 ({time.time() - t0:.1f}s)")

        t1 = time.time()
        log.info(f"  预加载 {td_str} 涨跌停价...")
        limit_df = None
        try:
            limit_df = api.stk_limit(trade_date=td_ts)
        except Exception as e:
            log.warning(f"  涨跌停价获取失败（非致命）: {e}")
        if limit_df is not None and not limit_df.empty:
            log.info(f"  涨跌停: {len(limit_df)} 条 ({time.time() - t1:.1f}s)")
            limit_map = {}
            for _, r in limit_df.iterrows():
                limit_map[r["ts_code"]] = (r.get("up_limit"), r.get("down_limit"))
        else:
            limit_map = {}

        t2 = time.time()
        rows = []
        for _, r in daily_df.iterrows():
            code = r["ts_code"]
            lim = limit_map.get(code, (None, None))
            rows.append((
                code, td_str,
                r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                r.get("vol"), r.get("amount"),
                lim[0], lim[1],
                r.get("pre_close"),
            ))
        cache.batch_save_daily(rows)
        log.info(f"  已缓存 {len(rows)} 只日线到 SQLite ({time.time() - t2:.1f}s)")

    # ------------------------------------------------------------------
    # 历史行情
    # ------------------------------------------------------------------
    def get_price(self, stocks, end_date, count, fields, frequency="daily", skip_paused=True):
        single = isinstance(stocks, str)
        stock_list = [stocks] if single else list(stocks)

        if not single and count == 1 and len(stock_list) > 100:
            return self._get_price_bulk_single_date(stock_list, end_date, fields)

        total = len(stock_list)
        show_progress = total > 50
        frames = []
        api_hits = 0
        for i, code in enumerate(stock_list):
            if show_progress and i % 500 == 0 and i > 0:
                log.info(f"    get_price 进度: {i}/{total} (API调用 {api_hits} 次)")
            df, hit_api = self._get_daily_cached(code, end_date, count, return_api_flag=True)
            if hit_api:
                api_hits += 1
            if df is not None and not df.empty:
                avail = [f for f in fields if f in df.columns]
                sub = df[avail].tail(count).copy()
                sub["stock"] = code
                frames.append(sub)
        if show_progress and api_hits > 0:
            log.info(f"    get_price 完成: {total} 只, 其中 {api_hits} 只命中API")
        if not frames:
            return pd.DataFrame()
        result = pd.concat(frames)
        if single:
            result.drop(columns=["stock"], inplace=True, errors="ignore")
        return result

    def _get_price_bulk_single_date(
        self, stock_list: list[str], end_date, fields: list[str],
    ) -> pd.DataFrame:
        """批量获取单日数据的快速路径：一条 SQL 读全市场，内存过滤。"""
        end_d = end_date if isinstance(end_date, dt.date) else dt.datetime.strptime(str(end_date), "%Y-%m-%d").date()
        date_str = end_d.strftime("%Y-%m-%d")

        bulk = cache.get_cached_daily_bulk(date_str)
        if bulk.empty:
            self.prefetch_daily_date(end_date)
            bulk = cache.get_cached_daily_bulk(date_str)
        if bulk.empty:
            log.warning(f"  批量查询 {date_str} 无缓存数据")
            return pd.DataFrame()

        wanted = set(stock_list)
        df = bulk[bulk["code"].isin(wanted)].copy()
        df.rename(columns={"code": "stock"}, inplace=True)
        avail = [f for f in fields if f in df.columns]
        keep = ["stock"] + avail
        return df[keep].reset_index(drop=True)

    def _get_daily_cached(
        self, code: str, end_date, count: int, *, return_api_flag: bool = False,
    ):
        """返回 (DataFrame, hit_api: bool) 当 return_api_flag=True，否则仅返回 DataFrame。"""
        from utils.trade_calendar import get_trade_days

        end_d = end_date if isinstance(end_date, dt.date) else dt.datetime.strptime(str(end_date), "%Y-%m-%d").date()
        trade_days = get_trade_days(end_d, count)
        if not trade_days:
            return (None, False) if return_api_flag else None
        start_str = trade_days[0].strftime("%Y-%m-%d")
        end_str = end_d.strftime("%Y-%m-%d")

        cached = cache.get_cached_daily(code, start_str, end_str)
        if cached is not None and len(cached) >= count:
            return (cached, False) if return_api_flag else cached

        api = _api()
        is_idx = _is_index(code)
        try:
            if is_idx:
                df = api.index_daily(
                    ts_code=code,
                    start_date=_date_str(trade_days[0]),
                    end_date=_date_str(end_d),
                )
            else:
                df = api.daily(
                    ts_code=code,
                    start_date=_date_str(trade_days[0]),
                    end_date=_date_str(end_d),
                )
        except Exception as e:
            log.error(f"Tushare {'index_daily' if is_idx else 'daily'} 获取失败 {code}: {e}")
            return (cached, True) if return_api_flag else cached

        if df is None or df.empty:
            return (cached, True) if return_api_flag else cached

        df.rename(columns={"vol": "volume", "trade_date": "date"}, inplace=True)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)

        if not is_idx:
            limit_df = self._fetch_limit_prices(code, trade_days[0], end_d)
            if limit_df is not None and not limit_df.empty:
                df = df.join(limit_df, how="left")

        cache.save_daily(code, df)
        result = df.tail(count)
        return (result, True) if return_api_flag else result

    def _fetch_limit_prices(self, code, start_date, end_date):
        try:
            api = _api()
            df = api.stk_limit(
                ts_code=code,
                start_date=_date_str(start_date),
                end_date=_date_str(end_date),
            )
            if df is not None and not df.empty:
                df.rename(columns={"trade_date": "date", "up_limit": "high_limit", "down_limit": "low_limit"}, inplace=True)
                df["date"] = pd.to_datetime(df["date"])
                df.set_index("date", inplace=True)
                return df[["high_limit", "low_limit"]]
        except Exception:
            pass
        return None

    def get_minute_price(self, stock, end_dt, count, frequency="30m", fields=None):
        freq_map = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "60m": "60min"}
        ts_freq = freq_map.get(frequency, frequency)
        try:
            api = _api()
            df = api.stk_mins(ts_code=stock, freq=ts_freq, end_date=end_dt.strftime("%Y-%m-%d %H:%M:%S"))
            if df is not None and not df.empty:
                df["trade_time"] = pd.to_datetime(df["trade_time"])
                df.set_index("trade_time", inplace=True)
                df.sort_index(inplace=True)
                df.rename(columns={"vol": "volume"}, inplace=True)
                if fields:
                    df = df[[f for f in fields if f in df.columns]]
                return df.tail(count)
        except Exception as e:
            log.warning(f"Tushare 分钟线获取失败 {stock}: {e}")
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 实时行情（Tushare 不提供真实时行情，降级为空实现）
    # ------------------------------------------------------------------
    def get_realtime_quotes(self, stocks):
        log.warning("Tushare 不支持实时行情，请配合东方财富数据源使用")
        return {}

    # ------------------------------------------------------------------
    # 估值
    # ------------------------------------------------------------------
    def get_valuation(self, stocks, date, fields):
        single = isinstance(stocks, str)
        stock_list = [stocks] if single else list(stocks)
        date_str = date if isinstance(date, str) else date.strftime("%Y-%m-%d")
        ts_date = date_str.replace("-", "")
        results = []
        for code in stock_list:
            cached = cache.get_cached_valuation(code, date_str)
            if cached:
                results.append(cached)
                continue
            try:
                api = _api()
                df = api.daily_basic(ts_code=code, trade_date=ts_date)
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    data = {
                        "code": code,
                        "pe_ratio": row.get("pe_ttm"),
                        "pb_ratio": row.get("pb"),
                        "ps_ratio": row.get("ps_ttm"),
                        "market_cap": row.get("total_mv", 0) / 10000,
                        "circulating_market_cap": row.get("circ_mv", 0) / 10000,
                        "turnover_ratio": row.get("turnover_rate"),
                    }
                    cache.save_valuation(code, date_str, data)
                    results.append(data)
            except Exception as e:
                log.error(f"Tushare 估值获取失败 {code}: {e}")
        return pd.DataFrame(results)

    # ------------------------------------------------------------------
    # 资金流向
    # ------------------------------------------------------------------
    def get_money_flow(self, stocks, start_date, end_date):
        try:
            api = _api()
            frames = []
            for code in stocks:
                df = api.moneyflow(
                    ts_code=code,
                    start_date=_date_str(start_date),
                    end_date=_date_str(end_date),
                )
                if df is not None and not df.empty:
                    df["stock"] = code
                    frames.append(df)
                time.sleep(0.12)
            return pd.concat(frames) if frames else pd.DataFrame()
        except Exception as e:
            log.error(f"Tushare 资金流向获取失败: {e}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # 集合竞价
    # ------------------------------------------------------------------
    def get_call_auction(self, stocks, date, fields=None):
        single = isinstance(stocks, str)
        stock_list = [stocks] if single else list(stocks)
        ts_date = _date_str(date)
        frames = []
        for code in stock_list:
            try:
                api = _api()
                df = api.stk_auction(ts_code=code, trade_date=ts_date)
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception:
                pass
            time.sleep(0.1)
        return pd.concat(frames) if frames else pd.DataFrame()

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
            api = _api()
            df = api.stock_basic(ts_code=stock)
            if df is not None and not df.empty:
                row = df.iloc[0]
                info_dict = {
                    "display_name": row.get("name", ""),
                    "start_date": _to_std_date(row.get("list_date", "")),
                    "end_date": "",
                    "concepts": [],
                }
                cache.save_security_info(stock, info_dict)
                return SecurityInfo(
                    code=stock,
                    display_name=info_dict["display_name"],
                    start_date=dt.datetime.strptime(info_dict["start_date"], "%Y-%m-%d").date() if info_dict["start_date"] else None,
                )
        except Exception as e:
            log.error(f"Tushare 证券信息获取失败 {stock}: {e}")
        return SecurityInfo(code=stock, display_name=stock)

    # ------------------------------------------------------------------
    # 概念
    # ------------------------------------------------------------------
    def get_concept(self, stocks, date):
        result = {}
        try:
            api = _api()
            for code in stocks:
                df = api.concept_detail(ts_code=code)
                if df is not None and not df.empty:
                    result[code] = df["concept_name"].tolist()
                else:
                    result[code] = []
                time.sleep(0.1)
        except Exception as e:
            log.error(f"Tushare 概念获取失败: {e}")
        return result

    # ------------------------------------------------------------------
    # 全部证券
    # ------------------------------------------------------------------
    def get_all_securities(self, date):
        try:
            api = _api()
            df = api.stock_basic(exchange="", list_status="L")
            if df is not None and not df.empty:
                items = []
                for _, row in df.iterrows():
                    items.append({
                        "code": row["ts_code"],
                        "display_name": row.get("name", ""),
                        "start_date": _to_std_date(row.get("list_date", "")),
                        "end_date": "",
                        "concepts": [],
                    })
                cache.batch_save_security_info(items)
                log.info(f"已批量缓存 {len(items)} 只证券信息")
                return df["ts_code"].tolist()
        except Exception as e:
            log.error(f"Tushare 全部证券获取失败: {e}")
        return []
