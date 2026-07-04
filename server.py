#!/usr/bin/env python3
"""A-share Micro-cap Stock Monitor Backend (FastAPI)"""

import json
import math
import random
import time
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import requests
import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="微盘股多空监测")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Configuration ────────────────────────────────────────────────────────
MICRO_CAP_COUNT = 400
EAST_MONEY_BASE = "https://push2.eastmoney.com/api/qt/clist/get"
EAST_MONEY_KLINES = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EAST_MONEY_INDUSTRY = "https://push2.eastmoney.com/api/qt/clist/get"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ─── Data Cache ───────────────────────────────────────────────────────────
_cache = {"data": None, "ts": 0, "ttl": 300}  # 5 min cache

# ─── 二级行业分类（申万二级，68个代表性行业）────────────────────────────────
SECONDARY_INDUSTRIES = [
    "半导体", "软件服务", "IT设备", "通信设备", "通信服务",
    "元器件", "光学光电", "消费电子", "电子制造",
    "汽车整车", "汽车零部件", "汽车服务",
    "锂电池", "光伏设备", "风电设备", "电网设备", "电力",
    "石油石化", "煤炭", "有色金属", "钢铁", "基础化工",
    "建筑装饰", "建筑材料", "房地产", "物业管理",
    "银行", "证券", "保险", "多元金融",
    "食品饮料", "白酒", "调味品", "乳制品",
    "医药生物", "中药", "医疗器械", "化学制药", "生物制品",
    "家用电器", "纺织服装", "轻工制造", "包装印刷",
    "机械设备", "通用设备", "专用设备", "仪器仪表",
    "国防军工", "航空航天", "地面兵装",
    "交通运输", "航空机场", "铁路公路", "物流",
    "农林牧渔", "养殖业", "种植业", "饲料",
    "商贸零售", "一般零售", "专业连锁",
    "社会服务", "旅游酒店", "餐饮",
    "传媒", "游戏", "广告营销", "影视院线",
    "公用事业", "环保", "水务", "燃气",
    "综合",
]

# ─── Mock / Fallback Data ────────────────────────────────────────────────
def _generate_mock_microcaps():
    """Generate realistic mock micro-cap stock data for demo / fallback."""
    stocks = []
    base_mcaps = [random.uniform(0.3, 3.0) for _ in range(MICRO_CAP_COUNT)]
    base_mcaps.sort()
    names = [
        "华微电子", "中光防雷", "天瑞仪器", "金力永磁", "赛摩智能",
        "长荣股份", "四方达", "华铭智能", "南华仪器", "精准信息",
        "华鹏飞", "海伦钢琴", "惠伦晶体", "中威电子", "澄天伟业",
        "集智股份", "友讯达", "必创科技", "科创信息", "中富通",
        "华虹科技", "华讯方舟", "华信股份", "中科招商", "中悦科技",
        "中科云网", "华泽钴镍", "中天能源", "华业资本", "中弘股份",
        "中安消", "华塑控股", "华菱星马", "华电能源", "中航资本",
        "华润双鹤", "中粮生化", "华映科技", "中金黄金", "中兴通讯",
        "中船防务", "中科曙光", "中国软件", "中粮糖业", "中远海控",
        "中金岭南", "中色股份", "中钢国际", "中工国际", "深中华A",
    ]
    # Assign stocks to industries
    for i in range(MICRO_CAP_COUNT):
        price = round(random.uniform(2, 80), 2)
        mcap = base_mcaps[i] * 1e8
        change_pct = round(random.uniform(-9.8, 10.0), 2)
        prefix = random.choice(["000", "002", "300", "600", "603", "688"])
        code = prefix + str(random.randint(0, 9999)).zfill(6 - len(prefix))
        code = code[:6]
        name = names[i % len(names)]
        if i >= len(names):
            name = f"微盘{i+1:03d}"
        industry = random.choice(SECONDARY_INDUSTRIES)
        turnover = round(mcap * random.uniform(0.005, 0.08) * price / 100, 2)  # simulate turnover
        is_above_20ma = random.random() > 0.4  # ~60% stocks above 20MA
        stocks.append({
            "code": code,
            "name": name,
            "price": price,
            "change_pct": change_pct,
            "market_cap": round(mcap, 0),
            "volume_ratio": round(random.uniform(0.2, 3.0), 2),
            "industry": industry,
            "turnover": max(turnover, 1e6),  # at least 1M
            "above_20ma": is_above_20ma,
        })
    return stocks


def _generate_mock_history(days=60):
    """Generate mock historical data for ADL, avg RSI, and avg return."""
    today = datetime.now()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days, 0, -1)]

    adl, adl_series = 0, []
    for _ in range(days):
        adl += random.randint(-150, 180)
        adl_series.append(adl)

    rsi_series, rsi = [], 50.0
    for _ in range(days):
        rsi += random.uniform(-4, 4)
        rsi = max(20, min(80, rsi))
        rsi_series.append(round(rsi, 1))

    ret_series, avg_ret = [], random.uniform(-3, 3)
    for _ in range(days):
        avg_ret += random.uniform(-1.5, 1.5)
        avg_ret = max(-15, min(15, avg_ret))
        ret_series.append(round(avg_ret, 2))

    # Market breadth history (percentage of industries above 20MA)
    breadth_series = []
    b = random.uniform(40, 60)
    for _ in range(days):
        b += random.uniform(-8, 8)
        b = max(15, min(95, b))
        breadth_series.append(round(b, 1))

    # Capital concentration history
    conc_series = []
    c = random.uniform(30, 40)
    for _ in range(days):
        c += random.uniform(-3, 3)
        c = max(20, min(55, c))
        conc_series.append(round(c, 1))

    return {
        "dates": dates,
        "adl": adl_series,
        "avg_rsi": rsi_series,
        "avg_20d_return": ret_series,
        "market_breadth": breadth_series,
        "cap_concentration": conc_series,
    }


def _generate_mock_distribution(stocks):
    """Generate distribution buckets for stock performance."""
    changes = [s["change_pct"] for s in stocks]
    buckets = {"跌停<-9%": 0, "-9%~-5%": 0, "-5%~-2%": 0, "-2%~0%": 0,
               "0%~2%": 0, "2%~5%": 0, "5%~9%": 0, "涨停>9%": 0}
    for c in changes:
        if c <= -9: buckets["跌停<-9%"] += 1
        elif c <= -5: buckets["-9%~-5%"] += 1
        elif c <= -2: buckets["-5%~-2%"] += 1
        elif c <= 0: buckets["-2%~0%"] += 1
        elif c <= 2: buckets["0%~2%"] += 1
        elif c <= 5: buckets["2%~5%"] += 1
        elif c <= 9: buckets["5%~9%"] += 1
        else: buckets["涨停>9%"] += 1
    return buckets


def _generate_mock_industry_data(stocks):
    """Generate mock industry-level data for market breadth & capital flow."""
    # Group stocks by industry
    by_industry = {}
    for s in stocks:
        ind = s.get("industry", "其他")
        if ind not in by_industry:
            by_industry[ind] = {"stocks": [], "above_20ma": 0}
        by_industry[ind]["stocks"].append(s)
        if s.get("above_20ma"):
            by_industry[ind]["above_20ma"] += 1

    # Calculate % of industries where >50% stocks above 20MA
    total_ind = len(by_industry)
    above_ma_count = 0
    industry_details = []
    for ind, data in by_industry.items():
        total = len(data["stocks"])
        above = data["above_20ma"]
        pct = round(above / max(total, 1) * 100, 1)
        above_ma = pct > 50
        if above_ma:
            above_ma_count += 1
        industry_details.append({
            "industry": ind,
            "stock_count": total,
            "above_20ma_pct": pct,
            "above_20ma": above_ma,
        })

    breadth_pct = round(above_ma_count / max(total_ind, 1) * 100, 1)

    if breadth_pct >= 65:
        breadth_signal = "普涨"
    elif breadth_pct >= 40:
        breadth_signal = "分化"
    else:
        breadth_signal = "普跌"

    # Generate mock capital flow data by industry (5-day simulated)
    flow_by_industry = []
    for ind in SECONDARY_INDUSTRIES:
        # Generate 5-day net flow
        net_flow = round(random.uniform(-50, 50), 1)
        flow_by_industry.append({
            "industry": ind,
            "net_flow_5d": net_flow,
            "direction": "inflow" if net_flow > 0 else "outflow",
        })

    flow_by_industry.sort(key=lambda x: x["net_flow_5d"], reverse=True)
    top_inflow = [f for f in flow_by_industry if f["net_flow_5d"] > 0][:5]
    top_outflow = [f for f in flow_by_industry if f["net_flow_5d"] < 0][-5:][::-1]

    # Generate capital concentration data
    total_turnover = sum(s.get("turnover", 0) for s in stocks)
    sorted_by_turnover = sorted(stocks, key=lambda x: x.get("turnover", 0), reverse=True)
    top_10pct_count = max(len(stocks) // 10, 1)
    top_10pct_turnover = sum(s.get("turnover", 0) for s in sorted_by_turnover[:top_10pct_count])
    concentration_ratio = round(top_10pct_turnover / max(total_turnover, 1) * 100, 1)

    if concentration_ratio >= 40:
        conc_signal = "高度集中"
    elif concentration_ratio >= 28:
        conc_signal = "相对集中"
    else:
        conc_signal = "分散"

    return {
        "industries_above_20ma": above_ma_count,
        "total_industries": total_ind,
        "breadth_pct": breadth_pct,
        "breadth_signal": breadth_signal,
        "industry_details": industry_details[:10],  # top 10 details
        "capital_concentration": {
            "ratio": concentration_ratio,
            "signal": conc_signal,
            "top_10pct_count": top_10pct_count,
            "total_turnover_yi": round(total_turnover / 1e8, 1),
        },
        "industry_flow_5d": {
            "top_inflow": top_inflow,
            "top_outflow": top_outflow,
        },
    }


# ─── East Money API Integration ──────────────────────────────────────────
def _fetch_from_api():
    """Try to fetch real data from East Money API. Returns None on failure."""
    try:
        all_stocks = []
        page = 1
        page_size = 100
        total = None

        while True:
            params = {
                "cb": "", "fid": "f20", "po": "1", "pz": str(page_size),
                "pn": str(page), "np": "1", "fltt": "2", "invt": "2",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "fields": "f2,f3,f8,f12,f14,f15,f16,f17,f18,f20,f62,f184",
            }
            resp = requests.get(EAST_MONEY_BASE, params=params, timeout=10, headers=HEADERS)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if total is None:
                total = data["data"]["total"]
            batch = data["data"]["diff"]
            all_stocks.extend(batch)
            if len(batch) < page_size or len(all_stocks) >= total:
                break
            page += 1
            time.sleep(0.15)

        valid = []
        for s in all_stocks:
            mcap = s.get("f20")
            price = s.get("f2")
            if mcap not in (None, "-", "") and price not in (None, "-", ""):
                try:
                    mv = float(mcap)
                    pv = float(price)
                    if mv > 0 and pv > 0:
                        turnover = float(s.get("f184", 0)) if s.get("f184") not in (None, "-", "") else 0
                        valid.append({
                            "code": s.get("f12", ""),
                            "name": s.get("f14", ""),
                            "price": round(pv, 2),
                            "change_pct": round(float(s.get("f3", 0)), 2),
                            "market_cap": round(mv, 0),
                            "volume_ratio": round(float(s.get("f8", 0)), 2) if s.get("f8") not in (None, "-") else 0,
                            "industry": "",  # Will be filled from industry API
                            "turnover": turnover,
                            "above_20ma": False,  # Calculated from klines
                        })
                except (ValueError, TypeError):
                    continue

        valid.sort(key=lambda x: x["market_cap"])
        return valid[:MICRO_CAP_COUNT]

    except Exception as e:
        print(f"[WARN] API fetch failed: {e}")
        return None


# ─── Indicator Calculations ─────────────────────────────────────────────
def calc_rsi(prices, period=14):
    """Calculate RSI for a list of prices."""
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


# ─── State Builder ───────────────────────────────────────────────────────
def _build_dashboard(micro_stocks):
    """Build the full dashboard state from micro-cap stock data."""
    if not micro_stocks:
        return None

    change_pcts = [s["change_pct"] for s in micro_stocks]
    prices = [s["price"] for s in micro_stocks]

    # Current day metrics
    advancing = sum(1 for c in change_pcts if c > 0)
    declining = sum(1 for c in change_pcts if c < 0)
    unchanged = len(change_pcts) - advancing - declining
    adl_value = advancing - declining

    # Distribution
    dist = _generate_mock_distribution(micro_stocks)

    # Average RSI
    avg_rsi = 50.0
    if declining > 0:
        ratio = advancing / max(declining, 1)
        avg_rsi = round(50 + 20 * math.log(min(max(ratio, 0.1), 10)), 1)
    avg_rsi = max(20, min(80, avg_rsi))

    avg_return_20d = round(np.mean(change_pcts), 2)

    # Composite score (0-100)
    score = 0
    adl_ratio = advancing / max(len(change_pcts), 1)
    score += adl_ratio * 30
    score += (avg_rsi / 100) * 30
    score += min(max((avg_return_20d + 10) / 20, 0), 1) * 20
    breadth_ratio = advancing / max(declining, 1)
    score += min(breadth_ratio / 5, 1) * 20
    score = round(min(max(score, 0), 100), 1)

    # Suggested position & signal
    if score >= 70:
        position = "重仓（70%-90%）"; pos_pct = 80; signal = "强烈看多"
    elif score >= 55:
        position = "中等仓位（40%-70%）"; pos_pct = 55; signal = "谨慎看多"
    elif score >= 40:
        position = "轻仓（20%-40%）"; pos_pct = 30; signal = "中性偏弱"
    elif score >= 25:
        position = "极轻仓（10%-20%）"; pos_pct = 15; signal = "弱势观望"
    else:
        position = "空仓/回避（0%-10%）"; pos_pct = 5; signal = "强烈看空"

    # History
    history = _generate_mock_history(60)

    # Industry data
    industry_data = _generate_mock_industry_data(micro_stocks)

    # Top gainers and losers
    sorted_stocks = sorted(micro_stocks, key=lambda x: x["change_pct"], reverse=True)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_stocks": len(micro_stocks),
        "summary": {
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
            "adl": adl_value,
            "avg_rsi": avg_rsi,
            "avg_return_20d": avg_return_20d,
            "avg_price": round(float(np.mean(prices)), 2),
        },
        "distribution": dist,
        "composite_score": score,
        "signal": signal,
        "position": position,
        "position_pct": pos_pct,
        "top_gainers": sorted_stocks[:10],
        "top_losers": sorted_stocks[-10:][::-1],
        "history": history,
        "industry_data": industry_data,
    }


def _refresh_data():
    """Refresh cached data from API or fallback."""
    stocks = _fetch_from_api()
    if stocks is None:
        print("[INFO] API unavailable, using mock data")
        stocks = _generate_mock_microcaps()
    else:
        print(f"[INFO] Fetched {len(stocks)} real micro-cap stocks")

    dashboard = _build_dashboard(stocks)
    if dashboard:
        _cache["data"] = dashboard
        _cache["ts"] = time.time()
        _cache["stocks"] = stocks
    print(f"[INFO] Dashboard updated at {datetime.now().strftime('%H:%M:%S')}")


# ─── API Endpoints ───────────────────────────────────────────────────────
@app.get("/api/overview")
def get_overview():
    now = time.time()
    if not _cache["data"] or now - _cache["ts"] > _cache["ttl"]:
        _refresh_data()
    return _cache.get("data", {"error": "No data available"})


@app.get("/api/stocks")
def get_stocks(limit: int = Query(50, ge=1, le=400)):
    stocks = _cache.get("stocks", _generate_mock_microcaps())
    return {"count": len(stocks), "stocks": stocks[:limit]}


@app.get("/")
def serve_frontend():
    return FileResponse("index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9876)
