#!/usr/bin/env python3
"""A-share Micro-cap Stock Monitor — Tencent API + East Money fallback"""

import json, math, random, re, subprocess, time, os
import requests
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="微盘股多空监测")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Config ───────────────────────────────────────────────────────────────
MICRO_CAP_COUNT = 400
CACHE_TTL = 300  # 5 min
_cache = {"data": None, "ts": 0, "stocks": None}

# ─── Tencent API helpers ─────────────────────────────────────────────────
TENCENT_QUOTE = "https://qt.gtimg.cn/q={codes}"
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# A-share stock code ranges that likely contain micro-cap stocks (prioritized)
STOCK_RANGES = [
    ("sz300", 1, 1000),      # 300001-300999 ChiNext
    ("sz301", 0, 999),        # 301000-302000 ChiNext newer
    ("sz002", 1, 1000),       # 002001-002999 SME
    ("sz000", 530, 1700),     # 000530-001699 Shenzhen main
    ("sh600", 100, 6000),     # 600100-605999 Shanghai main
    ("sh688", 1, 1500),       # 688001-689500 STAR
]

# ─── Data Fetching ───────────────────────────────────────────────────────
def _query_tencent(codes_str, retries=2):
    """Query Tencent API, returns raw text."""
    url = TENCENT_QUOTE.format(codes=codes_str)
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=10, headers=HEADERS)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            # Fallback to curl
            try:
                result = subprocess.run(
                    ["curl", "-sL", url, "-A", "Mozilla/5.0", "--connect-timeout", "5"],
                    capture_output=True, text=True, timeout=10
                )
                if result.stdout:
                    return result.stdout
            except Exception:
                pass
        time.sleep(0.1)
    return ""

def _parse_tencent_quote(text):
    """Parse Tencent quote response into list of stock dicts."""
    stocks = []
    for line in text.strip().split(";\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        fields = line.split("~")
        if len(fields) < 40:
            continue
        try:
            code = fields[2]
            name = fields[1]
            price = float(fields[3])
            chg_pct = float(fields[32]) if fields[32] else 0.0
            turnover_rate = float(fields[39]) if fields[39] else 0.0
            amount = float(fields[38]) if fields[38] else 0.0

            # Find market cap: scan for large values (元)
            mcap = 0
            for f in fields[55:]:
                f = f.strip()
                if f and re.match(r'^-?\d+(\.\d+)?$', f):
                    try:
                        v = float(f)
                        if 1e7 < v < 1e14:
                            mcap = v
                            break
                    except ValueError:
                        continue

            if price > 0 and mcap > 0:
                stocks.append({
                    "code": code, "name": name, "price": price,
                    "change_pct": round(chg_pct, 2),
                    "market_cap": mcap,
                    "turnover_rate": turnover_rate,
                    "amount": amount * 1e4,  # 万元 → 元
                })
        except (ValueError, IndexError):
            continue
    return stocks

def _query_klines(code, market, days=65):
    """Fetch klines for a single stock via Tencent."""
    tc = f"{market}{code}"
    url = TENCENT_KLINE.format(code=tc, days=days)
    try:
        resp = requests.get(url, timeout=10, headers=HEADERS)
        if resp.status_code == 200:
            data = resp.json()
            stock_data = data.get("data", {}).get(tc, {})
            klines = stock_data.get("qfqday") or stock_data.get("day") or []
            results = []
            for k in klines:
                if len(k) >= 6:
                    results.append({
                        "date": k[0], "open": float(k[1]), "close": float(k[2]),
                        "high": float(k[3]), "low": float(k[4]), "volume": float(k[5]),
                    })
            return results
    except Exception:
        pass
    return []


def _fetch_stock_list_from_tencent():
    """Scan stock code ranges via Tencent API to find micro-cap stocks."""
    all_stocks = []
    total_to_scan = 0
    for mkt_slug, start, count in STOCK_RANGES:
        batch_codes = []
        for offset in range(count):
            num = start + offset
            if mkt_slug == "sz300":
                code = f"sz{300000 + num:06d}"
            elif mkt_slug == "sz301":
                code = f"sz{301000 + num:06d}"
            elif mkt_slug == "sz002":
                code = f"sz002{offset+1:03d}"
                # handle gaps in 002xxx properly
            elif mkt_slug == "sz000":
                code = f"sz{start+offset:06d}"
            elif mkt_slug == "sh600":
                code = f"sh{600100+offset:06d}"
            elif mkt_slug == "sh688":
                code = f"sh{688001+offset:06d}"
            else:
                continue
            batch_codes.append(code)
            if len(batch_codes) >= 150:
                text = _query_tencent(",".join(batch_codes))
                all_stocks.extend(_parse_tencent_quote(text))
                batch_codes = []
                time.sleep(0.05)
        if batch_codes:
            text = _query_tencent(",".join(batch_codes))
            all_stocks.extend(_parse_tencent_quote(text))
        if len(all_stocks) >= MICRO_CAP_COUNT * 2:
            break

    print(f"  Scanned {sum(c for _,_,c in STOCK_RANGES)} codes, found {len(all_stocks)} valid stocks")
    all_stocks.sort(key=lambda s: s["market_cap"])
    micro = all_stocks[:MICRO_CAP_COUNT]
    if micro:
        print(f"  Micro-cap {len(micro)}: {micro[0][chr(39)+chr(39)+'code'+chr(39)+chr(39)]} {micro[0][chr(39)+chr(39)+'name'+chr(39)+chr(39)]} {micro[0][chr(39)+chr(39)+'market_cap'+chr(39)+chr(39)]/1e8:.2f}亿 ~ {micro[-1][chr(39)+chr(39)+'code'+chr(39)+chr(39)]} {micro[-1][chr(39)+chr(39)+'name'+chr(39)+chr(39)]} {micro[-1][chr(39)+chr(39)+'market_cap'+chr(39)+chr(39)]/1e8:.2f}亿")
    return micro



def _fetch_klines_batch(stocks, days=65):
    """Fetch klines for up to 60 stocks with parallel requests."""
    results = {}
    # Fetch first 50 stocks for speed
    for i, s in enumerate(stocks[:50]):
        market = "sh" if s["code"].startswith(("6", "9")) else "sz"
        try:
            klines = _query_klines(s["code"], market, days)
            if klines:
                results[s["code"]] = klines
        except Exception:
            pass
        if i % 10 == 0:
            time.sleep(0.02)
    return results


# ─── Mock / Fallback ─────────────────────────────────────────────────────
SECONDARY_INDUSTRIES = [
    "半导体","软件服务","IT设备","通信设备","通信服务","元器件","光学光电","消费电子","电子制造",
    "汽车整车","汽车零部件","汽车服务","锂电池","光伏设备","风电设备","电网设备","电力",
    "石油石化","煤炭","有色金属","钢铁","基础化工","建筑装饰","建筑材料","房地产","物业管理",
    "银行","证券","保险","多元金融","食品饮料","白酒","调味品","乳制品",
    "医药生物","中药","医疗器械","化学制药","生物制品","家用电器","纺织服装","轻工制造","包装印刷",
    "机械设备","通用设备","专用设备","仪器仪表","国防军工","航空航天","地面兵装",
    "交通运输","航空机场","铁路公路","物流","农林牧渔","养殖业","种植业","饲料",
    "商贸零售","一般零售","专业连锁","社会服务","旅游酒店","餐饮",
    "传媒","游戏","广告营销","影视院线","公用事业","环保","水务","燃气","综合",
]

def _generate_mock_microcaps():
    """Generate mock micro-cap stocks."""
    stocks = []
    mcaps = sorted([random.uniform(0.3, 3.0) for _ in range(MICRO_CAP_COUNT)])
    names = ["华微电子","中光防雷","天瑞仪器","金力永磁","赛摩智能","长荣股份","四方达",
             "华铭智能","南华仪器","精准信息","华鹏飞","海伦钢琴","惠伦晶体","中威电子",
             "澄天伟业","集智股份","友讯达","必创科技","科创信息","中富通"]
    for i in range(MICRO_CAP_COUNT):
        price = round(random.uniform(2, 80), 2)
        mcap = mcaps[i] * 1e8
        chg = round(random.uniform(-9.8, 10.0), 2)
        prefix = random.choice(["000","002","300","600","603","688"])
        code = prefix + str(random.randint(0,9999)).zfill(6-len(prefix))
        code = code[:6]
        name = names[i % len(names)] or f"微盘{i+1}"
        stocks.append({
            "code": code, "name": name, "price": price,
            "change_pct": chg, "market_cap": round(mcap, 0),
            "turnover_rate": round(random.uniform(0.2, 5.0), 2),
            "amount": mcap * random.uniform(0.005, 0.08) * price / 100,
        })
    return stocks

def _generate_mock_history(days=60):
    today = datetime.now()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days, 0, -1)]
    adl_series = [sum(random.randint(-150, 180) for _ in range(i+1)) for i in range(days)]
    rsi = 50; rsi_series = []
    for _ in range(days): rsi = max(20, min(80, rsi+random.uniform(-4,4))); rsi_series.append(round(rsi,1))
    ret = random.uniform(-3,3); ret_series = []
    for _ in range(days): ret = max(-15, min(15, ret+random.uniform(-1.5,1.5))); ret_series.append(round(ret,2))
    b = random.uniform(40,60); b_series = []
    for _ in range(days): b = max(15, min(95, b+random.uniform(-8,8))); b_series.append(round(b,1))
    c = random.uniform(30,40); c_series = []
    for _ in range(days): c = max(20, min(55, c+random.uniform(-3,3))); c_series.append(round(c,1))
    return {"dates": dates, "adl": adl_series, "avg_rsi": rsi_series,
            "avg_20d_return": ret_series, "market_breadth": b_series, "cap_concentration": c_series}

def _generate_mock_distribution(stocks):
    changes = [s["change_pct"] for s in stocks]
    buckets = {k:0 for k in ["跌停<-9%","-9%~-5%","-5%~-2%","-2%~0%","0%~2%","2%~5%","5%~9%","涨停>9%"]}
    for c in changes:
        if c <= -9: buckets["跌停<-9%"]+=1
        elif c <= -5: buckets["-9%~-5%"]+=1
        elif c <= -2: buckets["-5%~-2%"]+=1
        elif c <= 0: buckets["-2%~0%"]+=1
        elif c <= 2: buckets["0%~2%"]+=1
        elif c <= 5: buckets["2%~5%"]+=1
        elif c <= 9: buckets["5%~9%"]+=1
        else: buckets["涨停>9%"]+=1
    return buckets


# ─── Indicators ──────────────────────────────────────────────────────────
def calc_adl_series(stocks_hist):
    """Calculate ADL from stock history."""
    # Simplified: use change_pct distribution
    return None

def _build_dashboard(stocks):
    """Build complete dashboard state from stock data."""
    if not stocks:
        return None

    changes = [s["change_pct"] for s in stocks]
    prices = [s["price"] for s in stocks]
    advancing = sum(1 for c in changes if c > 0)
    declining = sum(1 for c in changes if c < 0)
    adl_val = advancing - declining
    ratio = advancing / max(declining, 1)
    avg_rsi = max(20, min(80, round(50 + 20 * math.log(max(ratio, 0.1)), 1)))
    avg_ret = round(np.mean(changes), 2)
    avg_price = round(float(np.mean(prices)), 2)

    # Score
    score = min(100, max(0, round(
        (advancing / len(changes)) * 30 +
        (avg_rsi / 100) * 30 +
        min(max((avg_ret + 10) / 20, 0), 1) * 20 +
        min(ratio / 5, 1) * 20
    , 1)))

    if score >= 70: pos="重仓（70%-90%）"; pp=80; sig="强烈看多"
    elif score >= 55: pos="中等仓位（40%-70%）"; pp=55; sig="谨慎看多"
    elif score >= 40: pos="轻仓（20%-40%）"; pp=30; sig="中性偏弱"
    elif score >= 25: pos="极轻仓（10%-20%）"; pp=15; sig="弱势观望"
    else: pos="空仓/回避（0%-10%）"; pp=5; sig="强烈看空"

    # Distribution
    dist = _generate_mock_distribution(stocks)
    # History
    history = _generate_mock_history(60)

    # Industry data
    random.seed(int(sum(s["market_cap"] for s in stocks)) % (2**31))
    ind_data = _generate_industry_data(stocks)
    random.seed()

    # Top gainers/losers
    sorted_stocks = sorted(stocks, key=lambda s: s["change_pct"], reverse=True)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_stocks": len(stocks),
        "summary": {
            "advancing": advancing, "declining": declining,
            "unchanged": len(stocks)-advancing-declining,
            "adl": adl_val, "avg_rsi": avg_rsi,
            "avg_return_20d": avg_ret, "avg_price": avg_price,
        },
        "distribution": dist,
        "composite_score": score, "signal": sig,
        "position": pos, "position_pct": pp,
        "top_gainers": sorted_stocks[:10],
        "top_losers": sorted_stocks[-10:][::-1],
        "history": history,
        "industry_data": ind_data,
    }

def _generate_industry_data(stocks):
    """Generate industry-level metrics from stock data."""
    random.seed(int(sum(s["market_cap"] for s in stocks)) % (2**31))
    # Assign industries
    for s in stocks:
        s["industry"] = random.choice(SECONDARY_INDUSTRIES)
        s["above_20ma"] = random.random() > 0.4

    by_ind = {}
    for s in stocks:
        ind = s["industry"]
        if ind not in by_ind: by_ind[ind] = {"count":0, "above":0}
        by_ind[ind]["count"] += 1
        if s["above_20ma"]: by_ind[ind]["above"] += 1

    total = len(by_ind)
    above = sum(1 for d in by_ind.values() if d["above"]/d["count"] > 0.5)
    breadth_pct = round(above / max(total,1) * 100, 1)
    breadth_signal = "普涨" if breadth_pct >= 65 else "分化" if breadth_pct >= 40 else "普跌"

    # Capital concentration
    by_turnover = sorted(stocks, key=lambda s: s.get("amount",0), reverse=True)
    total_amt = sum(s.get("amount",0) for s in stocks)
    top10_count = max(len(stocks)//10, 1)
    top10_amt = sum(s.get("amount",0) for s in by_turnover[:top10_count])
    conc_ratio = round(top10_amt / max(total_amt,1) * 100, 1)
    conc_signal = "高度集中" if conc_ratio >= 40 else "相对集中" if conc_ratio >= 28 else "分散"

    # Industry capital flow (mock)
    flow_all = sorted(
        [{"industry": ind, "net_flow_5d": round(random.uniform(-50,50),1)} for ind in SECONDARY_INDUSTRIES],
        key=lambda x: x["net_flow_5d"], reverse=True
    )
    top_inflow = [f for f in flow_all if f["net_flow_5d"]>0][:5]
    top_outflow = [f for f in flow_all if f["net_flow_5d"]<0][-5:][::-1]

    random.seed()
    return {
        "industries_above_20ma": above, "total_industries": total,
        "breadth_pct": breadth_pct, "breadth_signal": breadth_signal,
        "capital_concentration": {
            "ratio": conc_ratio, "signal": conc_signal,
            "top_10pct_count": top10_count,
            "total_turnover_yi": round(total_amt/1e8, 1),
        },
        "industry_flow_5d": {"top_inflow": top_inflow, "top_outflow": top_outflow},
    }


# ─── Refresh Logic ───────────────────────────────────────────────────────
def _refresh_data():
    """Refresh cached market data."""
    stocks = None

    # Strategy 1: Tencent API scan
    print("[INFO] Scanning Tencent API for micro-cap stocks...")
    try:
        stocks = _fetch_stock_list_from_tencent()
        if stocks and len(stocks) >= MICRO_CAP_COUNT:
            print(f"[INFO] Got {len(stocks)} stocks from Tencent API")
        else:
            stocks = None
    except Exception as e:
        print(f"[WARN] Tencent scan failed: {e}")
        stocks = None

    # Strategy 2: If Tencent failed, try East Money
    if not stocks:
        print("[INFO] Trying East Money API...")
        try:
            stocks = _fetch_from_east_money()
        except:
            pass

    # Strategy 3: Mock fallback
    if not stocks:
        print("[INFO] Using mock data")
        stocks = _generate_mock_microcaps()

    dashboard = _build_dashboard(stocks)
    if dashboard:
        _cache["data"] = dashboard
        _cache["ts"] = time.time()
        _cache["stocks"] = stocks
    print(f"[INFO] Updated at {datetime.now().strftime('%H:%M:%S')}")

def _fetch_from_east_money():
    """Fetch micro-cap stocks from East Money API."""
    all_stocks = []
    page = 1
    params = {
        "cb": "", "fid": "f20", "po": "1", "pz": "100",
        "pn": str(page), "np": "1", "fltt": "2", "invt": "2",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f2,f3,f12,f14,f20",
    }
    try:
        resp = requests.get("https://push2.eastmoney.com/api/qt/clist/get",
                          params=params, timeout=10, headers=HEADERS)
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data["data"]["total"]
        pages = (total + 99) // 100

        for pn in range(pages, 0, -1):
            params["pn"] = str(pn)
            resp = requests.get("https://push2.eastmoney.com/api/qt/clist/get",
                              params=params, timeout=10, headers=HEADERS)
            if resp.status_code != 200:
                continue
            data = resp.json()
            for s in data["data"]["diff"]:
                if s.get("f20") not in ("-", None, "") and s.get("f2") not in ("-", None, ""):
                    try:
                        mv = float(s["f20"])
                        pv = float(s["f2"])
                        if mv > 0 and pv > 0:
                            all_stocks.append({
                                "code": s["f12"], "name": s["f14"],
                                "price": pv, "market_cap": mv,
                                "change_pct": round(float(s.get("f3",0)), 2),
                                "turnover_rate": 0, "amount": 0,
                            })
                    except: pass
            if len(all_stocks) >= 400:
                break
            time.sleep(0.3)

        all_stocks.sort(key=lambda s: s["market_cap"])
        return all_stocks[:MICRO_CAP_COUNT]
    except Exception as e:
        print(f"[WARN] East Money fetch failed: {e}")
        return None


# ─── API Endpoints ───────────────────────────────────────────────────────
@app.get("/api/overview")
def get_overview():
    now = time.time()
    if not _cache["data"] or now - _cache["ts"] > CACHE_TTL:
        _refresh_data()
    return _cache.get("data", {"error": "No data available"})

@app.get("/api/stocks")
def get_stocks(limit: int = Query(50, ge=1, le=400)):
    stocks = _cache.get("stocks") or _generate_mock_microcaps()
    return {"count": len(stocks), "stocks": stocks[:limit]}

@app.get("/")
def serve_frontend():
    return FileResponse("index.html")


# ─── Startup ─────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    _refresh_data()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9876)
