"""
策略主入口：启动调度器 + Tick 监控 + FastAPI 服务。
本地长期运行，交易日自动执行策略，非交易日休眠。
API 文档: http://<host>:<port>/docs
"""
import signal
import sys
import threading
import time

import uvicorn

import config
from api.app import create_app
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

    # FastAPI 嵌入式服务（daemon 线程，随主进程退出）
    app = create_app(ctx, scheduler=sched)
    api_thread = threading.Thread(
        target=uvicorn.run,
        kwargs={
            "app": app,
            "host": config.API_HOST,
            "port": config.API_PORT,
            "log_level": "warning",
        },
        daemon=True,
    )
    api_thread.start()
    log.info(f"API 服务已启动: http://{config.API_HOST}:{config.API_PORT}/docs")

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
