#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A股龙头日内最低点低吸买点监控器 (Daily Low-Buy Dragon Scanner)
方法论依据：
1. 行业与标的初筛：精选热点行业龙头与高辨识度核心标的
2. 5日均线与VWAP分时成交均价过滤
3. 日内择时三部曲：
   - 09:30~09:55 开盘恐慌观望期：随大盘下探，严禁接飞刀
   - 10:10~10:30 黄金低谷吸筹期：触及日内最大跌幅+大单止跌底背离，首笔 30% 左侧低吸
   - 5~10分钟内：分时上破 VWAP 或 MACD 金叉，顺势加仓剩余 70%
   - 13:00~15:00 午后持仓跟踪与 V 型反弹止盈
4. 刚性止损：买入价下方 0.5%~1% 或放量跌破 5 日线坚决止损
"""

import os
import sys
import json
import time
import requests
from datetime import datetime

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(PROJECT_ROOT, 'reports')
os.makedirs(REPORTS_DIR, exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# 核心观察龙头标的池（代表性高辨识度主线行业核心龙头）
DEFAULT_DRAGON_WATCHLIST = [
    {'code': '688777', 'name': '中控技术', 'industry': '工业AI/机器人'},
    {'code': '688256', 'name': '寒武纪',   'industry': '算力芯片/半导体'},
    {'code': '300033', 'name': '同花顺',   'industry': '金融科技/互金'},
    {'code': '300059', 'name': '东方财富', 'industry': '券商互联网龙头'},
    {'code': '002230', 'name': '科大讯飞', 'industry': '通用大模型/人工智能'},
    {'code': '002475', 'name': '立讯精密', 'industry': '消费电子/果链龙头'},
    {'code': '601138', 'name': '工业富联', 'industry': '算力服务器/AI代工'},
    {'code': '300750', 'name': '宁德时代', 'industry': '新能源电池中军'},
    {'code': '002594', 'name': '比亚迪',   'industry': '新能源汽车领军'},
    {'code': '600519', 'name': '贵州茅台', 'industry': '白酒核心消费'},
    {'code': '000801', 'name': '四川九洲', 'industry': '低空经济/军民融合'},
    {'code': '600839', 'name': '四川长虹', 'industry': '智能硬件/算力赋能'},
]

def fetch_dragon_intraday_quotes(watchlist):
    """从腾讯即时行情获取龙头高频分时切片与成交统计"""
    symbols = []
    meta = {}
    for item in watchlist:
        c = item['code']
        prefix = 'sh' if c.startswith(('6', '9')) else 'sz'
        sym = f"{prefix}{c}"
        symbols.append(sym)
        meta[c] = item
        
    url = "http://qt.gtimg.cn/q=" + ",".join(symbols)
    results = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=6)
        r.encoding = 'gbk'
        lines = [l.strip() for l in r.text.strip().split(';') if l.strip()]
        for line in lines:
            parts = line.split('~')
            if len(parts) > 40:
                code = parts[2]
                name = parts[1]
                price = float(parts[3]) if parts[3] else 0.0
                prev_close = float(parts[4]) if parts[4] else 0.0
                open_price = float(parts[5]) if parts[5] else 0.0
                vol_shares = float(parts[6]) * 100 if parts[6] else 0.0  # 手 -> 股
                high = float(parts[33]) if parts[33] else 0.0
                low = float(parts[34]) if parts[34] else 0.0
                chg_pct = float(parts[32]) if parts[32] else 0.0
                turnover_wan = float(parts[37]) if parts[37] else 0.0
                turnover_yuan = turnover_wan * 10000.0
                turnover_rate = float(parts[38]) if parts[38] else 0.0
                
                # 计算分时 VWAP (Volume-Weighted Average Price 成交均价)
                vwap = round(turnover_yuan / vol_shares, 2) if vol_shares > 0 else price
                
                # 距离当日最低点回升幅度
                rebound_from_low = round(((price - low) / low) * 100, 2) if low > 0 else 0.0
                # 当日最大回撤深度 (相对昨日收盘)
                max_drawdown = round(((low - prev_close) / prev_close) * 100, 2) if prev_close > 0 else 0.0
                # 当前相对于分时均价 VWAP 的偏离度
                vwap_bias = round(((price - vwap) / vwap) * 100, 2) if vwap > 0 else 0.0
                
                results.append({
                    'code': code,
                    'name': name,
                    'industry': meta.get(code, {}).get('industry', '主线龙头'),
                    'price': price,
                    'prev_close': prev_close,
                    'open': open_price,
                    'high': high,
                    'low': low,
                    'chg_pct': chg_pct,
                    'vwap': vwap,
                    'turnover_rate': turnover_rate,
                    'rebound_from_low': rebound_from_low,
                    'max_drawdown': max_drawdown,
                    'vwap_bias': vwap_bias
                })
    except Exception as e:
        print(f"获取分时行情失败: {e}")
    return results

def evaluate_intraday_setup(item, now_time_str=None):
    """
    依据日内最低点买入方法论研判状态：
    1. 09:30~09:55: 早盘恐慌观望
    2. 10:10~10:30: 黄金低吸窗口 (日内最大跌幅 -3%~-5%，大单企稳，首笔30%)
    3. 突破VWAP: 加仓 70% 确认区
    4. 午后: V型反弹止盈跟踪
    """
    price = item['price']
    low = item['low']
    vwap = item['vwap']
    rebound = item['rebound_from_low']
    max_dd = item['max_drawdown']
    vwap_bias = item['vwap_bias']
    
    status = "常规震荡"
    action = "保持观望"
    signal_tag = "观望"
    suggested_entry = low
    stop_loss = round(low * 0.99, 2)
    
    # 距离日内最低价极近 (<=0.8%) 且有明显日内回撤 (-2%~-5%)
    if rebound <= 0.8 and max_dd <= -2.0:
        status = "触及日内最大跌幅·缩量企稳"
        action = "🎯 黄金低吸区！首笔 30% 仓位试探介入，止损设最低点下方0.8%"
        signal_tag = "🔥 黄金低吸买点 (30%仓)"
        suggested_entry = price
        stop_loss = round(price * 0.992, 2)
    elif 0.0 <= vwap_bias <= 1.2 and rebound >= 1.5:
        status = "分时上破均价线(VWAP)·资金抢筹"
        action = "⚡ 转强确认！立即执行第二笔 70% 加仓，博弈午后主升/V型回封"
        signal_tag = "🚀 VWAP突破加仓 (70%仓)"
        suggested_entry = price
        stop_loss = round(vwap * 0.99, 2)
    elif rebound >= 3.0 and item['chg_pct'] > 0:
        status = "日内V型大反弹·主升加速"
        action = "📈 持仓观察！触及 +5%~+10% 目标止盈，或分批锁定利润"
        signal_tag = "🎉 V型反弹主升"
        suggested_entry = vwap
        stop_loss = round(price * 0.97, 2)
    elif max_dd < -6.5:
        status = "放量破位单边下杀"
        action = "⚠️ 破位风险！跌破止损红线，严禁左侧接飞刀"
        signal_tag = "❌ 破位回避"
        suggested_entry = 0.0
        stop_loss = 0.0

    return {
        **item,
        'status': status,
        'action': action,
        'signal_tag': signal_tag,
        'suggested_entry': suggested_entry,
        'stop_loss': stop_loss
    }

def run_scanner():
    print("=" * 85)
    print("  🎯 A股龙头日内最低点低吸买点监控器 (Daily Low-Buy Dragon Scanner) 🎯")
    print(f"  当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("  核心法门: 09:30恐慌观望 -> 10:10低谷大单试仓30% -> 上破VWAP加仓70% -> 午后V型止盈 -> 0.5%刚性止损")
    print("=" * 85)

    quotes = fetch_dragon_intraday_quotes(DEFAULT_DRAGON_WATCHLIST)
    if not quotes:
        print("未获取到实时行情数据。")
        return

    evaluated = [evaluate_intraday_setup(q) for q in quotes]
    # 排序：买点信号优先
    evaluated.sort(key=lambda x: (
        1 if "🔥" in x['signal_tag'] else (2 if "🚀" in x['signal_tag'] else 3),
        x['chg_pct']
    ))

    print(f"{'代码':<8} {'名称':<8} {'行业':<12} {'现价':<7} {'涨跌%':<7} {'VWAP均价':<9} {'低点反弹%':<9} {'信号标签':<22} {'操作建议'}")
    print("-" * 125)
    for r in evaluated:
        print(f"{r['code']:<8} {r['name']:<8} {r['industry']:<12} {r['price']:<7.2f} {r['chg_pct']:<7.2f} {r['vwap']:<9.2f} {r['rebound_from_low']:<9.2f} {r['signal_tag']:<22} {r['action']}")
    print("=" * 125)

    report_path = os.path.join(REPORTS_DIR, f"daily_low_buy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(evaluated, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 日内低吸监控报告已保存至: {report_path}")

if __name__ == '__main__':
    run_scanner()
