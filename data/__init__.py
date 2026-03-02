"""
数据层入口。
根据配置创建合适的 DataProvider 组合。
"""
from data.provider import DataProvider, RealtimeQuote, SecurityInfo


def create_provider() -> DataProvider:
    """
    工厂函数：返回最优数据源。
    优先使用东方财富（继承 AKShare + 实时行情），
    如有 Tushare token 则历史数据走 Tushare。
    """
    import config

    if config.TUSHARE_TOKEN:
        from data.composite import CompositeProvider
        return CompositeProvider()

    from data.eastmoney_src import EastMoneyProvider
    return EastMoneyProvider()
