#!/usr/bin/env python3
"""DULITRADE - Finnhub Only Server"""
import json, time, re, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request
from socketserver import ThreadingMixIn

PORT = int(os.environ.get("PORT", 10000))
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
POLYGON_KEY = os.environ.get("POLYGON_KEY", "")
FH = "https://finnhub.io/api/v1"
PG = "https://api.polygon.io"

HEADERS_OUT = {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
}

def fh(path):
    if not FINNHUB_KEY: return {}
    try:
        sep = "&" if "?" in path else "?"
        url = f"{FH}{path}{sep}token={FINNHUB_KEY}"
        req = Request(url, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except: return {}

def get_quote(symbol):
    d = fh(f"/quote?symbol={symbol}")
    c = d.get("c", 0)
    if c > 0:
        bid = d.get("b") or d.get("bid")
        ask = d.get("a") or d.get("ask")
        # חשב spread
        spread = None
        spread_pct = None
        if bid and ask and bid > 0 and ask > 0:
            spread = round(ask - bid, 4)
            spread_pct = round((ask - bid) / c * 100, 3)

        # ── חישוב vr אמיתי מנרות ────────────────────────────
        # Finnhub לא מחזיר vr ישירות — נחשב מנפח יומי vs ממוצע
        vr = 1.0
        try:
            import datetime
            today_vol = d.get("t") or 0  # נפח יומי נוכחי מ-Finnhub
            if not today_vol:
                # נסה לחשב מ-Polygon אם יש
                pass
            if today_vol and today_vol > 0:
                # קבל ממוצע נפח 20 ימים מ-candles
                candles_d = get_candles(symbol)
                vols = candles_d.get("v", [])
                if vols and len(vols) >= 5:
                    avg_vol = sum(vols[-20:]) / min(20, len(vols))
                    if avg_vol > 0:
                        vr = round(today_vol / avg_vol, 1)
        except: pass

        # ── Pre-Market price ─────────────────────────────────
        import datetime
        pm_price = d.get("preMarketPrice") or d.get("extendedPrice")
        pm_pct   = None
        pm_active = False
        if pm_price and pm_price > 0:
            pm_pct = round((pm_price - c) / c * 100, 2) if c > 0 else 0
            # בדוק שעת ET
            now_utc = datetime.datetime.utcnow()
            is_dst = 3 <= now_utc.month <= 11
            et_time = (now_utc.hour - (4 if is_dst else 5)) % 24 + now_utc.minute/60
            pm_active = 4 <= et_time < 9.5

        return {
            "c":  round(c, 2),
            "pc": round(d.get("pc", c), 2),
            "h":  round(d.get("h", c), 2),
            "l":  round(d.get("l", c), 2),
            "o":  round(d.get("o", c), 2),
            "dp": round(d.get("dp", 0), 2),
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "spreadPct": spread_pct,
            "vr": vr,
            "pmPrice":  round(pm_price, 2) if pm_price else None,
            "pmPct":    pm_pct,
            "pmActive": pm_active,
        }
    return {"c":0,"pc":0,"h":0,"l":0,"o":0,"dp":0,"bid":None,"ask":None,"spread":None,"spreadPct":None,"vr":1,"pmPrice":None,"pmPct":None,"pmActive":False}

def pg(path):
    """Polygon.io API"""
    if not POLYGON_KEY: return {}
    try:
        sep = "&" if "?" in path else "?"
        url = f"{PG}{path}{sep}apiKey={POLYGON_KEY}"
        req = Request(url, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except: return {}

def get_candles_hourly(symbol):
    """נרות שעתיים — Polygon"""
    import datetime
    if not POLYGON_KEY:
        return {"c":[],"o":[],"h":[],"l":[],"v":[],"t":[],"s":"no_data"}
    try:
        to_dt  = datetime.date.today().isoformat()
        frm_dt = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
        for attempt in range(3):
            try:
                d = pg(f"/v2/aggs/ticker/{symbol}/range/1/hour/{frm_dt}/{to_dt}?adjusted=true&sort=asc&limit=200")
                results = d.get("results", [])
                if results and len(results) >= 5:
                    return {
                        "c": [round(r["c"],2) for r in results],
                        "o": [round(r["o"],2) for r in results],
                        "h": [round(r["h"],2) for r in results],
                        "l": [round(r["l"],2) for r in results],
                        "v": [int(r.get("v",0)) for r in results],
                        "t": [int(r["t"]//1000) for r in results],
                        "s": "ok", "source": "polygon_hourly"
                    }
                if attempt < 2: time.sleep(1)
            except:
                if attempt < 2: time.sleep(1)
    except: pass
    return {"c":[],"o":[],"h":[],"l":[],"v":[],"t":[],"s":"no_data"}


def get_candles(symbol):
    """נרות יומיים — Polygon ראשי, Finnhub fallback, עם retry"""
    import datetime

    to_dt  = datetime.date.today().isoformat()
    frm_dt = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()

    # ── Polygon — 3 ניסיונות ─────────────────────────────────
    if POLYGON_KEY:
        for attempt in range(3):
            try:
                url_path = f"/v2/aggs/ticker/{symbol}/range/1/day/{frm_dt}/{to_dt}?adjusted=true&sort=asc&limit=365"
                d = pg(url_path)
                results = d.get("results", [])
                if results and len(results) >= 2:
                    c = [round(r["c"], 2) for r in results]
                    o = [round(r["o"], 2) for r in results]
                    h = [round(r["h"], 2) for r in results]
                    l = [round(r["l"], 2) for r in results]
                    v = [int(r.get("v", 0)) for r in results]
                    t = [int(r["t"] // 1000) for r in results]
                    return {"c":c,"o":o,"h":h,"l":l,"v":v,"t":t,"s":"ok","source":"polygon"}
                # תוצאות ריקות — נסה שוב
                if attempt < 2:
                    time.sleep(1)
            except:
                if attempt < 2:
                    time.sleep(1)

    # ── Finnhub fallback — daily + weekly ───────────────────
    to  = int(time.time())
    frm = to - 365 * 86400
    for resolution in ["D", "W"]:  # נסה יומי קודם, אחר כך שבועי
        for attempt in range(3):
            try:
                d = fh(f"/stock/candle?symbol={symbol}&resolution={resolution}&from={frm}&to={to}")
                if d.get("s") == "ok" and d.get("c") and len(d["c"]) >= 2:
                    return {"c":[round(x,2) for x in d["c"]],"o":[round(x,2) for x in d["o"]],
                            "h":[round(x,2) for x in d["h"]],"l":[round(x,2) for x in d["l"]],
                            "v":[int(x) for x in d["v"]],"t":d["t"],"s":"ok","source":f"finnhub_{resolution.lower()}"}
                if attempt < 2:
                    time.sleep(1)
            except:
                if attempt < 2:
                    time.sleep(1)

    return {"c":[],"o":[],"h":[],"l":[],"v":[],"t":[],"s":"no_data"}

def get_indicators(symbol):
    d = fh(f"/scan/technical-indicator?symbol={symbol}&resolution=D")
    return d

def get_profile(symbol):
    p = fh(f"/stock/profile2?symbol={symbol}")
    m = fh(f"/stock/metric?symbol={symbol}&metric=all")
    metric = m.get("metric", {})
    pe   = metric.get("peNormalizedAnnual") or metric.get("peTTM")
    pb   = metric.get("pbAnnual") or metric.get("pb")
    beta = metric.get("beta")
    mc   = p.get("marketCapitalization")
    return {
        "name":     p.get("name", symbol),
        "sector":   p.get("finnhubIndustry", "—"),
        "industry": p.get("finnhubIndustry", "—"),
        "marketCapitalization": mc,
        "pe": pe, "forwardPE": None, "pb": pb, "ps": None,
        "revenueGrowth":   metric.get("revenueGrowthTTMYoy"),
        "earningsGrowth":  metric.get("epsGrowthTTMYoy"),
        "profitMargin":    metric.get("netProfitMarginTTM"),
        "operatingMargin": metric.get("operatingMarginTTM"),
        "grossMargin":     metric.get("grossMarginTTM"),
        "roe":  metric.get("roeTTM"),
        "roa":  metric.get("roaTTM"),
        "debtToEquity":  metric.get("totalDebt/totalEquityAnnual"),
        "currentRatio":  metric.get("currentRatioAnnual"),
        "beta": beta, "shortRatio": None,
        "targetMeanPrice": None, "recommendationKey": None, "numberOfAnalysts": None,
        "dividendYield":    metric.get("dividendYieldIndicatedAnnual"),
        "fiftyTwoWeekHigh": metric.get("52WeekHigh"),
        "fiftyTwoWeekLow":  metric.get("52WeekLow"),
        # EPS Growth מפורט
        "epsGrowthAnnual":  metric.get("epsGrowth3Y") or metric.get("epsGrowthTTMYoy"),
        "epsGrowth3Y":      metric.get("epsGrowth3Y"),
        "epsGrowth5Y":      metric.get("epsGrowth5Y"),
        # Free Cash Flow Yield
        "fcfPerShareTTM":   metric.get("fcfPerShareTTM"),
        "freeCashFlowTTM":  metric.get("freeCashFlowTTM"),
        # Revenue Surprise — מ-earnings
        "revenuePerShareTTM": metric.get("revenuePerShareTTM"),
    }

def get_news(symbol):
    today = time.strftime("%Y-%m-%d")
    month_ago = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30*86400))
    items = fh(f"/company-news?symbol={symbol}&from={month_ago}&to={today}")
    if not isinstance(items, list): return []
    return [
        {"headline": n.get("headline",""), "url": n.get("url","#"), "source": n.get("source","Finnhub")}
        for n in items[:15] if n.get("headline")
    ]

def get_extnews(symbol):
    # RSS fallback
    sym = symbol.upper()
    results = []
    try:
        url = "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"
        req = Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urlopen(req, timeout=6) as r: xml = r.read().decode("utf-8","ignore")
        for m in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)[:40]:
            tm = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>",m) or re.search(r"<title>(.*?)</title>",m)
            lm = re.search(r"<link>(.*?)</link>",m)
            if not tm: continue
            title = tm.group(1)
            if sym not in title.upper(): continue
            link = lm.group(1).strip() if lm else "#"
            sent = "positive" if re.search(r"beat|surge|rise|gain|strong|buy|upgrade|rally",title,re.I) \
                else "negative" if re.search(r"miss|drop|fall|loss|weak|sell|warn|crash",title,re.I) else "neutral"
            results.append({"source":"CNBC","title":title.strip(),"url":link,"sentiment":sent})
    except: pass
    return results[:8]

COMP_MAP = {
    "AAPL":["MSFT","GOOGL","META"],"NVDA":["AMD","INTC","QCOM"],"TSLA":["F","GM","RIVN"],
    "MSFT":["AAPL","GOOGL","AMZN"],"AMZN":["MSFT","GOOGL","WMT"],"META":["SNAP","PINS","GOOGL"],
    "GOOGL":["META","MSFT","AMZN"],"AMD":["NVDA","INTC","QCOM"],"NFLX":["DIS","PARA","WBD"],
    "TEVA":["MRK","PFE","AMGN"],"CHKP":["PANW","CRWD","FTNT"],"MNDY":["CRM","NOW","WDAY"],
    "JPM":["BAC","WFC","GS"],"V":["MA","AXP","PYPL"],"SOFI":["SQ","HOOD","AFRM"],
    "COIN":["HOOD","IBKR","CME"],"PLTR":["AI","BB","SOUN"],
}

def get_competitors(symbol):
    peers = COMP_MAP.get(symbol.upper(), [])
    results = []
    for peer in peers[:3]:
        try:
            q = get_quote(peer)
            if not q["c"]: continue
            p = get_profile(peer)
            results.append({
                "symbol": peer,
                "name": p.get("name", peer),
                "price": q["c"],
                "changePct": q["dp"],
                "pe": p.get("pe"),
                "pb": p.get("pb"),
                "revenueGrowth": p.get("revenueGrowth"),
                "profitMargin": p.get("profitMargin"),
                "beta": p.get("beta"),
                "marketCap": round(p["marketCapitalization"],1) if p.get("marketCapitalization") else None,
            })
        except: continue
    return results

def get_macro():
    # Finnhub indices
    mapping = {
        "^VIX":  "VIX",
        "^TNX":  "TNX",
        "^DXY":  "DXY",
        "^GSPC": "SPY",  # S&P ETF
    }
    results = {}
    for key, sym in mapping.items():
        try:
            d = fh(f"/quote?symbol={sym}")
            c = d.get("c", 0)
            if c > 0:
                results[key] = {"price": round(c,2), "prev": round(d.get("pc",c),2)}
            else:
                results[key] = {"price":0,"prev":0}
        except:
            results[key] = {"price":0,"prev":0}
    return results

def get_earnings(symbol):
    try:
        items = fh(f"/stock/earnings?symbol={symbol}&limit=4")
        if not isinstance(items, list) or not items:
            return {"available": False}
        last = items[0]
        actual   = last.get("actual")
        estimate = last.get("estimate")
        if actual is not None and estimate and estimate != 0:
            surprise = round((actual - estimate) / abs(estimate) * 100, 1)
        else:
            surprise = None

        # Revenue Surprise מ-4 רבעונים אחרונים
        rev_surprises = []
        for item in items[:4]:
            rev_act = item.get("revenueActual")
            rev_est = item.get("revenueEstimate")
            if rev_act and rev_est and rev_est != 0:
                rev_surp = round((rev_act - rev_est) / abs(rev_est) * 100, 1)
                rev_surprises.append(rev_surp)

        rev_surprise = rev_surprises[0] if rev_surprises else None

        # EPS history — 4 רבעונים
        eps_history = []
        for item in items[:4]:
            a = item.get("actual")
            e = item.get("estimate")
            if a is not None:
                eps_history.append({
                    "period": item.get("period",""),
                    "actual": a,
                    "estimate": e,
                    "surprise": round((a-e)/abs(e)*100,1) if e and e!=0 else None
                })

        return {
            "available": True,
            "surprise": surprise,
            "quarter": last.get("period",""),
            "revSurprise": rev_surprise,
            "epsHistory": eps_history,
            "beat3of4": sum(1 for h in eps_history if (h.get("surprise") or 0) > 0) >= 3,
        }
    except:
        return {"available": False}

def get_insider(symbol):
    try:
        items = fh(f"/stock/insider-transactions?symbol={symbol}")
        txs = items.get("data", []) if isinstance(items, dict) else []
        buys  = sum(1 for t in txs if t.get("transactionType","") in ("P - Purchase","Buy"))
        sells = sum(1 for t in txs if t.get("transactionType","") in ("S - Sale","Sell"))
        return {
            "available": True,
            "buys":  buys,
            "sells": sells,
            "net":   buys - sells,
            "transactions": [{"name": t.get("name",""), "date": t.get("transactionDate","")} for t in txs[:3]],
        }
    except:
        return {"available": False}

def get_short_interest(symbol):
    """Short Interest מ-Finviz scraping"""
    try:
        url = f"https://finviz.com/quote.ashx?t={symbol}&ty=c&ta=1&p=d"
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", "ignore")

        # Short Float %
        import re
        short_float = None
        short_ratio = None

        m = re.search(r'Short Float[^>]*>([^<]+)<', html)
        if m:
            val = m.group(1).strip().replace('%','').replace('-','')
            try: short_float = float(val)
            except: pass

        m2 = re.search(r'Short Ratio[^>]*>([^<]+)<', html)
        if m2:
            val2 = m2.group(1).strip().replace('-','')
            try: short_ratio = float(val2)
            except: pass

        if short_float is None:
            return {"available": False}

        return {
            "available": True,
            "shortFloat": short_float,       # % מהמניה בשורט
            "shortRatio": short_ratio,        # ימים לכיסוי
            "squeezeRisk": short_float > 15,  # סיכון Short Squeeze
        }
    except:
        return {"available": False}


def get_earnings_calendar(symbol):
    """תאריך דוח רווחים הבא מ-Finnhub"""
    import time, datetime
    try:
        today = datetime.date.today()
        future = today + datetime.timedelta(days=90)
        frm = today.isoformat()
        to  = future.isoformat()

        d = fh(f"/calendar/earnings?from={frm}&to={to}&symbol={symbol}")
        items = d.get("earningsCalendar", [])

        if not items:
            return {"available": False, "daysUntil": None}

        # מצא את הדוח הקרוב ביותר בעתיד
        upcoming = []
        for item in items:
            date_str = item.get("date", "")
            if not date_str: continue
            try:
                report_date = datetime.date.fromisoformat(date_str)
                days_until = (report_date - today).days
                if days_until >= 0:
                    upcoming.append({
                        "date": date_str,
                        "daysUntil": days_until,
                        "hour": item.get("hour", "amc"),  # bmo=before market open, amc=after market close
                        "epsEstimate": item.get("epsEstimate"),
                    })
            except: continue

        if not upcoming:
            return {"available": False, "daysUntil": None}

        next_report = min(upcoming, key=lambda x: x["daysUntil"])
        return {
            "available": True,
            "date": next_report["date"],
            "daysUntil": next_report["daysUntil"],
            "hour": next_report["hour"],
            "epsEstimate": next_report["epsEstimate"],
            "soon": next_report["daysUntil"] <= 14,   # פחות מ-2 שבועות
            "imminent": next_report["daysUntil"] <= 3, # פחות מ-3 ימים
        }
    except:
        return {"available": False, "daysUntil": None}


def get_premarket_volume(symbol):
    """Pre-Market נפח אמיתי מ-Polygon extended hours"""
    import datetime
    if not POLYGON_KEY:
        return {"available": False}
    try:
        today = datetime.date.today()
        # בנה URL ל-aggs דקתיות עם extended hours
        # מ-4:00 AM עד 9:30 AM ET — Pre-Market
        frm_dt = today.isoformat()
        to_dt  = today.isoformat()
        # 5-דקות נרות, extended hours
        url_path = (
            f"/v2/aggs/ticker/{symbol}/range/5/minute/{frm_dt}/{to_dt}"
            f"?adjusted=true&sort=asc&limit=200&extended=true"
        )
        d = pg(url_path)
        results = d.get("results", [])
        if not results:
            return {"available": False}

        # סנן רק נרות Pre-Market: 4:00–9:30 ET
        # Polygon מחזיר timestamps ב-ms UTC
        # ET = UTC-4 (קיץ) / UTC-5 (חורף)
        # 4:00 AM ET = 8:00/9:00 UTC → בdst: 8*3600, בחורף: 9*3600
        import calendar
        # בדוק DST פשוט — מרץ-נובמבר
        month = today.month
        is_dst = 3 <= month <= 11
        et_offset = 4 if is_dst else 5  # שעות מאחורי UTC

        # Pre-Market: 4:00-9:30 AM ET
        pre_start_utc = (4 + et_offset) * 3600  # שניות מתחילת יום
        pre_end_utc   = (9 * 3600 + 30 * 60) + et_offset * 3600

        pre_volume = 0
        pre_open   = None
        pre_close  = None
        pre_high   = None
        pre_low    = None

        for bar in results:
            ts_ms = bar.get("t", 0)
            ts_sec = ts_ms / 1000
            # שניות מתחילת היום UTC
            secs_in_day = ts_sec % 86400

            if pre_start_utc <= secs_in_day <= pre_end_utc:
                v = bar.get("v", 0)
                pre_volume += v
                if pre_open is None:
                    pre_open = bar.get("o")
                pre_close = bar.get("c")
                h = bar.get("h")
                l = bar.get("l")
                if pre_high is None or (h and h > pre_high): pre_high = h
                if pre_low  is None or (l and l < pre_low):  pre_low  = l

        if pre_volume == 0:
            return {"available": False}

        # חישוב % שינוי מסגירת אתמול
        prev_close = None
        pct_change = None
        if pre_open and pre_close:
            # קבל מחיר סגירה של אתמול מ-quote
            try:
                quote_d = fh(f"/quote?symbol={symbol}")
                prev_close = quote_d.get("pc")
                if prev_close and prev_close > 0:
                    pct_change = round((pre_close - prev_close) / prev_close * 100, 2)
            except: pass

        significant = pre_volume >= 50000  # נפח משמעותי

        return {
            "available":  True,
            "volume":     int(pre_volume),
            "open":       pre_open,
            "close":      pre_close,
            "high":       pre_high,
            "low":        pre_low,
            "pctChange":  pct_change,
            "significant": significant,
            "prevClose":  prev_close,
        }
    except Exception as e:
        return {"available": False}


def get_yahoo_fundamentals(symbol):
    """נתונים פונדמנטליים מ-Yahoo Finance API לא רשמי"""
    try:
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=defaultKeyStatistics,financialData,summaryDetail,earnings"
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        })
        with urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))

        result = data.get("quoteSummary", {}).get("result", [])
        if not result:
            return {}

        d = result[0]
        ks = d.get("defaultKeyStatistics", {})
        fd = d.get("financialData", {})
        sd = d.get("summaryDetail", {})

        def val(obj, key):
            v = obj.get(key, {})
            if isinstance(v, dict):
                return v.get("raw")
            return v

        # PEG Ratio
        peg = val(ks, "pegRatio")
        # Forward PE
        forward_pe = val(ks, "forwardPE")
        # Short Float %
        short_float = val(ks, "shortPercentOfFloat")
        if short_float: short_float = round(short_float * 100, 2)
        # Short Ratio
        short_ratio = val(ks, "shortRatio")
        # Beta
        beta = val(ks, "beta")
        # Book Value per share
        book_val = val(ks, "bookValue")
        # EPS Forward
        eps_forward = val(ks, "forwardEps")
        # EPS Trailing
        eps_trailing = val(ks, "trailingEps")
        # Revenue per share
        rev_per_share = val(fd, "revenuePerShare")
        # Gross Margins
        gross_margin = val(fd, "grossMargins")
        # Operating Margins
        op_margin = val(fd, "operatingMargins")
        # Profit Margins
        profit_margin = val(fd, "profitMargins")
        # Revenue Growth
        rev_growth = val(fd, "revenueGrowth")
        # Earnings Growth
        earn_growth = val(fd, "earningsGrowth")
        # Current Ratio
        current_ratio = val(fd, "currentRatio")
        # Debt to Equity
        debt_eq = val(fd, "debtToEquity")
        if debt_eq: debt_eq = round(debt_eq / 100, 2)  # Yahoo מחזיר כ-%
        # Return on Equity
        roe = val(fd, "returnOnEquity")
        # Return on Assets
        roa = val(fd, "returnOnAssets")
        # Free Cash Flow
        fcf = val(fd, "freeCashflow")
        # Target mean price
        target = val(fd, "targetMeanPrice")
        # Recommendation
        rec = fd.get("recommendationKey", "")
        # Dividend Yield
        div_yield = val(sd, "dividendYield")
        # 52W High/Low
        week52_high = val(sd, "fiftyTwoWeekHigh")
        week52_low  = val(sd, "fiftyTwoWeekLow")
        # Market Cap
        market_cap = val(sd, "marketCap")
        if market_cap: market_cap = round(market_cap / 1e9, 2)  # B
        # PE trailing
        pe_trailing = val(sd, "trailingPE")
        # PS ratio
        ps_ratio = val(ks, "priceToSalesTrailing12Months")

        return {
            "available": True,
            "pegRatio":        peg,
            "forwardPE":       forward_pe,
            "pe":              pe_trailing,
            "ps":              ps_ratio,
            "shortFloat":      short_float,
            "shortRatio":      short_ratio,
            "beta":            beta,
            "bookValue":       book_val,
            "epsForward":      eps_forward,
            "epsTrailing":     eps_trailing,
            "grossMargin":     gross_margin,
            "operatingMargin": op_margin,
            "profitMargin":    profit_margin,
            "revenueGrowth":   rev_growth,
            "earningsGrowth":  earn_growth,
            "currentRatio":    current_ratio,
            "debtToEquity":    debt_eq,
            "roe":             roe,
            "roa":             roa,
            "freeCashflow":    fcf,
            "targetMeanPrice": target,
            "recommendationKey": rec,
            "dividendYield":   div_yield,
            "fiftyTwoWeekHigh": week52_high,
            "fiftyTwoWeekLow":  week52_low,
            "marketCapB":      market_cap,
        }
    except Exception as e:
        return {"available": False}


def get_market_context_for_chart(ticker=None, **kwargs):
    """שולף נתוני שוק אמיתיים לפני ניתוח תמונה"""
    ctx = {}
    
    # SPY — מצב השוק הכללי
    try:
        spy = fh("/quote?symbol=SPY")
        if spy.get("c"):
            ctx["spy_price"] = round(spy["c"], 2)
            ctx["spy_change"] = round(spy.get("dp", 0), 2)
            ctx["spy_direction"] = "עולה" if spy.get("dp",0) > 0 else "יורד"
    except: pass
    
    # VIX — מדד פחד
    try:
        vix = fh("/quote?symbol=VIX")
        if vix.get("c"):
            ctx["vix"] = round(vix["c"], 1)
            ctx["vix_level"] = "גבוה - פחד" if vix["c"] > 25 else "נמוך - רגוע" if vix["c"] < 15 else "בינוני"
    except: pass
    
    # QQQ — נאסד"ק
    try:
        qqq = fh("/quote?symbol=QQQ")
        if qqq.get("c"):
            ctx["qqq_change"] = round(qqq.get("dp", 0), 2)
    except: pass
    
    # חדשות + Pre-Market על המניה הספציפית
    if ticker and ticker not in ("", "null", "None"):
        try:
            # חדשות Finnhub
            news = get_news(ticker)
            all_news = []
            if news:
                all_news += [{"src":"Finnhub","h":n.get("headline","")[:120]} for n in news[:5]]
            # חדשות CNBC
            try:
                ext = get_extnews(ticker)
                if ext:
                    all_news += [{"src":"CNBC","h":n.get("title","")[:120]} for n in ext[:3]]
            except: pass
            if all_news:
                ctx["ticker_news"] = all_news
        except: pass
        
        try:
            quote = fh(f"/quote?symbol={ticker}")
            if quote.get("c"):
                ctx["ticker_price"]  = round(quote["c"], 2)
                ctx["ticker_change"] = round(quote.get("dp", 0), 2)
                pm = quote.get("preMarketPrice")
                if pm:
                    ctx["premarket_price"]  = round(pm, 2)
                    ctx["premarket_change"] = round((pm - quote["c"]) / quote["c"] * 100, 2) if quote["c"] > 0 else 0
        except: pass

        # ── Pattern Recognition מהמערכת ────────────────────────
        try:
            candles_for_pat = daily if daily.get("c") else get_candles(ticker)
            if candles_for_pat.get("c") and len(candles_for_pat["c"]) >= 5:
                c = candles_for_pat["c"]
                o = candles_for_pat.get("o", c)
                h = candles_for_pat.get("h", c)
                l = candles_for_pat.get("l", c)
                v = candles_for_pat.get("v", [0]*len(c))
                N = len(c)

                detected = []

                # Hammer
                if N >= 2:
                    body = abs(c[-2]-o[-2]) if len(o)>1 else 0
                    rng  = h[-2]-l[-2] if len(h)>1 else 0
                    lwick = min(c[-2],o[-2])-l[-2] if len(l)>1 else 0
                    if rng > 0 and body > 0 and lwick > body*1.5 and c[-1] > c[-2]:
                        detected.append("Hammer מאושר 🔨 (שורי)")

                # Bullish Engulfing
                if N >= 2 and c[-1] > o[-1] and c[-2] < o[-2]:
                    if c[-1] > o[-2] and o[-1] < c[-2]:
                        detected.append("Bullish Engulfing 🟢 (שורי חזק)")

                # Bearish Engulfing
                if N >= 2 and c[-1] < o[-1] and c[-2] > o[-2]:
                    if c[-1] < o[-2] and o[-1] > c[-2]:
                        detected.append("Bearish Engulfing 🔴 (דובי חזק)")

                # Doji
                if N >= 1:
                    body = abs(c[-1]-o[-1]) if o else 0
                    rng  = h[-1]-l[-1] if h and l else 0
                    if rng > 0 and body/rng < 0.1:
                        detected.append("Doji ⚖️ (אי-החלטיות)")

                # Three White Soldiers
                if N >= 3 and all(c[i]>o[i] for i in [-3,-2,-1]) and c[-1]>c[-2]>c[-3]:
                    detected.append("Three White Soldiers 🚀 (מומנטום שורי)")

                # Three Black Crows
                if N >= 3 and all(c[i]<o[i] for i in [-3,-2,-1]) and c[-1]<c[-2]<c[-3]:
                    detected.append("Three Black Crows 📉 (מומנטום דובי)")

                # Morning Star
                if N >= 3:
                    big_red   = c[-3] < o[-3] and abs(c[-3]-o[-3]) > (h[-3]-l[-3])*0.5
                    small_mid = abs(c[-2]-o[-2]) < (h[-2]-l[-2])*0.3 if h and l else False
                    big_green = c[-1] > o[-1] and c[-1] > (o[-3]+c[-3])/2
                    if big_red and small_mid and big_green:
                        detected.append("Morning Star 🌅 (היפוך שורי)")

                if detected:
                    ctx["algo_patterns"] = detected
        except: pass

        # ── Multi-Timeframe Analysis ──────────────────────────
        try:
            # שלוף נרות יומיים + שעתיים לניתוח MTF
            daily = get_candles(ticker)  # נרות יומיים כבר מחושבים
            
            def calc_rsi(closes, n=14):
                if len(closes) < n+1: return 50
                gains = losses = 0
                for i in range(-n, 0):
                    d = closes[i] - closes[i-1]
                    if d > 0: gains += d
                    else: losses -= d
                ag, al = gains/n, losses/n
                return round(100 - 100/(1 + ag/al), 1) if al > 0 else 100

            def calc_macd(closes):
                if len(closes) < 26: return 0
                k12, k26 = 2/13, 2/27
                e12 = e26 = closes[0]
                for p in closes:
                    e12 = p*k12 + e12*(1-k12)
                    e26 = p*k26 + e26*(1-k26)
                return round(e12 - e26, 2)

            def calc_ma(closes, n):
                if len(closes) < n: return closes[-1] if closes else 0
                return round(sum(closes[-n:])/n, 2)

            if daily.get("c") and len(daily["c"]) >= 20:
                dc = daily["c"]
                dh = daily.get("h", dc)
                dl = daily.get("l", dc)
                
                # Daily indicators
                d_rsi   = calc_rsi(dc)
                d_macd  = calc_macd(dc)
                d_ma50  = calc_ma(dc, 50)
                d_ma200 = calc_ma(dc, 200)
                d_price = dc[-1]
                
                # מגמה יומית לפי HH/HL
                d_hh_hl = "עולה (HH/HL)" if len(dc)>=3 and dc[-1]>dc[-3] and min(dl[-3:])>min(dl[-6:-3]) else                           "יורדת (LH/LL)" if len(dc)>=3 and dc[-1]<dc[-3] else "דשדוש"
                
                ctx["mtf_daily"] = {
                    "rsi":    d_rsi,
                    "macd":   "חיובי ▲" if d_macd > 0 else "שלילי ▼",
                    "vs_ma50":  f"{'מעל' if d_price > d_ma50 else 'מתחת'} MA50 ({round((d_price/d_ma50-1)*100,1):+.1f}%)" if d_ma50 > 0 else "—",
                    "vs_ma200": f"{'מעל' if d_price > d_ma200 else 'מתחת'} MA200 ({round((d_price/d_ma200-1)*100,1):+.1f}%)" if d_ma200 > 0 else "—",
                    "trend":  d_hh_hl,
                }

                # Hourly — נרות שעתיים מ-Polygon
                if POLYGON_KEY:
                    import datetime
                    to_dt  = datetime.date.today().isoformat()
                    frm_dt = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
                    h_data = pg(f"/v2/aggs/ticker/{ticker}/range/1/hour/{frm_dt}/{to_dt}?adjusted=true&sort=asc&limit=100")
                    h_results = h_data.get("results", [])
                    if h_results and len(h_results) >= 10:
                        hc = [r["c"] for r in h_results]
                        h_rsi  = calc_rsi(hc)
                        h_macd = calc_macd(hc)
                        h_ema9 = hc[-1]
                        k = 2/10
                        for p in hc[-20:]: h_ema9 = p*k + h_ema9*(1-k)
                        
                        ctx["mtf_hourly"] = {
                            "rsi":  h_rsi,
                            "macd": "חיובי ▲" if h_macd > 0 else "שלילי ▼",
                            "ema9_vs_price": "מחיר מעל EMA9 ▲" if hc[-1] > h_ema9 else "מחיר מתחת EMA9 ▼",
                            "trend": "עולה" if hc[-1] > hc[-5] else "יורד" if hc[-1] < hc[-5] else "דשדוש",
                        }
        except: pass

        # ── Max Drawdown Protection ──────────────────────────
        # בדוק אם המשתמש הפסיד 2+ עסקאות ברצף (מ-localStorage דרך header)
        # השרת מקבל את מצב ה-drawdown מה-Frontend
        max_dd = kwargs.get("max_drawdown_active", False) if kwargs else False
        if max_dd:
            ctx["drawdown_warning"] = "🛑 STOP TRADING — הפסדת 2 עסקאות ברצף! כלל מקצועי: עצור למינימום שעה, בדוק מה השתבש, חזור רק אחרי הפסקה."
            ctx["drawdown_active"] = True

        # ── Liquidity Check ──────────────────────────────────
        if ticker and ticker not in ("", "null", "None"):
            try:
                candles_l = get_candles(ticker)
                vl = candles_l.get("v", [])
                cl = candles_l.get("c", [])
                if len(vl) >= 20 and len(cl) >= 1:
                    avg_vol_20 = sum(vl[-20:]) / 20
                    price_l = cl[-1]
                    # Dollar Volume = נפח × מחיר
                    dollar_vol = avg_vol_20 * price_l

                    if avg_vol_20 < 300000:
                        ctx["liquidity_warning"] = f"🚨 נפח ממוצע נמוך מאוד ({int(avg_vol_20/1000)}K מניות/יום) — מניה לא נזילה! קשה לצאת מהר. הקטן פוזיציה ב-75%."
                        ctx["liquidity_level"] = "very_low"
                    elif avg_vol_20 < 1000000:
                        ctx["liquidity_warning"] = f"⚠️ נפח ממוצע נמוך ({int(avg_vol_20/1000)}K מניות/יום) — נזילות בינונית. הקטן פוזיציה ב-25%."
                        ctx["liquidity_level"] = "low"
                    elif avg_vol_20 >= 10000000:
                        ctx["liquidity_warning"] = f"✅ נפח ממוצע גבוה מאוד ({int(avg_vol_20/1000000)}M מניות/יום) — נזילות מצוינת. אין בעיה."
                        ctx["liquidity_level"] = "excellent"
                    else:
                        ctx["liquidity_warning"] = f"✅ נפח ממוצע תקין ({int(avg_vol_20/1000)}K מניות/יום) — נזילות טובה."
                        ctx["liquidity_level"] = "good"
            except: pass

        # ── News Sentiment Score ─────────────────────────────
        if ticker and ticker not in ("", "null", "None"):
            try:
                pos_words = ["beat","beats","surge","surges","rise","rises","gain","gains",
                             "strong","record","buy","upgrade","rally","bullish","profit",
                             "growth","exceed","exceeds","positive","boost","jump","jumps"]
                neg_words = ["miss","misses","drop","drops","fall","falls","loss","losses",
                             "weak","sell","warn","warns","crash","bearish","decline",
                             "cut","downgrade","debt","lawsuit","risk","concern","disappoint"]
                
                all_headlines = []
                news_items = ctx.get("ticker_news", [])
                for n in news_items:
                    h = n.get("h","") if isinstance(n, dict) else str(n)
                    all_headlines.append(h.lower())
                
                pos_count = sum(1 for h in all_headlines 
                               for w in pos_words if w in h)
                neg_count = sum(1 for h in all_headlines 
                               for w in neg_words if w in h)
                total = pos_count + neg_count
                
                if total > 0:
                    sentiment_score = round((pos_count - neg_count) / total * 10, 1)
                    if sentiment_score >= 3:
                        ctx["news_sentiment"] = f"✅ סנטימנט חדשות: חיובי מאוד ({pos_count}+ vs {neg_count}-) ציון: +{sentiment_score}/10"
                        ctx["news_sentiment_level"] = "bullish"
                    elif sentiment_score >= 1:
                        ctx["news_sentiment"] = f"🟡 סנטימנט חדשות: חיובי ({pos_count}+ vs {neg_count}-) ציון: +{sentiment_score}/10"
                        ctx["news_sentiment_level"] = "slight_bullish"
                    elif sentiment_score <= -3:
                        ctx["news_sentiment"] = f"🚨 סנטימנט חדשות: שלילי מאוד ({neg_count}- vs {pos_count}+) ציון: {sentiment_score}/10 — זהירות עם LONG!"
                        ctx["news_sentiment_level"] = "bearish"
                    elif sentiment_score <= -1:
                        ctx["news_sentiment"] = f"⚠️ סנטימנט חדשות: שלילי ({neg_count}- vs {pos_count}+) ציון: {sentiment_score}/10"
                        ctx["news_sentiment_level"] = "slight_bearish"
                    else:
                        ctx["news_sentiment"] = f"🟡 סנטימנט חדשות: ניטרלי (ציון: {sentiment_score}/10)"
                        ctx["news_sentiment_level"] = "neutral"
            except: pass

        # ── Gap Risk Filter ──────────────────────────────────
        if ticker and ticker not in ("", "null", "None"):
            try:
                candles_g = get_candles(ticker)
                cg = candles_g.get("c", [])
                vg = candles_g.get("v", [])
                if len(cg) >= 2 and len(vg) >= 2:
                    prev_close = cg[-2]
                    today_open = candles_g.get("o", cg)[-1]
                    today_vol  = vg[-1]
                    avg_vol    = sum(vg[-20:]) / min(20, len(vg)) if len(vg) >= 5 else 0

                    if prev_close > 0:
                        gap_pct = round((today_open - prev_close) / prev_close * 100, 2)
                        vol_ratio = round(today_vol / avg_vol, 1) if avg_vol > 0 else 0

                        if abs(gap_pct) >= 3:
                            if vol_ratio < 1.5:
                                ctx["gap_warning"] = f"🚨 Gap {'Up' if gap_pct>0 else 'Down'} {gap_pct}% ללא נפח (x{vol_ratio}) — Gap מלכודת! סבירות Gap Fill גבוהה. המתן לאישור."
                                ctx["gap_type"] = "trap"
                            else:
                                ctx["gap_warning"] = f"✅ Gap {'Up' if gap_pct>0 else 'Down'} {gap_pct}% עם נפח (x{vol_ratio}) — Gap אמיתי עם כסף. Gap & Go אפשרי."
                                ctx["gap_type"] = "real"
                        elif abs(gap_pct) >= 1.5:
                            ctx["gap_warning"] = f"🟡 Gap קטן {gap_pct}% — שים לב לרמת הפתיחה."
                            ctx["gap_type"] = "small"
            except: pass

        # ── VWAP Standard Deviation Bands ────────────────────
        if ticker and ticker not in ("", "null", "None"):
            try:
                candles_sd = get_candles(ticker)
                csd = candles_sd.get("c", [])
                hsd = candles_sd.get("h", [])
                lsd = candles_sd.get("l", [])
                vsd = candles_sd.get("v", [])

                if len(csd) >= 20 and len(vsd) >= 20:
                    # חשב VWAP יומי (מהנרות האחרונים)
                    hlc3 = [(hsd[i]+lsd[i]+csd[i])/3 for i in range(len(csd))]
                    total_vol = sum(vsd[-20:])
                    if total_vol > 0:
                        vwap_val = sum(hlc3[i]*vsd[i] for i in range(len(csd)-20, len(csd))) / total_vol

                        # סטיית תקן מ-VWAP
                        variance = sum(vsd[i] * (hlc3[i] - vwap_val)**2
                                      for i in range(len(csd)-20, len(csd))) / total_vol
                        import math
                        sd_val = math.sqrt(variance)

                        # רמות SD
                        sd1_upper = round(vwap_val + sd_val, 2)
                        sd1_lower = round(vwap_val - sd_val, 2)
                        sd2_upper = round(vwap_val + 2*sd_val, 2)
                        sd2_lower = round(vwap_val - 2*sd_val, 2)
                        sd3_upper = round(vwap_val + 3*sd_val, 2)
                        sd3_lower = round(vwap_val - 3*sd_val, 2)
                        vwap_r    = round(vwap_val, 2)
                        price_sd  = csd[-1]

                        ctx["vwap_calc"]    = vwap_r
                        ctx["sd1_upper"]    = sd1_upper
                        ctx["sd1_lower"]    = sd1_lower
                        ctx["sd2_upper"]    = sd2_upper
                        ctx["sd2_lower"]    = sd2_lower
                        ctx["sd3_upper"]    = sd3_upper
                        ctx["sd3_lower"]    = sd3_lower

                        # זיהוי מיקום המחיר ביחס ל-SD bands
                        if price_sd >= sd3_upper:
                            ctx["sd_position"] = f"מעל SD3 ({sd3_upper}) — קיצוני! היפוך SHORT צפוי בסבירות גבוהה מאוד"
                            ctx["sd_signal"] = "extreme_high"
                        elif price_sd >= sd2_upper:
                            ctx["sd_position"] = f"בין SD2 ({sd2_upper}) ל-SD3 — מתוח, זהירות עם LONG חדש"
                            ctx["sd_signal"] = "high"
                        elif price_sd >= sd1_upper:
                            ctx["sd_position"] = f"בין SD1 ({sd1_upper}) ל-SD2 — מעל VWAP, מגמה חיובית"
                            ctx["sd_signal"] = "bullish"
                        elif price_sd <= sd3_lower:
                            ctx["sd_position"] = f"מתחת SD3 ({sd3_lower}) — קיצוני! היפוך LONG צפוי בסבירות גבוהה מאוד"
                            ctx["sd_signal"] = "extreme_low"
                        elif price_sd <= sd2_lower:
                            ctx["sd_position"] = f"בין SD3 ל-SD2 ({sd2_lower}) — מתוח, זהירות עם SHORT חדש"
                            ctx["sd_signal"] = "low"
                        elif price_sd <= sd1_lower:
                            ctx["sd_position"] = f"בין SD2 ל-SD1 ({sd1_lower}) — מתחת VWAP, מגמה שלילית"
                            ctx["sd_signal"] = "bearish"
                        else:
                            ctx["sd_position"] = f"בתוך SD1 (VWAP: {vwap_r}) — אזור ניטרלי, המתן לפריצה"
                            ctx["sd_signal"] = "neutral"
            except: pass

        # ── Correlation Filter ───────────────────────────────
        try:
            spy_chg = ctx.get("spy_change", 0)
            qqq_chg = ctx.get("qqq_change", 0)
            
            # זיהוי סקטור המניה לקורלציה
            is_tech = any(w in (ctx.get("sector_name","")).lower() 
                         for w in ["tech","software","semiconductor","internet"])
            
            if spy_chg <= -1.5:
                ctx["correlation_warning"] = f"🚨 SPY יורד {spy_chg}% — שוק חלש מאוד. הסבירות ל-LONG להצליח: נמוכה מאוד. עדיף SHORT בלבד."
                ctx["correlation_level"] = "critical"
            elif spy_chg <= -0.8:
                ctx["correlation_warning"] = f"⚠️ SPY יורד {spy_chg}% — רוח נגדית. LONG בסיכון גבוה. אם נכנס — פוזיציה קטנה בחצי."
                ctx["correlation_level"] = "warning"
            elif spy_chg >= 1.5:
                ctx["correlation_warning"] = f"✅ SPY עולה {spy_chg}% — שוק חזק. LONG מועדף, רוח גבית."
                ctx["correlation_level"] = "bullish"
            elif spy_chg >= 0.5:
                ctx["correlation_warning"] = f"🟡 SPY עולה {spy_chg}% — שוק ניטרלי-חיובי."
                ctx["correlation_level"] = "neutral_bull"
            else:
                ctx["correlation_warning"] = f"🟡 SPY {spy_chg}% — שוק ניטרלי."
                ctx["correlation_level"] = "neutral"

            # קורלציה לטכנולוגיה
            if is_tech and qqq_chg <= -1.0:
                ctx["correlation_warning"] += f" QQQ יורד {qqq_chg}% - מניות טכנולוגיה בסיכון מוגבר."
        except: pass

        # ── Volatility Filter (ATR) ──────────────────────────
        if ticker and ticker not in ("", "null", "None"):
            try:
                candles_v = get_candles(ticker)
                cv = candles_v.get("c", [])
                hv = candles_v.get("h", [])
                lv = candles_v.get("l", [])
                if len(cv) >= 15 and len(hv) >= 15 and len(lv) >= 15:
                    atr_len = 14
                    tr_sum = 0
                    for i in range(len(cv)-atr_len, len(cv)):
                        tr = max(hv[i]-lv[i],
                                 abs(hv[i]-cv[i-1]),
                                 abs(lv[i]-cv[i-1]))
                        tr_sum += tr
                    atr = round(tr_sum / atr_len, 2)
                    price = cv[-1]
                    atr_pct = round(atr / price * 100, 1)
                    ctx["atr"] = atr
                    ctx["atr_pct"] = atr_pct
                    if atr_pct > 4:
                        ctx["volatility_level"] = "גבוהה מאוד"
                        ctx["volatility_warning"] = f"🔥 ATR {atr_pct}% — תנודתיות גבוהה מאוד! הקטן פוזיציה ב-50%, הרחב SL."
                    elif atr_pct > 2.5:
                        ctx["volatility_level"] = "גבוהה"
                        ctx["volatility_warning"] = f"⚠️ ATR {atr_pct}% — תנודתיות גבוהה. הקטן פוזיציה ב-25%."
                    elif atr_pct > 1.5:
                        ctx["volatility_level"] = "בינונית"
                        ctx["volatility_warning"] = f"✅ ATR {atr_pct}% — תנודתיות בינונית. פוזיציה רגילה."
                    else:
                        ctx["volatility_level"] = "נמוכה"
                        ctx["volatility_warning"] = f"💤 ATR {atr_pct}% — תנודתיות נמוכה. תנועות קטנות, R/R נמוך."
            except: pass

        # ── Earnings Filter ──────────────────────────────────
        if ticker and ticker not in ("", "null", "None"):
            try:
                ec = get_earnings_calendar(ticker)
                if ec.get("available") and ec.get("daysUntil") is not None:
                    days = ec["daysUntil"]
                    date_str = ec.get("date", "")
                    hour = "לפני פתיחה" if ec.get("hour") == "bmo" else "אחרי סגירה"
                    if days == 0:
                        ctx["earnings_warning"] = f"🚨 דוח רווחים היום ({hour}) — סיכון גבוה מאוד! לא מומלץ להיכנס לעסקה."
                        ctx["earnings_days"] = 0
                    elif days <= 2:
                        ctx["earnings_warning"] = f"⚠️ דוח רווחים בעוד {days} ימים ({date_str} {hour}) — שנה אסטרטגיה: SL רחוק יותר, פוזיציה קטנה יותר."
                        ctx["earnings_days"] = days
                    elif days <= 7:
                        ctx["earnings_warning"] = f"📅 דוח רווחים בעוד {days} ימים ({date_str}) — שים לב, תנודתיות עשויה לעלות."
                        ctx["earnings_days"] = days
            except: pass

        # ── Time of Day Filter ───────────────────────────────
        try:
            import datetime
            now_utc = datetime.datetime.utcnow()
            is_dst  = 3 <= now_utc.month <= 11
            et_hour = (now_utc.hour - (4 if is_dst else 5)) % 24
            et_min  = now_utc.minute
            et_time = et_hour + et_min / 60

            if 9.5 <= et_time < 10.0:
                ctx["time_of_day"] = "open"
                ctx["time_warning"] = "⚠️ 9:30-10:00 ET — פתיחה תנודתית מאוד. מומלץ להמתין 30 דקות לפני כניסה."
                ctx["time_quality"] = "נמוכה"
            elif 10.0 <= et_time < 11.5:
                ctx["time_of_day"] = "prime"
                ctx["time_warning"] = "✅ 10:00-11:30 ET — זמן מסחר אופטימלי. הכי טוב ל-Day Trade."
                ctx["time_quality"] = "גבוהה"
            elif 11.5 <= et_time < 14.0:
                ctx["time_of_day"] = "midday"
                ctx["time_warning"] = "🟡 11:30-14:00 ET — צהריים, נפח יורד. עדיף Swing על Day Trade."
                ctx["time_quality"] = "בינונית"
            elif 14.0 <= et_time < 15.5:
                ctx["time_of_day"] = "slow"
                ctx["time_warning"] = "⚠️ 14:00-15:30 ET — נפח נמוך, תנועות לא אמינות. הימנע מכניסות חדשות."
                ctx["time_quality"] = "נמוכה"
            elif 15.5 <= et_time < 16.0:
                ctx["time_of_day"] = "close"
                ctx["time_warning"] = "⚡ 15:30-16:00 ET — סגירה, תנועות חדות. הזדמנויות אבל סיכון גבוה."
                ctx["time_quality"] = "גבוהה-מסוכנת"
            elif et_time < 9.5 and et_time >= 4.0:
                ctx["time_of_day"] = "premarket"
                ctx["time_warning"] = "📊 Pre-Market — נפח נמוך, מחירים פחות אמינים. זהירות."
                ctx["time_quality"] = "נמוכה"
            else:
                ctx["time_of_day"] = "closed"
                ctx["time_warning"] = "🔴 שוק סגור — ניתוח לתכנון מחר."
                ctx["time_quality"] = "—"
            ctx["et_time_str"] = f"{et_hour:02d}:{et_min:02d} ET"
        except: pass

        # ── Sector Context ────────────────────────────────────
        SECTOR_ETFS = {
            "Technology":"XLK","Healthcare":"XLV","Financials":"XLF","Energy":"XLE",
            "Consumer Cyclical":"XLY","Communication Services":"XLC","Industrials":"XLI",
            "Materials":"XLB","Real Estate":"XLRE","Utilities":"XLU","Consumer Defensive":"XLP",
        }
        try:
            profile = fh(f"/stock/profile2?symbol={ticker}")
            sector_name = profile.get("finnhubIndustry","")
            # מצא ETF מתאים
            sector_etf = None
            for k, v in SECTOR_ETFS.items():
                if any(w in sector_name for w in k.split()):
                    sector_etf = v
                    break
            if sector_etf:
                etf_q = fh(f"/quote?symbol={sector_etf}")
                if etf_q.get("c"):
                    ctx["sector_name"]       = sector_name
                    ctx["sector_etf"]        = sector_etf
                    ctx["sector_etf_change"] = round(etf_q.get("dp", 0), 2)
        except: pass

        # ── Support/Resistance היסטורי ────────────────────────
        # 52W High/Low + POC מהמערכת הראשית
        try:
            m = fh(f"/stock/metric?symbol={ticker}&metric=all")
            metric = m.get("metric", {})
            w52_high = metric.get("52WeekHigh")
            w52_low  = metric.get("52WeekLow")
            price    = ctx.get("ticker_price", 0)

            if w52_high and w52_low and price > 0:
                ctx["week52_high"] = round(w52_high, 2)
                ctx["week52_low"]  = round(w52_low, 2)
                dist_high = round((w52_high - price) / price * 100, 1)
                dist_low  = round((price - w52_low)  / price * 100, 1)
                ctx["dist_from_52w_high"] = dist_high
                ctx["dist_from_52w_low"]  = dist_low
                # קרוב לשיא/שפל = רמה חשובה
                ctx["near_52w_high"] = dist_high < 3   # תוך 3% מהשיא
                ctx["near_52w_low"]  = dist_low  < 3   # תוך 3% מהשפל

            # נרות לחישוב POC ורמות תמיכה/התנגדות
            candles = get_candles(ticker)
            if candles.get("c") and len(candles["c"]) >= 20:
                c = candles["c"]
                h = candles.get("h", c)
                l = candles.get("l", c)
                v = candles.get("v", [1]*len(c))

                # POC — Volume Profile
                if len(h) >= 20 and len(l) >= 20:
                    all_h = max(h[-90:]) if len(h) >= 90 else max(h)
                    all_l = min(l[-90:]) if len(l) >= 90 else min(l)
                    step  = (all_h - all_l) / 50 if all_h > all_l else 1
                    vol_map = {}
                    for i in range(len(c)-min(90,len(c)), len(c)):
                        hi_i = h[i] if i < len(h) else c[i]
                        lo_i = l[i] if i < len(l) else c[i]
                        vi   = v[i] if i < len(v) else 0
                        bars = max(1, round((hi_i - lo_i) / step))
                        vpb  = vi / bars
                        price_lvl = lo_i
                        while price_lvl <= hi_i:
                            bucket = round((price_lvl - all_l) / step)
                            vol_map[bucket] = vol_map.get(bucket, 0) + vpb
                            price_lvl += step
                    if vol_map:
                        max_bucket = max(vol_map, key=vol_map.get)
                        poc = round(all_l + max_bucket * step, 2)
                        ctx["poc"] = poc

                # תמיכה/התנגדות — שיא/שפל 20 נרות
                ctx["resistance_20d"] = round(max(h[-20:]), 2) if len(h) >= 20 else None
                ctx["support_20d"]    = round(min(l[-20:]), 2) if len(l) >= 20 else None

                # MA50 / MA200
                if len(c) >= 50:
                    ctx["ma50"]  = round(sum(c[-50:]) / 50, 2)
                if len(c) >= 200:
                    ctx["ma200"] = round(sum(c[-200:]) / 200, 2)

        except: pass

    return ctx


def identify_ticker_from_chart(image_base64, media_type="image/jpeg"):
    """שלב 1 — זיהוי טיקר מהתמונה בלבד (מהיר)"""
    anthropic_key = os.environ.get("ANTHROPIC_KEY", "")
    if not anthropic_key:
        return None
    try:
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": "claude-opus-4-5",
            "max_tokens": 50,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64,
                        }
                    },
                    {
                        "type": "text",
                        "text": "What is the stock ticker symbol shown in this chart? Reply with ONLY the ticker symbol (e.g. AAPL, TSLA, NVDA). If you cannot identify it, reply with: null"
                    }
                ]
            }]
        }
        req = Request(url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "x-api-key": anthropic_key,
                "anthropic-version": "2023-06-01"
            },
            method="POST"
        )
        with urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
        ticker = resp.get("content", [{}])[0].get("text", "").strip().upper()
        # נקה — רק אותיות וספרות
        import re
        ticker = re.sub(r"[^A-Z0-9.]", "", ticker)
        return ticker if ticker and ticker != "NULL" and len(ticker) <= 6 else None
    except:
        return None


def analyze_chart_image(image_base64, media_type="image/jpeg", ticker=None):
    """ניתוח גרף מתמונה עם Claude Vision"""
    anthropic_key = os.environ.get("ANTHROPIC_KEY", "")
    if not anthropic_key:
        return {"error": "ANTHROPIC_KEY לא מוגדר"}
    try:
        # שלוף context שוק אמיתי
        max_dd = body.get('maxDrawdown', False) if 'body' in dir() else False
        market_ctx = get_market_context_for_chart(ticker, max_drawdown_active=max_dd)
        
        # בנה טקסט context
        ctx_lines = ["═══ נתוני שוק בזמן אמת ═══"]
        if market_ctx.get("spy_price"):
            ctx_lines.append(f"SPY: ${market_ctx['spy_price']} ({market_ctx.get('spy_direction','')}{market_ctx.get('spy_change',0):+.2f}%)")
        if market_ctx.get("vix"):
            ctx_lines.append(f"VIX: {market_ctx['vix']} — {market_ctx.get('vix_level','')}")
        if market_ctx.get("qqq_change") is not None:
            ctx_lines.append(f"QQQ: {market_ctx['qqq_change']:+.2f}%")
        if market_ctx.get("ticker_price") and ticker:
            ctx_lines.append(f"{ticker}: ${market_ctx['ticker_price']} ({market_ctx.get('ticker_change',0):+.2f}%)")
        if market_ctx.get("premarket_price"):
            ctx_lines.append(f"Pre-Market: ${market_ctx['premarket_price']} ({market_ctx.get('premarket_change',0):+.2f}%)")
        if market_ctx.get("ticker_news"):
            ctx_lines.append("חדשות אחרונות:")
            for n in market_ctx["ticker_news"]:
                if isinstance(n, dict):
                    ctx_lines.append(f"  [{n.get('src','')}] {n.get('h','')}")
                else:
                    ctx_lines.append(f"  • {n}")
        # VWAP SD Bands
        if market_ctx.get("sd_position"):
            ctx_lines.append("")
            ctx_lines.append("── VWAP Standard Deviation Bands ──")
            ctx_lines.append(f"VWAP: ${market_ctx.get('vwap_calc','—')} | SD1: ${market_ctx.get('sd1_lower','—')}-${market_ctx.get('sd1_upper','—')}")
            ctx_lines.append(f"SD2: ${market_ctx.get('sd2_lower','—')}-${market_ctx.get('sd2_upper','—')} | SD3: ${market_ctx.get('sd3_lower','—')}-${market_ctx.get('sd3_upper','—')}")
            ctx_lines.append(f"מיקום נוכחי: {market_ctx['sd_position']}")
            sig = market_ctx.get("sd_signal","")
            if sig == "extreme_high":
                ctx_lines.append("=> המלצה: SHORT על נגיעה ב-SD3, SL מעל SD3, TP ב-VWAP")
            elif sig == "extreme_low":
                ctx_lines.append("=> המלצה: LONG על נגיעה ב-SD3, SL מתחת SD3, TP ב-VWAP")
            elif sig == "neutral":
                ctx_lines.append("=> המתן לפריצה מעל SD1 או שבירה מתחת SD1")

        # Max Drawdown
        if market_ctx.get("drawdown_warning"):
            ctx_lines.append("")
            ctx_lines.append("!! " + market_ctx["drawdown_warning"])

        # Liquidity
        if market_ctx.get("liquidity_warning"):
            ctx_lines.append("")
            ctx_lines.append("── נזילות ──")
            ctx_lines.append(market_ctx["liquidity_warning"])

        # News Sentiment
        if market_ctx.get("news_sentiment"):
            ctx_lines.append("")
            ctx_lines.append("── סנטימנט חדשות ──")
            ctx_lines.append(market_ctx["news_sentiment"])

        # Gap Risk
        if market_ctx.get("gap_warning"):
            ctx_lines.append("")
            ctx_lines.append("── Gap Risk ──")
            ctx_lines.append(market_ctx["gap_warning"])

        # Correlation
        if market_ctx.get("correlation_warning"):
            ctx_lines.append("")
            ctx_lines.append("── קורלציה עם השוק ──")
            ctx_lines.append(market_ctx["correlation_warning"])

        # Volatility
        if market_ctx.get("volatility_warning"):
            ctx_lines.append("")
            ctx_lines.append("── תנודתיות (ATR) ──")
            ctx_lines.append(market_ctx["volatility_warning"])

        # Earnings Warning
        if market_ctx.get("earnings_warning"):
            ctx_lines.append("")
            ctx_lines.append("── דוח רווחים ──")
            ctx_lines.append(market_ctx["earnings_warning"])

        # Time of Day
        if market_ctx.get("time_warning"):
            ctx_lines.append("")
            ctx_lines.append("── שעת מסחר ──")
            ctx_lines.append(f"שעה: {market_ctx.get('et_time_str','—')} | איכות: {market_ctx.get('time_quality','—')}")
            ctx_lines.append(market_ctx["time_warning"])

        # Pattern Recognition מהאלגוריתם
        if market_ctx.get("algo_patterns"):
            ctx_lines.append("")
            ctx_lines.append("── דפוסי נרות שזוהו על ידי האלגוריתם ──")
            for pat in market_ctx["algo_patterns"]:
                ctx_lines.append(f"  ✓ {pat}")
            ctx_lines.append("השווה לדפוסים שאתה רואה בגרף — אישור כפול = כניסה חזקה יותר")

        # Multi-Timeframe
        if market_ctx.get("mtf_daily"):
            ctx_lines.append("")
            ctx_lines.append("── Multi-Timeframe Analysis ──")
            d = market_ctx["mtf_daily"]
            ctx_lines.append(f"Daily: RSI {d['rsi']} | MACD {d['macd']} | {d['vs_ma50']} | מגמה: {d['trend']}")
            if d.get("vs_ma200"):
                ctx_lines.append(f"Daily: {d['vs_ma200']}")
        if market_ctx.get("mtf_hourly"):
            h = market_ctx["mtf_hourly"]
            ctx_lines.append(f"Hourly: RSI {h['rsi']} | MACD {h['macd']} | {h['ema9_vs_price']} | מגמה: {h['trend']}")
            # סיכום MTF
            d_bull = market_ctx["mtf_daily"]["trend"].startswith("עולה")
            h_bull = h["trend"] == "עולה"
            if d_bull and h_bull:
                ctx_lines.append("✅ MTF מסכים: יומי + שעתי עולים → כנס LONG בלבד")
            elif not d_bull and not h_bull:
                ctx_lines.append("🔴 MTF מסכים: יומי + שעתי יורדים → כנס SHORT בלבד")
            else:
                ctx_lines.append("⚠️ MTF סותר: כיוונים שונים → זהירות, המתן להסכמה")

        # Support/Resistance היסטורי
        if market_ctx.get("week52_high"):
            ctx_lines.append("")
            ctx_lines.append("── רמות מפתח היסטוריות ──")
            near_h = " ← קרוב מאוד!" if market_ctx.get("near_52w_high") else ""
            near_l = " ← קרוב מאוד!" if market_ctx.get("near_52w_low") else ""
            ctx_lines.append(f"שיא 52W: ${market_ctx['week52_high']} ({market_ctx.get('dist_from_52w_high',0):+.1f}%){near_h}")
            ctx_lines.append(f"שפל 52W: ${market_ctx['week52_low']} (-{market_ctx.get('dist_from_52w_low',0):.1f}%){near_l}")
        if market_ctx.get("poc"):
            ctx_lines.append(f"POC: ${market_ctx['poc']} — נפח מרוכז (תמיכה/התנגדות חזקה)")
        if market_ctx.get("resistance_20d"):
            ctx_lines.append(f"התנגדות 20 ימים: ${market_ctx['resistance_20d']}")
        if market_ctx.get("support_20d"):
            ctx_lines.append(f"תמיכה 20 ימים: ${market_ctx['support_20d']}")
        if market_ctx.get("ma50"):
            ctx_lines.append(f"MA50: ${market_ctx['ma50']}")
        if market_ctx.get("ma200"):
            ctx_lines.append(f"MA200: ${market_ctx['ma200']}")

        # Sector Context
        if market_ctx.get("sector_name"):
            ctx_lines.append("")
            ctx_lines.append("── ביצועי סקטור ──")
            ctx_lines.append(f"סקטור: {market_ctx['sector_name']} ({market_ctx.get('sector_etf','')})")
            etf_chg = market_ctx.get('sector_etf_change', 0)
            spy_chg = market_ctx.get('spy_change', 0)
            rel = round(etf_chg - spy_chg, 2)
            direction = "מוביל ▲" if rel > 0.5 else "פגור ▼" if rel < -0.5 else "ניטרלי"
            ctx_lines.append(f"ETF היום: {etf_chg:+.2f}% | S&P: {spy_chg:+.2f}% | יחסי: {rel:+.2f}% — {direction}")
            if rel > 1:
                ctx_lines.append("⚡ הסקטור חזק מאוד היום — רוח גבית לעסקה")
            elif rel < -1:
                ctx_lines.append("⚠️ הסקטור חלש היום — רוח נגדית, הקטן פוזיציה")
        ctx_lines.append("═══════════════════════════")
        context_text = "\n".join(ctx_lines)
        
        # בנה טקסט הפרומפט המלא כstring נפרד
        prompt_body = """═══════════════════════════════════
שלב 1 — זיהוי מידע בסיסי מהגרף
═══════════════════════════════════
1. זהה את שם/טיקר המניה (מוצג בדרך כלל בפינה שמאל עליון ב-TradingView — למשל AAPL, NVDA, SPY)
2. זהה את ה-Timeframe (1m/2m/3m/5m/10m/15m/30m/1h/2h/4h/D/W)
3. זהה את המחיר הנוכחי (המחיר האחרון הנראה בגרף)
4. קבע סוג מסחר: DAY_TRADE (דקות) או SWING_TRADE (שעה ומעלה)

═══════════════════════════════════
שלב 2 — ניתוח Day Trading (נרות דקות)
═══════════════════════════════════
חשוב: זהה תחילה את האסטרטגיה הדומיננטית מהרשימה — אל תתמקד רק ב-VWAP!
VWAP הוא כלי אחד מתוך רבים — משקלו 1/12 בלבד.

אסטרטגיות Day Trade — זהה איזו מהן מופיעה בגרף:
- Bull Flag: עמוד → דחיסה → פריצה
- Bear Flag: ירידה חדה → דחיסה → המשך ירידה
- VWAP Bounce: נגיעה ב-VWAP + נר היפוך
- Momentum Scalp: נר גדול + נפח גבוה + המשך כיוון
- Gap Fill: פתיחה עם Gap → ממלא לכיוון הסגירה
- 9/20 EMA Cross: EMA9 חוצה EMA20
- Supply/Demand Zone: פריצת אזור היצע/ביקוש היסטורי
- HOD/LOD Reversal: היפוך בשיא/שפל היום
- Pre-Market Level Break: פריצת רמת Pre-Market
- Inside Bar Breakout: נר בתוך הקודם → פריצה
- 3 Bar Play: נר 1 גדול → נר 2 קטן → נר 3 פורץ מעל נר 1 = כניסה
- Flat Top Breakout: התנגדות אופקית עם 3+ נגיעות → פריצה עם נפח = כניסה חזקה
- Ascending Triangle: תחתיות עולות + התנגדות אופקית → פריצה כלפי מעלה
- Descending Triangle: שיאים יורדים + תמיכה אופקית → שבירה כלפי מטה
- Cup & Handle: עיגול + דחיסה קטנה → פריצה = אחת האסטרטגיות האמינות ביותר
- Double Bottom (W): שני שפלים זהים + פריצת הצוואר = היפוך שורי חזק
- Double Top (M): שני שיאים זהים + שבירת הצוואר = היפוך דובי חזק
- Rubber Band: מניה ירדה 3%+ מהממוצע ב-5 נרות → חזרה מהירה לממוצע
- Abandoned Baby: נר + Doji עם גאפ + נר הפוך = היפוך חזק מאוד (נדיר ואמין)
- Kicker Pattern: גאפ פתאומי לכיוון הפוך עם נר חזק = שינוי מגמה חד וחריף
- Belt Hold Bullish: נר ירוק גדול שנפתח בשפל ועולה ללא צל תחתון = כניסה אגרסיבית
- Belt Hold Bearish: נר אדום גדול שנפתח בשיא ויורד ללא צל עליון = לחץ מכירה חזק
- Tasuki Gap: 3 נרות — גדול + גאפ + נר קטן הפוך לא מסגר את הגאפ = המשך מגמה
- Rising Three Methods: נר ירוק גדול + 3 נרות אדומים קטנים בתוכו + נר ירוק חזק = המשך עלייה
- Falling Three Methods: נר אדום גדול + 3 נרות ירוקים קטנים בתוכו + נר אדום חזק = המשך ירידה
- Deliberation Pattern: 3 נרות ירוקים עולים שהולכים וקטנים = מגמת עלייה מתשת, היפוך קרוב

Breaker Blocks + Liquidity Pools — Smart Money Advanced:
Breaker Block — Order Block שהפך לרמה הפוכה:
- Bullish Breaker: Order Block דובי שנשבר כלפי מעלה → הופך לתמיכה חזקה
- Bearish Breaker: Order Block שורי שנשבר כלפי מטה → הופך להתנגדות חזקה
- כניסה על Breaker = כניסה חזקה כי Smart Money שינה כיוון
- Breaker + FVG = צירוף חזק מאוד לכניסה

Liquidity Pools — ציד SL של Smart Money:
- Buy Side Liquidity (BSL): SL מצטברים מעל שיאים קודמים → Smart Money ינסה לגעת בהם
- Sell Side Liquidity (SSL): SL מצטברים מתחת לשפלים קודמים → Smart Money ינסה לגעת בהם
- Equal Highs: 2+ שיאים זהים = BSL חזק, Smart Money ישבור מעלה ואז יהפוך
- Equal Lows: 2+ שפלים זהים = SSL חזק, Smart Money ישבור מטה ואז יהפוך
- Liquidity Sweep: פריצת שיא/שפל + היפוך מיידי = Smart Money סיים ציד, כנס בכיוון ההפוך
- Stop Hunt: ירידה מהירה מתחת לשפל + חזרה מיידית = Long squeeze, כניסה LONG
- זהה: מחיר עולה לשיאים קודמים (BSL) → נר דחייה → כניסה SHORT
- זהה: מחיר יורד לשפלים קודמים (SSL) → נר דחייה → כניסה LONG

Power of 3 (AMD) + Change of Character (CHoCH):
Power of 3 — מבנה יומי של Smart Money:
- Accumulation: בוקר — Smart Money צובר פוזיציות בשקט, תנועה איטית + נפח נמוך
- Manipulation: אמצע — תנועה מזויפת לכיוון ההפוך (ציד SL) + נפח גבוה פתאומי
- Distribution: אחה"צ — התנועה האמיתית מתחילה, Smart Money מוכר לקהל
- זהה: Gap Up בפתיחה → ירידה (Manipulation) → עלייה חזקה (Distribution) = LONG
- זהה: Gap Down בפתיחה → עלייה (Manipulation) → ירידה חזקה (Distribution) = SHORT

Change of Character (CHoCH) — זיהוי היפוך מוקדם:
- Bullish CHoCH: במגמת ירידה — שיא חדש נשבר מעלה לראשונה = היפוך מגמה מתחיל
- Bearish CHoCH: במגמת עלייה — שפל חדש נשבר מטה לראשונה = היפוך מגמה מתחיל
- Strong CHoCH: שבירה עם נפח גבוה + נר גדול = אמין מאוד
- Weak CHoCH: שבירה ללא נפח = ייתכן False Break
- CHoCH + Order Block = כניסה מושלמת בתחילת מגמה חדשה
- CHoCH vs BOS: CHoCH = היפוך | BOS = המשך מגמה

Rejection Wicks + Compression — דחייה ודחיסה לפני פיצוץ:
- Rejection Wick: פתיל > 3× גוף הנר = דחייה חזקה מרמה → כנס בכיוון ההפוך
- Long Upper Wick: פתיל עליון ארוך = דחייה מהתנגדות → כניסה SHORT
- Long Lower Wick: פתיל תחתון ארוך = דחייה מתמיכה → כניסה LONG
- Multiple Rejection Wicks: 2+ פתילים ארוכים באותה רמה = התנגדות/תמיכה חזקה מאוד
- Pin Bar: גוף קטן + פתיל ארוך מאוד (> 4× גוף) = אחד הדפוסים האמינים ביותר
- Compression (Squeeze): טווח נרות הולך וקטן ב-5+ נרות + נפח יורד = פיצוץ מגמה קרוב
- Tight Consolidation: 3+ נרות בטווח צר מאוד (< 0.5%) = אנרגיה מצטברת לפני תנועה
- NR7 (Narrowest Range 7): הנר עם הטווח הקטן ביותר מ-7 נרות = פיצוץ קרוב מאוד
- Inside Bar Compression: 2+ Inside Bars ברצף = דחיסה כפולה, פיצוץ חזק
- Bollinger Band Squeeze: רצועות BB מתכנסות = תנועה גדולה קרובה

Fair Value Gap (FVG) + Imbalance — פערים שהמחיר חוזר למלא:
- Bullish FVG: פער בין שפל נר 1 לשיא נר 3 (נר 2 גדול עלה) = מגנט למחיר מלמטה
- Bearish FVG: פער בין שיא נר 1 לשפל נר 3 (נר 2 גדול ירד) = מגנט למחיר מלמעלה
- Fresh FVG: פער שטרם מולא = כניסה חזקה כשמחיר נוגע בו
- Filled FVG: פער שכבר מולא = פחות אמין
- Partial Fill: פער שמולא חלקית = עדיין פעיל
- Balanced Price Range: שני FVG מולאו = אזור ניטרלי
- כאשר מחיר חוזר ל-FVG + נר היפוך = כניסה מדויקת מאוד
- FVG על טיים פריים גבוה (1h/4h) = חזק יותר מ-FVG על 5 דקות
- Imbalance: תנועה חדה בנר אחד = בדרך כלל תימלא לפני המשך מגמה

Order Blocks — אזורי כניסה של מוסדיים (חשוב מאוד):
זהה Order Blocks בגרף — אלה האזורים שממנם Smart Money נכנס:
- Bullish Order Block: נר אדום גדול לפני תנועת עלייה חזקה — המחיר חוזר לבדוק אותו = כניסה LONG
- Bearish Order Block: נר ירוק גדול לפני תנועת ירידה חזקה — המחיר חוזר לבדוק אותו = כניסה SHORT
- Strong Order Block: נר עם נפח חריג + תנועה חדה אחריו = אזור חזק במיוחד
- Mitigated Order Block: אזור שכבר נבדק — פחות אמין לכניסה נוספת
- Unmitigated Order Block: אזור שטרם נבדק = הכי חזק לכניסה
- כאשר מחיר חוזר ל-Order Block + יש דפוס נרות = כניסה מושלמת

גורמי אישור (לא עיקריים — רק מחזקים את האסטרטגיה):
- VWAP: מחיר מעל = חיובי, מתחת = שלילי, Reclaim = חזק (משקל 1/12)
- Opening Range: פריצה מעל OR High = חיובי, שבירה = שלילי (משקל 1/12)
- EMA9/EMA20: מחיר מעל EMA = חיובי (משקל 1/12)

נפח — ניתוח מתקדם (חשוב מאוד):
- נפח גבוה + תנועה = תנועה אמיתית ומהימנה
- נפח נמוך + תנועה = לא אמין, הימנע מכניסה
- Climax Volume: נפח חריג פי 3+ מהממוצע = תנועה מתשת, לרוב היפוך קרוב — אל תיכנס!
- Dry Up Volume: נפח נמוך מאוד אחרי ירידה = דחיסה לפני פריצה, המתן לנפח
- Volume Spread Analysis: נר גדול + נפח נמוך = מניפולציה, לא אמין
- נפח עולה עם מחיר עולה = מגמה בריאה
- נפח יורד עם מחיר עולה = מגמה נחלשת, שקול יציאה

Market Structure (חשוב לזיהוי מגמה):
- Higher Highs + Higher Lows (HH/HL) = מגמת עלייה מאושרת → עדיף LONG
- Lower Highs + Lower Lows (LH/LL) = מגמת ירידה מאושרת → עדיף SHORT
- Break of Structure (BOS): שבירת שיא/שפל קודם = שינוי מגמה — כניסה חזקה
- Fair Value Gap (FVG): פער בין נרות ללא מסחר = מגנט למחיר, רמת כניסה מצוינת
- Liquidity Sweep: פריצת שיא/שפל ואז היפוך מיידי = מלכודת, היפוך חזק

═══════════════════════════════════
שלב 3 — ניתוח Swing Trading (שעה ומעלה)
═══════════════════════════════════
- מגמה: MA50/MA200 אם מוצגים + HH/HL vs LH/LL
- תמיכה/התנגדות: רמות ברורות בגרף + Fair Value Gaps
- דפוסי היפוך: Morning Star, Evening Star, Head & Shoulders, Double Top/Bottom
- Fibonacci: רמות 38.2%, 50%, 61.8%
- RSI/MACD: אם מוצגים — Divergence = סימן חזק
- Trend Pullback: נסיגה לממוצע נע → כניסה
- Breakout: פריצת התנגדות עם נפח + BOS

═══════════════════════════════════
שלב 4 — חישוב רמת ביטחון מפורטת
═══════════════════════════════════
חשב ציון לכל גורם (0-2 נקודות כל אחד):
- דפוס נרות ברור: 0/1/2
- VWAP מאשר: 0/1/2
- נפח מאשר: 0/1/2
- כיוון מגמה תומך: 0/1/2
- R/R >= 2:1: 0/1/2
- Opening Range מאשר: 0/1/2
- EMA/MA מאשרים: 0/1/2
- אין התנגדות קרובה: 0/1/2
סה"כ: X/16 → המר ל-X/10

═══════════════════════════════════
שלב 5 — Entry Timing מדויק
═══════════════════════════════════
בחר את סוג הכניסה המדויק:
- Breakout Entry: "כנס על פריצת $X עם נר ירוק סוגר מעל הרמה" — אמין יותר, מחיר גבוה יותר
- Pullback Entry: "כנס על חזרה ל-$X (VWAP/EMA/תמיכה)" — מחיר טוב יותר, R/R גבוה יותר
- Momentum Entry: "כנס מיד על נר גדול עם נפח" — מהיר, מסוכן יותר
- Confirmation Entry: "המתן לסגירת נר מעל $X לפני כניסה" — הכי בטוח
- Volume Trigger: "כנס רק אם נפח הנר > ממוצע 10 נרות"

═══════════════════════════════════
שלב 6 — SL/TP מדויקים + Risk Management
═══════════════════════════════════
Day Trade:
- SL: מתחת לשפל הנר האחרון / מתחת ל-VWAP / מתחת ל-OR Low
- TP1: יעד ראשון — R/R 1:1.5 (צא 50% כאן)
- TP2: יעד שני — שיא קודם / רמת התנגדות / R/R 2:1+
- אחרי הגעה ל-TP1: זוז SL לנקודת הכניסה (Breakeven Stop)

Swing Trade:
- SL: מתחת לתמיכה / מתחת ל-MA50 / מתחת ל-FVG
- TP1: התנגדות הקרובה (צא 50% כאן)
- TP2: Fibonacci extension / התנגדות הבאה
- אחרי הגעה ל-TP1: זוז SL לכניסה (Breakeven Stop)

כלל זהב — Partial Exit:
- תמיד צא 50% ב-TP1 ותן לשאר לרוץ ל-TP2
- זה מבטיח רווח גם אם המחיר לא מגיע ל-TP2

═══════════════════════════════════
כלל ברזל — R/R מינימלי
═══════════════════════════════════
אם R/R מתחת ל-1.0 (כלומר SL גדול מ-TP) — זו לא עסקה!
במקרה זה:
1. קבע signal = "NEUTRAL"
2. כתוב ב-reasoning: "R/R של X לא מצדיק כניסה — הסיכון גדול מהרווח"
3. אל תציע entry/tp/sl
זה כלל מוחלט — אין יוצאים מן הכלל!

═══════════════════════════════════
ענה בפורמט JSON בלבד — ללא טקסט לפני או אחרי:
═══════════════════════════════════
{
  "ticker": "שם המניה שזוהה מהגרף (למשל AAPL) או null אם לא נראה",
  "trade_type": "DAY_TRADE" או "SWING_TRADE",
  "timeframe": "הזמן שזוהה (1m/5m/15m/1h/4h/Daily)",
  "current_price": המחיר הנוכחי הנראה בגרף,
  "signal": "LONG" או "SHORT" או "NEUTRAL" — חשוב: אם R/R מתחת ל-1.0 (כלומר הסיכון גדול מהרווח) — חובה להחזיר NEUTRAL עם הסבר ב-reasoning,
  "confidence": מספר 1-10 (חשב לפי total/12*10, עגל),
  "confidence_breakdown": {
    "pattern": 0=אין דפוס / 1=דפוס חלש (Doji,Inside Bar) / 2=דפוס חזק (Engulfing,Hammer,Flag,Cup&Handle) — משקל 2,
    "volume": 0=נפח נמוך מהממוצע / 1=נפח ממוצע / 2=נפח גבוה פי 1.5+ עם תנועה — משקל 2,
    "market_structure": 0=אין מבנה ברור / 1=מבנה חלש / 2=HH/HL ברור או BOS או FVG — משקל 2,
    "strategy": 0=אין אסטרטגיה ברורה / 1=אסטרטגיה חלשה / 2=אסטרטגיה ברורה (Bull Flag, 3 Bar Play וכו') — משקל 2,
    "trend": 0=מגמה נגד העסקה / 1=מגמה ניטרלית / 2=מגמה לכיוון העסקה — משקל 1,
    "vwap": 0=VWAP נגד / 1=VWAP ניטרלי / 2=VWAP מאשר — משקל 1,
    "ema_ma": 0=EMA/MA נגד / 1=EMA/MA ניטרלי / 2=EMA/MA מאשר — משקל 1,
    "no_resistance": 0=התנגדות חזקה קרובה / 1=התנגדות בינונית / 2=אין התנגדות קרובה — משקל 1,
    "total": סכום כל השדות (0-12), confidence = total/12*10
  },
  "entry": מחיר כניסה מדויק,
  "entry_timing": "תיאור מדויק מתי להיכנס (למשל: על פריצת $X / על סגירה מעל VWAP / על pullback ל-EMA9)",
  "tp": מחיר TP,
  "tp2": מחיר TP שני אם רלוונטי או null,
  "sl": מחיר SL,
  "tp_pct": אחוז רווח עד TP1,
  "tp2_pct": אחוז רווח עד TP2 או null,
  "sl_pct": אחוז הפסד,
  "rr_ratio": יחס R/R עד TP1,
  "rr_ratio_tp2": יחס R/R עד TP2 או null,
  "strategy": "שם האסטרטגיה המדויקת",
  "entry_type": "Breakout Entry / Pullback Entry / Momentum Entry / Confirmation Entry / Volume Trigger",
  "partial_exit": "הוראה מדויקת: למשל צא 50% ב-TP1 ב-$X, הזז SL לכניסה, תן לשאר לרוץ ל-TP2",
  "breakeven_stop": "מתי לזוז SL לכניסה — למשל: אחרי הגעה ל-$X",
  "market_structure": "HH/HL עולה / LH/LL יורד / BOS / FVG / Liquidity Sweep",
  "volume_analysis_detail": "Climax/Dry Up/Normal + מה זה אומר לעסקה",
  "patterns": ["דפוס1", "דפוס2"],
  "trend": "תיאור המגמה",
  "market_context": "הקשר כללי — שוק חזק/חלש, מגמה ראשית",
  "support": מחיר תמיכה עיקרי,
  "resistance": מחיר התנגדות עיקרי,
  "vwap": מחיר VWAP אם נראה או null,
  "vwap_position": "above" או "below" או null,
  "opening_range_high": שיא OR או null,
  "opening_range_low": שפל OR או null,
  "ema9": מחיר EMA9 אם נראה או null,
  "ema20": מחיר EMA20 אם נראה או null,
  "volume_analysis": "ניתוח נפח — גבוה/נמוך/ממוצע ומה זה אומר",
  "holding": "זמן החזקה מוצע",
  "key_levels": ["רמה חשובה 1", "רמה חשובה 2", "רמה חשובה 3"],
  "reasoning": "הסבר מקצועי מפורט בעברית: מה אתה רואה, למה זו הכניסה, מה הקטליסט, מה הסיכון",
  "warnings": ["אזהרה ספציפית אם יש"]
}"""
        full_prompt = context_text + "\n\n" + prompt_body
        
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": "claude-opus-4-5",
            "max_tokens": 3000,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64,
                        }
                    },
                    {
                        "type": "text",
                        "text": full_prompt
                    }
                ]
            }]
        }
        req = Request(url, 
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "x-api-key": anthropic_key,
                "anthropic-version": "2023-06-01"
            },
            method="POST"
        )
        with urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        
        # חלץ את הטקסט מהתגובה
        text = resp.get("content", [{}])[0].get("text", "")
        # נקה backticks ותוכן לפני/אחרי JSON
        text = text.strip()
        # הסר ```json ו-```
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        # מצא את ה-JSON בתוך הטקסט — מ-{ עד }
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]
        # נסה parse
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            # נקה תווים בעייתיים ונסה שוב
            import re
            # הסר newlines בתוך string values
            text = re.sub(r'(?<=: ")([^"]*)\n([^"]*?)(?=")', r'\1 \2', text)
            try:
                result = json.loads(text)
            except:
                # fallback — החזר תוצאה חלקית
                result = {
                    "signal": "NEUTRAL",
                    "confidence": 5,
                    "reasoning": text[:500] if text else "לא ניתן לנתח את התגובה",
                    "warnings": ["הניתוח חזר בפורמט לא תקין — נסה שוב"]
                }
        return {"success": True, "analysis": result}
    except Exception as e:
        return {"error": str(e)}


def get_usdils():
    """מחיר דולר/שקל מ-ExchangeRate API (חינמי, ללא key)"""
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        rate = data.get("rates", {}).get("ILS")
        if not rate:
            return {"c": 0, "pc": 0}
        # אין prev — נחזיר רק מחיר נוכחי
        return {"c": round(rate, 4), "pc": round(rate, 4), "dp": 0}
    except:
        # fallback — נסה Finnhub OANDA
        try:
            d = fh("/quote?symbol=OANDA:USD_ILS")
            c = d.get("c", 0)
            if c > 0:
                return {"c": round(c, 4), "pc": round(d.get("pc", c), 4), "dp": round(d.get("dp", 0), 2)}
        except: pass
        return {"c": 0, "pc": 0, "dp": 0}


def get_sector(sector_name):
    # Sector performance via Finnhub ETFs
    SECTOR_ETFS = {
        "Technology":"XLK","Healthcare":"XLV","Financials":"XLF","Energy":"XLE",
        "Consumer Cyclical":"XLY","Communication Services":"XLC","Industrials":"XLI",
        "Materials":"XLB","Real Estate":"XLRE","Utilities":"XLU","Consumer Defensive":"XLP",
    }
    etf = SECTOR_ETFS.get(sector_name)
    if not etf:
        for k,v in SECTOR_ETFS.items():
            if any(w in sector_name for w in k.split()):
                etf = v; break
    if not etf: return {"available": False}
    try:
        q_etf = fh(f"/quote?symbol={etf}")
        q_spy = fh(f"/quote?symbol=SPY")
        etf_chg = round(q_etf.get("dp", 0), 2)
        spy_chg = round(q_spy.get("dp", 0), 2)
        rel = round(etf_chg - spy_chg, 2)
        return {
            "available": True,
            "sector":   sector_name,
            "etf":      etf,
            "etfChg1M": etf_chg,
            "spyChg1M": spy_chg,
            "relative": rel,
            "leading":  rel > 0.5,
            "lagging":  rel < -0.5,
        }
    except:
        return {"available": False}


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def handle_error(self, request, client_address): pass

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/analyze-chart":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                image_b64 = body.get("image", "")
                media_type = body.get("mediaType", "image/jpeg")
                ticker = body.get("ticker", None)

                if not image_b64:
                    self._json({"error": "חסרה תמונה"}, 400)
                    return

                # שלב 1 — אם אין טיקר, זהה קודם (מהיר)
                if not ticker:
                    ticker = identify_ticker_from_chart(image_b64, media_type)

                # שלב 2 — ניתוח מלא עם context + חדשות
                result = analyze_chart_image(image_b64, media_type, ticker)

                # הוסף את הטיקר שזוהה לתגובה
                if ticker and result.get("success"):
                    if result.get("analysis") and not result["analysis"].get("ticker"):
                        result["analysis"]["ticker"] = ticker

                self._json(result)
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        self.send_response(404); self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            try:
                with open("index.html","rb") as f: body = f.read()
                self.send_response(200)
                self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin","*")
                self.end_headers()
                self.wfile.write(body)
            except:
                self.send_response(404); self.end_headers()
            return

        # WebSocket Key endpoint
        if parsed.path == "/api/wskey":
            self._send_json({"key": FINNHUB_KEY})
            return

        if parsed.path == "/api/stock":
            symbol   = qs.get("symbol",[""])[0].upper().strip()
            endpoint = qs.get("endpoint",[""])[0]
            if not symbol:
                self._json({"error":"חסר סימבול"}, 400); return
            try:
                if   endpoint=="quote":       data = get_quote(symbol)
                elif endpoint=="candle":      data = get_candles(symbol)
                elif endpoint=="candle_hourly": data = get_candles_hourly(symbol)
                elif endpoint=="profile":     data = get_profile(symbol)
                elif endpoint=="news":        data = get_news(symbol)
                elif endpoint=="extnews":     data = get_extnews(symbol)
                elif endpoint=="competitors": data = get_competitors(symbol)
                elif endpoint=="macro":       data = get_macro()
                elif endpoint=="earnings":    data = get_earnings(symbol)
                elif endpoint=="insider":     data = get_insider(symbol)
                elif endpoint=="indicators":  data = get_indicators(symbol)
                elif endpoint=="short":       data = get_short_interest(symbol)
                elif endpoint=="earningscal": data = get_earnings_calendar(symbol)
                elif endpoint=="premarket":   data = get_premarket_volume(symbol)
                elif endpoint=="yahoo":       data = get_yahoo_fundamentals(symbol)
                elif endpoint=="usdils":     data = get_usdils()
                elif endpoint=="sector":
                    sector = qs.get("sector",[""])[0]
                    data = get_sector(sector)
                else: data = {"error":"endpoint לא תקין"}
            except Exception as e:
                data = {"error": str(e)}
            self._json(data)
            return

        self.send_response(404); self.end_headers()

    def _json(self, data, code=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            for k,v in HEADERS_OUT.items(): self.send_header(k,v)
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

if __name__ == "__main__":
    print(f"DULITRADE | port {PORT} | Finnhub: {'✓' if FINNHUB_KEY else '✗'} | Polygon: {'✓' if POLYGON_KEY else '✗'}", flush=True)
    ThreadedHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
