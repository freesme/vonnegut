"""
证券代码格式互转。

三种格式：
  聚宽    000300.XSHG / 000001.XSHE
  Tushare 000300.SH   / 000001.SZ
  AKShare 000300      （纯数字6位）

策略内部统一使用 Tushare 格式，仅在调用不同数据源 API 时转换。
"""

_JQ_SUFFIX_MAP = {"XSHG": "SH", "XSHE": "SZ"}
_TS_SUFFIX_MAP = {v: k for k, v in _JQ_SUFFIX_MAP.items()}


def jq_to_ts(code: str) -> str:
    """000300.XSHG → 000300.SH"""
    symbol, suffix = code.split(".")
    return f"{symbol}.{_JQ_SUFFIX_MAP.get(suffix, suffix)}"


def ts_to_jq(code: str) -> str:
    """000300.SH → 000300.XSHG"""
    symbol, suffix = code.split(".")
    return f"{symbol}.{_TS_SUFFIX_MAP.get(suffix, suffix)}"


def ts_to_ak(code: str) -> str:
    """000300.SH → 000300"""
    return code.split(".")[0]


def jq_to_ak(code: str) -> str:
    """000300.XSHG → 000300"""
    return code.split(".")[0]


def ak_to_ts(code: str, market: str | None = None) -> str:
    """
    纯数字代码 → Tushare 格式。
    market 可选 'SH'/'SZ'；若不提供则按首位数字推断。
    """
    if market:
        return f"{code}.{market}"
    if code.startswith(("6", "9", "5")):
        return f"{code}.SH"
    return f"{code}.SZ"


def ak_to_jq(code: str, market: str | None = None) -> str:
    return ts_to_jq(ak_to_ts(code, market))


def ts_exchange(code: str) -> str:
    """返回交易所标识 SH / SZ"""
    return code.split(".")[-1]


def is_index(code: str) -> bool:
    """判断是否为指数代码（Tushare 格式）。"""
    symbol = code.split(".")[0]
    return symbol.startswith(("000", "399")) and len(symbol) == 6


def batch_jq_to_ts(codes: list[str]) -> list[str]:
    return [jq_to_ts(c) for c in codes]


def batch_ts_to_jq(codes: list[str]) -> list[str]:
    return [ts_to_jq(c) for c in codes]
