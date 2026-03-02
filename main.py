"""
策略主入口：启动调度器 + Tick 监控。
本地长期运行，交易日自动执行策略，非交易日休眠。
"""
import signal
import sys
import time

import config
from data import create_provider
from portfolio.tracker import PortfolioTracker
from scheduler import build_scheduler
from strategy.core import Context
from strategy.tick_monitor import TickMonitor
from utils.logger import log, setup_logging


def main():
    setup_logging(config.LOG_DIR)
    log.info("=" * 50)
    log.info("Hot Stock 本地策略系统启动")
    log.info(f"数据目录: {config.DATA_DIR}")
    log.info(f"日志目录: {config.LOG_DIR}")
    log.info(f"通知方式: {config.NOTIFY_BACKEND}")
    log.info("=" * 50)

    dp = create_provider()
    portfolio = PortfolioTracker()
    ctx = Context(dp=dp, portfolio=portfolio)
    ctx.update_time()

    log.info(f"初始资金: {portfolio.starting_cash:.2f}")
    log.info(f"当前总资产: {portfolio.total_value:.2f}")
    log.info(f"持仓数: {len(portfolio.positions)}")

    sched = build_scheduler(ctx)
    sched.start()

    tick_monitor = TickMonitor(ctx)
    tick_monitor.start()

    def _shutdown(signum, frame):
        log.info("收到退出信号，正在关闭...")
        tick_monitor.stop()
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("系统已就绪，等待交易信号...")
    log.info("按 Ctrl+C 退出")

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        _shutdown(None, None)


if __name__ == "__main__":
    main()
