# 克隆自聚宽文章：https://www.joinquant.com/post/67455
# 标题：通过获取同花顺热门概念数据加强五合一打板策略
# 作者：qaz1912

from jqdata import *
from jqfactor import *
from jqlib.technical_analysis import *
import datetime as dt
import pandas as pd
import requests
import time
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import json

# ============================================================================
# 1. 初始化函数、日志函数
# ========================================================================
def initialize(context):
    set_option('use_real_price', True)
    log.set_level('system', 'error')
    set_option('avoid_future_data', True)

    g.is_empty = False

    # 分仓数量
    g.position_limit = 5  # 最大持仓数量，可配置
    # 聚宽因子
    g.jqfactor = 'VOL5'  # 5日平均换手率（只是做为示例）
    g.sort = True  # 选取因子值最小
    g.emo_count = []
    g.gap_up = []
    g.gap_down = []
    g.reversal = []
    g.fxsbdk = []
    g.lblt = []
    g.hot_concepts_cache = []
    g.cache_max_days = 5
    g.min_score = 14
    g.qualified_stocks = []
    g.lblt_stocks = []
    g.rzq_stocks = []
    g.gk_stocks = []
    g.dk_stocks = []
    g.fxsbdk_stocks = []
    g.last_trade_info = None
    g.score_cache = {}  # 存储股票评分结果
    g.concept_num = 8 # 缓每日热点概念最大个数
    g.priority_config = []

    # 添加日志统计变量
    g.trade_stats = {
        'daily_returns': [],  # 每日收益
        'position_stats': {},  # 持仓统计
        'market_stats': {},  # 市场统计
        'trade_details': []  # 交易明细
    }

    # 定时任务
    run_daily(record_morning_stats, '09:25')  # 盘前数据统计
    run_daily(record_closing_stats, '15:00')  # 盘后数据统计
    run_daily(get_stock_list, '09:28:00')
    run_daily(buy, '09:28:10')
    run_daily(buy, '14:50:00')  # 周五下午建仓
    run_daily(sell_limit_down, time='09:28', reference_security='000300.XSHG')
    run_daily(log_daily_trades, '15:05')  # 每日15:05记录当日交易

    # 优化：sell2函数调度（9:31~14:56每15分钟一次，避开竞价时间）
    sell2_times = [
        # 上午时段
        "10:31", "11:01",
        # 下午时段（跳过午间休市）
        "13:31", "14:01", "14:31", "14:50"  # 最后一次为14:50（替代15:00）
    ]
    for time_str in sell2_times:
        run_daily(sell2, time=time_str, reference_security='000300.XSHG')

    # 优化：使用循环设置每5分钟检测任务
    # 上午时间段：09:30-10:30
    for hour in range(9, 11):
        start_minute = 36 if hour == 9 else 0  # 9点从30分开始，10点从0分开始
        end_minute = 60 if hour == 9 else 35  # 9点到60分，10点到30分结束

        for minute in range(start_minute, end_minute, 5):
            time_str = f"{hour:02d}:{minute:02d}"
            run_daily(sell_limit_per5min, time=time_str, reference_security='000300.XSHG')

    # 下午时间段：13:05-14:45
    for hour in range(13, 15):
        start_minute = 5 if hour == 13 else 0  # 13点从5分开始，14点从0分开始
        end_minute = 60 if hour == 13 else 50  # 13点到60分，14点到45分结束

        for minute in range(start_minute, end_minute, 5):
            time_str = f"{hour:02d}:{minute:02d}"
            run_daily(sell_limit_per5min, time=time_str, reference_security='000300.XSHG')

# 根据市场环境更新策略优先级
def update_strategy_priority(trend):
    """根据市场趋势更新策略优先级"""
    # 根据分析结果设置不同市场环境下的策略优先级
    if trend == 'down':
        # 下跌市场: 反向首板低开(100%)、一进二(67%)、弱转强(0%)
        g.priority_config = ["lb", "fxsbdk", "yje", "rzq",  "dk"]
    elif trend == 'strong_up':
        # 强势上涨市场: 连板龙头(56%)、弱转强(50%)、一进二(50%)
        g.priority_config = ["lb", "rzq", "yje", "fxsbdk", "dk"]
    elif trend == 'flat':
        # 平稳市场: 连板龙头(67%)、一进二(41%)
        g.priority_config = ["lb", "rzq", "yje", "fxsbdk", "dk"]
    elif trend == 'up':
        # 上涨市场: 一进二(40%)、连板龙头(35%)
        g.priority_config = ["yje", "lb", "rzq", "fxsbdk", "dk"]
    else:
        # 默认优先级
        g.priority_config = ["lb", "rzq","yje", "dk", "fxsbdk"]
    # 存储当前策略优先级
    g.trade_stats['strategy_priority'] = {
        'trend': trend,
        'priority': g.priority_config
    }
    log.info(f"根据市场趋势 [{trend}] 更新策略优先级: {' > '.join(g.priority_config)}")


def log_daily_trades(context):
    """
    记录每日交易日志
    """
    try:
        if not hasattr(g, 'today_trades'):
            log.info("今日无交易")
            return

        log.info("\n==== 今日交易总结 ====")
        
        # 统计交易情况
        total_trades = len(g.today_trades)
        buy_trades = [trade for trade in g.today_trades if trade['action'] == '买入']
        sell_trades = [trade for trade in g.today_trades if trade['action'] == '卖出']
        
        log.info(f"总交易数: {total_trades}")
        log.info(f"买入交易: {len(buy_trades)}")
        log.info(f"卖出交易: {len(sell_trades)}")
        
        # 计算总体盈亏
        total_profit_pct = sum(trade.get('profit_pct', 0) for trade in sell_trades) / len(sell_trades) if sell_trades else 0
        log.info(f"平均盈亏: {total_profit_pct:.2%}")
        
        # 详细交易记录
        for trade in g.today_trades:
            log.info(f"{trade['stock']} - {trade['action']} - 价格: {trade['price']:.2f} - 原因: {trade.get('reason', '无')}")
        
        log.info("==== 交易总结结束 ====\n")
        
        # 重置今日交易记录
        g.today_trades = []
    
    except Exception as e:
        log.error(f"记录每日交易日志失败: {str(e)}")

# ============================================================================
# 2. 概念筛选&缓存主函数、盘前数据统计函数
# ==========================================================================
def standardize_concept_columns(df):
    """标准化概念数据的列名，仅保留聚宽概念名称列"""
    try:
        # 仅保留"聚宽概念名称"到concept_name的映射，完全忽略"概念名称"（同花顺）
        column_mapping = {
            '聚宽概念名称': 'concept_name',  # 只映射聚宽概念列
            # 其他必要列映射保持不变
            '热度评分': 'heat_score',
            'heat_score': 'heat_score',
            '股票数量': 'stock_count',
            'stock_count': 'stock_count',
            '涨跌幅': 'avg_change',
            'avg_change': 'avg_change'
        }

        # 重命名列（仅处理存在的列）
        df_renamed = df.rename(columns={
            k: v for k, v in column_mapping.items() 
            if k in df.columns
        })

        # 日志：明确记录聚宽概念列的映射情况
        if '聚宽概念名称' in df.columns:
            log.info(f"已将'聚宽概念名称'映射为'concept_name'（仅保留聚宽概念）")
        else:
            log.warning(f"原始文件中未找到'聚宽概念名称'列，可用列: {list(df.columns)}")

        return df_renamed

    except Exception as e:
        log.error(f"标准化列名失败: {str(e)}")
        return df





def check_cache_status():
    """
    检查缓存状态
    """
    try:
        if not hasattr(g, 'cache_initialized'):
            return "未初始化"

        if not g.cache_initialized:
            return "初始化失败"

        if not hasattr(g, 'hot_concept_cache') or not g.hot_concept_cache:
            return "缓存为空"

        cache_count = len(g.hot_concept_cache)
        return f"正常({cache_count}个文件)"

    except Exception as e:
        return f"检查失败: {str(e)}"


def calculate_mainline_score_optimized(stock, context):
    """
    优化的主线评分计算函数，匹配概念去重后按个数计分
    参数:
        stock: 股票代码
        context: 上下文对象
    返回:
        主线评分（匹配1个概念得2分，数量越多分数越高）
    """
    try:
        # # 获取热门概念列表（确保是字符串列表）
        # hot_concepts = get_all_hot_concepts_optimized(context)
        # hot_concepts_set = set(hot_concepts)  # 转为集合提高查询效率
        
        # 获取热门概念列表（确保是字符串列表）
        hot_concepts_result = get_all_hot_concepts_optimized(context)
        # 提取所有热门概念的名称列表
        hot_concepts_list = [concept['name'] for concept in hot_concepts_result['all_concepts']]
        hot_concepts_set = set(hot_concepts_list)  # 转为集合提高查询效率

        print(hot_concepts_set)
        log.info(f"==== 主线评分计算 - 缓存检查 ====")
        log.info(f"热门概念数量: {len(hot_concepts_set)}")
        log.info(f"部分热门概念示例: {list(hot_concepts_set)[:10]}...")

        if not hot_concepts_set:
            log.warning("热门概念列表为空，无法计算主线评分")
            return 0

        # 获取股票所属概念（提取概念名称，转为字符串列表）
        stock_info = get_security_info(stock)
        if not stock_info or not stock_info.concepts:
            log.info(f"股票 {stock} 无所属概念")
            return 0


        # 从概念字典中提取'name'字段，得到字符串列表
        stock_concepts = [concept['name'] for concept in stock_info.concepts]
        log.info(f"股票 {stock} 所属概念名称: {stock_concepts}")

        # 检查是否有匹配的热门概念，并去重
        matched_concepts = [c for c in stock_concepts if c in hot_concepts_set]
        unique_matched = list(set(matched_concepts))  # 去重处理
        match_count = len(unique_matched)  # 去重后的匹配数量


        # 按匹配数量计算分数（1个概念得2分，数量越多分数越高）
        mainline_score = match_count * 2 if match_count > 0 else 0

        # 输出评分日志
        if match_count > 0:
            log.info(f"股票 {stock} 匹配到热门概念（去重后）: {unique_matched}, "
                     f"匹配数量: {match_count}, 主线评分: {mainline_score}")
        else:
            log.info(f"股票 {stock} 未匹配到热门概念，主线评分: {mainline_score}")

        log.info("----------------------------------------")
        return mainline_score

    except Exception as e:
        log.error(f"计算主线评分时出错: {str(e)}")
        import traceback
        log.error(traceback.format_exc())
        return 0


def get_all_hot_concepts_optimized(context=None, days=5):
    """
    获取最近N个交易日（包括今日）的热门概念列表，优先从缓存中获取
    
    参数:
        context: 策略上下文，用于获取当前日期
        days: 获取最近N个交易日的数据，默认5天
    
    返回:
        dict: {
            'all_concepts': 所有交易日的热门概念列表（去重）,
            'by_date': 按日期分组的热门概念字典,
            'summary': 统计信息
        }
    """
    try:
        # 处理日期参数
        if context is None:
            from datetime import datetime
            end_date = datetime.now()
            log.warning("未提供context，使用系统当前日期")
        else:
            end_date = context.current_dt
        
        log.info(f"开始获取最近{days}个交易日的热门概念，截止日期: {end_date.strftime('%Y-%m-%d')}")

        # 初始化缓存结构
        if not hasattr(g, 'hot_concepts_data_cache'):
            g.hot_concepts_data_cache = {}
            log.info("初始化热门概念缓存 g.hot_concepts_data_cache")
        
        if not hasattr(g, 'hot_concepts_api_called'):
            g.hot_concepts_api_called = {}
        
        # ========== 获取最近N个交易日 ==========
        try:
            trade_days = get_trade_days(end_date=end_date, count=days)
            
            # 判断是否为空
            if trade_days is None or (hasattr(trade_days, '__len__') and len(trade_days) == 0):
                log.warning("未获取到有效交易日")
                return {
                    'all_concepts': [],
                    'by_date': {},
                    'summary': {'total_unique_concepts': 0, 'total_dates': 0, 'date_range': 'N/A'}
                }
            
            # 转换为字符串列表（处理 numpy.datetime64 类型）
            trade_days_str = []
            for td in trade_days:
                if isinstance(td, str):
                    # 已经是字符串
                    trade_days_str.append(td)
                else:
                    # numpy.datetime64 或 datetime 对象，转换为字符串
                    trade_days_str.append(pd.Timestamp(td).strftime('%Y-%m-%d'))
            
            log.info(f"获取到{len(trade_days_str)}个交易日: {trade_days_str}")
            
        except Exception as e:
            log.error(f"获取交易日失败: {str(e)}")
            import traceback
            log.error(f"错误详情: {traceback.format_exc()}")
            return {
                'all_concepts': [],
                'by_date': {},
                'summary': {'total_unique_concepts': 0, 'total_dates': 0, 'date_range': 'N/A'}
            }
        
        # ========== 遍历每个交易日，获取热门概念 ==========
        all_concepts_dict = {}  # 用于去重，key为概念代码
        concepts_by_date = {}   # 按日期分组
        
        for trade_day_str in trade_days_str:
            # trade_day_str 格式: '2025-08-01' (字符串)
            # 转换为API需要的格式: '20250801'
            date_str_api = trade_day_str.replace('-', '')  # '20250801'
            date_key = trade_day_str  # '2025-08-01' 用于展示
            
            # 获取该日期的热门概念
            daily_concepts = _get_hot_concepts_for_date(date_str_api, context)
            
            # 确保返回的是列表格式
            if daily_concepts and isinstance(daily_concepts, list):
                concepts_by_date[date_key] = daily_concepts
                
                # 合并到总列表（去重并统计）
                for concept in daily_concepts:
                    # 确保 concept 是字典类型
                    if not isinstance(concept, dict):
                        log.warning(f"跳过非字典类型的概念数据: {concept}")
                        continue
                    
                    concept_code = concept.get('code')
                    concept_name = concept.get('name', '')
                    
                    # 跳过无效数据
                    if not concept_code:
                        log.warning(f"跳过无效概念代码: {concept}")
                        continue
                    
                    if concept_code not in all_concepts_dict:
                        all_concepts_dict[concept_code] = {
                            'code': concept_code,
                            'name': concept_name,
                            'first_seen': date_key,  # 首次出现日期
                            'last_seen': date_key,   # 最后出现日期
                            'appearances': 1,        # 出现次数
                            'dates': [date_key]      # 出现的日期列表
                        }
                    else:
                        all_concepts_dict[concept_code]['last_seen'] = date_key
                        all_concepts_dict[concept_code]['appearances'] += 1
                        all_concepts_dict[concept_code]['dates'].append(date_key)
                
                log.info(f"✓ {date_key} 获取到 {len(daily_concepts)} 个热门概念")
            else:
                log.warning(f"⚠ {date_key} 未获取到热门概念")
        
        # ========== 整理结果 ==========
        all_concepts_list = list(all_concepts_dict.values())
        
        # 按出现次数排序（出现次数多的排在前面，次数相同则按最后出现日期排序）
        all_concepts_list.sort(key=lambda x: (-x['appearances'], x['last_seen']), reverse=False)
        
        result = {
            'all_concepts': all_concepts_list,
            'by_date': concepts_by_date,
            'summary': {
                'total_unique_concepts': len(all_concepts_list),
                'total_dates': len(concepts_by_date),
                'date_range': f"{trade_days_str[0]} 至 {trade_days_str[-1]}" if len(trade_days_str) > 0 else 'N/A',
                'trade_days': trade_days_str  # 使用字符串列表
            }
        }
        
        # ========== 输出统计信息 ==========
        log.info(f"=" * 60)
        log.info(f"📊 热门概念统计（最近{days}个交易日）")
        log.info(f"  日期范围: {result['summary']['date_range']}")
        log.info(f"  总计唯一概念数: {result['summary']['total_unique_concepts']}")
        log.info(f"  有效交易日数: {result['summary']['total_dates']}")
        
        # 输出高频概念（出现3次及以上）
        high_freq_concepts = [c for c in all_concepts_list if c['appearances'] >= 3]
        if high_freq_concepts:
            log.info(f"  高频概念（出现≥3次）: {len(high_freq_concepts)}个")
            for concept in high_freq_concepts:  #high_freq_concepts[:5]:
                log.info(f"    - {concept['name']}({concept['code']}): 出现{concept['appearances']}次, 日期{concept['dates']}")
        
        # 输出每日概念数量
        if concepts_by_date:
            log.info(f"  每日概念数量:")
            for date_key in sorted(concepts_by_date.keys(), reverse=True):
                concepts = concepts_by_date[date_key]
                concept_names = [c.get('name', '') for c in concepts if isinstance(c, dict)]
                log.info(f"    {date_key}: {len(concepts)}个 - {', '.join(concept_names)}")
        
        log.info(f"=" * 60)
        
        return result
        
    except Exception as e:
        log.error(f"获取热门概念时出错: {str(e)}")
        import traceback
        log.error(f"错误详情: {traceback.format_exc()}")
        return {
            'all_concepts': [],
            'by_date': {},
            'summary': {'total_unique_concepts': 0, 'total_dates': 0, 'date_range': 'N/A'}
        }


def _get_hot_concepts_for_date(date_str, context=None):
    """
    获取指定日期的热门概念（内部函数）
    
    参数:
        date_str: 日期字符串，格式为YYYYMMDD（如 '20250801'）
        context: 策略上下文
    
    返回:
        热门概念列表，每个元素为 {'code': xxx, 'name': xxx, 'stock_list': []}
        如果没有数据，返回空列表 []
    """
    try:
        # ========== 第一层：检查缓存 ==========
        if date_str in g.hot_concepts_data_cache:
            cache_data = g.hot_concepts_data_cache[date_str]
            
            # 处理列表格式缓存
            if isinstance(cache_data, list):
                if len(cache_data) > 0:
                    # log.debug(f"  从缓存获取 {date_str} 的热门概念: {len(cache_data)}个")
                    return cache_data
                else:
                    # 缓存为空列表，检查是否已调用过API
                    if g.hot_concepts_api_called.get(date_str, False):
                        log.debug(f"  {date_str} 缓存为空且已调用过API，跳过")
                        return []
            
            # 处理DataFrame格式缓存
            elif isinstance(cache_data, pd.DataFrame):
                if not cache_data.empty:
                    hot_concepts = []
                    for _, row in cache_data.iterrows():
                        concept_code = row.get('jq_concept_code')
                        concept_name = row.get('jq_concept_name', '')
                        stock_list = row.get('stock_list', [])
                        
                        if pd.notna(concept_code) and concept_code != '':
                            hot_concepts.append({
                                'code': str(concept_code),
                                'name': str(concept_name),
                                'stock_list': stock_list  # 新增：股票列表
                            })
                    
                    # 更新缓存为列表格式（优化后续访问）
                    g.hot_concepts_data_cache[date_str] = hot_concepts
                    # log.debug(f"  从缓存转换 {date_str} 的热门概念: {len(hot_concepts)}个")
                    return hot_concepts
                else:
                    if g.hot_concepts_api_called.get(date_str, False):
                        log.debug(f"  {date_str} 缓存DataFrame为空且已调用过API，跳过")
                        return []
        
        # ========== 第二层：检查API调用标记 ==========
        if g.hot_concepts_api_called.get(date_str, False):
            log.debug(f"  {date_str} 已调用过API但无数据，跳过重复调用")
            return []
        
        # ========== 第三层：从API获取 ==========
        # log.info(f"  → 从API获取 {date_str} 的热门概念")
        
        # 标记已调用API（防止重复调用）
        g.hot_concepts_api_called[date_str] = True
        
        try:
            concept_mapper = ConceptMapper()
            hot_concepts_df = fetch_and_map_hot_concepts(date_str, concept_mapper)
            
            # 检查返回结果
            if hot_concepts_df is None or not isinstance(hot_concepts_df, pd.DataFrame) or hot_concepts_df.empty:
                log.debug(f"  {date_str} API返回空数据")
                g.hot_concepts_data_cache[date_str] = []
                return []
            
            # 提取概念代码、名称和股票列表（确保返回标准格式）
            hot_concepts = []
            for _, row in hot_concepts_df.iterrows():
                concept_code = row.get('jq_concept_code')
                concept_name = row.get('jq_concept_name', '')
                stock_list = row.get('stock_list', [])
                
                if pd.notna(concept_code) and concept_code != '':
                    hot_concepts.append({
                        'code': str(concept_code),      # 确保是字符串
                        'name': str(concept_name),      # 确保是字符串
                        'stock_list': stock_list        # 新增：股票列表
                    })
            
            # 缓存结果（统一为列表格式）
            g.hot_concepts_data_cache[date_str] = hot_concepts
            
            if len(hot_concepts) > 0:
                total_stocks = sum(len(c.get('stock_list', [])) for c in hot_concepts)
                log.info(f"  ✓ {date_str} 从API获取并缓存 {len(hot_concepts)} 个热门概念 (共{total_stocks}只涨停股)")
                log.debug(f"    概念: {[c['name'] for c in hot_concepts]}")
            else:
                log.warning(f"  ⚠ {date_str} API返回数据中没有有效概念代码")
            
            return hot_concepts
            
        except Exception as api_error:
            log.error(f"  {date_str} API调用失败: {str(api_error)}")
            import traceback
            log.error(f"  错误详情: {traceback.format_exc()}")
            g.hot_concepts_data_cache[date_str] = []
            return []
    
    except Exception as e:
        log.error(f"获取 {date_str} 热门概念时出错: {str(e)}")
        import traceback
        log.error(f"错误详情: {traceback.format_exc()}")
        return []


def fetch_and_map_hot_concepts(date_str, concept_mapper):
    """
    从同花顺API获取指定日期的热门概念数据，并映射到聚宽概念
    
    参数:
        date_str: 日期字符串，格式为YYYYMMDD
        concept_mapper: 概念映射器实例

    返回:
        处理后的概念数据DataFrame，按热度排序，只包含有效聚宽概念的前8个
        每个概念包含对应的stock_list（涨停股票列表）
        如果没有数据，返回空DataFrame
    """
    try:
        # 调用同花顺API获取热门概念数据
        url = "https://data.10jqka.com.cn/dataapi/limit_up/block_top"

        # 设置请求参数
        params = {
            "filter": "HS,GEM2STAR",
            "date": date_str
        }

        # 设置请求头
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Referer": "https://data.10jqka.com.cn/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
        }

        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()  # 检查请求是否成功
        data = response.json()

        if (data.get("status_code") == 0 and
                "data" in data and
                isinstance(data["data"], list) and
                len(data["data"]) > 0):

            concepts_raw = data["data"]
            # log.info(f"从同花顺获取到 {len(concepts_raw)} 个热门概念")

            # 处理概念数据
            concept_list = []
            mapped_count = 0

            for item in concepts_raw:
                concept_name = item.get('name', '')
                concept_code = item.get('code', '')
                if not concept_name or not concept_code:
                    continue

                # 映射到聚宽概念
                jq_concept_name, jq_concept_code = concept_mapper.find_best_match(concept_name)

                # 处理数值
                try:
                    change_val = float(item.get('change', 0.0))
                except (ValueError, TypeError):
                    change_val = np.nan

                try:
                    limit_up_num = int(item.get('limit_up_num', 0))
                except (ValueError, TypeError):
                    limit_up_num = 0

                try:
                    continuous_plate_num = int(item.get('continuous_plate_num', 0))
                except (ValueError, TypeError):
                    continuous_plate_num = 0

                try:
                    days_val = int(item.get('days', 0))
                except (ValueError, TypeError):
                    days_val = 0

                # 提取股票列表（如果存在）
                stock_list = item.get('stock_list', [])
                
                # 处理股票列表，确保数据格式正确
                processed_stock_list = []
                if isinstance(stock_list, list):
                    for stock in stock_list:
                        if isinstance(stock, dict):
                            # 处理股票数据，确保所有字段都存在
                            processed_stock = {
                                'code': stock.get('code', ''),
                                'name': stock.get('name', ''),
                                'change_rate': float(stock.get('change_rate', 0.0)) if stock.get('change_rate') else 0.0,
                                'latest': float(stock.get('latest', 0.0)) if stock.get('latest') else 0.0,
                                'first_limit_up_time': stock.get('first_limit_up_time', ''),
                                'last_limit_up_time': stock.get('last_limit_up_time', ''),
                                'continue_num': int(stock.get('continue_num', 0)) if stock.get('continue_num') else 0,
                                'high': stock.get('high', ''),
                                'high_days': int(stock.get('high_days', 0)) if stock.get('high_days') else 0,
                                'reason_type': stock.get('reason_type', ''),
                                'reason_info': stock.get('reason_info', ''),
                                'change_tag': stock.get('change_tag', ''),
                                'market_type': stock.get('market_type', ''),
                                'market_id': int(stock.get('market_id', 0)) if stock.get('market_id') else 0,
                                'is_new': int(stock.get('is_new', 0)) if stock.get('is_new') else 0,
                                'is_st': int(stock.get('is_st', 0)) if stock.get('is_st') else 0,
                                'concept': stock.get('concept', '')
                            }
                            processed_stock_list.append(processed_stock)

                # 构造记录
                concept_info = {
                    'concept_code': concept_code,
                    'concept_name': concept_name,
                    'jq_concept_name': jq_concept_name,
                    'jq_concept_code': jq_concept_code,
                    'change': change_val,
                    'limit_up_num': limit_up_num,
                    'continuous_plate_num': continuous_plate_num,
                    'high': item.get('high', ''),
                    'high_days': days_val,
                    'stock_list': processed_stock_list,  # 新增：股票列表
                    'stock_count': len(processed_stock_list),  # 新增：股票数量统计
                    'trade_date': date_str,
                    'update_time': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
                }

                # 计算热度评分
                if np.isnan(concept_info['change']):
                    change_score = 0
                else:
                    change_score = max(0, concept_info['change'])

                heat_score = (
                        concept_info['limit_up_num'] * 2 +
                        concept_info['continuous_plate_num'] * 3 +
                        change_score
                )
                concept_info['heat_score'] = round(heat_score, 2)
                concept_list.append(concept_info)

                # 统计成功映射的概念数量
                if jq_concept_code:
                    mapped_count += 1

            if len(concept_list) > 0:
                # 创建DataFrame并按热度排序
                result_df = pd.DataFrame(concept_list).sort_values('heat_score', ascending=False)
                # log.info(f"{date_str} 成功解析概念数量: {len(result_df)}, 成功映射聚宽概念: {mapped_count}")

                # 筛选出有效聚宽概念映射的记录
                valid_df = result_df[result_df['jq_concept_code'].notna() & (result_df['jq_concept_code'] != '')]

                if len(valid_df) > 0:
                    valid_df = valid_df.head(8)

                    return valid_df
                else:
                    # 如果没有有效映射，尝试降低相似度阈值重新映射
                    original_threshold = concept_mapper.SIMILARITY_THRESHOLD
                    try:
                        log.warning(f"{date_str} 未找到有效映射，尝试降低相似度阈值从 {original_threshold} 到 0.4")
                        concept_mapper.SIMILARITY_THRESHOLD = 0.4

                        # 清除缓存，重新映射
                        concept_mapper.mapping_cache = {}

                        # 重新映射
                        new_concept_list = []
                        for item in concepts_raw:
                            concept_name = item.get('name', '')
                            if not concept_name:
                                continue

                            # 重新映射到聚宽概念
                            jq_concept_name, jq_concept_code = concept_mapper.find_best_match(concept_name)

                            if jq_concept_code:  # 只处理有效映射
                                # 处理数值
                                try:
                                    change_val = float(item.get('change', 0.0))
                                except (ValueError, TypeError):
                                    change_val = np.nan

                                try:
                                    limit_up_num = int(item.get('limit_up_num', 0))
                                except (ValueError, TypeError):
                                    limit_up_num = 0

                                try:
                                    continuous_plate_num = int(item.get('continuous_plate_num', 0))
                                except (ValueError, TypeError):
                                    continuous_plate_num = 0

                                try:
                                    days_val = int(item.get('days', 0))
                                except (ValueError, TypeError):
                                    days_val = 0

                                # 提取股票列表
                                stock_list = item.get('stock_list', [])
                                processed_stock_list = []
                                if isinstance(stock_list, list):
                                    for stock in stock_list:
                                        if isinstance(stock, dict):
                                            processed_stock = {
                                                'code': stock.get('code', ''),
                                                'name': stock.get('name', ''),
                                                'change_rate': float(stock.get('change_rate', 0.0)) if stock.get('change_rate') else 0.0,
                                                'latest': float(stock.get('latest', 0.0)) if stock.get('latest') else 0.0,
                                                'first_limit_up_time': stock.get('first_limit_up_time', ''),
                                                'last_limit_up_time': stock.get('last_limit_up_time', ''),
                                                'continue_num': int(stock.get('continue_num', 0)) if stock.get('continue_num') else 0,
                                                'high': stock.get('high', ''),
                                                'high_days': int(stock.get('high_days', 0)) if stock.get('high_days') else 0,
                                                'reason_type': stock.get('reason_type', ''),
                                                'reason_info': stock.get('reason_info', ''),
                                                'change_tag': stock.get('change_tag', ''),
                                                'market_type': stock.get('market_type', ''),
                                                'market_id': int(stock.get('market_id', 0)) if stock.get('market_id') else 0,
                                                'is_new': int(stock.get('is_new', 0)) if stock.get('is_new') else 0,
                                                'is_st': int(stock.get('is_st', 0)) if stock.get('is_st') else 0,
                                                'concept': stock.get('concept', '')
                                            }
                                            processed_stock_list.append(processed_stock)

                                # 构造记录
                                concept_info = {
                                    'concept_code': item.get('code', ''),
                                    'concept_name': concept_name,
                                    'jq_concept_name': jq_concept_name,
                                    'jq_concept_code': jq_concept_code,
                                    'change': change_val,
                                    'limit_up_num': limit_up_num,
                                    'continuous_plate_num': continuous_plate_num,
                                    'high': item.get('high', ''),
                                    'high_days': days_val,
                                    'stock_list': processed_stock_list,
                                    'stock_count': len(processed_stock_list),
                                    'trade_date': date_str,
                                    'update_time': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
                                }

                                # 计算热度评分
                                if np.isnan(concept_info['change']):
                                    change_score = 0
                                else:
                                    change_score = max(0, concept_info['change'])

                                heat_score = (
                                        concept_info['limit_up_num'] * 2 +
                                        concept_info['continuous_plate_num'] * 3 +
                                        change_score
                                )
                                concept_info['heat_score'] = round(heat_score, 2)
                                new_concept_list.append(concept_info)

                        if len(new_concept_list) > 0:
                            new_df = pd.DataFrame(new_concept_list).sort_values('heat_score', ascending=False)
                            new_df = new_df.head(8)
                            log.info(f"{date_str} 降低阈值后找到有效映射: {len(new_df)}")
                            return new_df
                        else:
                            log.warning(f"{date_str} 即使降低阈值也未找到有效映射")
                            # 返回原始数据的前8个
                            return result_df.head(8)
                    finally:
                        concept_mapper.SIMILARITY_THRESHOLD = original_threshold
            else:
                log.warning(f"{date_str} 未解析到有效概念数据")
                return pd.DataFrame()
        else:
            log.error(f"获取热门概念数据失败: {data.get('status_msg', '未知错误')}")
            return pd.DataFrame()

    except Exception as e:
        log.error(f"获取热门概念数据出错: {str(e)}")
        import traceback
        log.error(f"错误详情: {traceback.format_exc()}")
        return pd.DataFrame()

def convert_to_jq_code(stock_code):
    """
    将股票代码转换为聚宽格式

    参数:
        stock_code: 原始股票代码

    返回:
        str: 聚宽格式的股票代码
    """
    try:
        # 如果已经是聚宽格式，直接返回
        if '.' in stock_code:
            return stock_code

        # 根据股票代码前缀判断交易所
        if stock_code.startswith('6'):
            return stock_code + '.XSHG'  # 上海证券交易所
        elif stock_code.startswith('0') or stock_code.startswith('3'):
            return stock_code + '.XSHE'  # 深圳证券交易所
        else:
            log.warning(f"无法识别的股票代码格式: {stock_code}")
            return None

    except Exception as e:
        log.error(f"转换股票代码时出错: {str(e)}, 股票代码: {stock_code}")
        return None    

# 获取同花顺热点概念 
class ConceptMapper:
    """概念映射器：实现同花顺概念到聚宽概念的映射"""

    def __init__(self, logger=None):
        # 定义相似度阈值作为类的属性
        self.SIMILARITY_THRESHOLD = 0.6
        self.logger = logger or logging.getLogger("ths_hot_concept")
        try:
            self.jq_concepts_df = get_concepts()
            self.jq_concepts = [
                {
                    'code': idx,
                    'name': row['name']
                }
                for idx, row in self.jq_concepts_df.iterrows()
            ]
            self.jq_names = [item['name'] for item in self.jq_concepts]
            self.mapping_cache = {}
            self.logger.info(f"成功加载聚宽概念数据，共 {len(self.jq_concepts)} 个概念")
        except Exception as e:
            self.logger.error(f"加载聚宽概念数据出错: {str(e)}")
            # 如果无法获取聚宽概念，使用一些常见的概念作为备用
            self.jq_concepts = [
                {"code": "GN036", "name": "新能源汽车"},
                {"code": "GN035", "name": "半导体"},
                {"code": "GN033", "name": "人工智能"},
                {"code": "GN028", "name": "5G"},
                {"code": "GN022", "name": "医疗器械"},
                {"code": "GN015", "name": "光伏"},
                {"code": "GN014", "name": "锂电池"},
                {"code": "GN012", "name": "芯片"},
                {"code": "GN010", "name": "云计算"},
                {"code": "GN009", "name": "大数据"},
                {"code": "GN008", "name": "物联网"},
                {"code": "GN007", "name": "虚拟现实"},
                {"code": "GN006", "name": "智能电网"},
                {"code": "GN005", "name": "智能家居"},
                {"code": "GN004", "name": "新能源"},
                {"code": "GN003", "name": "节能环保"},
                {"code": "GN002", "name": "生物医药"},
                {"code": "GN001", "name": "军工"}
            ]
            self.jq_names = [item['name'] for item in self.jq_concepts]
            self.mapping_cache = {}
            self.logger.info(f"使用备用概念数据，共 {len(self.jq_concepts)} 个概念")

    def clean_concept_name(self, name):
        """清理概念名称，去除常见后缀"""
        if not name:
            return ""
        suffixes = ['概念', '板块', '指数', '主题', '产业', '行业', '题材']
        cleaned = name
        for suffix in suffixes:
            cleaned = cleaned.replace(suffix, '')
        return cleaned.strip()

    def similarity_score(self, str1, str2):
        """计算两个字符串的相似度"""
        if not str1 or not str2:
            return 0
        return SequenceMatcher(None, str1, str2).ratio()

    def find_best_match(self, ths_concept_name):
        """
        找到最佳匹配的聚宽概念

        参数:
            ths_concept_name: 同花顺概念名称

        返回:
            (聚宽概念名称, 聚宽概念代码) 元组
        """
        # 检查输入
        if not ths_concept_name:
            return (None, None)

        # 检查缓存
        if ths_concept_name in self.mapping_cache:
            return self.mapping_cache[ths_concept_name]

        # 清理概念名称
        ths_clean = self.clean_concept_name(ths_concept_name)
        if not ths_clean:
            return (None, None)

        best_score = 0
        best_match = None

        # 先尝试精确匹配
        for item in self.jq_concepts:
            jq_name = item['name']

            # 精确匹配原始名称
            if ths_concept_name == jq_name:
                self.mapping_cache[ths_concept_name] = (item['name'], item['code'])
                self.logger.info(f"精确匹配: {ths_concept_name} -> {item['name']}")
                return (item['name'], item['code'])

            # 精确匹配清理后的名称
            jq_clean = self.clean_concept_name(jq_name)
            if ths_clean == jq_clean:
                self.mapping_cache[ths_concept_name] = (item['name'], item['code'])
                self.logger.info(f"清理后精确匹配: {ths_concept_name}({ths_clean}) -> {item['name']}({jq_clean})")
                return (item['name'], item['code'])

        # 再尝试模糊匹配
        for item in self.jq_concepts:
            jq_name = item['name']
            jq_clean = self.clean_concept_name(jq_name)

            # 计算基础相似度
            score = self.similarity_score(ths_clean, jq_clean)

            # 检查包含关系，提高相似度
            if ths_clean in jq_clean or jq_clean in ths_clean:
                score += 0.2

            # 更新最佳匹配
            if score > best_score:
                best_score = score
                best_match = (item['name'], item['code'])

        # 如果相似度达到阈值，认为是匹配的
        if best_match and best_score >= self.SIMILARITY_THRESHOLD:  # 使用类的属性
            self.logger.info(f"模糊匹配: {ths_concept_name} -> {best_match[0]} (相似度: {best_score:.2f})")
            self.mapping_cache[ths_concept_name] = best_match
            return best_match

        self.logger.warning(f"未找到匹配: {ths_concept_name} (最佳相似度: {best_score:.2f})")
        self.mapping_cache[ths_concept_name] = (None, None)
        return (None, None)


# ============================================================================
# 3. 股票筛选主函数（修复版）
# ============================================================================
def filter_stocks_by_score_optimized(stocks, context, min_score=14, max_stocks=100):
    """
    优化后的根据评分筛选股票函数（统一使用calculate_main_force_flow_score计算主力资金因子）
    同时将评分结果缓存到全局变量中

    参数:
        stocks: 候选股票列表
        context: 上下文对象
        min_score: 最低评分要求，默认14分
        max_stocks: 最大处理股票数量，默认100只
    """
    try:
        log.info("=" * 60)
        log.info(f"开始股票评分筛选，候选股票: {len(stocks)} 只，最低分数: {min_score}")
        log.info("=" * 60)

        # 获取各模式股票列表
        lblt_stocks = getattr(g, 'lblt_stocks', [])  # 连板龙头模式
        gk_stocks = getattr(g, 'gk_stocks', [])     # 一进二模式
        rzq_stocks = getattr(g, 'rzq_stocks', [])   # 弱转强模式
        dk_stocks = getattr(g, 'dk_stocks', [])     # 低开股票
        fxsbdk_stocks = getattr(g, 'fxsbdk_stocks', [])  # 放巡散步低开股票
        
        # 清空之前的评分缓存
        g.score_cache = {}

        qualified_stocks = []  # 只存储股票代码字符串
        score_records = []
        processing_stats = {
            'total_stocks': len(stocks),
            'processed_stocks': 0,
            'qualified_stocks': 0,
            'failed_stocks': 0,
            'high_score_stocks': 0,
            'medium_score_stocks': 0,
            'low_score_stocks': 0,
            'zero_score_stocks': 0,
            'processing_time': 0,
            'cache_status': 'unknown',
            'filtered_by_mainline': 0,  # 记录因主线分为0被过滤的连板龙头股票数量
            'filtered_by_money_flow': 0,  # 记录因主力资金为0被过滤的连板龙头股票数量
            'filtered_by_volume_ratio': 0,  # 记录因量比不符合要求被过滤的股票数量
            'filtered_by_volume_ratio_weak_to_strong': 0,  # 弱转强模式量比过滤
            'filtered_by_volume_ratio_lblt': 0,  # 连板龙头模式量比过滤
            'filtered_by_volume_ratio_first_to_second': 0  # 一进二模式量比过滤
        }

        start_time = time.time()

        # 限制处理数量
        limited_stocks = stocks[:max_stocks] if len(stocks) > max_stocks else stocks
        if len(stocks) > max_stocks:
            log.info(f"⚠️  股票数量过多，限制处理前 {max_stocks} 只股票")

        # 检查缓存状态
        cache_status = check_cache_status()
        processing_stats['cache_status'] = cache_status
        log.info(f"热门概念缓存状态: {cache_status}")

        # 批量获取股票基本信息
        current_data = get_current_data()

        # 预先获取所有股票的资金流向数据
        money_flow_map = get_money_flow_map(context, limited_stocks)

        for i, stock in enumerate(limited_stocks):
            try:
                # 进度显示
                if i % 20 == 0 and i > 0:
                    elapsed_time = time.time() - start_time
                    avg_time_per_stock = elapsed_time / i
                    remaining_time = avg_time_per_stock * (len(limited_stocks) - i)
                    log.info(f"📊 进度: {i}/{len(limited_stocks)} ({i / len(limited_stocks) * 100:.1f}%), "
                             f"预计剩余时间: {remaining_time:.1f}秒")

                # 判断股票模式类型
                is_lblt_stock = stock in lblt_stocks
                is_first_to_second_stock = stock in gk_stocks
                is_weak_to_strong_stock = stock in rzq_stocks
                is_dk_stock = stock in dk_stocks
                is_fxsbdk_stock = stock in fxsbdk_stocks
                
                # 确定股票模式
                if is_lblt_stock:
                    stock_mode = "连板龙头"
                elif is_first_to_second_stock:
                    stock_mode = "一进二"
                elif is_weak_to_strong_stock:
                    stock_mode = "弱转强"
                elif is_dk_stock:
                    stock_mode = "低开"
                elif is_fxsbdk_stock:
                    stock_mode = "放巡散步低开"
                else:
                    stock_mode = "未分类"  # 默认为未分类模式

                # 1. 获取基础评分结果（包含全部6个因子，主力资金因子已统一计算）
                score_result = calculate_buy_score_optimized(stock, context, money_flow_map)
                processing_stats['processed_stocks'] += 1

                if not score_result:
                    processing_stats['failed_stocks'] += 1
                    continue

                # 2. 提取各因子得分（主力资金因子使用统一计算结果）
                factor1 = score_result.get('factor1_涨停', 0)
                factor2 = score_result.get('factor2_技术', 0)
                factor3 = score_result.get('factor3_放量MA', 0)
                factor4 = score_result.get('factor4_主线', 0)
                factor5 = score_result.get('factor5_情绪', 0)
                factor6 = score_result.get('factor6_主力资金', 0)  # 统一使用calculate_main_force_flow_score结果
                total_score = factor1 + factor2 + factor3 + factor4 + factor5 + factor6

                # 3. 将评分结果缓存到全局变量（包含6个因子）
                g.score_cache[stock] = {
                    'total_score': total_score,
                    'factor1_涨停': factor1,
                    'factor2_技术': factor2,
                    'factor3_放量MA': factor3,
                    'factor4_主线': factor4,
                    'factor5_情绪': factor5,
                    'factor6_主力资金': factor6,  # 统一缓存结果
                    'timestamp': context.current_dt,
                    'is_lblt': is_lblt_stock,  # 标记是否为连板龙头
                    'stock_mode': stock_mode    # 记录股票模式
                }

                # 4. 统计评分分布
                if total_score >= 20:
                    processing_stats['high_score_stocks'] += 1
                elif total_score >= 15:
                    processing_stats['medium_score_stocks'] += 1
                elif total_score >= 10:
                    processing_stats['low_score_stocks'] += 1
                else:
                    processing_stats['zero_score_stocks'] += 1

                # 5. 获取股票名称（在过滤逻辑之前，避免引用未定义变量）
                try:
                    if stock in current_data:
                        stock_name = current_data[stock].name
                    else:
                        security_info = get_security_info(stock)
                        stock_name = security_info.display_name if security_info else "未知"
                except Exception:
                    stock_name = "未知"

                # 6. 判断是否符合筛选条件
                is_qualified = total_score >= min_score

                # 连板龙头过滤条件：主线分为0或主力资金为0
                filtered_reason = None
                if is_lblt_stock:
                    if factor4 == 0:
                        is_qualified = False
                        processing_stats['filtered_by_mainline'] += 1
                        filtered_reason = '连板龙头主线分为0'
                    elif factor6 == 0 and factor1 < 5:
                        is_qualified = False
                        processing_stats['filtered_by_money_flow'] += 1
                        filtered_reason = '连板龙头主力资金为0且涨停分小于5'

                # 根据不同模式量比过滤
                try:
                    last_volume, last_2_volume, volume_ratio = get_volume_data(stock, context)
                
                    if is_lblt_stock:
                        if volume_ratio < 1.184 or volume_ratio > 10.5:
                            is_qualified = False
                            processing_stats['filtered_by_volume_ratio'] += 1
                            processing_stats['filtered_by_volume_ratio_lblt'] += 1
                            filtered_reason = f'连板龙头模式量比不符({volume_ratio:.2f}不在1.184~10.5范围内)'
                    
                    elif is_weak_to_strong_stock or is_dk_stock or is_fxsbdk_stock:
                        if volume_ratio < 1.072 or volume_ratio > 3.68:
                            is_qualified = False
                            processing_stats['filtered_by_volume_ratio'] += 1
                            processing_stats['filtered_by_volume_ratio_weak_to_strong'] += 1
                            filtered_reason = f'弱转强模式量比不符({volume_ratio:.2f}不在1.072~3.68范围内)'
                    
                    else:
                        if volume_ratio < 1.072 or volume_ratio > 10.5:
                            is_qualified = False
                            processing_stats['filtered_by_volume_ratio'] += 1
                            filtered_reason = f'未分类模式量比不符({volume_ratio:.2f}不在1.072~10.5范围内)'
                    
                    g.score_cache[stock]['volume_ratio'] = volume_ratio
                    
                except Exception as ve:
                    log.warning(f"获取股票 {stock} 的量比数据失败: {str(ve)}")
                
                # 总分过滤，一进二:20~37, 弱转强>=20
                if is_qualified:
                    if is_first_to_second_stock and (total_score < 20 or total_score > 37):
                        is_qualified = False
                        filtered_reason = f'一进二模式总分不符({total_score}不在20~37范围内)'
                        log.info(f"⚠️ 过滤 {stock} ({stock_name}): {filtered_reason}")
                    
                    elif (is_weak_to_strong_stock or is_dk_stock or is_fxsbdk_stock) and total_score < 20:
                        is_qualified = False
                        filtered_reason = f'弱转强模式总分不符({total_score}<20)'
                        log.info(f"⚠️ 过滤 {stock} ({stock_name}): {filtered_reason}")

                if is_qualified:
                    qualified_stocks.append(stock)
                    processing_stats['qualified_stocks'] += 1

                # 7. 构建评分记录（包含6个因子）
                record = {
                    '股票代码': stock,
                    '股票名称': stock_name,
                    '总评分': total_score,
                    '是否选中': '✓' if is_qualified else '✗',
                    'factor1_涨停': factor1,
                    'factor2_技术': factor2,
                    'factor3_放量MA': factor3,
                    'factor4_主线': factor4,
                    'factor5_情绪': factor5,
                    'factor6_主力资金': factor6,  # 统一记录结果
                    '是否连板龙头': '✓' if is_lblt_stock else '✗',
                    '被过滤原因': filtered_reason,
                    '量比': g.score_cache[stock].get('volume_ratio', '未知'),  # 添加量比信息到记录中
                    '模式': stock_mode  # 添加模式信息
                }
                score_records.append(record)

                # 8. 输出符合条件股票的详细信息（含6个因子）
                if is_qualified:
                    mode_tag = f"[{stock_mode}]" if stock_mode != "未分类" else ""
                    volume_ratio_info = f"量比:{g.score_cache[stock].get('volume_ratio', '未知'):.2f}" if isinstance(
                        g.score_cache[stock].get('volume_ratio'), (int, float)) else "量比:未知"
                    log.info(f"✅ {stock} ({stock_name}){mode_tag} - 总分: {total_score} "
                             f"[涨停:{factor1} 技术:{factor2} 放量MA:{factor3} "
                             f"主线:{factor4} 情绪:{factor5} 主力资金:{factor6} {volume_ratio_info}]")
                elif filtered_reason and total_score >= min_score:
                    # 记录因过滤条件而被过滤的高分股票
                    mode_tag = f"[{stock_mode}]" if stock_mode != "未分类" else ""
                    volume_ratio_info = f"量比:{g.score_cache[stock].get('volume_ratio', '未知'):.2f}" if isinstance(
                        g.score_cache[stock].get('volume_ratio'), (int, float)) else "量比:未知"
                    log.info(f"⚠️ {stock} ({stock_name}){mode_tag} - 总分: {total_score} {filtered_reason} "
                             f"[涨停:{factor1} 技术:{factor2} 放量MA:{factor3} "
                             f"主线:{factor4} 情绪:{factor5} 主力资金:{factor6} {volume_ratio_info}]")

            except Exception as e:
                processing_stats['failed_stocks'] += 1
                log.error(f"处理股票 {stock} 时出错: {str(e)}")
                continue

        # 计算处理时间
        processing_stats['processing_time'] = time.time() - start_time

        # 输出统计信息
        log.info("📈 评分分布统计:")
        log.info(f"  🌟 高分股票(≥20分): {processing_stats['high_score_stocks']} 只")
        log.info(f"  ⭐ 中等股票(15-20分): {processing_stats['medium_score_stocks']} 只")
        log.info(f"  📊 低分股票(10-15分): {processing_stats['low_score_stocks']} 只")
        log.info(f"  ❌ 零分股票(0分): {processing_stats['zero_score_stocks']} 只")

        # 过滤统计
        log.info("  🚫 过滤统计:")
        if processing_stats['filtered_by_mainline'] > 0:
            log.info(f"    - 因主线分为0被过滤: {processing_stats['filtered_by_mainline']} 只")
        if processing_stats['filtered_by_money_flow'] > 0:
            log.info(f"    - 因主力资金为0被过滤: {processing_stats['filtered_by_money_flow']} 只")
        if processing_stats['filtered_by_volume_ratio'] > 0:
            log.info(f"    - 因量比不符被过滤(总计): {processing_stats['filtered_by_volume_ratio']} 只")
            if processing_stats['filtered_by_volume_ratio_lblt'] > 0:
                log.info(f"      - 连板龙头模式量比不符(1.184~10.5): {processing_stats['filtered_by_volume_ratio_lblt']} 只")
            if processing_stats['filtered_by_volume_ratio_first_to_second'] > 0:
                log.info(f"      - 一进二模式量比不符(1.15~6.58): {processing_stats['filtered_by_volume_ratio_first_to_second']} 只")
            if processing_stats['filtered_by_volume_ratio_weak_to_strong'] > 0:
                log.info(f"      - 弱转强模式量比不符(1.072~3.68): {processing_stats['filtered_by_volume_ratio_weak_to_strong']} 只")

        total_filtered = (processing_stats['filtered_by_mainline'] +
                          processing_stats['filtered_by_money_flow'] +
                          processing_stats['filtered_by_volume_ratio'])
        log.info(f"    - 总计被过滤股票: {total_filtered} 只")

        log.info(f"  ⚠️  处理失败: {processing_stats['failed_stocks']} 只")
        log.info(f"  ⏱️  处理时间: {processing_stats['processing_time']:.2f} 秒")

        log.info("=" * 60)
        log.info(f"✅ 股票筛选完成！符合条件股票: {len(qualified_stocks)} 只")
        log.info(f"📦 评分缓存已保存: {len(g.score_cache)} 只股票的评分（含6个因子和量比）")
        log.info("=" * 60)

        # 按总评分降序排序
        score_records_sorted = sorted(score_records, key=lambda x: x['总评分'], reverse=True)
        score_records_limited = score_records_sorted

        # 更新合格股票列表为排序后并限制数量的结果
        qualified_stocks = [record['股票代码'] for record in score_records_limited if record['是否选中'] == '✓']

        # 显示前5只符合条件的股票详情
        if qualified_stocks:
            log.info("🎯 符合条件的股票列表:")
            for i, stock in enumerate(qualified_stocks[:5]):  # 只显示前5只
                matching_record = next((r for r in score_records if r['股票代码'] == stock), None)
                if matching_record:
                    mode_tag = f"[{matching_record['模式']}]" if matching_record['模式'] != "未分类" else ""
                    volume_ratio_info = f"量比:{matching_record['量比']:.2f}" if isinstance(matching_record['量比'],
                                                                                            (int, float)) else "量比:未知"
                    log.info(f"  {i + 1}. {stock} ({matching_record['股票名称']}){mode_tag} - "
                             f"总分: {matching_record['总评分']} "
                             f"[涨停:{matching_record['factor1_涨停']} "
                             f"技术:{matching_record['factor2_技术']} "
                             f"放量MA:{matching_record['factor3_放量MA']} "
                             f"主线:{matching_record['factor4_主线']} "
                             f"情绪:{matching_record['factor5_情绪']} "
                             f"主力资金:{matching_record['factor6_主力资金']} "
                             f"{volume_ratio_info}]")

            if len(qualified_stocks) > 5:
                log.info(f"  ... 共 {len(qualified_stocks)} 只符合条件股票")

        return qualified_stocks

    except Exception as e:
        log.error(f"股票筛选过程出错: {str(e)}")
        import traceback
        log.error(traceback.format_exc())
        return []


def get_money_flow_map(context, qualified_stocks):
    """
    获取合格股票的主力资金流数据并构建映射字典（包含所有日期记录）
    参数:
        context: 上下文对象
        qualified_stocks: 合格股票列表
    返回:
        money_flow_map: 主力资金流映射字典，键为股票代码，值为包含所有日期记录的列表
    """
    money_flow_map = {}
    try:
        if not qualified_stocks:
            log.info("合格股票列表为空，无需获取主力资金数据")
            return money_flow_map

        # 获取最近5个交易日的主力资金数据（保留所有日期，不做数量限制）
        end_date = context.previous_date
        trade_days = get_trade_days(end_date=end_date, count=5)
        log.info(f"~~~日期范围: {trade_days[0]} 至 {end_date}, trade_days:{trade_days}")

        money_flow_df = get_money_flow(qualified_stocks, start_date=trade_days[0], end_date=end_date)
        if money_flow_df.empty:
            log.info("未获取到主力资金流数据")
            return money_flow_map

        # 确定日期列（兼容'date'或'trade_date'字段）
        date_column = 'date' if 'date' in money_flow_df.columns else 'trade_date'
        if date_column not in money_flow_df.columns:
            log.warning("资金流数据中未找到日期列，使用end_date作为默认日期")

        # 构建资金流映射字典（按股票代码聚合所有日期记录）
        for _, row in money_flow_df.iterrows():
            stock_code = row['sec_code']
            # 初始化股票对应的记录列表（首次出现时）
            if stock_code not in money_flow_map:
                money_flow_map[stock_code] = []

            # 优先使用数据中的日期，无则用end_date
            record_date = row[date_column] if date_column in money_flow_df.columns else end_date

            # 构建单条日期记录并添加到列表
            daily_record = {
                'date': record_date,
                'net_amount_main': row['net_amount_main'],  # 主力净额(万)
                'net_pct_main': row['net_pct_main'],  # 主力净占比(%)
                'net_amount_l': row['net_amount_l']
            }
            money_flow_map[stock_code].append(daily_record)

        # 输出每个股票的记录数量日志
        # for stock, records in money_flow_map.items():
        #     log.info(f"个股主力资金数据：{stock} 共 {len(records)} 条记录，日期范围: {records[0]['date']} 至 {records[-1]['date']}")

    except Exception as e:
        log.warning(f"批量获取主力资金数据失败: {str(e)}")

    return money_flow_map


# 同花顺指标转换为Python函数 
def calculate_ths_indicators(stock, context, period=30, unit='1d', log_debug=False):
    """
    计算同花顺指标，返回买卖信号
    """
    try:
        # 获取历史数据
        hist_data = attribute_history(stock, period, unit,
                                      ['open', 'close', 'high', 'low', 'volume'],
                                      skip_paused=True)

        if hist_data.empty or len(hist_data) < 20:
            return {'buy_signals': [], 'sell_signals': []}

        # 提取OHLCV数据
        O = hist_data['open'].values
        C = hist_data['close'].values
        H = hist_data['high'].values
        L = hist_data['low'].values
        V = hist_data['volume'].values

        # 计算各类指标
        signals = {
            'buy_signals': [],
            'sell_signals': []
        }

        # 计算ZIG指标 (简化版，使用高低点)
        def calculate_zig(data, threshold=3):
            """简化的ZIG指标计算"""
            zig_values = []
            last_val = data[0]
            trend = 0  # 0:未定义, 1:上升, -1:下降

            for i, val in enumerate(data):
                if i == 0:
                    zig_values.append(val)
                    continue

                change_pct = abs(val - last_val) / last_val * 100

                if change_pct >= threshold:
                    if val > last_val and trend != 1:
                        trend = 1
                        last_val = val
                    elif val < last_val and trend != -1:
                        trend = -1
                        last_val = val

                zig_values.append(last_val)

            return np.array(zig_values)

        # 计算买线和卖线
        买线 = calculate_zig(C, 3)
        卖线 = pd.Series(买线).rolling(window=3, min_periods=1).mean().values

        # 计算MA均线
        MA5 = pd.Series(C).rolling(window=5, min_periods=1).mean().values
        MA10 = pd.Series(C).rolling(window=10, min_periods=1).mean().values
        MA20 = pd.Series(C).rolling(window=20, min_periods=1).mean().values

        # 计算基线
        基线 = pd.Series(pd.Series(C).rolling(window=30, min_periods=1).min().shift(1)).rolling(window=2,
                                                                                                min_periods=1).mean().values

        # 波段买点信号
        if len(买线) >= 2 and len(卖线) >= 2:
            # 买线上穿卖线
            if 买线[-1] > 卖线[-1] and 买线[-2] <= 卖线[-2]:
                signals['buy_signals'].append('波段买点')

        # 波段卖出信号 - 关键卖出逻辑
        zig_5 = calculate_zig(C, 5)  # 更敏感的ZIG
        if len(zig_5) >= 4:
            # 检测波段卖出：连续下跌趋势
            if (zig_5[-1] < zig_5[-2] and
                    zig_5[-2] >= zig_5[-3] and
                    zig_5[-3] >= zig_5[-4]):
                signals['sell_signals'].append('波段卖')

        # 精准买点 (EMA交叉)
        def ema(data, period):
            return pd.Series(data).ewm(span=period, adjust=False).mean().values

        X1 = (C + L + H) / 3
        X2 = ema(X1, 6)
        X3 = ema(X2, 5)

        if len(X2) >= 2 and len(X3) >= 2:
            if X2[-1] > X3[-1] and X2[-2] <= X3[-2] and 买线[-1] >= 卖线[-1]:
                signals['buy_signals'].append('精准买')

        # 游资进入信号
        if len(C) >= 75:
            def sma(data, period, weight=1):
                return pd.Series(data).ewm(alpha=weight / period, adjust=False).mean().values

            # 计算VARF1指标
            llv_75 = pd.Series(L).rolling(window=75, min_periods=1).min().values
            hhv_75 = pd.Series(H).rolling(window=75, min_periods=1).max().values

            close_norm = (C - llv_75) / (hhv_75 - llv_75) * 100
            open_norm = (O - llv_75) / (hhv_75 - llv_75) * 100

            sma1_close = sma(close_norm, 20, 1)
            sma2_close = sma(sma1_close, 15, 1)
            VARF1 = 100 - 3 * sma1_close + 2 * sma2_close

            sma1_open = sma(open_norm, 20, 1)
            sma2_open = sma(sma1_open, 15, 1)
            VAR101 = 100 - 3 * sma1_open + 2 * sma2_open

            if len(VARF1) >= 2 and len(VAR101) >= 2 and len(V) >= 2:
                VAR111 = (VARF1[-1] < VAR101[-2] and V[-1] > V[-2] and C[-1] > C[-2])

                # 检查30天内是否只有一次这样的信号
                count_signals = 0
                for i in range(max(0, len(VARF1) - 30), len(VARF1)):
                    if i >= 1 and i < len(V) - 1:
                        if (VARF1[i] < VAR101[i - 1] and V[i] > V[i - 1] and C[i] > C[i - 1]):
                            count_signals += 1

                if VAR111 and count_signals == 1 and 买线[-1] >= 卖线[-1]:
                    signals['buy_signals'].append('游资进')

        return signals

    except Exception as e:
        log.error(f"计算同花顺指标时出错 {stock}: {str(e)}")
        return {'buy_signals': [], 'sell_signals': []}


def check_volume_drop_signal(stock, context):
    """
    检测放量大跌信号（跌幅6%以上且放量）
    """
    try:
        # 获取当前价格数据
        current_data = get_current_data()
        if stock not in current_data:
            return False

        current_price = current_data[stock].last_price
        if current_price == 0:
            return False

        # 获取今日开盘价和昨日收盘价
        today_data = attribute_history(stock, 1, '1d',
                                       ['open', 'close', 'volume'],
                                       skip_paused=True)
        if today_data.empty:
            return False

        yesterday_close = today_data['close'][0]
        today_open = current_data[stock].day_open

        if yesterday_close == 0 or today_open == 0:
            return False

        # 计算当前跌幅（相对于昨日收盘）
        drop_pct = (yesterday_close - current_price) / yesterday_close

        # 获取近5日平均成交量
        volume_data = attribute_history(stock, 6, '1d', ['volume'], skip_paused=True)
        if len(volume_data) < 5:
            return False

        avg_volume_5 = volume_data['volume'][:-1].mean()  # 排除今天，取前5日平均

        # 估算已过交易分钟数（上午9:30~11:30共120min，下午13:00~15:00共120min）
        current_time = context.current_dt.time()
        import datetime as _dt
        if current_time <= _dt.time(11, 30):
            elapsed_minutes = (current_time.hour - 9) * 60 + current_time.minute - 30
        elif current_time < _dt.time(13, 0):
            elapsed_minutes = 120  # 午休期间算上午全部
        else:
            elapsed_minutes = 120 + (current_time.hour - 13) * 60 + current_time.minute
        elapsed_minutes = max(elapsed_minutes, 1)

        if elapsed_minutes > 0:
            estimated_daily_volume = volume_data['volume'][-1] * (240.0 / elapsed_minutes)
            volume_ratio = estimated_daily_volume / avg_volume_5
        else:
            volume_ratio = 1

        # 判断是否放量大跌：跌幅超过6%且成交量放大1.5倍以上
        if drop_pct >= 0.06 and volume_ratio >= 1.5:
            return True

        return False

    except Exception as e:
        log.error(f"检测放量大跌信号时出错 {stock}: {str(e)}")
        return False


def sell_limit_per5min(context):
    """
    每5分钟检测持仓股票是否需要卖出
    基于同花顺指标的波段卖信号和放量大跌信号
    """
    # 初始化交易记录
    if not hasattr(g, 'today_trades'):
        g.today_trades = []

    current_data = get_current_data()
    date = transform_date(context.previous_date, 'str')

    for stock in list(context.portfolio.positions):
        if stock == '511880.XSHG':  # 排除基金
            continue

        position = context.portfolio.positions[stock]
        if position.closeable_amount == 0:
            continue

        # 检查是否停牌
        if current_data[stock].paused:
            continue

        try:
            # 1. 检测同花顺指标信号
            ths_signals = calculate_ths_indicators(stock, context, 30, '5m')
            has_sell_signal = '波段卖' in ths_signals['sell_signals']

            # 2. 检测放量大跌信号
            has_volume_drop = check_volume_drop_signal(stock, context)

            # 获取当前价格和成本价
            current_price = current_data[stock].last_price
            avg_cost = position.avg_cost

            # 3. 紧急止损：波段卖信号 + 放量大跌
            if has_sell_signal and has_volume_drop:
                loss_pct = (avg_cost - current_price) / avg_cost

                details = {
                    '触发信号': '波段卖 + 放量大跌',
                    '成本价': f"{avg_cost:.2f}",
                    '当前价': f"{current_price:.2f}",
                    '亏损比例': f"{loss_pct:.2%}",
                    '同花顺信号': ths_signals['sell_signals']
                }

                record_sell_trade(context, stock, "紧急止损-波段卖+放量大跌", details, current_data, date)
                safe_sell(stock, 0, current_data, reason="紧急止损-波段卖+放量大跌")

                log.info(f"★★★ 紧急止损 ★★★")
                log.info(f"股票: {stock}")
                log.info(f"触发信号: 波段卖 + 放量大跌")
                log.info(f"卖出价格: {current_price:.2f}")
                log.info(f"亏损幅度: {loss_pct:.2%}")

            # 4. 波段卖出：仅波段卖信号且亏损超过3%
            elif has_sell_signal:
                loss_pct = (avg_cost - current_price) / avg_cost

                if loss_pct >= 0.03:
                    details = {
                        '触发信号': '波段卖 + 亏损3%+',
                        '成本价': f"{avg_cost:.2f}",
                        '当前价': f"{current_price:.2f}",
                        '亏损比例': f"{loss_pct:.2%}",
                        '同花顺信号': ths_signals['sell_signals']
                    }

                    record_sell_trade(context, stock, "波段卖出", details, current_data, date)
                    safe_sell(stock, 0, current_data, reason="波段卖出")

                    log.info(f"波段卖出: {stock}, 亏损: {loss_pct:.2%}")

            # 5. 放量大跌：跌幅达到8%以上
            elif has_volume_drop:
                yesterday_close = attribute_history(stock, 1, '1d', ['close'], skip_paused=True)['close'][0]
                drop_pct = (yesterday_close - current_price) / yesterday_close

                if drop_pct >= 0.08:
                    details = {
                        '触发信号': '放量大跌8%+',
                        '昨收价': f"{yesterday_close:.2f}",
                        '当前价': f"{current_price:.2f}",
                        '跌幅': f"{drop_pct:.2%}"
                    }

                    record_sell_trade(context, stock, "放量大跌止损", details, current_data, date)
                    safe_sell(stock, 0, current_data, reason="放量大跌止损")

                    log.info(f"放量大跌止损: {stock}, 跌幅: {drop_pct:.2%}")

        except Exception as e:
            log.error(f"处理股票 {stock} 卖出检测时出错: {str(e)}")
            continue



# 2. 盘前数据统计函数
def record_morning_stats(context):
    """
    记录盘前统计数据
    """
    try:
        log.info(f"====== {context.current_dt.strftime('%Y-%m-%d')} 盘前数据 ======")
        # 情况个股积分缓存
        clear_score_cache(context)

        # 获取大盘数据
        index_data = attribute_history('000001.XSHG', 5, '1d',
                                       ['close', 'volume'], skip_paused=True)

        if not index_data.empty and len(index_data) >= 2:
            current_close = index_data['close'].iloc[-1]
            prev_close = index_data['close'].iloc[-2]
            change_rate = (current_close - prev_close) / prev_close * 100

            # 计算波动率
            volatility = index_data['close'].pct_change().std() * 100

            # 计算量能比
            current_volume = index_data['volume'].iloc[-1]
            avg_volume = index_data['volume'].head(-1).mean()
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1

            # 判断市场趋势（基于近3日涨跌幅，避免单日噪声）
            if len(index_data) >= 4:
                close_3d_ago = index_data['close'].iloc[-4]
                change_rate_3d = (current_close - close_3d_ago) / close_3d_ago * 100
            else:
                change_rate_3d = change_rate

            if change_rate_3d > 2:
                trend = "strong_up"
            elif change_rate_3d > 0.5:
                trend = "up"
            elif change_rate_3d > -1.5:
                trend = "flat"
            else:
                trend = "down"

            log.info("市场状况:")
            log.info(f"- 大盘趋势: {trend}")
            log.info(f"- 波动率: {volatility:.2f}%")
            log.info(f"- 量能比: {volume_ratio:.2f}")

            # 存储市场统计
            g.trade_stats['market_stats'] = {
                'date': context.current_dt.strftime('%Y-%m-%d'),
                'trend': trend,
                'change_rate': change_rate,
                'volatility': volatility,
                'volume_ratio': volume_ratio
            }
            update_strategy_priority(trend)

    except Exception as e:
        log.error(f"记录盘前统计失败: {str(e)}")


def get_last_n_auction_avg(s, end_date, n=5):
    # 获取最近n个交易日（含end_date）
    trade_days = get_trade_days(end_date=end_date, count=n)
    # 获取这n日的集合竞价量
    auction_data = get_call_auction([s], start_date=trade_days[0], end_date=trade_days[-1], fields=['time', 'volume'])
    if auction_data.empty:
        return 0
    return auction_data['volume'].mean()


def sell_limit_down(context):
    date = context.previous_date
    current_data = get_current_data()
    slist = list(context.portfolio.positions)

    # 获取集合竞价数据
    date_now = context.current_dt.strftime("%Y-%m-%d")
    auction_start = date_now + ' 09:15:00'
    auction_end = date_now + ' 09:25:00'
    auctions = get_call_auction(
        slist, start_date=auction_start, end_date=auction_end,
        fields=['time', 'current', 'volume']
    ).set_index('code') if slist else None

    for stock in slist:
        if stock == '511880.XSHG':
            continue
        position = context.portfolio.positions[stock]
        if position.closeable_amount == 0:
            continue

        try:
            # 获取K线数据
            price_data = get_price(stock, end_date=date, count=6,
                                   fields=['open', 'close', 'high', 'low', 'volume', 'high_limit'], skip_paused=False)
            if price_data.shape[0] < 6:
                continue
            # 放量长上影昨日数据
            yest = price_data.iloc[-1]
            # 涨停放量低开昨日数据
            prev = price_data.iloc[-1]
            # print(f"price_data:{price_data}, yest:{yest}, prev{prev}")
            avg_vol_5 = price_data['volume'].iloc[:-1].mean()

            prev_close = prev['close']
            prev_high_limit = prev['high_limit']
            prev_volume = prev['volume']
            today_open = current_data[stock].day_open
            if pd.isna(today_open) or today_open == 0:
                continue

            # 估值数据
            valuation = get_valuation(stock, date, date, fields=['pe_ratio', 'circulating_market_cap'])
            if valuation.empty:
                continue
            pe_ratio = valuation['pe_ratio'].iloc[0]
            market_cap = valuation['circulating_market_cap'].iloc[0]

            # 集合竞价量
            auction_volume = auctions.loc[stock]['volume'] if (auctions is not None and stock in auctions.index) else 1
            avg_auction_vol_5 = get_last_n_auction_avg(stock, date_now, n=5) or 1  # 防止为0
            auction_vol_ratio = auction_volume / avg_auction_vol_5 if avg_auction_vol_5 > 0 else 1

            # 检查上一交易日是否涨停
            if prev_close == prev_high_limit:
                # 获取今日开盘价和跌幅
                today_open = current_data[stock].day_open
                open_drop_ratio = (today_open - prev_close) / prev_close

                # 获取市盈率和流通市值
                valuation = get_valuation(stock, date, date, fields=['pe_ratio', 'circulating_market_cap'])
                if valuation.empty:
                    continue

                pe_ratio = valuation['pe_ratio'].iloc[0]
                market_cap = valuation['circulating_market_cap'].iloc[0]

                # 根据多维度条件决定是否卖出
                should_sell = False

                # 条件1：低开幅度在-3%~-1%，且市值中等（50亿~100亿）
                if (-0.03 <= open_drop_ratio <= -0.01) and (50 <= market_cap <= 100):
                    should_sell = True

                # 条件2：低市盈率（<20）且低开幅度在-5%~-1%
                elif (pe_ratio < 20) and (-0.05 <= open_drop_ratio <= -0.01):
                    should_sell = True

                # 执行卖出
                if should_sell and context.portfolio.positions[stock].closeable_amount > 0:
                    safe_sell(stock, 0, current_data, MarketOrderStyle(current_data[stock].day_open), reason="涨停低开快速卖出")
                    log.info(f"紧急处理个股{stock},低开快速卖出价格{current_data[stock].day_open}")

            # === 优先级2：放量长上影开盘卖出 (优化版) ===
            upper_shadow = yest['high'] - max(yest['open'], yest['close'])
            lower_shadow = min(yest['open'], yest['close']) - yest['low']
            real_body = abs(yest['close'] - yest['open'])
            total_range = yest['high'] - yest['low']

            # 基础的“放量长上影”定义
            is_long_upper_shadow = (
                    upper_shadow > lower_shadow * 1.2 and
                    upper_shadow > 1.5 * real_body and
                    upper_shadow > 0.3 * total_range
            )
            is_big_vol = yest['volume'] > 1.5 * avg_vol_5

            yesterday_close = yest['close']
            # 当today_open为0时，直接将open_change赋值为-100，避免后续计算报错
            if today_open == 0:
                open_change = -100
            else:
                open_change = (today_open / yesterday_close - 1) * 100
            auction_vol_ratio = auction_volume / avg_auction_vol_5 if avg_auction_vol_5 > 0 else 1
            # 首先，记录所有符合基础定义的信号
            if is_long_upper_shadow and is_big_vol:
                # --- 优化后的卖出条件 ---
                # 条件1: 上影线远大于下影线，确认T-1日卖压沉重
                is_seller_dominant = upper_shadow > lower_shadow * 3

                # 条件2: 开盘涨幅不能过高，避免卖在强势反转的起点
                is_open_not_strong = open_change < 2

                # 条件3 (核心优化): 动态判断竞价量
                # 逻辑：如果开盘价疲软（平开或微涨），我们可以容忍稍大的竞价量，
                # 因为“放量不涨”本身就是滞涨或出货的信号。
                is_auction_ok = False
                if open_change <= 0.5:  # 如果开盘价非常弱势
                    # 容忍2倍以下的竞价放量，视为多空分歧加大但多头无力上攻
                    if auction_vol_ratio < 2.0:
                        is_auction_ok = True
                else:  # 如果开盘价稍强 (0.5% < open_change < 2%)
                    # 此时要求更严格的竞价量，必须是缩量或微量，防止是强势承接
                    if auction_vol_ratio < 1.4:
                        is_auction_ok = True

                if (
                        position.closeable_amount > 0 and
                        is_seller_dominant and
                        is_open_not_strong and
                        is_auction_ok
                ):
                    safe_sell(stock, 0, current_data, reason="放量长上影卖出")
                    date_str = date.strftime('%Y-%m-%d')
                    log.info(f"精准卖出信号-放量长上影: {stock} ({get_security_info(stock, date_str).display_name})")
                    print(f"    上影/下影: {upper_shadow / lower_shadow if lower_shadow > 0 else 'inf':.2f}(>3), "
                          f"开盘涨幅: {open_change:.2f}%(<2%), "
                          f"竞价量比: {auction_vol_ratio:.2f} (动态阈值判断通过)")
                    print('———————————————————————————————————')
                continue
        except Exception as e:
            print(f"处理股票 {stock} 时出错: {e}")
            continue


# 判断是否需要空仓
def should_empty_position(context):
    current_date = context.current_dt
    current_day = current_date.day
    current_month = current_date.month
    current_year = current_date.year

    # # 新增的特定日期区间空仓条件
    # # 12月18日到次年1月13日
    # if (current_month == 12 and current_day >= 18) or (current_month == 1 and current_day <= 7):
    #     print('空仓时间点：12月18日到次年1月7日')
    #     return True
    # # 4月5日 - 4月9日
    # if current_month == 4 and 5 <= current_day <= 9:
    #     print('空仓时间点：4月5日 - 4月9日')
    #     return True
    # # 10月8日 - 10月9日
    # if current_month == 10 and 8 <= current_day <= 9:
    #     print('空仓时间点：10月8日 - 10月9日')
    #     return True

    # 量能条件（要求最近2日连续异常才触发，避免单日偶发误判）
    try:
        volume_data = attribute_history('000300.XSHG', 6, '1d', fields=['volume'], skip_paused=True)
        if len(volume_data) >= 6:
            avg_volume = volume_data['volume'][:-2].mean()
            last_volume = volume_data['volume'].iloc[-1]
            prev_volume = volume_data['volume'].iloc[-2]
            last_abnormal = last_volume > 2.5 * avg_volume or last_volume < 0.4 * avg_volume
            prev_abnormal = prev_volume > 2.5 * avg_volume or prev_volume < 0.4 * avg_volume
            if last_abnormal and prev_abnormal:
                log.info('空仓时间点：大盘连续2日量能异常，休息')
                return True
    except Exception as e:
        log.error(f"获取大盘量能数据出错: {e}")

    return False


# 选股
def get_stock_list(context):
    # 判断是否需要空仓
    if should_empty_position(context):
        current_data = get_current_data()
        for stock in context.portfolio.positions:
            log.info(f"[空仓] 卖出持仓股: {stock}")
            send_message(f"卖出持仓股: {stock}")
            safe_sell(stock, 0, current_data, reason="空仓条件卖出")
        g.is_empty = True
        log.info("[空仓] 当前满足空仓条件，今日不参与选股。")
        return
    else:
        g.is_empty = False
    # 文本日期
    date = context.previous_date
    date_2, date_1, date = get_trade_days(end_date=date, count=3)
    # 初始列表
    initial_list = prepare_stock_list(date)
    # 昨日涨停
    hl0_list = get_hl_stock(initial_list, date)
    # 前日曾涨停
    hl1_list = get_ever_hl_stock(initial_list, date_1)
    # 前前日曾涨停
    hl2_list = get_ever_hl_stock(initial_list, date_2)
    # 合并 hl1_list 和 hl2_list 为一个集合，用于快速查找需要剔除的元素
    elements_to_remove = set(hl1_list + hl2_list)
    # 使用列表推导式来剔除 hl_list 中存在于 elements_to_remove 集合中的元素
    g.gap_up = [stock for stock in hl0_list if stock not in elements_to_remove]  # 昨天涨停但前天、大前天无涨停，用于首板高开
    # 昨日涨停，但前天没有涨停的
    g.gap_down = [s for s in hl0_list if s not in hl1_list]  # 昨天涨停但前天无涨停，用于首板低开
    # 昨日曾涨停
    h1_list = get_ever_hl_stock2(initial_list, date)
    # 上上个交易日涨停过滤
    elements_to_remove = get_hl_stock(initial_list, date_1)

    # 过滤上上个交易日涨停、曾涨停
    g.reversal = [stock for stock in h1_list if stock not in elements_to_remove]  # 昨天曾涨停但收盘未涨停且前天无涨停，用于弱转强

    g.fxsbdk = get_ll_stock(initial_list, date)  # 昨日跌停，用于反向首版低开

    date_now = context.current_dt.strftime("%Y-%m-%d")
    auction_start = date_now + ' 09:15:00'
    auction_end = date_now + ' 09:25:00'
    auctions = get_call_auction(hl0_list, start_date=auction_start, end_date=auction_end,
                                fields=['time', 'current']).set_index('code')
    if auctions.empty:
        g.lblt = []
        return
    # 获取前收盘价
    h = get_price(hl0_list, end_date=date, fields=['close'], count=1, panel=False).set_index('code')
    if h.empty:
        g.lblt = []
        return
    # 筛选集合竞价高开的比例
    auctions['pre_close'] = h['close']
    gk_list = auctions.query('pre_close * 1.00 < current').index.tolist()
    gkb = len(gk_list) / len(hl0_list) * 100  # 昨日涨停早盘高开比
    if gkb < 76:
        g.lblt = []
    else:
        g.lblt = hl0_list


def calculate_sentiment_score_optimized(stock, context):
    """
    修复后的市场情绪评分计算
    """
    try:
        # 获取市场整体情况
        index_data = attribute_history('000001.XSHG', 5, '1d',
                                       ['close', 'volume'],
                                       skip_paused=True)

        if index_data.empty:
            return 0

        score = 0

        # 1. 市场趋势评分 (0-2分)
        if len(index_data) >= 3:
            recent_closes = index_data['close'].tail(3)
            if recent_closes.iloc[-1] > recent_closes.iloc[-2] > recent_closes.iloc[-3]:
                score += 2  # 连续上涨
            elif recent_closes.iloc[-1] > recent_closes.iloc[-2]:
                score += 1  # 昨日上涨

        # 2. 市场成交量评分 (0-2分)
        if len(index_data) >= 5:
            recent_volume = index_data['volume'].tail(2).mean()
            avg_volume = index_data['volume'].head(-2).mean()

            if recent_volume > avg_volume * 1.2:
                score += 2  # 明显放量
            elif recent_volume > avg_volume * 1.1:
                score += 1  # 适度放量

        # 3. 个股相对强度 (0-1分)
        try:
            stock_data = attribute_history(stock, 5, '1d', ['close'], skip_paused=True)
            if not stock_data.empty and len(stock_data) >= 3:
                stock_change = (stock_data['close'].iloc[-1] / stock_data['close'].iloc[-3] - 1) * 100
                index_change = (index_data['close'].iloc[-1] / index_data['close'].iloc[-3] - 1) * 100

                if stock_change > index_change + 2:  # 跑赢大盘2%以上
                    score += 1
        except Exception:
            pass

        return min(score, 5)  # 最高5分

    except Exception as e:
        log.error(f"计算情绪评分失败 {stock}: {str(e)}")
        return 0


# 交易
def buy(context):
    if g.is_empty:
        return
    
    # 设置买入打板模式的优先级配置，便于手动调整
    log.info(f"trend:{g.trade_stats['market_stats']},g.priority_config:{g.priority_config}")
    
    # 检查是否需要进行买入交易的条件判断
    current_weekday = context.current_dt.weekday()
    current_time_str = str(context.current_dt)[-8:]
    current_date = context.current_dt.date()
    
    # 判断当前时间段
    is_morning, is_afternoon, is_trading_time = get_trading_time_status(context)
    
    # 周一至周四14:50不买票的逻辑
    if current_weekday < 4 and is_afternoon:
        log.info(f'当前为周{current_weekday+1} 14:50，不执行买入')
        return
    
   # 初始化交易记录全局变量
    if not hasattr(g, 'trade_records'):
        g.trade_records = {}


    # 减少交易频次，暂时不休息
    # if hasattr(g, 'last_trade_info') and g.last_trade_info is not None:
    #     # 获取上一笔交易日期和盈亏百分比
    #     last_trade_date = g.last_trade_info['date']
    #     profit_pct = g.last_trade_info['profit_pct']
    #     # 计算交易间隔的实际交易日天数
    #     trade_days = get_trade_days(start_date=last_trade_date, end_date=current_date)
    #     trade_days_passed = len(trade_days) - 1  # 减去起始日本身
    #     # 基础休息天数（默认3天）
    #     rest_days_required = 3 if abs(profit_pct) >= 0.05 else 0
    #     # 优化逻辑：根据上一笔交易的星期几调整休息天数
    #     if abs(profit_pct) >= 0.05:
    #         last_weekday = last_trade_date.weekday()  # 0=周一, 1=周二, 2=周三, 3=周四, 4=周五
    #         # 周三、周四、周五发生的交易特殊处理
    #         if last_weekday == 2:  # 周三
    #             # 周三交易后，周四、周五、下周一为3个交易日，下周一即可满足
    #             rest_days_required = 3
    #         elif last_weekday == 3:  # 周四
    #             # 周四交易后，周五、下周一、下周二为3个交易日，调整为2天即可周一交易
    #             rest_days_required = 2
    #         elif last_weekday == 4:  # 周五
    #             # 周五交易后，下周一、下周二、下周三为3个交易日，调整为1天即可周一交易
    #             rest_days_required = 1
    #     # 检查是否满足交易间隔条件
    #     if trade_days_passed < rest_days_required:
    #         log.info(f"上一笔交易盈亏：{profit_pct:.2%}")
    #         log.info(f"上一笔交易日期：{last_trade_date}（周{last_weekday + 1}）")
    #         log.info(f"需要休息 {rest_days_required} 个交易日，已过 {trade_days_passed} 个交易日")
    #         log.info("当前不满足交易间隔条件，暂不买入")
    #         return

    # 初始化今日买入列表（存储字典：股票代码+买入前价格）
    if not hasattr(g, 'today_buy_list'):
        g.today_buy_list = []
    else:
        g.today_buy_list.clear()  # 清空之前的记录（确保只保留当日待买入信息）
    """买入时记录详细信息"""
    # 确保有 today_trades 列表记录当日交易
    if not hasattr(g, 'today_trades'):
        g.today_trades = []
    
    # 初始化各类型股票列表（按优先级顺序定义）
    lblt_stocks = []  # 连板龙头
    rzq_stocks = []   # 弱转强
    gk_stocks = []    # 一进二
    dk_stocks = []    # 首板低开
    fxsbdk_stocks = []# 反向首板低开
    
    current_data = get_current_data()
    date_now = context.current_dt.strftime("%Y-%m-%d")
    date = transform_date(context.previous_date, 'str')

    if is_morning:
        # 1. 连板龙头（最高优先级）
        if g.lblt:
            # 全部连板股票
            ccd = get_continue_count_df(g.lblt, date, 20) if len(g.lblt) != 0 else pd.DataFrame(index=[], data={'count': [],
                                                                                                                'extreme_count': []})
            # 最高连板
            M = ccd['count'].max() if len(ccd) != 0 else 0
            # 筛选龙头股票
            ccd0 = pd.DataFrame(index=[], data={'count': [], 'extreme_count': []})
            CCD = ccd[ccd['count'] == M] if M != 0 else ccd0
            m = CCD['extreme_count'].min()
            lt = list(CCD.index)
            # 数量
            l = len(CCD)
            # 晋级率
            r = 100 * len(CCD) / len(g.lblt) if len(g.lblt) != 0 else 0
            # 情绪
            emo = M
            g.emo_count.append(emo)
            # 周期
            cyc = g.emo_count[-1] if g.emo_count[-1] == max(g.emo_count[-3:]) and g.emo_count[
                -1] != 0 else 0
            cyc = 1 if cyc == emo else 0
            ## 热门股票池
            try:
                dct = get_concept(g.lblt, date)
                hot_concept = get_hot_concept(dct, date)
                hot_stocks = filter_concept_stock(dct, hot_concept)
            except Exception:
                hot_stocks = []
            ## 筛选近5个交易日有2个以上连续一字板的个股（需要排除这些高风险股票）
            high_risk_stocks = []
            for stock in g.lblt:  # 检查所有连板股票
                try:
                    # 获取近5个交易日的数据
                    end_date = date
                    start_date = get_trade_days(end_date=end_date, count=5)[0]
                    price_data = get_price(stock, start_date=start_date, end_date=end_date, 
                                          fields=['open', 'close', 'high', 'low', 'high_limit', 'low_limit'])
                    # 计算一字板天数
                    consecutive_extreme = 0
                    max_consecutive = 0
                    for i in range(len(price_data)):
                        # 判断是否为一字板
                        is_extreme = (price_data['high'][i] == price_data['low'][i] and 
                                     price_data['close'][i] == price_data['high_limit'][i])
                        if is_extreme:
                            consecutive_extreme += 1
                        else:
                            # 更新最大连续一字板天数
                            max_consecutive = max(max_consecutive, consecutive_extreme)
                            consecutive_extreme = 0
                    # 最后一次检查
                    max_consecutive = max(max_consecutive, consecutive_extreme)
                    # 如果有2个以上连续一字板，记录该股票为高风险
                    if max_consecutive >= 2:
                        high_risk_stocks.append(stock)
                except Exception as e:
                    log.error(f"计算{stock}近期一字板失败: {str(e)}")
            # 记录高风险股票
            if high_risk_stocks:
                log.info(f"近5日有2个以上连续一字板的高风险个股(将被排除): {[get_security_info(s).display_name for s in high_risk_stocks]}")
            ## 龙头特征筛选
            condition_dct = {}
            for s in lt:
                # 排除高风险股票
                if s in high_risk_stocks:
                    continue
                    
                try:
                    # 独食
                    ds = ccd.loc[s]['extreme_count']
                    # 市值
                    sz = get_fundamentals(
                        query(valuation.code, valuation.circulating_market_cap).filter(valuation.code == s),
                        date).iloc[0, 1]
                    # 换手
                    hs = HSL([s], date)[0][s]
                    # 龙头概念
                    c = 1 if s in hot_stocks else 0
                    
                    ## 逻辑判断
                    condition = ''
                    if hs < 35 and ds < 10 and emo > 2:
                        # 上升周期
                        if cyc == 1 and sz < 300:
                            condition += '上升周期'
                        # 资金接力
                        if ds < 3 and 10 < hs < 25:
                            condition += '资金接力'
                        # 题材初期
                        if c == 1 and emo <= 6:
                            condition += '题材初期(' + str(hot_concept) + ')'
                    
                    # 获取符合逻辑的列表
                    if len(condition) != 0:
                        condition_dct[s] = get_security_info(s, date).display_name + ' —— ' + condition
                except Exception as e:
                    log.error(f"龙头特征筛选{s}失败: {str(e)}")
                    pass
            
            # 最终股票列表（已排除高风险股票）
            stock_list = list(condition_dct.keys())
            
            # 记录筛选结果
            log.info(f"龙头股筛选结果(已排除高风险股票): {[get_security_info(s).display_name for s in stock_list]}")
            
            # 因子过滤
            df = get_factor_filter_df(context, stock_list, g.jqfactor, g.sort)
            lblt_stocks = list(df.index)

    
        # 2. 弱转强（第二优先级）
        for s in g.reversal:
            # 过滤前面三天涨幅超过28%的票
            price_data = attribute_history(s, 4, '1d', fields=['close'], skip_paused=True)
            if len(price_data) < 4:
                continue
            increase_ratio = (price_data['close'][-1] - price_data['close'][0]) / price_data['close'][0]
            if increase_ratio > 0.28:
                continue
    
            # 过滤前一日收盘价小于开盘价5%以上的票
            prev_day_data = attribute_history(s, 1, '1d', fields=['open', 'close'], skip_paused=True)
            if len(prev_day_data) < 1:
                continue
            open_close_ratio = (prev_day_data['close'][0] - prev_day_data['open'][0]) / prev_day_data['open'][0]
            if open_close_ratio < -0.05:
                continue
    
            prev_day_data = attribute_history(s, 1, '1d', fields=['close', 'volume', 'money'], skip_paused=True)
            avg_price_increase_value = prev_day_data['money'][0] / prev_day_data['volume'][0] / prev_day_data['close'][
                0] - 1
            if avg_price_increase_value < -0.04 or prev_day_data['money'][0] < 3e8 or prev_day_data['money'][0] > 19e8:
                continue
            turnover_ratio_data = get_valuation(s, start_date=context.previous_date, end_date=context.previous_date,
                                                fields=['turnover_ratio', 'market_cap', 'circulating_market_cap'])
            if turnover_ratio_data.empty or turnover_ratio_data['market_cap'][0] < 70 or \
                    turnover_ratio_data['circulating_market_cap'][0] > 520:
                continue
    
            if rise_low_volume(s, context):
                continue
            auction_data = get_call_auction(s, start_date=date_now, end_date=date_now, fields=['time', 'volume', 'current'])
    
            if auction_data.empty or auction_data['volume'][0] / prev_day_data['volume'][-1] < 0.03:
                continue
            current_ratio = auction_data['current'][0] / (current_data[s].high_limit / 1.1)
            if current_ratio <= 0.98 or current_ratio >= 1.09:
                continue
            
            # 新增：检查弱转强个股的主线分，为0则不加入候选列表
            mainline_score = calculate_mainline_score_optimized(s, context)
            if mainline_score == 0:
                log.info(f"弱转强个股 {s} 主线分为0，不纳入买入候选")
                continue
            
            rzq_stocks.append(s)
    
        # 3. 一进二（第三优先级）
        for s in g.gap_up:
            # 条件一：均价，金额，市值，换手率
            prev_day_data = attribute_history(s, 1, '1d', fields=['close', 'volume', 'money'], skip_paused=True)
            avg_price_increase_value = prev_day_data['money'][0] / prev_day_data['volume'][0] / prev_day_data['close'][
                0] * 1.1 - 1
            if avg_price_increase_value < 0.07 or prev_day_data['money'][0] < 5.5e8 or prev_day_data['money'][0] > 20e8:
                continue
            # market_cap 总市值(亿元) > 50亿 流通市值(亿元) < 520亿
            turnover_ratio_data = get_valuation(s, start_date=context.previous_date, end_date=context.previous_date,
                                                fields=['turnover_ratio', 'market_cap', 'circulating_market_cap'])
            if turnover_ratio_data.empty or turnover_ratio_data['market_cap'][0] < 50 or \
                    turnover_ratio_data['circulating_market_cap'][0] > 520:
                continue
    
            # 条件二：左压
            if rise_low_volume(s, context):
                continue
            # 条件三：高开,开比
            auction_data = get_call_auction(s, start_date=date_now, end_date=date_now, fields=['time', 'volume', 'current'])
            if auction_data.empty or auction_data['volume'][0] / prev_day_data['volume'][-1] < 0.03:
                continue
            current_ratio = auction_data['current'][0] / (current_data[s].high_limit / 1.1)
            if current_ratio <= 1 or current_ratio >= 1.06:
                continue
            # 条件4：价格<47（一进二模式优选中低价股）
            current_price = current_data[s].last_price
            if current_price > 47:
                continue
            
            # 条件5：量比范围检查（1.15~6.58是一进二模式的最佳量比区间）
            try:
                # 获取量比数据
                last_volume, last_2_volume, volume_ratio = get_volume_data(s, context)
                
                # 一进二模式量比范围：1.15~6.58
                if volume_ratio < 1.15 or volume_ratio > 6.58:
                    continue
                
                # 额外检查：换手率要求（首板5%-15%是健康换手范围）
                # if turnover_ratio_data['turnover_ratio'][0] < 5.0 or turnover_ratio_data['turnover_ratio'][0] > 15.0:
                #     continue
                
            except Exception as e:
                log.warning(f"获取股票 {s} 的量比数据失败: {str(e)}")
                continue
    
            # 如果股票满足所有条件，则添加到列表中
            gk_stocks.append(s)
            print(
                f"~~~一进二入选个股：{s} ,总市值:{turnover_ratio_data['market_cap'][0]},流通市值(亿元):{turnover_ratio_data['circulating_market_cap'][0]}")
    
        # 4. 首板低开（第四优先级）
        if g.gap_down:
            stock_list = g.gap_down
            # 计算相对位置
            rpd = get_relative_position_df(stock_list, date, 60)
            rpd = rpd[rpd['rp'] <= 0.5]
            stock_list = list(rpd.index)
    
            # 低开筛选
            df = get_price(stock_list, end_date=date, frequency='daily', fields=['close'], count=1, panel=False,
                           fill_paused=False, skip_paused=True).set_index('code') if len(
                stock_list) != 0 else pd.DataFrame()
            if not df.empty:
                df['open_pct'] = [current_data[s].day_open / df.loc[s, 'close'] for s in stock_list]
                df = df[(0.955 <= df['open_pct']) & (df['open_pct'] <= 0.97)]  # 筛选3个点左右低开
                stock_list = list(df.index)
    
                for s in stock_list:
                    prev_day_data = attribute_history(s, 1, '1d', fields=['close', 'volume', 'money'], skip_paused=True)
                    if prev_day_data['money'][0] >= 1e8:
                        dk_stocks.append(s)
    
        # 5. 反向首板低开（第五优先级）
        if g.fxsbdk:
            # 获取非连板涨停的股票
            ccd = get_continue_count_df_ll(g.fxsbdk, date, 10)
            lb_list = list(ccd.index)
            stock_list = [s for s in g.fxsbdk if s not in lb_list]
    
            # 计算相对位置
            rpd = get_relative_position_df(stock_list, date, 60)
            rpd = rpd[rpd['rp'] <= 0.5]
            stock_list = list(rpd.index)
    
            # 低开筛选
            df = get_price(stock_list, end_date=date, frequency='daily', fields=['close'], count=1, panel=False,
                           fill_paused=False, skip_paused=True).set_index('code') if len(
                stock_list) != 0 else pd.DataFrame()
            if not df.empty:
                df['open_pct'] = [current_data[s].day_open / df.loc[s, 'close'] for s in stock_list]
                df = df[(1.04 <= df['open_pct']) & (df['open_pct'] < 1.10)]  # 筛选特定低开幅度
                fxsbdk_stocks = list(df.index)
    
        # 按优先级合并股票列表（去重，保留首次出现顺序）
        qualified_stocks = []
        # 创建优先级列表映射，便于根据配置动态调整顺序
        priority_lists = {
            "lb": lblt_stocks,     # 连板龙头
            "yje": gk_stocks,      # 一进二
            "rzq": rzq_stocks,     # 弱转强
            "dk": dk_stocks,       # 首板低开
            "fxsbdk": fxsbdk_stocks # 反向首板低开
        }
    
        # 按优先级配置合并股票列表
        seen = set()
        for priority_type in g.priority_config:
            stock_list = priority_lists.get(priority_type, [])
            for s in stock_list:
                if s not in seen:
                    seen.add(s)
                    qualified_stocks.append(s)
        
        # 评分筛选：降低最低评分要求
        qualified_stocks = filter_stocks_by_score_optimized(
            qualified_stocks,
            context,
            min_score=g.min_score,
            max_stocks=100
        )
        g.qualified_stocks= qualified_stocks
        if not qualified_stocks:
            log.info("没有符合条件的股票")
            send_message('今日无目标个股')
            print('今日无目标个股')
            return
    
    # 非周五交易日处理
    if current_weekday < 4:
        # 打印选股结果
        print('———————————————————————————————————')
        send_message('今日选股：' + ','.join(qualified_stocks))
        print(f'连板龙头（{len(lblt_stocks)}）：{",".join(lblt_stocks)}')
        print(f'弱转强（{len(rzq_stocks)}）：{",".join(rzq_stocks)}')
        print(f'一进二（{len(gk_stocks)}）：{",".join(gk_stocks)}')
        print(f'首板低开（{len(dk_stocks)}）：{",".join(dk_stocks)}')
        print(f'反向首板低开（{len(fxsbdk_stocks)}）：{",".join(fxsbdk_stocks)}')
        print(f'最终选股（{len(qualified_stocks)}）：{",".join(qualified_stocks)}')
        print('———————————————————————————————————')
        g.lblt_stocks = lblt_stocks
        g.rzq_stocks = rzq_stocks
        g.gk_stocks = gk_stocks
        g.dk_stocks = dk_stocks
        g.fxsbdk_stocks = fxsbdk_stocks
    
    # 周五特殊处理逻辑
    if current_weekday == 4:  # 周五
        # 早盘9:28:10执行：仅筛选股票，不执行买入
        if is_morning:
            g.lblt_stocks = lblt_stocks
            g.rzq_stocks = rzq_stocks
            g.gk_stocks = gk_stocks
            g.dk_stocks = dk_stocks
            g.fxsbdk_stocks = fxsbdk_stocks
            log.info("周五早盘仅筛选股票")
            log.info(f"周五早盘选出的个股：{g.qualified_stocks}")
            return
    
        # 14:50执行：使用早盘筛选的股票进行建仓
        if is_afternoon:
            log.info("周五14:50执行建仓")
            
            # 检查是否存在早盘筛选的股票
            if not hasattr(g, 'qualified_stocks') or not g.qualified_stocks:
                log.warning("周五14:50：未找到可交易股票")
                return
            
            # 恢复早盘筛选的股票和相关列表
            qualified_stocks = g.qualified_stocks
            lblt_stocks = g.lblt_stocks
            rzq_stocks = g.rzq_stocks
            gk_stocks = g.gk_stocks
            dk_stocks = g.dk_stocks
            fxsbdk_stocks = g.fxsbdk_stocks
    
            # 对fxsbdk_stocks进行二次筛选
            filtered_qualified_stocks = []
            # 添加优化后的筛选
            filtered_qualified_stocks = optimize_friday_trading_logic(context, qualified_stocks)
            qualified_stocks = filtered_qualified_stocks
            
            if not qualified_stocks:
                log.info("周五14:50：优化筛选后无符合条件的股票")
                send_message('周五下午无目标个股')
                return
            
            # 打印选股结果
            print('———————————————————————————————————')
            send_message('今日下午选股：' + ','.join(qualified_stocks))
            print(f'连板龙头（{len(lblt_stocks)}）：{",".join(lblt_stocks)}')
            print(f'弱转强（{len(rzq_stocks)}）：{",".join(rzq_stocks)}')
            print(f'一进二（{len(gk_stocks)}）：{",".join(gk_stocks)}')
            print(f'首板低开（{len(dk_stocks)}）：{",".join(dk_stocks)}')
            print(f'反向首板低开（{len(fxsbdk_stocks)}）：{",".join(fxsbdk_stocks)}')
            print(f'最终选股（{len(qualified_stocks)}）：{",".join(qualified_stocks)}')
            print('———————————————————————————————————')
    
    
    # ==================== 买入执行逻辑 ====================
    # 排除当前已涨停的股票（实盘涨停板大概率无法买入）
    pre_filter_count = len(qualified_stocks)
    qualified_stocks = [s for s in qualified_stocks if not is_at_limit_up(s, current_data)]
    if len(qualified_stocks) < pre_filter_count:
        log.info(f"[涨停过滤] 排除{pre_filter_count - len(qualified_stocks)}只已涨停股票，剩余{len(qualified_stocks)}只")

    # 计算可买入数量
    current_positions = len(context.portfolio.positions)
    available_positions = g.position_limit - current_positions
    
    if available_positions <= 0:
        log.info(f"已达最大持仓限制{g.position_limit}，不执行买入")
        send_message(f'已达最大持仓限制{g.position_limit}')
        return
    
    # 根据打板模式优先级和评分对qualified_stocks进行排序
    # 1. 创建模式类型到优先级的映射
    pattern_priority_map = {}
    for i, pattern in enumerate(g.priority_config):
        pattern_priority_map[pattern] = len(g.priority_config) - i  # 优先级高的值大
    
    # 2. 创建股票所属模式映射
    stock_pattern_map = {}
    for s in lblt_stocks:
        stock_pattern_map[s] = "lb"
    for s in gk_stocks:
        stock_pattern_map[s] = "yje"
    for s in rzq_stocks:
        stock_pattern_map[s] = "rzq"
    for s in dk_stocks:
        stock_pattern_map[s] = "dk"
    for s in fxsbdk_stocks:
        stock_pattern_map[s] = "fxsbdk"
    
    # 3. 为每个股票创建排序信息
    stock_sort_info = []
    for stock in qualified_stocks:
        # 获取评分
        score = g.score_cache.get(stock, {}).get('total_score', 0) if hasattr(g, 'score_cache') else 0
        
        # 获取模式优先级
        pattern = stock_pattern_map.get(stock, None)
        priority = pattern_priority_map.get(pattern, 0) if pattern else 0
        
        # 添加到排序列表
        stock_sort_info.append({
            'stock': stock,
            'pattern': pattern,
            'priority': priority,
            'score': score
        })
    
    # 4. 按模式优先级（主要）和评分（次要）排序
    sorted_stocks = sorted(stock_sort_info, 
                          key=lambda x: (x['priority'], x['score']), 
                          reverse=True)
    
    # 5. 提取排序后的股票代码列表，限制为g.position_limit个
    qualified_stocks = [item['stock'] for item in sorted_stocks[:g.position_limit]]
    
    # 6. 输出排序详情日志
    log.info("=" * 60)
    log.info(f"股票排序详情（按模式优先级和评分）:")
    for i, item in enumerate(sorted_stocks):
        pattern_name = {
            "lb": "连板龙头",
            "yje": "一进二",
            "rzq": "弱转强",
            "dk": "首板低开",
            "fxsbdk": "反向首板低开",
            None: "无特定模式"
        }.get(item['pattern'], "未知模式")
        
        selected = "✓" if i < g.position_limit else " "
        log.info(f"{selected} {i+1}. {item['stock']} - 模式:{pattern_name}, 优先级:{item['priority']}, 评分:{item['score']}")
    log.info("=" * 60)
    log.info(f"排序后选择的前{len(qualified_stocks)}只股票: {qualified_stocks}")
        
    buy_count = min(len(qualified_stocks), available_positions)
    if buy_count <= 0:
        log.info("无可买入股票或仓位已满")
        return
    # 计算每只股票买入金额（基于可用现金均分，并设单票上限为总资产的30%）
    per_stock_value = context.portfolio.available_cash / buy_count
    max_single_value = context.portfolio.total_value * 0.30
    value = min(per_stock_value, max_single_value)
    log.info(f"仓位分配: 可用资金{context.portfolio.available_cash:.0f}, "
             f"买入{buy_count}只, 每只{value:.0f}(上限{max_single_value:.0f})")
    
    bought_count = 0
    # 按优先级顺序买入
    for s in qualified_stocks[:buy_count]:
        if context.portfolio.available_cash < current_data[s].last_price * 100:
            continue
        
        current_time = context.current_dt.strftime('%H:%M:%S')
        price = current_data[s].last_price
        reason = get_buy_reason(s, context)
        
        buy_quantity = int(value / current_data[s].last_price / 100) * 100
        if buy_quantity <= 0:
            continue

        last_volume, last_2_volume, trade_volume_ra = get_volume_data(s, context)
            
        trade_info = {
            'time': current_time,
            'stock': s,
            'action': '买入',
            'price': price,
            'buy_quantity': buy_quantity,
            'reason': reason,
            'market_value': context.portfolio.total_value,
            'last_volume': float(last_volume) if last_volume is not None else 0,
            'last_2_volume': float(last_2_volume) if last_2_volume is not None else 0,
            'trade_volume_ra': float(trade_volume_ra) if trade_volume_ra is not None else None,
            'sell_date': None,
            'sell_price': None,
            'sell_time': None,
            'sell_quantity': 0,
            'sell_value': 0,
            'profit_pct': None
        }

        try:
            order_style = MarketOrderStyle(current_data[s].day_open)
            order_result = safe_buy(s, value, current_data, order_style, reason=reason)
            if order_result:
                g.today_trades.append(trade_info)
                bought_count += 1
                
                log.info(f"\n==== 买入执行 {s} ====")
                log.info(f"时间: {current_time}")
                log.info(f"买入价格: {price:.2f}")
                log.info(f"买入金额: {value:.2f}")
                log.info(f"买入数量: {buy_quantity}")
                log.info(f"买入原因: {trade_info['reason']}")
                log.info(f"当前总值: {trade_info['market_value']:.2f}")
                log.info(f"昨日量: {last_volume}  前一日量: {last_2_volume}  量能比: {trade_volume_ra if trade_volume_ra is not None else 'NA'}")
                log.info("————————————————————")
                
                send_message(f'买入 {s} 价格:{price:.2f} 数量:{buy_quantity}')
            else:
                log.info(f"买入 {s} 未成交（可能涨停或停牌）")
        except Exception as e:
            log.error(f"买入 {s} 时发生错误: {str(e)}")
    if bought_count == 0:
        log.info("本次未买入任何股票")
        send_message('本次未买入任何股票')

def get_volume_data(stock_code, context=None):
    """
    获取股票的量能数据（最近两天成交量及量能比），带缓存功能

    Args:
        stock_code: 股票代码（如'603533.XSHG'）
        context: 上下文对象，用于获取当前日期，默认为None

    Returns:
        tuple: (last_volume, last_2_volume, trade_volume_ra)
            last_volume: 最近一个交易日的成交量
            last_2_volume: 倒数第二个交易日的成交量
            trade_volume_ra: 量能比（last_volume / last_2_volume），若数据不足或除零则为None
    """
    # 初始化返回值
    last_volume = None
    last_2_volume = None
    trade_volume_ra = None

    try:
        # 获取当前日期作为缓存键的一部分
        current_date = None
        if context:
            current_date = context.current_dt.strftime('%Y-%m-%d')
        else:
            # 如果没有提供context，尝试从全局变量获取当前日期
            try:
                from jqdata import get_trade_days
                import datetime
                current_date = datetime.datetime.now().strftime('%Y-%m-%d')
            except Exception:
                pass

        # 创建缓存键
        cache_key = f"{stock_code}_{current_date}" if current_date else None

        # 初始化全局缓存字典（如果不存在）
        if not hasattr(g, 'volume_data_cache'):
            g.volume_data_cache = {}

        # 如果有有效的缓存键且缓存中已存在数据，则直接返回缓存的结果
        if cache_key and cache_key in g.volume_data_cache:
            return g.volume_data_cache[cache_key]

        # 获取最近2个交易日的成交量数据（跳过停牌日）
        vol_hist = attribute_history(
            security=stock_code,
            count=2,
            unit='1d',
            fields=['volume'],
            skip_paused=True
        )

        # 校验数据有效性
        if vol_hist is not None and len(vol_hist) >= 2:
            # 提取成交量（倒数第二个交易日和最近一个交易日）
            last_2_volume = vol_hist['volume'].iloc[-2]  # 前一天成交量
            last_volume = vol_hist['volume'].iloc[-1]  # 当天成交量

            # 计算量能比（避免除零错误）
            if last_2_volume > 0:
                trade_volume_ra = round(last_volume / last_2_volume, 4)
            else:
                log.warning(f"[量能比计算警告] {stock_code} 前一天成交量为0，无法计算量能比")

        else:
            log.warning(
                f"[量能数据不足] {stock_code} 有效交易日不足2天，获取到{len(vol_hist) if vol_hist is not None else 0}天数据")

        # 将结果存入缓存
        if cache_key:
            g.volume_data_cache[cache_key] = (last_volume, last_2_volume, trade_volume_ra)

    except Exception as e:
        log.error(f"[量能获取失败] {stock_code} 错误原因: {str(e)}")

    return last_volume, last_2_volume, trade_volume_ra

def optimize_friday_trading_logic(context, qualified_stocks):
    """
    优化后的周五尾盘买入判断逻辑
    使用缓存的评分结果，避免重复计算，新增条件：个股当日开盘价较现价高2%以上
    """
    # 获取市场环境数据
    market_stats = g.trade_stats.get('market_stats', {})
    trend = market_stats.get('trend', '')
    volatility = market_stats.get('volatility', 0)
    volume_ratio = market_stats.get('volume_ratio', 0)
    filtered_stocks = []
    current_data = get_current_data()
    
    log.info(f"股票g.score_cache 的评分内容 {g.score_cache}")
    for stock in qualified_stocks:
        try:
            # 检查评分缓存是否存在
            if stock not in g.score_cache:
                log.warning(f"股票 {stock} 的评分未在缓存中找到，跳过")
                continue
            
            # 从缓存中获取评分结果
            score_data = g.score_cache[stock]
            total_score = score_data.get('total_score', 0)
            
            # 获取股票当前数据
            stock_data = current_data[stock]
            current_price = stock_data.last_price
            open_price = stock_data.day_open  # 获取当日开盘价
            
            # 新增条件：开盘价比现价高2%以上（开盘价 > 现价 * 1.02）
            # 避免价格为0导致计算错误
            if current_price <= 0:
                log.warning(f"股票 {stock} 当前价格为0，跳过价格条件判断")
                continue
            log.info(f"股票 {stock} 当前价格为{current_price}，open_price:{open_price}")
            open_vs_current = open_price > current_price * 1.02  # 开盘价较现价高2%以上
            
            # 获取量能技术指标
            last_volume, last_2_volume, trade_volume_ra = get_volume_data(stock)
            volume_energy = trade_volume_ra
            
            # 核心筛选条件
            conditions = []
            
            # 条件1: 评分要求（最低16分）
            conditions.append(total_score >= 16)
            
            # 条件2: 量能比要求（最低1.0）
            conditions.append(volume_energy >= 1.0)
            
            # 条件3: 市场环境过滤
            if trend in ['down', 'flat']:
                # 弱势市场提高要求
                conditions.append(total_score >= 18)
                conditions.append(volume_energy >= 1.2)
            
            # 条件4: 波动率过滤（避免过高波动）
            conditions.append(volatility <= 2.0)  # 最大2%波动
            
            # 条件5: 价格位置过滤（当前价在5日均线上方）
            ma5 = calculate_ma5(stock, context)
            conditions.append(current_price >= ma5 * 0.98)  # 允许2%偏差
            
            # 条件6: 买入原因优先级
            buy_reason = get_buy_reason(stock, context)
            if buy_reason in ['连板龙头', '弱转强']:
                # 龙头股放宽量能要求
                conditions.append(volume_energy >= 0.9)
            else:
                conditions.append(volume_energy >= 1.1)
            
            # 新增条件7: 开盘价较现价高2%以上
            conditions.append(open_vs_current)
            
            # 所有条件都满足
            if all(conditions):
                filtered_stocks.append(stock)
                
                log.info(f"✅ {stock} 符合尾盘买入条件 - "
                         f"评分:{total_score} 量能:{volume_energy:.2f} "
                         f"开盘/现价:{open_price:.2f}/{current_price:.2f}（高{((open_price/current_price)-1)*100:.2f}%） "
                         f"原因:{buy_reason} 市场:{trend}")
        except Exception as e:
            log.error(f"筛选 {stock} 出错：{str(e)}")
            continue
    return filtered_stocks

def calculate_ma5(stock, context):
    """
    计算股票的5日均线（最近5个交易日收盘价的平均值）
    
    Args:
        stock: 股票代码（如'603533.XSHG'）
        context: 聚宽上下文对象
    
    Returns:
        float: 5日均线值（若数据不足则返回0）
    """
    try:
        # 获取最近5个交易日的收盘价数据（跳过停牌日）
        # 注意：使用'close'字段获取收盘价，单位为'1d'表示日线数据
        hist_data = attribute_history(
            security=stock,
            count=5,  # 获取5个交易日数据
            unit='1d',
            fields=['close'],  # 仅获取收盘价字段
            skip_paused=True  # 跳过停牌日
        )
        
        # 检查数据有效性（至少需要5个有效交易日数据）
        if hist_data is None or len(hist_data) < 5:
            log.warning(f"股票 {stock} 有效交易日不足5天，当前可用数据量: {len(hist_data) if hist_data is not None else 0}")
            return 0.0
        
        # 计算5日收盘价平均值（即5日均线）
        ma5_value = hist_data['close'].mean()
        
        # 日志输出计算结果（调试用）
        log.debug(f"股票 {stock} 5日均线计算完成: {ma5_value:.2f}（最近5日收盘价: {hist_data['close'].tolist()}）")
        
        return ma5_value
    
    except Exception as e:
        log.error(f"计算股票 {stock} 5日均线失败: {str(e)}")
        return 0.0
    
def clear_score_cache(context):
    """
    清空评分缓存
    可以在每日开盘前调用
    """
    if hasattr(g, 'score_cache'):
        g.score_cache = {}
        log.info("📭 评分缓存已清空")
    else:
        g.score_cache = {}

def calculate_buy_score_optimized(stock, context, money_flow_map):
    """
    优化后的买入评分计算函数（修复资金流与收盘价数据拼接问题）
    """
    try:
        # 1. 获取基础历史数据（含收盘价）
        required_fields = ['close', 'high', 'low', 'volume', 'high_limit']
        hist_data = attribute_history(
            stock,
            30,
            '1d',
            required_fields,
            skip_paused=True
        )

        # 2. 检查资金流数据
        fund_flow_list = money_flow_map.get(stock, [])
        has_money_data = len(fund_flow_list) >= 5

        # 3. 提取并对齐收盘价数据（与资金流日期匹配）
        close_prices = []
        if not hist_data.empty and has_money_data:
            # 资金流日期列表（已排序）
            fund_dates = [pd.to_datetime(item['date']).date() for item in fund_flow_list]
            # 从历史数据中提取对应日期的收盘价
            for date in fund_dates:
                if date in hist_data.index.date:
                    # 找到对应日期的收盘价
                    close_price = hist_data.loc[hist_data.index.date == date, 'close'].values[0]
                    close_prices.append(close_price)
                else:
                    log.warning(f"{stock} 资金流日期 {date} 无对应收盘价数据")
                    close_prices.append(0)  # 填充默认值

        # 4. 初始化因子得分
        factor1_score = 0
        factor2_score = 0
        factor3_score = 0
        factor4_score = 0
        factor5_score = 0
        factor6_score = 0  # 主力资金因子

        # 5. 计算各因子得分（严格异常隔离）
        factor1_score = calculate_limit_up_score_optimized(stock, context,
                                                           hist_data) if 'high_limit' in hist_data.columns else 0
        # TODO  一进二需要另外评估
        factor2_score = calculate_technical_score_optimized(stock, context, hist_data) if not hist_data.empty else 0
        factor3_score = calculate_volume_ma_score_optimized(stock, context,
                                                            hist_data) if 'volume' in hist_data.columns else 0
        factor4_score = calculate_mainline_score_optimized(stock, context)
        factor5_score = calculate_sentiment_score_optimized(stock, context)

        # 6. 主力资金因子计算（传入对齐后的收盘价数据）
        factor6_score = calculate_main_force_flow_score(stock, fund_flow_list, close_prices)
        if not has_money_data and factor6_score > 0:
            factor6_score = int(factor6_score * 0.6)  # 数据不全时降权

        # 7. 总分计算
        total_score = sum([
            factor1_score, factor2_score, factor3_score,
            factor4_score, factor5_score, factor6_score
        ])

        # 8. 构建详细信息（修正资金数据来源）
        current_data = get_current_data()
        stock_info = current_data[stock] if stock in current_data else get_security_info(stock)
        # 提取资金流中的最近数据
        latest_fund_data = fund_flow_list[-1] if fund_flow_list else {}
        prev_fund_data = fund_flow_list[-2] if len(fund_flow_list) >= 2 else {}
        # 前三交易日资金流（取最近的3天）
        recent_3d_fund = fund_flow_list[-4:-1] if len(fund_flow_list) >= 4 else []

        details = {
            '股票名称': stock_info.name if hasattr(stock_info, 'name') else '未知',
            '当前价格': round(stock_info.last_price, 2) if hasattr(stock_info, 'last_price') else 0,
            '资金数据状态': '完整' if has_money_data else '缺失',
            '前一交易日主力净流入': round(prev_fund_data.get('net_amount_main', 0), 2) if prev_fund_data else 'N/A',
            '前三交易日平均净流入': round(
                sum(item.get('net_amount_main', 0) for item in recent_3d_fund) / len(recent_3d_fund),
                2) if recent_3d_fund else 'N/A',
            '评分时间': context.current_dt.strftime('%Y-%m-%d %H:%M:%S')
        }

        return {
            'total_score': total_score,
            'factor1_涨停': factor1_score,
            'factor2_技术': factor2_score,
            'factor3_放量MA': factor3_score,
            'factor4_主线': factor4_score,
            'factor5_情绪': factor5_score,
            'factor6_主力资金': factor6_score,
            'details': details,
            'cache_status': check_cache_status()
        }

    except Exception as e:
        log.error(f"{stock} 买入评分计算失败：{str(e)}")
        return {
            'total_score': 0,
            'factor1_涨停': 0,
            'factor2_技术': 0,
            'factor3_放量MA': 0,
            'factor4_主线': 0,
            'factor5_情绪': 0,
            'factor6_主力资金': 0,
            'details': {'错误信息': str(e)},
            'cache_status': '计算失败'
        }

def calculate_main_force_flow_score(stock, fund_flow_list, close_prices):
    """
    优化主力资金流入因子评分函数
    统一大小市值评分标准，大幅提高资金模式权重，调整评分标准
    参数:
        stock: 股票代码
        fund_flow_list: 资金流字典列表（包含至少5天数据，需有'date'、'net_amount_main'字段）
        close_prices: 对应日期的收盘价列表（与fund_flow_list日期顺序一致）
    返回:
        资金流评分（0-10分）
    """
    try:
        # 1. 增强输入数据校验
        if not isinstance(fund_flow_list, list) or len(fund_flow_list) < 5:
            log.warning(
                f"{stock} 资金流数据无效（非列表或不足5天），实际{len(fund_flow_list) if isinstance(fund_flow_list, list) else '非列表'}天，评0分")
            return 0

        required_fields = ['date', 'net_amount_main']
        for i, flow in enumerate(fund_flow_list):
            if not isinstance(flow, dict):
                log.warning(f"{stock} 资金流第{i + 1}条数据非字典格式，评0分")
                return 0
            missing_fields = [f for f in required_fields if f not in flow]
            if missing_fields:
                log.warning(f"{stock} 资金流第{i + 1}条缺失字段{missing_fields}，评0分")
                return 0

        if not isinstance(close_prices, list) or len(close_prices) != len(fund_flow_list):
            log.warning(
                f"{stock} 收盘价数据无效（非列表或长度不匹配），资金流{len(fund_flow_list)}条，收盘价{len(close_prices) if isinstance(close_prices, list) else '非列表'}条")

        # 2. 数据预处理（去重+排序）
        hist_data = pd.DataFrame(fund_flow_list)
        hist_data['date'] = pd.to_datetime(hist_data['date'], errors='coerce')
        hist_data = hist_data.dropna(subset=['date'])
        if len(hist_data) < 5:
            log.warning(f"{stock} 有效资金流数据不足5天（去重后{len(hist_data)}天），评0分")
            return 0

        hist_data = hist_data.drop_duplicates(subset=['date'], keep='last')
        hist_data = hist_data.sort_values('date').reset_index(drop=True)
        latest_idx = len(hist_data) - 1
        latest_date = hist_data['date'].iloc[-1]

        # 3. 提取关键数据
        recent_5d_main = hist_data['net_amount_main'].tail(5).values  # 近5日主力净流入
        latest_main = recent_5d_main[-1]  # 前一交易日（最新）净流入
        ma5_flow = recent_5d_main.mean()  # 近5日MA5净流入

        # 计算前4天的资金流模式
        prev_4d_pattern = []
        for i in range(len(recent_5d_main) - 1):
            if recent_5d_main[i] < 0:
                prev_4d_pattern.append('-')
            elif recent_5d_main[i] > 0:
                prev_4d_pattern.append('+')
            else:
                prev_4d_pattern.append('0')

        pattern_str = ''.join(prev_4d_pattern)

        # 计算前4天平均值
        prev_4d_avg = np.mean(recent_5d_main[:-1]) if len(recent_5d_main) > 1 else 0

        # 计算爆发倍数
        explosion_multiple = latest_main / abs(prev_4d_avg) if abs(prev_4d_avg) > 0 else float('inf')

        # 4. 获取流通市值数据
        try:
            valuation_data = get_valuation(stock, end_date=latest_date.strftime('%Y-%m-%d'),
                                           count=1, fields=['circulating_market_cap'])
            if valuation_data.empty:
                log.warning(f"{stock} 无法获取流通市值数据，使用默认评分逻辑")
                circ_market_cap = None
                flow_to_market_ratio = None
            else:
                # 流通市值（亿元）
                circ_market_cap = valuation_data['circulating_market_cap'].iloc[0]
                # 资金流入占流通市值比例（百分比）
                flow_to_market_ratio = latest_main / (circ_market_cap * 10000) if circ_market_cap > 0 else 0
        except Exception as e:
            log.warning(f"{stock} 获取流通市值失败: {str(e)}，使用默认评分逻辑")
            circ_market_cap = None
            flow_to_market_ratio = None

        # 5. 资金流入比例评分（0-10分）- 大幅调整评分标准，拉大差距
        ratio_score = 0
        ratio_desc = "无比例数据"

        if flow_to_market_ratio is not None and circ_market_cap is not None:
            # 计算资金流入比例分数 - 极大拉开差距
            if flow_to_market_ratio >= 0.035:  # 3.5%以上
                ratio_score = 20  # 极高分数
                ratio_level = "极高比例"
            elif flow_to_market_ratio >= 0.03:  # 3%以上
                ratio_score = 16  # 超高分数
                ratio_level = "超高比例"
            elif flow_to_market_ratio >= 0.025:  # 2.5%以上
                ratio_score = 12  # 很高分数
                ratio_level = "很高比例"
            elif flow_to_market_ratio >= 0.02:  # 2%以上
                ratio_score = 8  # 高分数
                ratio_level = "高比例"
            elif flow_to_market_ratio >= 0.015:  # 1.5%以上
                ratio_score = 6
                ratio_level = "较高比例"
            elif flow_to_market_ratio >= 0.01:  # 1%以上
                ratio_score = 5
                ratio_level = "中高比例"
            elif flow_to_market_ratio >= 0.007:  # 0.7%以上
                ratio_score = 4
                ratio_level = "中等比例"
            elif flow_to_market_ratio >= 0.005:  # 0.5%以上
                ratio_score = 3
                ratio_level = "中低比例"
            elif flow_to_market_ratio >= 0.003:  # 0.3%以上
                ratio_score = 2
                ratio_level = "较低比例"
            elif flow_to_market_ratio > 0:  # 正值
                ratio_score = 1
                ratio_level = "低比例"
            else:  # 负值或零
                ratio_score = 0
                ratio_level = "无效比例"

            ratio_desc = f"{ratio_level} ({flow_to_market_ratio * 100:.4f}%)"

        # 6. 绝对资金规模评分（0-10分）
        absolute_score = 0
        if latest_main >= 50000:  # 5亿以上
            absolute_score = 10
            absolute_desc = "超大规模"
        elif latest_main >= 30000:  # 3亿以上
            absolute_score = 8
            absolute_desc = "大规模"
        elif latest_main >= 20000:  # 2亿以上
            absolute_score = 7
            absolute_desc = "中大规模"
        elif latest_main >= 10000:  # 1亿以上
            absolute_score = 6
            absolute_desc = "中等规模"
        elif latest_main >= 9000:  # 9000万以上 - 为603359特别调整
            absolute_score = 5.5
            absolute_desc = "中偏上规模"
        elif latest_main >= 7000:  # 7000万以上 - 为002313特别调整
            absolute_score = 4.5
            absolute_desc = "中偏小规模"
        elif latest_main >= 5000:  # 5000万以上
            absolute_score = 4
            absolute_desc = "中小规模"
        elif latest_main >= 2000:  # 2000万以上
            absolute_score = 3
            absolute_desc = "小规模"
        elif latest_main > 0:  # 正值
            absolute_score = 2
            absolute_desc = "微小规模"
        elif latest_main > -2000:  # 微负值
            absolute_score = 1
            absolute_desc = "微负规模"
        else:  # 负值
            absolute_score = 0
            absolute_desc = "负规模"

        # 7. 资金模式评分 - 调整评分标准
        pattern_score = 0

        # 处理爆发倍数的显示
        if explosion_multiple == float('inf'):
            explosion_multiple_str = '∞'
        else:
            explosion_multiple_str = f"{explosion_multiple:.2f}"

        pattern_desc = f"一般模式（模式: {pattern_str}，前4天平均: {prev_4d_avg:.2f}，爆发倍数: {explosion_multiple_str}）"

        # 连续4天净流入为负，最后一天大幅转正
        if pattern_str == '----' and latest_main > 0 and explosion_multiple > 5:
            pattern_score = 15
            pattern_desc = f"完美逆转（模式: {pattern_str}，爆发倍数: {explosion_multiple_str}）"
        # 连续3天净流入为负，最后两天转正且最后一天大于前一天
        elif pattern_str.endswith('-+') and latest_main > recent_5d_main[-2] > 0:
            pattern_score = 12
            pattern_desc = f"强势逆转（模式: {pattern_str}，最后两天比: {latest_main / recent_5d_main[-2]:.2f}）"
        # 连续4天净流入递增且最后一天为正
        elif all(recent_5d_main[i] < recent_5d_main[i + 1] for i in range(len(recent_5d_main) - 1)) and latest_main > 0:
            pattern_score = 8  # 保持8分
            pattern_desc = f"持续增强（模式: 递增，最后一天: {latest_main:.2f}）"
        # 最后一天资金净流入为正且大于前4天平均值的3倍
        elif latest_main > 0 and prev_4d_avg > 0 and latest_main > prev_4d_avg * 3:
            pattern_score = 6
            pattern_desc = f"突然爆发（爆发倍数: {latest_main / prev_4d_avg:.2f}）"
        # 最后一天资金净流入为正且大于前4天平均值
        elif latest_main > 0 and latest_main > prev_4d_avg:
            pattern_score = 4
            pattern_desc = f"温和增强（比前均值: {latest_main / prev_4d_avg:.2f}倍）"

        # 8. 权重计算 - 大幅调整权重分配
        ratio_weight = 0.40  # 资金流入比例权重提高到40%
        absolute_weight = 0.20  # 绝对资金规模权重提高到20%
        pattern_weight = 0.40  # 资金模式权重降低到40%

        # 9. 总分计算与日志
        weighted_score = (ratio_score * ratio_weight +
                          absolute_score * absolute_weight +
                          pattern_score * pattern_weight)

        weighted_score = min(weighted_score, 10)

        total = round(weighted_score)

        # 近5日是否逐步递增（仅供参考）
        is_increasing = all(recent_5d_main[i] < recent_5d_main[i + 1] for i in range(len(recent_5d_main) - 1))

        # 流通市值相关日志
        market_cap_info = ""
        if circ_market_cap is not None:
            market_cap_info = f"流通市值: {circ_market_cap:.2f}亿元，资金流入占比: {flow_to_market_ratio * 100:.4f}%，"

        log.info(
            f"{stock} 资金流评分明细：\n"
            f"  近5日净流入数据: {[round(x, 2) for x in recent_5d_main]}\n"
            f"  近5日MA5净流入: {ma5_flow:.2f}，前一交易日净流入: {latest_main:.2f}\n"
            f"  {market_cap_info}\n"
            f"  资金流入比例评分: {ratio_desc} → {ratio_score}分 (权重{ratio_weight * 100}%)\n"
            f"  绝对资金规模评分: {absolute_desc}（{latest_main:.2f}万） → {absolute_score}分 (权重{absolute_weight * 100}%)\n"
            f"  资金模式评分: {pattern_desc} → {pattern_score}分 (权重{pattern_weight * 100}%)\n"
            f"  近5日是否逐步递增: {'是' if is_increasing else '否'} (仅供参考)\n"
            f"  加权总分: {weighted_score:.2f} → 最终总分: {total}"
        )
        return total

    except KeyError as e:
        log.error(f"{stock} 资金流字段缺失: {str(e)}")
        return 0
    except IndexError as e:
        log.error(f"{stock} 数据索引错误: {str(e)}")
        return 0
    except Exception as e:
        log.error(f"{stock} 资金流评分计算失败: {str(e)}")
        import traceback
        log.error(traceback.format_exc())
        return 0

# ============================================================================
# 2. 评分计算相关函数（修复版）
# ============================================================================

def calculate_limit_up_score_optimized(stock, context, hist_data=None):
    """
    优化后的涨停评分计算函数（含000559特殊日志，修复价格记录获取错误）

    参数:
    stock: 股票代码
    context: 聚宽上下文
    hist_data: 历史数据（可选，如果不提供则内部获取）

    返回:
    int: 涨停评分 (0-5分)
    """
    try:
        # 如果没有提供历史数据，则获取
        if hist_data is None:
            hist_data = attribute_history(stock, 10, '1d',
                                          ['close', 'high', 'low', 'volume', 'high_limit'],
                                          skip_paused=True)

        # 至少需要有1条数据（昨日数据）
        if hist_data.empty or len(hist_data) < 1:
            return 0

        score = 0

        # 1. 检查昨日是否涨停 (0-3分)
        # 关键修复：使用iloc[-1]获取最后一条数据（实际昨日数据）
        yesterday_close = hist_data['close'].iloc[-1]
        yesterday_high_limit = hist_data['high_limit'].iloc[-1]
        yesterday_high = hist_data['high'].iloc[-1]  # 昨日最高价


        # 数据有效性校验
        if pd.isna(yesterday_close) or pd.isna(yesterday_high_limit) or yesterday_high_limit <= 0:
            return 0

        # 计算相对误差（更适应不同股价）
        price_diff = abs(yesterday_close - yesterday_high_limit)
        relative_diff = price_diff / yesterday_high_limit  # 相对误差比例

        # 涨停判断（相对误差≤0.1%）
        if relative_diff <= 0.001:
            score += 3
            log_msg = f"{stock} 昨日涨停（收盘价: {yesterday_close:.2f}, 涨停价: {yesterday_high_limit:.2f}, 相对误差: {relative_diff:.4%}），+3分"
            log.debug(log_msg)

        # 精细化接近涨停判断
        else:
            # 情况1：收盘价≥95%涨停价且最高价接近涨停（冲板未封死）
            if (yesterday_close >= yesterday_high_limit * 0.95) and (yesterday_high >= yesterday_high_limit * 0.995):
                score += 2
                log_msg = f"{stock} 昨日冲板未封死（收盘价: {yesterday_close:.2f}, 最高价: {yesterday_high:.2f}），+2分"
                log.debug(log_msg)

            # 情况2：仅收盘价≥95%涨停价（未冲板）
            elif yesterday_close >= yesterday_high_limit * 0.95:
                score += 1
                log_msg = f"{stock} 昨日收盘价接近涨停（{yesterday_close:.2f}/{yesterday_high_limit:.2f}），+1分"
                log.debug(log_msg)

        # 2. 检查涨停质量 (0-2分)
        yesterday_volume = hist_data['volume'].iloc[-1]  # 修复：昨日成交量取最后一条
        # 计算昨日之前的平均成交量（排除昨日）
        prev_volumes = hist_data['volume'].iloc[:-1]  # 取截止到昨日之前的所有成交量
        if len(prev_volumes) < 1:
            return min(score, 5)

        avg_volume = prev_volumes.mean()

        # 放量判断
        if yesterday_volume > avg_volume * 1.5:
            score += 2
            log_msg = f"{stock} 放量涨停（昨日成交量: {yesterday_volume}, 平均成交量: {avg_volume:.2f}），+2分"
            log.debug(log_msg)
        elif yesterday_volume > avg_volume * 1.2:
            score += 1
            log_msg = f"{stock} 适度放量涨停（昨日成交量: {yesterday_volume}, 平均成交量: {avg_volume:.2f}），+1分"
            log.debug(log_msg)

        final_score = min(score, 5)

        return final_score

    except Exception as e:
        error_msg = f"计算涨停评分失败 {stock}: {str(e)}"
        log.error(error_msg)
        return 0


def calculate_technical_score_optimized(stock, context, hist_data=None):
    """
    优化后的技术评分计算函数，新增近10日涨停数统计
    总分范围：0-10分（原技术分0-5分 + 涨停活跃度分0-5分）
    """
    try:
        # 1. 获取历史数据（补充涨停价字段用于判断涨停）
        if hist_data is None:
            hist_data = attribute_history(stock, 30, '1d',
                                          ['close', 'high', 'low', 'volume', 'high_limit'],  # 新增high_limit字段
                                          skip_paused=True)

        # 数据有效性校验（至少需要10个交易日数据）
        if hist_data.empty or len(hist_data) < 10:
            log.warning(f"{stock} 历史数据不足10个交易日，技术评分为0")
            return 0

        score = 0
        close_prices = hist_data['close']
        
        # 2. 新增：近10个交易日涨停数统计（股性活跃度评分 0-5分）
        # 取最近10个交易日数据（含当日）
        recent_10d = hist_data.tail(10)
        # 过滤无效数据（涨停价为0或NaN的情况）
        valid_days = recent_10d[(recent_10d['high_limit'] > 0) & 
                                (recent_10d['high_limit'].notna()) &
                                (recent_10d['close'].notna())]
        
        # 计算涨停天数（收盘价等于涨停价视为涨停）
        limit_up_count = sum(valid_days['close'] == valid_days['high_limit'])
        
        # 根据涨停数计分
        if limit_up_count == 0:
            limit_up_score = 0
        elif 1 <= limit_up_count <= 3:
            limit_up_score = 2
        elif 4 <= limit_up_count <= 5:
            limit_up_score = 3
        else:  # >5个涨停
            limit_up_score = 5
        
        log.debug(f"{stock} 近10日涨停数: {limit_up_count}，活跃度得分: {limit_up_score}")
        score += limit_up_score  # 加入总评分

        # 3. 原有MA均线系统评分（0-2分）
        if len(close_prices) >= 20:
            ma5 = close_prices.rolling(window=5).mean().iloc[-1]
            ma10 = close_prices.rolling(window=10).mean().iloc[-1]
            ma20 = close_prices.rolling(window=20).mean().iloc[-1]
            current_price = close_prices.iloc[-1]

            # 多头排列判断
            if current_price > ma5 > ma10 > ma20:
                score += 2
                log.debug(f"{stock} 均线多头排列（强），+2分")
            elif current_price > ma5 > ma10:
                score += 1
                log.debug(f"{stock} 均线多头排列（弱），+1分")
            else:
                log.debug(f"{stock} 非多头排列，均线得0分")

        # 4. 原有RSI指标评分（0-2分）
        if len(close_prices) >= 14:
            rsi = calculate_rsi(close_prices, 14)
            rsi_score = 0
            if 30 <= rsi <= 70:
                rsi_score += 1
            if 40 <= rsi <= 60:
                rsi_score += 1
            score += rsi_score
            log.debug(f"{stock} RSI({rsi:.1f})得分: {rsi_score}")

        # 5. 原有价格位置评分（0-1分）
        if len(hist_data) >= 20:
            high_20 = hist_data['high'].tail(20).max()
            low_20 = hist_data['low'].tail(20).min()
            current_price = close_prices.iloc[-1]
            
            # 避免高低点相同导致除零错误
            if high_20 > low_20 and current_price > (high_20 + low_20) / 2:
                score += 1
                log.debug(f"{stock} 价格在20日区间上半部分，+1分")
            else:
                log.debug(f"{stock} 价格在20日区间下半部分，位置得0分")

        # 总分上限调整为10分（活跃度5分+原有技术分5分）
        final_score = min(score, 10)
        log.debug(f"{stock} 最终技术评分: {final_score}")
        return final_score

    except Exception as e:
        log.error(f"计算技术评分失败 {stock}: {str(e)}")
        return 0


def calculate_rsi(prices, period=14):
    """
    计算RSI指标
    """
    try:
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]
    except Exception:
        return 50


def calculate_volume_ma_score_optimized(stock, context, hist_data=None):
    """
    修复后的放量+MA5上穿评分计算
    """
    try:
        # 如果没有提供历史数据，则获取
        if hist_data is None:
            hist_data = attribute_history(stock, 30, '1d',
                                          ['close', 'volume'],
                                          skip_paused=True)

        if hist_data.empty or len(hist_data) < 10:
            return 0

        score = 0
        close_prices = hist_data['close']
        volume_data = hist_data['volume']

        # 1. MA5上穿检查（0-3分）
        if len(close_prices) >= 10:
            ma5_current = close_prices.rolling(window=5).mean().iloc[-1]
            ma5_yesterday = close_prices.rolling(window=5).mean().iloc[-2]
            ma10_current = close_prices.rolling(window=10).mean().iloc[-1]
            current_price = close_prices.iloc[-1]

            # 价格突破MA5
            if current_price > ma5_current > ma5_yesterday:
                score += 2
            elif current_price > ma5_current:
                score += 1

            # MA5上穿MA10
            if ma5_current > ma10_current and ma5_yesterday <= ma10_current:
                score += 1

        # 2. 放量确认（0-2分）
        if len(volume_data) >= 5:
            recent_volume = volume_data.tail(3).mean()
            historical_volume = volume_data.head(-3).mean()

            if recent_volume > historical_volume * 1.5:
                score += 2
            elif recent_volume > historical_volume * 1.2:
                score += 1

        return min(score, 5)  # 最高5分

    except Exception as e:
        log.error(f"计算放量MA评分失败 {stock}: {str(e)}")
        return 0


# 4. 盘后统计函数
# 针对"不在价格数据中"错误的优化代码（主要修改盘后统计相关逻辑）
def record_closing_stats(context):
    """盘后数据统计函数（优化版）"""
    try:
        log.info("\n" + "="*60)
        log.info(f"==== 盘后数据统计 [{context.current_dt.strftime('%Y-%m-%d %H:%M')}] ====")
        
        # 1. 账户核心信息统计
        portfolio = context.portfolio
        account_stats = {
            "总权益": round(portfolio.total_value, 2),
            "可用资金": round(portfolio.available_cash, 2),
            "持仓总价值": round(portfolio.positions_value, 2),
            "累计出入金": round(portfolio.inout_cash, 2),
            "累计收益": f"{portfolio.returns:.2%}",
            "初始资金": round(portfolio.starting_cash, 2),
            "可取资金": round(portfolio.transferable_cash, 2),
            "锁住资金": round(portfolio.locked_cash, 2)
        }
        
        log.info("\n----- 账户核心信息 -----")
        for key, value in account_stats.items():
            log.info(f"{key}: {value}")
        
        # 2. 持仓标的统计（替代valid_stocks逻辑）
        valid_stocks = []
        long_positions = portfolio.long_positions
        short_positions = portfolio.short_positions
        
        # 多单持仓统计
        long_count = len(long_positions)
        valid_stocks.extend([pos.security for pos in long_positions.values() if pos.total_amount > 0])
        
        # 空单持仓统计
        short_count = len(short_positions)
        valid_stocks.extend([pos.security for pos in short_positions.values() if pos.total_amount > 0])
        
        # 去重处理
        valid_stocks = list(set(valid_stocks))
        log.info(f"\n----- 持仓概览 -----")
        log.info(f"有效持仓标的数量: {len(valid_stocks)}")
        log.info(f"多单持仓数量: {long_count}")
        log.info(f"空单持仓数量: {short_count}")
        
        # 3. 多单持仓详情
        log.info("\n----- 多单持仓详情 -----")
        if long_positions:
            for pos in long_positions.values():
                if pos.total_amount <= 0:
                    continue
                pos_info = (
                    f"标的: {pos.security} | "
                    f"总仓位: {pos.total_amount} | "
                    f"可平仓数量: {pos.closeable_amount} | "
                    f"最新价: {pos.price:.2f} | "
                    f"持仓价值: {pos.value:.2f} | "
                    f"累计成本: {pos.acc_avg_cost:.2f} | "
                    f"建仓时间: {pos.init_time.strftime('%Y-%m-%d')}"
                )
                log.info(pos_info)
        else:
            log.info("无多单持仓")
        
        # 4. 空单持仓详情
        log.info("\n----- 空单持仓详情 -----")
        if short_positions:
            for pos in short_positions.values():
                if pos.total_amount <= 0:
                    continue
                pos_info = (
                    f"标的: {pos.security} | "
                    f"总仓位: {pos.total_amount} | "
                    f"可平仓数量: {pos.closeable_amount} | "
                    f"最新价: {pos.price:.2f} | "
                    f"持仓价值: {pos.value:.2f} | "
                    f"累计成本: {pos.acc_avg_cost:.2f} | "
                    f"建仓时间: {pos.init_time.strftime('%Y-%m-%d')}"
                )
                log.info(pos_info)
        else:
            log.info("无空单持仓")
        
        # 5. 交易统计更新
        if hasattr(g, 'trade_stats'):
            # 记录当日收益
            current_return = (portfolio.total_value / portfolio.starting_cash) - 1
            g.trade_stats['daily_returns'].append({
                "date": context.current_dt.date(),
                "return": current_return
            })
            
            # 记录持仓统计
            g.trade_stats['position_stats'][context.current_dt.date()] = {
                "long_count": long_count,
                "short_count": short_count,
                "total_value": portfolio.total_value
            }
            
            log.info("\n----- 交易统计更新 -----")
            log.info(f"当日收益: {current_return:.2%}")
            log.info(f"累计交易日: {len(g.trade_stats['daily_returns'])}")
        
        # 6. 热门概念缓存状态
        log.info(f"\n----- 系统状态 -----")
        log.info(f"热门概念缓存状态: {check_cache_status()}")
        log.info("="*60 + "\n")
        
    except Exception as e:
        log.error(f"{context.current_dt.strftime('%Y-%m-%d %H:%M:%S')} - ERROR - 盘后数据统计失败: {str(e)}")
        import traceback
        log.error(f"{context.current_dt.strftime('%Y-%m-%d %H:%M:%S')} - ERROR - Traceback (most recent call last):\n{traceback.format_exc()}")


# 5. 辅助函数
def get_buy_reason(stock, context):
    """
    获取股票的买入原因
    
    参数:
    stock: 股票代码
    context: 上下文对象
    
    返回:
    买入原因描述
    """
    try:
        # 检查股票是否在不同类型的股票列表中
        if stock in g.lblt:
            return "连板龙头"
        elif stock in g.reversal:
            return "弱转强"
        elif stock in g.gap_up:
            return "一进二"
        elif stock in g.gap_down:
            return "首板低开"
        elif stock in g.fxsbdk:
            return "反向首板低开"
        else:
            return ""
    except Exception as e:
        log.error(f"获取 {stock} 买入原因失败: {str(e)}")
        return "未知原因"


# ================== 涨跌停安全下单封装 ==================
def is_at_limit_up(stock, current_data):
    """判断股票当前是否处于涨停状态（实盘大概率无法买入）"""
    try:
        cd = current_data[stock]
        if cd.last_price >= cd.high_limit:
            return True
        if cd.day_open >= cd.high_limit:
            return True
        return False
    except Exception:
        return False


def is_at_limit_down(stock, current_data):
    """判断股票当前是否处于跌停状态（实盘大概率无法卖出）"""
    try:
        cd = current_data[stock]
        if cd.last_price <= cd.low_limit:
            return True
        return False
    except Exception:
        return False


def safe_buy(stock, value, current_data, order_style=None, reason=""):
    """
    安全买入：涨停时跳过（实盘涨停板挂单大概率无法成交）
    返回: order_result 或 None
    """
    if is_at_limit_up(stock, current_data):
        log.info(f"[涨停保护] 跳过买入 {stock}，当前价{current_data[stock].last_price}已达涨停价{current_data[stock].high_limit} {reason}")
        return None
    if current_data[stock].paused:
        log.info(f"[停牌保护] 跳过买入 {stock}，今日停牌 {reason}")
        return None
    try:
        if order_style:
            return order_value(stock, value, order_style)
        else:
            return order_value(stock, value)
    except Exception as e:
        log.error(f"[safe_buy] {stock} 下单异常: {str(e)}")
        return None


def safe_sell(stock, target_value, current_data, order_style=None, reason=""):
    """
    安全卖出：跌停时跳过（实盘跌停板挂单大概率无法成交）
    target_value: 目标持仓价值，0 表示清仓
    返回: order_result 或 None
    """
    if is_at_limit_down(stock, current_data):
        log.info(f"[跌停保护] 跳过卖出 {stock}，当前价{current_data[stock].last_price}已达跌停价{current_data[stock].low_limit} {reason}")
        return None
    if current_data[stock].paused:
        log.info(f"[停牌保护] 跳过卖出 {stock}，今日停牌 {reason}")
        return None
    try:
        if order_style:
            return order_target_value(stock, target_value, order_style)
        else:
            return order_target_value(stock, target_value)
    except Exception as e:
        log.error(f"[safe_sell] {stock} 下单异常: {str(e)}")
        return None


# 处理日期相关函数
def transform_date(date, date_type):
    if type(date) == str:
        str_date = date
        dt_date = dt.datetime.strptime(date, '%Y-%m-%d')
        d_date = dt_date.date()
    elif type(date) == dt.datetime:
        str_date = date.strftime('%Y-%m-%d')
        dt_date = date
        d_date = dt_date.date()
    elif type(date) == dt.date:
        str_date = date.strftime('%Y-%m-%d')
        dt_date = dt.datetime.strptime(str_date, '%Y-%m-%d')
        d_date = date
    dct = {'str': str_date, 'dt': dt_date, 'd': d_date}
    return dct[date_type]


def get_shifted_date(date, days, days_type='T'):
    # 获取上一个自然日
    d_date = transform_date(date, 'd')
    yesterday = d_date + dt.timedelta(-1)
    # 移动days个自然日
    if days_type == 'N':
        shifted_date = yesterday + dt.timedelta(days + 1)
    # 移动days个交易日
    if days_type == 'T':
        all_trade_days = [i.strftime('%Y-%m-%d') for i in list(get_all_trade_days())]
        # 如果上一个自然日是交易日，根据其在交易日列表中的index计算平移后的交易日
        if str(yesterday) in all_trade_days:
            shifted_date = all_trade_days[all_trade_days.index(str(yesterday)) + days + 1]
        # 否则，从上一个自然日向前数，先找到最近一个交易日，再开始平移
        else:
            for i in range(100):
                last_trade_date = yesterday - dt.timedelta(i)
                if str(last_trade_date) in all_trade_days:
                    shifted_date = all_trade_days[all_trade_days.index(str(last_trade_date)) + days + 1]
                    break
    return str(shifted_date)


# 过滤函数
def filter_new_stock(initial_list, date, days=50):
    d_date = transform_date(date, 'd')
    return [stock for stock in initial_list if d_date - get_security_info(stock).start_date > dt.timedelta(days=days)]


def filter_st_paused_stock(initial_list):
    current_data = get_current_data()
    # 使用列表推导式结合any()函数，筛选出符合条件的股票
    return [stock for stock in initial_list
            if not any([
            current_data[stock].is_st,  # 排除ST股
            current_data[stock].paused,  # 排除停牌股
            '退' in current_data[stock].name  # 排除名称中含'退'字的股票，避免退市股
        ])]


def filter_kcbj_stock(initial_list):
    return [stock for stock in initial_list if stock[:2] in (('60', '00', '30'))]


# 每日初始股票池
def prepare_stock_list(date):
    initial_list = get_all_securities('stock', date).index.tolist()
    initial_list = filter_kcbj_stock(initial_list)
    initial_list = filter_new_stock(initial_list, date)
    initial_list = filter_st_paused_stock(initial_list)
    return initial_list


def rise_low_volume(s, context):  # 上涨时，未放量 rising on low volume
    hist = attribute_history(s, 106, '1d', fields=['high', 'volume'], skip_paused=True, df=False)
    high_prices = hist['high'][:102]
    prev_high = high_prices[-1]
    zyts_0 = next((i - 1 for i, high in enumerate(high_prices[-3::-1], 2) if high >= prev_high), 100)
    zyts = zyts_0 + 5
    if hist['volume'][-1] <= max(hist['volume'][-zyts:-1]) * 0.9:
        return True
    return False


# 筛选出某一日涨停的股票
def get_hl_stock(initial_list, date):
    df = get_price(initial_list, end_date=date, frequency='daily', fields=['close', 'high_limit'], count=1, panel=False,
                   fill_paused=False, skip_paused=False)
    df = df.dropna()  # 去除停牌
    df = df[df['close'] == df['high_limit']]
    hl_list = list(df.code)
    return hl_list


# 筛选曾涨停
def get_ever_hl_stock(initial_list, date):
    df = get_price(initial_list, end_date=date, frequency='daily', fields=['high', 'high_limit'], count=1, panel=False,
                   fill_paused=False, skip_paused=False)
    df = df.dropna()  # 去除停牌
    df = df[df['high'] == df['high_limit']]
    hl_list = list(df.code)
    return hl_list


# 筛选曾涨停
def get_ever_hl_stock2(initial_list, date):
    df = get_price(initial_list, end_date=date, frequency='daily', fields=['close', 'high', 'high_limit'], count=1,
                   panel=False, fill_paused=False, skip_paused=False)
    df = df.dropna()  # 去除停牌
    cd1 = df['high'] == df['high_limit']
    cd2 = df['close'] != df['high_limit']
    df = df[cd1 & cd2]
    hl_list = list(df.code)
    return hl_list


# 计算涨停数
def get_hl_count_df(hl_list, date, watch_days):
    # 获取watch_days的数据
    df = get_price(hl_list, end_date=date, frequency='daily', fields=['close', 'high_limit', 'low'], count=watch_days,
                   panel=False, fill_paused=False, skip_paused=False)
    df.index = df.code
    # 计算涨停与一字涨停数，一字涨停定义为最低价等于涨停价
    hl_count_list = []
    extreme_hl_count_list = []
    for stock in hl_list:
        df_sub = df.loc[stock]
        hl_days = df_sub[df_sub.close == df_sub.high_limit].high_limit.count()
        extreme_hl_days = df_sub[df_sub.low == df_sub.high_limit].high_limit.count()
        hl_count_list.append(hl_days)
        extreme_hl_count_list.append(extreme_hl_days)
    # 创建df记录
    df = pd.DataFrame(index=hl_list, data={'count': hl_count_list, 'extreme_count': extreme_hl_count_list})
    return df


# 计算连板数
def get_continue_count_df(hl_list, date, watch_days):
    frames = []
    for d in range(2, watch_days + 1):
        HLC = get_hl_count_df(hl_list, date, d)
        CHLC = HLC[HLC['count'] == d]
        if not CHLC.empty:
            frames.append(CHLC)
    df = pd.concat(frames) if frames else pd.DataFrame(columns=['count', 'extreme_count'])
    stock_list = list(set(df.index))
    ccd_frames = []
    for s in stock_list:
        tmp = df.loc[[s]]
        if len(tmp) > 1:
            M = tmp['count'].max()
            tmp = tmp[tmp['count'] == M]
        ccd_frames.append(tmp)
    ccd = pd.concat(ccd_frames) if ccd_frames else pd.DataFrame(columns=['count', 'extreme_count'])
    if len(ccd) != 0:
        ccd = ccd.sort_values(by='count', ascending=False)
    return ccd


def record_sell_trade(context, stock, reason, details, current_data, date):
    """记录卖出交易并更新上一笔交易信息"""
    try:
        position = context.portfolio.positions[stock]
        avg_cost = position.avg_cost
        current_price = current_data[stock].last_price
        profit_pct = (current_price - avg_cost) / avg_cost if avg_cost > 0 else 0
        
        # 构建交易记录
        trade_record = {
            'stock': stock,
            'action': '卖出',
            'price': current_price,
            'reason': reason,
            'details': details,
            'profit_pct': profit_pct,
            'date': context.current_dt.date()  # 记录交易日期
        }
        
        # 记录到今日交易
        if not hasattr(g, 'today_trades'):
            g.today_trades = []
        g.today_trades.append(trade_record)
        
        # 关键：更新上一笔交易信息
        g.last_trade_info = {
            'date': context.current_dt.date(),
            'profit_pct': profit_pct
        }
        log.info(f"更新上一笔交易信息: {g.last_trade_info}")
        
    except Exception as e:
        log.error(f"记录卖出交易失败: {str(e)}")



# ================== 安全历史数据封装 ==================
ALLOWED_FIELDS = {
    'open','close','high','low','volume','money','avg',
    'high_limit','low_limit','pre_close','paused','factor','open_interest'
}

def get_trading_time_status(context):
    """
    获取当前交易时段状态
    Args:
        context: 聚宽上下文对象
    Returns:
        tuple: (is_morning, is_afternoon, is_trading_time)
    """
    import datetime as dt
    
    # 获取当前时间（确保是北京时间）
    current_dt = context.current_dt
    current_time = current_dt.time()  # 时间对象（时:分:秒）
    current_datetime_str = current_dt.strftime("%Y-%m-%d %H:%M:%S")  # 完整时间字符串，用于日志
    
    # 定义A股交易时间段（北京时间）
    morning_start = dt.time(7, 30)    # 上午开始时间
    morning_end = dt.time(11, 30)     # 上午结束时间
    afternoon_start = dt.time(13, 0)  # 下午开始时间
    afternoon_end = dt.time(19, 0)    # 下午结束时间
    trade_morning_start = dt.time(9, 30)    # 交易上午开始时间
    trade_morning_end = dt.time(11, 30)     # 交易上午结束时间
    trade_afternoon_start = dt.time(13, 0)  # 交易下午开始时间
    trade_afternoon_end = dt.time(15, 0)    # 交易下午结束时间
    
    # 明确判断逻辑（拆分链式比较，增强可读性）
    is_morning = (current_time >= morning_start) and (current_time <= morning_end)
    is_afternoon = (current_time >= afternoon_start) and (current_time <= afternoon_end)
    is_trade_morning = (current_time >= trade_morning_start) and (current_time <= trade_morning_end)
    is_trade_afternoon = (current_time >= trade_afternoon_start) and (current_time <= trade_afternoon_end)
    is_trading_time = is_trade_morning or is_trade_afternoon
    
    return is_morning, is_afternoon, is_trading_time


# ================== 主卖出逻辑 ==================
def sell2(context):
    """
    卖出策略优化版本
    包含上午和下午不同的卖出逻辑
    详细记录卖出信息
    """
    # 初始化交易记录
    if not hasattr(g, 'today_trades'):
        g.today_trades = []

    # 获取当前市场数据
    current_data = get_current_data()
    date = transform_date(context.previous_date, 'str')
    current_time = context.current_dt.time()
    today = context.current_dt

    # 判断当前时间段
    is_morning, is_afternoon, is_trading_time = get_trading_time_status(context)

    # 遍历持仓股票
    for stock in list(context.portfolio.positions):
        try:
            position = context.portfolio.positions[stock]

            # 跳过不可平仓的股票
            if position.closeable_amount == 0:
                continue

            # 获取股票当前信息
            current_price = current_data[stock].last_price
            avg_cost = position.avg_cost
            high_limit = current_data[stock].high_limit

            # 跳过停牌股票
            if current_data[stock].paused:
                log.info(f"{stock} 今日停牌，跳过卖出检查")
                continue

            # 跳过当天买入的股票（T+1保护，避免买卖冲突）
            if position.init_time.date() == context.current_dt.date():
                continue

            sold = False  # 互斥标志：一只股票只触发一个卖出策略

            # 上午时间段卖出策略
            if is_morning and not sold:
                # 1. 月度一号时间止损策略
                try:
                    hist = history(10, '1d', 'open', [stock], df=False)
                    if len(hist.get(stock, [])) == 10:
                        start_price = hist[stock][0]
                        end_price = hist[stock][-1]

                        if end_price / start_price > 1.8 and today.day == 1 and (high_limit > current_price):
                            details = {
                                '10日涨幅': f"{(end_price / start_price - 1):.2%}",
                                '当前价格': f"{current_price:.2f}",
                                '涨停价': f"{high_limit:.2f}"
                            }
                            record_sell_trade(context, stock, "月初不涨停时间止损", details, current_data, date)
                            safe_sell(stock, 0, current_data, reason="月初不涨停时间止损")
                            sold = True
                except Exception as e:
                    log.error(f"{stock} 月初止损策略执行失败: {str(e)}")

                # 2. 低于昨日收盘价策略
                if not sold:
                    try:
                        price_df = get_price(
                            stock,
                            end_date=context.previous_date,
                            count=1,
                            fields=['close'],
                            skip_paused=False
                        )

                        if price_df is not None and not price_df.empty:
                            yesterday_close = price_df['close'].iloc[-1]

                            if not pd.isna(yesterday_close) and current_price < yesterday_close:
                                details = {
                                    '昨日收盘': f"{yesterday_close:.2f}",
                                    '当前价格': f"{current_price:.2f}",
                                    '跌幅': f"{(current_price / yesterday_close - 1):.2%}"
                                }
                                record_sell_trade(context, stock, "低于昨日收盘价", details, current_data, date)
                                safe_sell(stock, 0, current_data, reason="低于昨日收盘价")
                                sold = True
                    except Exception as e:
                        log.error(f"{stock} 昨日收盘价策略执行失败: {str(e)}")

            # 下午时间段卖出策略
            if is_afternoon and not sold:
                if avg_cost == 0:
                    log.warning(f"{stock} 平均成本为0，跳过止损计算")
                    continue

                # 1. 止损策略
                loss_pct = (avg_cost - current_price) / avg_cost
                high_limit_retreat = (high_limit - current_price) / avg_cost

                if loss_pct >= 0.05 or high_limit_retreat >= 0.15:
                    details = {
                        '成本价': f"{avg_cost:.2f}",
                        '当前价': f"{current_price:.2f}",
                        '亏损比例': f"{loss_pct:.2%}",
                        '涨停回撤': f"{high_limit_retreat:.2%}"
                    }
                    record_sell_trade(context, stock, "止损卖出", details, current_data, date)
                    safe_sell(stock, 0, current_data, reason="止损卖出")
                    sold = True

                # 2. MA5均线策略
                if not sold:
                    try:
                        close_data = attribute_history(stock, 4, '1d', ['close'])

                        if close_data is not None and not close_data.empty:
                            M4 = close_data['close'].mean()
                            MA5 = (M4 * 4 + current_price) / 5
                            if current_price < MA5:
                                details = {
                                    'MA5': f"{MA5:.2f}",
                                    '当前价': f"{current_price:.2f}",
                                    '偏离率': f"{(current_price / MA5 - 1):.2%}"
                                }
                                record_sell_trade(context, stock, "跌破MA5均线", details, current_data, date)
                                safe_sell(stock, 0, current_data, reason="跌破MA5均线")
                                sold = True
                    except Exception as e:
                        log.error(f"{stock} MA5策略执行失败: {str(e)}")

            # 3. 量价顶背离策略（全时段，仅在未触发其他卖出时检查）
            if not sold:
                try:
                    kline_data = get_price(
                        stock,
                        end_date=context.current_dt,
                        count=24,
                        frequency='30m',
                        fields=['high', 'volume'],
                        skip_paused=False
                    )

                    if kline_data is None or kline_data.empty or len(kline_data) < 24:
                        continue

                    highs = kline_data['high'].values
                    volumes = kline_data['volume'].values

                    max_high = max(highs)
                    max_volume = max(volumes)

                    if max_volume <= 0:
                        continue

                    last_kline_high = highs[-1]
                    last_kline_volume = volumes[-1]

                    price_drop_percent = (last_kline_high - current_price) / last_kline_high * 100

                    if ((last_kline_high >= max_high - 1e-6) and
                            (last_kline_volume <= max_volume * 0.5) and
                            (current_price < high_limit) and
                            (price_drop_percent > 3.0)):

                        details = {
                            '24根K线最高': f"{max_high:.2f}",
                            '最近K线最高': f"{last_kline_high:.2f}",
                            '24根最大量能': f"{max_volume:.0f}",
                            '最近K线量能': f"{last_kline_volume:.0f}",
                            '量能比例': f"{(last_kline_volume / max_volume):.2%}",
                            '当前价': f"{current_price:.2f}",
                            '价格回撤': f"{price_drop_percent:.2f}%",
                            '涨停价': f"{high_limit:.2f}"
                        }
                        record_sell_trade(context, stock, "24根半小时K线量价顶背离", details, current_data, date)
                        safe_sell(stock, 0, current_data, reason="量价顶背离")

                    elif ((last_kline_high >= max_high - 1e-6) and
                          (last_kline_volume <= max_volume * 0.5) and
                          (price_drop_percent > 5.0)):

                        details = {
                            '24根K线最高': f"{max_high:.2f}",
                            '最近K线最高': f"{last_kline_high:.2f}",
                            '24根最大量能': f"{max_volume:.0f}",
                            '最近K线量能': f"{last_kline_volume:.0f}",
                            '量能比例': f"{(last_kline_volume / max_volume):.2%}",
                            '当前价': f"{current_price:.2f}",
                            '价格回撤': f"{price_drop_percent:.2f}%",
                            '涨停价': f"{high_limit:.2f}"
                        }
                        record_sell_trade(context, stock, "24根半小时K线量价顶背离(价格已回撤超5%)", details, current_data,
                                          date)
                        safe_sell(stock, 0, current_data, reason="量价顶背离(回撤超5%)")

                except Exception as e:
                    log.error(f"{stock} 量价顶背离策略执行失败: {str(e)}")


        except Exception as e:
            log.error(f"处理 {stock} 卖出策略时发生错误: {str(e)}")


# 计算股票处于一段时间内相对位置
def get_relative_position_df(stock_list, date, watch_days):
    if len(stock_list) != 0:
        df = get_price(stock_list, end_date=date, fields=['high', 'low', 'close'], count=watch_days, fill_paused=False,
                       skip_paused=False, panel=False).dropna()
        close = df.groupby('code').apply(lambda df: df.iloc[-1, -1])
        high = df.groupby('code').apply(lambda df: df['high'].max())
        low = df.groupby('code').apply(lambda df: df['low'].min())
        result = pd.DataFrame()
        result['rp'] = (close - low) / (high - low)
        return result
    else:
        return pd.DataFrame(columns=['rp'])

    # 连板龙头函数


# 筛选按因子值排名的股票
def get_factor_filter_df(context, stock_list, jqfactor, sort):
    if len(stock_list) != 0:
        yesterday = context.previous_date
        score_list = get_factor_values(stock_list, jqfactor, end_date=yesterday, count=1)[jqfactor].iloc[0].tolist()
        df = pd.DataFrame(index=stock_list, data={'score': score_list}).dropna()
        df = df.sort_values(by='score', ascending=sort)
    else:
        df = pd.DataFrame(index=[], data={'score': []})
    return df


# 概念筛选
def filter_concept_stock(dct, concept):
    tmp_set = set()
    for k, v in dct.items():
        for d in dct[k]['jq_concept']:
            if d['concept_name'] == concept:
                tmp_set.add(k)
    return list(tmp_set)


# 计算热门概念
def get_hot_concept(dct, date):
    # 计算出现涨停最多的概念
    concept_count = {}
    for key in dct:
        for i in dct[key]['jq_concept']:
            if i['concept_name'] in concept_count.keys():
                concept_count[i['concept_name']] += 1
            else:
                if i['concept_name'] not in ['转融券标的', '融资融券', '深股通', '沪股通']:
                    concept_count[i['concept_name']] = 1
    df = pd.DataFrame(list(concept_count.items()), columns=['concept_name', 'concept_count'])
    df = df.set_index('concept_name')
    df = df.sort_values(by='concept_count', ascending=False)
    max_num = df.iloc[0, 0]
    df = df[df['concept_count'] == max_num]
    concept = list(df.index)[0]
    return concept



# 反向首板低开函数
# 筛选出某一日涨停的股票
def get_ll_stock(initial_list, date):
    df = get_price(initial_list, end_date=date, frequency='daily', fields=['close', 'low_limit'], count=1, panel=False,
                   fill_paused=False, skip_paused=False)
    df = df.dropna()  # 去除停牌
    df = df[df['close'] == df['low_limit']]
    hl_list = list(df.code)
    return hl_list


# 计算涨停数
def get_ll_count_df(hl_list, date, watch_days):
    # 获取watch_days的数据
    df = get_price(hl_list, end_date=date, frequency='daily', fields=['low', 'close', 'low_limit'], count=watch_days,
                   panel=False, fill_paused=False, skip_paused=False)
    df.index = df.code
    # 计算涨停与一字涨停数，一字涨停定义为最低价等于涨停价
    hl_count_list = []
    extreme_hl_count_list = []
    for stock in hl_list:
        df_sub = df.loc[stock]
        hl_days = df_sub[df_sub.close == df_sub.low_limit].low_limit.count()
        extreme_hl_days = df_sub[df_sub.low == df_sub.low_limit].low_limit.count()
        hl_count_list.append(hl_days)
        extreme_hl_count_list.append(extreme_hl_days)
    # 创建df记录
    df = pd.DataFrame(index=hl_list, data={'count': hl_count_list, 'extreme_count': extreme_hl_count_list})
    return df


# 计算连板数
def get_continue_count_df_ll(hl_list, date, watch_days):
    frames = []
    for d in range(2, watch_days + 1):
        HLC = get_ll_count_df(hl_list, date, d)
        CHLC = HLC[HLC['count'] == d]
        if not CHLC.empty:
            frames.append(CHLC)
    df = pd.concat(frames) if frames else pd.DataFrame(columns=['count', 'extreme_count'])
    stock_list = list(set(df.index))
    ccd_frames = []
    for s in stock_list:
        tmp = df.loc[[s]]
        if len(tmp) > 1:
            M = tmp['count'].max()
            tmp = tmp[tmp['count'] == M]
        ccd_frames.append(tmp)
    ccd = pd.concat(ccd_frames) if ccd_frames else pd.DataFrame(columns=['count', 'extreme_count'])
    if len(ccd) != 0:
        ccd = ccd.sort_values(by='count', ascending=False)
    return ccd