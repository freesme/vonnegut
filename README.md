# Hot Stock 本地量化交易信号系统

A 股短线涨停板策略，从聚宽平台迁移至本地独立运行。自动选股、评分、生成买卖信号，支持 tick 级别实时卖出监控。

## 策略概述

五合一打板策略，基于 5 类选股模式 + 6 因子评分系统 + 三层卖出体系：

| 选股模式 | 逻辑 |
|---------|------|
| 连板龙头 | 最高连板数的龙头股，集合竞价高开 |
| 弱转强 | 昨日曾涨停未封，今日竞价强势 |
| 一进二 | 首板次日高开 1%~6%，量比适中 |
| 首板低开 | 首板次日低开 3%，相对低位 |
| 反向首板低开 | 昨日跌停后反弹高开 |

评分因子：涨停(0-5) + 技术(0-10) + 放量MA(0-5) + 主线概念(0-5) + 情绪(0-5) + 主力资金(0-10)，满分 40，阈值 14。

## 项目结构

```
SolutionDemo/
├── main.py                 # 入口：启动调度器 + Tick 监控 + API 服务（长期驻留）
├── scan.py                 # 盘中选股 CLI（随时手动调用）
├── config.py               # 全局配置（自动加载 .env）
├── scheduler.py            # APScheduler 定时任务调度
├── api/                    # FastAPI 接口层
│   ├── app.py              # FastAPI 应用工厂
│   ├── schemas.py          # Pydantic 请求/响应模型
│   └── routers/            # 持仓、交易、选股、市场等路由
├── strategy/
│   ├── core.py             # GlobalState + Context + 工具函数
│   ├── stock_select.py     # 5 类模式选股
│   ├── scoring.py          # 6 因子评分系统
│   ├── sell_rules.py       # 三层卖出规则
│   ├── buy.py              # 买入逻辑（含周五分支）
│   └── tick_monitor.py     # tick 级别实时卖出监控
├── data/
│   ├── provider.py         # 统一数据接口（抽象层）
│   ├── tushare_src.py      # Tushare Pro 实现
│   ├── akshare_src.py      # AKShare 实现（免费）
│   ├── eastmoney_src.py    # 东方财富实时行情 + tick
│   ├── composite.py        # Tushare + 东财组合数据源
│   └── cache.py            # SQLite 本地数据缓存
├── portfolio/
│   ├── models.py           # Position / TradeRecord 数据模型
│   └── tracker.py          # 本地持仓跟踪（JSON 持久化）
├── notify/
│   ├── signal.py           # 信号生成与格式化
│   └── push.py             # 推送：终端 / Server酱 / 钉钉
├── utils/
│   ├── logger.py           # 标准 logging
│   ├── code_convert.py     # 证券代码格式互转
│   └── trade_calendar.py   # 交易日历（AKShare + SQLite 缓存）
├── hot_stock.py            # 原聚宽版本（归档参考）
├── requirements.txt
├── .env.example
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入你的配置：

```env
# Tushare token（可选，不填则使用 AKShare + 东方财富）
TUSHARE_TOKEN=你的token

# 通知方式：console / serverchan / dingtalk
NOTIFY_BACKEND=console

# API 服务（可选，默认 8000）
# API_HOST=0.0.0.0
# API_PORT=8000
```

### 3. 启动与运行

**主程序（常驻模式）** — 同时启动策略调度、Tick 监控与 API 服务：

```bash
python main.py
```

启动成功后终端会输出：

- 数据目录、日志目录、通知方式
- 初始资金、当前总资产、持仓数
- 调度器任务数量
- **API 服务地址**：`http://0.0.0.0:8000/docs`（Swagger 文档）

按 `Ctrl+C` 可正常退出（会关闭调度器、Tick 监控与 API）。

**仅手动选股** — 不启动常驻服务，只执行一次选股扫描：

```bash
python scan.py
python scan.py --min-score 16
python scan.py --notify
```

### 4. API 服务

运行 `python main.py` 后，内置 FastAPI 服务默认监听 **8000** 端口，可通过 HTTP 查询与操作数据：

| 能力 | 说明 |
|------|------|
| 交互文档 | 浏览器打开 [http://localhost:8000/docs](http://localhost:8000/docs) 查看并调试所有接口 |
| 持仓 | `GET /api/portfolio` 账户总览，`GET /api/portfolio/positions` 持仓明细，`POST /api/portfolio/buy`、`POST /api/portfolio/sell` 模拟买卖 |
| 交易 | `GET /api/trades/signals` 今日信号，`GET /api/trades/history` 历史交易记录 |
| 选股 | `POST /api/scan` 触发一次选股扫描 |
| 市场 | `GET /api/market/stats` 市场趋势，`GET /api/market/strategy` 策略优先级，`GET /api/market/scheduler` 调度器状态 |

修改端口或绑定地址可在 `.env` 中设置 `API_HOST`、`API_PORT`，或在 `config.py` 中查看默认值。

## Docker 部署

### 1. 准备环境变量

```bash
cp .env.example .env
```

按需编辑 `.env`（如 `TUSHARE_TOKEN`、通知推送配置）。

### 2. 构建并启动（常驻模式）

```bash
docker compose up -d --build
```

若拉取 `python:3.11-slim` 失败（如 `failed to fetch oauth token`），可临时指定基础镜像地址：

```bash
BASE_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.11-slim docker compose up -d --build
```

也可以在 Docker Desktop 的 Engine 配置里增加 `registry-mirrors`，再重启 Docker。

### 3. 查看日志

```bash
docker compose logs -f trader
```

### 4. 停止

```bash
docker compose down
```

说明：

- `./data_store` 挂载到容器 `/app/data_store`，用于持仓与缓存持久化。
- `./logs` 挂载到容器 `/app/logs`，用于查看策略日志。
- **API 端口**：默认映射 `8000:8000`（可在 `.env` 中设置 `API_PORT` 修改），启动后可通过 `http://localhost:8000/docs` 访问接口文档。
- 手动运行一次扫描可用：`docker compose run --rm trader python scan.py --notify`。

## 使用方式

### 常驻模式（main.py）

启动后系统自动按以下时间表执行：

| 时间 | 动作 |
|------|------|
| 09:25 | 盘前统计，判断市场趋势，更新策略优先级 |
| 09:28 | 生成选股池（5 类模式） |
| 09:28 | 竞价卖出检测（涨停低开、放量长上影） |
| 09:28:10 | 早盘买入信号（周五仅筛选） |
| 09:36-10:30 | 每 5 分钟技术止损检测 |
| 10:31-14:50 | 每 15 分钟策略卖出（互斥） |
| 13:05-14:45 | 每 5 分钟技术止损检测 |
| 14:50 | 周五下午建仓信号 |
| 15:00 | 盘后统计 |
| 全天 | Tick 监控（3 秒轮询持仓股） |

非交易日自动休眠，无需手动干预。

### 手动选股（scan.py）

```python
# Python 代码调用
from scan import quick_scan

results = quick_scan()
for r in results:
    print(f"{r['name']}({r['stock']}) {r['pattern']} {r['total_score']}分")
```

输出格式：

```
===========================================================================
  选股结果  |  趋势: up  |  波动率: 1.23%  |  量能比: 1.35
===========================================================================
  序号  代码         名称     模式     总分 涨停 技术 量MA 主线 情绪 资金
---------------------------------------------------------------------------
   1.  600xxx.SH    某某股份 连板龙头   22    5    4    3    4    2    4
   2.  000xxx.SZ    某某科技 弱转强     18    3    5    2    2    3    3
===========================================================================
```

### 持仓管理

系统输出买卖信号后，你手动操作券商下单，然后确认更新本地持仓：

```python
from portfolio.tracker import PortfolioTracker

pt = PortfolioTracker()

# 确认买入
pt.confirm_buy("600xxx.SH", price=25.30, quantity=400, reason="连板龙头")

# 确认卖出
pt.confirm_sell("600xxx.SH", price=27.80, reason="止损卖出")

# 查看持仓
for code, pos in pt.positions.items():
    print(f"{code}: {pos.total_amount}股, 成本{pos.avg_cost:.2f}, 盈亏{pos.profit_pct:.2%}")
```

持仓数据自动保存到 `data_store/portfolio.json`。

## 数据源

| 数据类型 | 有 Tushare Token | 无 Token |
|---------|-----------------|---------|
| 历史 K 线 | Tushare | AKShare |
| 实时行情 | 东方财富 | AKShare / 东方财富 |
| Tick 数据 | 东方财富轮询 | 东方财富轮询 |
| 估值数据 | Tushare | - |
| 资金流向 | Tushare | AKShare |
| 概念板块 | Tushare | AKShare |
| 集合竞价 | Tushare | - |

历史数据自动缓存到本地 SQLite（`data_store/cache.db`），同一数据只拉取一次。

## 通知方式

在 `.env` 中设置 `NOTIFY_BACKEND`：

- **console** — 终端打印（默认）
- **serverchan** — 微信推送（需配置 `SERVERCHAN_KEY`，[申请](https://sct.ftqq.com/)）
- **dingtalk** — 钉钉群机器人（需配置 `DINGTALK_WEBHOOK`）

## 风控机制

- 最大持仓 5 只，单票上限 30% 总资产
- 涨停不买、跌停不卖（实盘保护）
- T+1 保护（当天买入不卖出）
- 大盘连续 2 日量能异常时全部清仓
- 三层卖出：竞价卖出 → 5 分钟技术止损 → 15 分钟策略卖出（互斥）
- Tick 级别监控：急跌止损、放量大跌、涨停打开

## 配置参数

核心参数在 `config.py` 中，可按需调整：

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `POSITION_LIMIT` | 5 | 最大持仓数 |
| `MIN_SCORE` | 14 | 评分最低阈值 |
| `MAX_SINGLE_POSITION` | 0.30 | 单票仓位上限 |
| `INITIAL_CASH` | 10,000 | 初始虚拟资金 |
| `API_HOST` | 0.0.0.0 | API 监听地址（环境变量 `API_HOST`） |
| `API_PORT` | 8000 | API 端口（环境变量 `API_PORT`） |
| `TICK_POLL_INTERVAL` | 3 | tick 轮询间隔（秒） |
| `TICK_RAPID_DROP_PCT` | 0.03 | 急跌止损阈值 |

## 源自

基于聚宽社区策略 [通过获取同花顺热门概念数据加强五合一打板策略](https://www.joinquant.com/post/67455)（作者：qaz1912），重构为本地独立运行版本。
