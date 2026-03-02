"""
东方财富实时行情数据源。
主要职责：盘中实时快照 + tick 级别轮询推送。
历史数据方法降级到 AKShare 实现。
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Callable

import pandas as pd
import requests

import config
from data.akshare_src import AKShareProvider
from data.provider import RealtimeQuote
from utils.code_convert import ts_to_ak, ak_to_ts, ts_exchange
from utils.logger import log


def _secid(ts_code: str) -> str:
    """Tushare 代码 → 东财 secid (1.600000 / 0.000001)"""
    symbol = ts_code.split(".")[0]
    prefix = "1" if ts_exchange(ts_code) == "SH" else "0"
    return f"{prefix}.{symbol}"


def _safe_float(val, default: float = 0.0) -> float:
    """东财接口停牌股会返回 '-' 等非数字值。"""
    if val is None or val == "" or val == "-":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _display_width(s: str) -> int:
    """计算字符串的终端显示宽度（CJK 字符算 2 宽度）。"""
    w = 0
    for ch in s:
        w += 2 if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef' else 1
    return w


def _pad(s: str, width: int, align: str = "<") -> str:
    """按显示宽度填充字符串，支持左对齐(<)和右对齐(>)。"""
    pad_len = width - _display_width(s)
    if pad_len <= 0:
        return s
    if align == ">":
        return " " * pad_len + s
    return s + " " * pad_len


_RED = "\033[91m"
_GREEN = "\033[92m"
_RESET = "\033[0m"


class EastMoneyProvider(AKShareProvider):
    """
    继承 AKShareProvider 获得历史数据能力，
    重写实时行情和 tick 订阅。
    """

    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
    }
    _MAX_RETRIES = 3
    _session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(self._HEADERS)
        return self._session

    # ------------------------------------------------------------------
    # 实时行情（东财 HTTP 接口，批量获取）
    # ------------------------------------------------------------------
    def get_realtime_quotes(self, stocks: list[str]) -> dict[str, RealtimeQuote]:
        if not stocks:
            return {}
        result = self._fetch_quotes_sina(stocks)
        if not result:
            log.info("新浪行情失败，降级到东财直连")
            result = self._fetch_quotes_eastmoney(stocks)
        if not result:
            log.info("东财也失败，降级到 AKShare")
            result = super().get_realtime_quotes(stocks)
        return result

    def _fetch_quotes_eastmoney(self, stocks: list[str]) -> dict[str, RealtimeQuote]:
        secids = ",".join(_secid(s) for s in stocks)
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": "f2,f3,f4,f5,f6,f7,f12,f13,f14,f15,f16,f17,f18,f51,f52",
            "secids": secids,
        }
        session = self._get_session()
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                resp = session.get(url, params=params, timeout=10)
                data = resp.json()
                items = data.get("data", {}).get("diff", [])
                if not items:
                    return {}
                result = {}
                stock_set = set(stocks)
                for item in items:
                    sym = str(item.get("f12", "")).zfill(6)
                    market = "SH" if item.get("f13") == 1 else "SZ"
                    ts_code = f"{sym}.{market}"
                    if ts_code not in stock_set:
                        continue
                    last_p = _safe_float(item.get("f2"))
                    result[ts_code] = RealtimeQuote(
                        code=ts_code,
                        last_price=last_p,
                        day_open=_safe_float(item.get("f17")),
                        high_limit=_safe_float(item.get("f51")),
                        low_limit=_safe_float(item.get("f52")),
                        volume=_safe_float(item.get("f5")),
                        amount=_safe_float(item.get("f6")),
                        paused=(last_p == 0),
                        pre_close=_safe_float(item.get("f18")),
                        high=_safe_float(item.get("f15")),
                        low=_safe_float(item.get("f16")),
                        name=str(item.get("f14") or ""),
                        time=dt.datetime.now(),
                    )
                return result
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt < self._MAX_RETRIES:
                    wait = attempt * 1.0
                    log.warning(f"东财行情连接失败 ({attempt}/{self._MAX_RETRIES})，{wait:.0f}s 后重试")
                    time.sleep(wait)
                else:
                    log.warning(f"东财行情重试 {self._MAX_RETRIES} 次仍失败: {e}")
            except Exception as e:
                log.warning(f"东财行情解析异常: {e}")
                break
        return {}

    # ------------------------------------------------------------------
    # 新浪财经行情（独立 CDN，作为兜底）
    # ------------------------------------------------------------------
    def _fetch_quotes_sina(self, stocks: list[str]) -> dict[str, RealtimeQuote]:
        """
        新浪财经实时行情接口，与东财完全独立的 CDN。
        返回格式: var hq_str_sh600000="浦发银行,10.58,...";
        字段顺序: 名称,今开,昨收,当前价,最高,最低,买一,卖一,...,成交量(手),成交额,...,日期,时间,...
        """
        sina_codes = []
        code_map = {}
        for ts_code in stocks:
            sym = ts_code.split(".")[0]
            ex = ts_exchange(ts_code).lower()
            sina_code = f"{ex}{sym}"
            sina_codes.append(sina_code)
            code_map[sina_code] = ts_code

        url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
        result = {}
        try:
            resp = self._get_session().get(
                url, timeout=10,
                headers={**self._HEADERS, "Referer": "https://finance.sina.com.cn/"},
            )
            resp.encoding = "gbk"
            for line in resp.text.strip().splitlines():
                if "=" not in line or '="";' in line:
                    continue
                var_part, _, val_part = line.partition("=")
                sina_code = var_part.split("_")[-1]
                ts_code = code_map.get(sina_code)
                if not ts_code:
                    continue
                fields = val_part.strip('";\n').split(",")
                if len(fields) < 32:
                    continue
                name = fields[0]
                last_p = _safe_float(fields[3])
                pre_close = _safe_float(fields[2])
                if pre_close > 0:
                    hl = round(pre_close * 1.1, 2)
                    ll = round(pre_close * 0.9, 2)
                else:
                    hl, ll = 0.0, 0.0
                quote = RealtimeQuote(
                    code=ts_code,
                    last_price=last_p,
                    day_open=_safe_float(fields[1]),
                    high_limit=hl,
                    low_limit=ll,
                    volume=_safe_float(fields[8]),
                    amount=_safe_float(fields[9]),
                    paused=(last_p == 0),
                    pre_close=pre_close,
                    high=_safe_float(fields[4]),
                    low=_safe_float(fields[5]),
                    name=name,
                    time=dt.datetime.now(),
                )
                result[ts_code] = quote
            if result:
                log.info(f"新浪行情获取成功: {len(result)} 只")
        except Exception as e:
            log.warning(f"新浪行情获取失败: {e}")
        return result

    # ------------------------------------------------------------------
    # Tick 订阅（轮询模式）
    # ------------------------------------------------------------------
    def subscribe_tick(
        self,
        stocks: list[str],
        callback: Callable[[str, dict], None],
    ):
        self._tick_running = True
        self._tick_thread = threading.Thread(
            target=self._tick_loop, args=(stocks, callback), daemon=True
        )
        self._tick_thread.start()
        log.info(f"Tick 轮询已启动，监控 {len(stocks)} 只股票，间隔 {config.TICK_POLL_INTERVAL}s")

    def unsubscribe_tick(self):
        self._tick_running = False

    def _tick_loop(self, stocks: list[str], callback: Callable):
        while getattr(self, "_tick_running", False):
            try:
                quotes = self.get_realtime_quotes(stocks)
                for code, quote in quotes.items():
                    callback(code, {
                        "last_price": quote.last_price,
                        "day_open": quote.day_open,
                        "high_limit": quote.high_limit,
                        "low_limit": quote.low_limit,
                        "volume": quote.volume,
                        "amount": quote.amount,
                        "high": quote.high,
                        "low": quote.low,
                        "pre_close": quote.pre_close,
                        "time": quote.time,
                    })
            except Exception as e:
                log.error(f"Tick 轮询异常: {e}")
            time.sleep(config.TICK_POLL_INTERVAL)
