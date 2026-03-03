"""
策略全局配置。
所有可调参数集中管理，运行时通过 config 模块引用。
"""
import os
from pathlib import Path

# 自动加载 .env 文件（无需额外依赖）
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data_store"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
PORTFOLIO_PATH = DATA_DIR / "portfolio.json"

# ---------------------------------------------------------------------------
# 数据库 (PostgreSQL)
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://hotstock:hotstock123@localhost:5432/hotstock_cache",
)

# ---------------------------------------------------------------------------
# 数据源 API Token
# ---------------------------------------------------------------------------
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
# AKShare / 东方财富 无需 token

# ---------------------------------------------------------------------------
# 策略参数（对应原 initialize 中 g.xxx）
# ---------------------------------------------------------------------------
POSITION_LIMIT = 5          # 最大持仓数量
MIN_SCORE = 14              # 最低评分阈值
CONCEPT_NUM = 8             # 每日热点概念最大个数
CACHE_MAX_DAYS = 5          # 热门概念缓存天数
MAX_SINGLE_POSITION = 0.30  # 单票仓位上限（占总资产）
INITIAL_CASH = 10_000.0    # 初始虚拟资金（用于持仓跟踪）

# ---------------------------------------------------------------------------
# 通知配置
# ---------------------------------------------------------------------------
NOTIFY_BACKEND = os.environ.get("NOTIFY_BACKEND", "console")  # console / serverchan / dingtalk
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")
DINGTALK_WEBHOOK = os.environ.get("DINGTALK_WEBHOOK", "")

# ---------------------------------------------------------------------------
# Tick 监控
# ---------------------------------------------------------------------------
TICK_POLL_INTERVAL = 3      # tick 轮询间隔（秒）
TICK_RAPID_DROP_PCT = 0.03  # 急跌止损阈值（1分钟内跌幅）
TICK_VOLUME_DROP_PCT = 0.06 # 放量大跌跌幅阈值
TICK_VOLUME_RATIO = 1.5     # 放量判定倍数

# ---------------------------------------------------------------------------
# API 服务
# ---------------------------------------------------------------------------
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8000"))

# ---------------------------------------------------------------------------
# 证券代码格式（策略内部统一使用 tushare 格式: 000300.SH）
# ---------------------------------------------------------------------------
CODE_FORMAT = "tushare"

# ---------------------------------------------------------------------------
# 调度时间表（交易日内）
# ---------------------------------------------------------------------------
SCHEDULE = {
    "record_morning_stats": "09:25",
    "get_stock_list": "09:28:00",
    "sell_limit_down": "09:28:00",
    "buy_morning": "09:28:10",
    "buy_afternoon": "14:50:00",
    "record_closing_stats": "15:00",
    "log_daily_trades": "15:05",
    "sell2_times": ["10:31", "11:01", "13:31", "14:01", "14:31", "14:50"],
    "sell_per5min_am": {"start": "09:36", "end": "10:30", "interval": 5},
    "sell_per5min_pm": {"start": "13:05", "end": "14:45", "interval": 5},
}
