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
            now_utc = datetime.datetime.now(datetime.timezone.utc)
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
    # SPY MA50 — לשמש את validate_and_filter_signal
    try:
        spy_candles = get_candles("SPY")
        spy_c = spy_candles.get("c", [])
        if len(spy_c) >= 50:
            spy_ma50 = sum(spy_c[-50:]) / 50
            spy_price_now = ctx.get("spy_price", 0) or 0
            ctx["aboveMa50"] = spy_price_now > spy_ma50 if spy_price_now > 0 else True
        else:
            ctx["aboveMa50"] = True
    except:
        ctx["aboveMa50"] = True
    
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

        # Pattern Recognition הוסר — Claude מזהה דפוסים מהתמונה

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

        # Liquidity הוסר

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

        # Gap Risk הוסר — Claude רואה Gap בתמונה

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

        # Correlation הוסר — SPY נשלח ישירות

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
            now_utc = datetime.datetime.now(datetime.timezone.utc)
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

        # Sector הוסר — Claude יודע סקטור מטיקר

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
            "model": "claude-sonnet-4-6",
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


def validate_and_filter_signal(result, market_ctx=None, trade_type="swing"):
    """ולידציה שרת — חסם סיגנלים חלשים לפני שהם מגיעים למשתמש.
    trade_type: "day" = Day Trade (threshold נמוך יותר), "swing" = Swing (strict)
    """
    if market_ctx is None:
        market_ctx = {}
    sig = result.get("signal", "NEUTRAL")
    if sig not in ("LONG", "SHORT"):
        return result  # כבר NEUTRAL

    rr   = result.get("rr_ratio", 0) or 0
    conf = result.get("confidence", 0) or 0
    reasons = []
    is_day = trade_type == "day"

    # שערים לפי סוג עסקה — מוגבהים כדי לסנן עסקאות חלשות
    min_rr = 1.5  # Day Trade ו-Swing שניהם דורשים R/R 1.5+
    if rr < min_rr:
        reasons.append(f"R/R={rr} מתחת ל-{min_rr} ({'Day Trade' if is_day else 'Swing'})")

    min_conf = 7 if is_day else 6  # Day Trade: דורש Confidence 7+ (היה 5)
    if conf < min_conf:
        reasons.append(f"Confidence={conf}/10 מתחת ל-{min_conf} ({'Day Trade' if is_day else 'Swing'})")

    # Win Probability — חסם אם מתחת ל-65%
    wp = result.get("win_probability", 0) or 0
    if wp > 0 and wp < 65:
        reasons.append(f"Win Probability={wp}% מתחת ל-65% — setup לא מספיק חזק")

    # Confluence — Day Trade דורש לפחות 6
    conf_score = result.get("confluence_score", 0) or 0
    if is_day and conf_score < 6:
        reasons.append(f"Confluence={conf_score} מתחת ל-6 ל-Day Trade")

    # Market Regime — שוק דובי + VIX גבוה מאוד → רק SHORT מותר
    vix = market_ctx.get("vix", 0) or 0
    spy_above_ma50 = market_ctx.get("aboveMa50", True)
    if spy_above_ma50 is None:
        spy_above_ma50 = True
    if sig == "LONG" and not spy_above_ma50 and vix > 30:
        reasons.append(f"שוק דובי (SPY מתחת MA50) + VIX={vix} — LONG נחסם")

    if reasons:
        result["signal"] = "NEUTRAL"
        result["original_signal"] = sig
        result["warnings"] = result.get("warnings", []) + [
            f"🛡 ולידציה בלמה את הסיגנל: {' | '.join(reasons)}"
        ]
    return result


def analyze_chart_image(image_base64, media_type="image/jpeg", ticker=None, image2_base64=None, media_type2="image/jpeg", trade_type="day"):
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
                ctx_lines.append("הערה: מחיר מעל SD3 = מתוח מאוד, שקול זהירות בכניסת LONG")
            elif sig == "extreme_low":
                ctx_lines.append("הערה: מחיר מתחת SD3 = מתוח מאוד, שקול זהירות בכניסת SHORT")

        # Max Drawdown
        if market_ctx.get("drawdown_warning"):
            ctx_lines.append("")
            ctx_lines.append("!! " + market_ctx["drawdown_warning"])

        # News Sentiment
        if market_ctx.get("news_sentiment"):
            ctx_lines.append("")
            ctx_lines.append("── סנטימנט חדשות ──")
            ctx_lines.append(market_ctx["news_sentiment"])

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

        # Pattern context הוסר

        # Multi-Timeframe — ב-Day Trade: רק שעתי (לא יומי)
        # Daily MTF גורם ל-Claude לסרב עסקאות Day Trade בגלל "Choppy Daily"
        is_day_trade_ctx = trade_type == "day"
        if market_ctx.get("mtf_daily") and not is_day_trade_ctx:
            ctx_lines.append("")
            ctx_lines.append("── Multi-Timeframe Analysis ──")
            d = market_ctx["mtf_daily"]
            ctx_lines.append(f"Daily: RSI {d['rsi']} | MACD {d['macd']} | {d['vs_ma50']} | מגמה: {d['trend']}")
            if d.get("vs_ma200"):
                ctx_lines.append(f"Daily: {d['vs_ma200']}")
        if market_ctx.get("mtf_hourly"):
            h = market_ctx["mtf_hourly"]
            if is_day_trade_ctx:
                # Day Trade: רק שעתי — זה הרלוונטי לגרף 5 דקות
                ctx_lines.append("")
                ctx_lines.append("── Hourly Context ──")
                ctx_lines.append(f"שעתי: RSI {h['rsi']} | MACD {h['macd']} | {h['ema9_vs_price']} | מגמה: {h['trend']}")
            else:
                ctx_lines.append(f"Hourly: RSI {h['rsi']} | MACD {h['macd']} | {h['ema9_vs_price']} | מגמה: {h['trend']}")
                # סיכום MTF — רק ל-Swing
                if market_ctx.get("mtf_daily"):
                    d_bull = market_ctx["mtf_daily"]["trend"].startswith("עולה")
                    h_bull = h["trend"] == "עולה"
                    if d_bull and h_bull:
                        ctx_lines.append("MTF: יומי + שעתי עולים — רוח גבית ל-LONG")
                    elif not d_bull and not h_bull:
                        ctx_lines.append("MTF: יומי + שעתי יורדים — רוח גבית ל-SHORT")
                    else:
                        ctx_lines.append("MTF: כיוונים שונים — זהירות, בדוק את הגרף בקפידה")

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

        # Sector context הוסר
        ctx_lines.append("═══════════════════════════")
        context_text = "\n".join(ctx_lines)
        
        # בנה טקסט הפרומפט המלא כstring נפרד
        # הוסף הוראה ל-Multi-Timeframe אם יש 2 תמונות
        # הוראת סוג עסקה
        if trade_type == "day":
            trade_instruction = """
═══════════════════════════════════
סוג עסקה: DAY TRADE
═══════════════════════════════════
המשתמש מחפש עסקה לאותו יום בלבד.
- התמקד בנרות הקצרים (1-15 דקות)
- מחפש: Breakout, VWAP Reclaim, Gap & Go, Bull Flag, Mean Reversion
- SL קרוב: 0.3-1% מהמחיר
- TP קרוב: R/R 1.5-2.5
- זמן החזקה: דקות עד שעות — לא יותר מיום
- חשוב: Daily Choppy לא מונע עסקת Day Trade — אבל הגרף הקצר חייב להראות Setup חד וברור. Choppy בגרף הקצר = NEUTRAL.
- רק אם יש Bounce/Pullback/Breakout חד וברור בגרף הקצר — זו עסקה.
"""
        else:
            trade_instruction = """
═══════════════════════════════════
סוג עסקה: SWING TRADE
═══════════════════════════════════
המשתמש מחפש עסקה לימים עד שבועות.
- התמקד במגמה הכללית ובנרות שעתיים/יומיים
- מחפש: Trend Pullback, Support Bounce, RS Leader, Breakout מרמה חשובה
- SL רחוק יותר: 2-5% מהמחיר
- TP רחוק: R/R 2-3
- זמן החזקה: ימים עד שבועות
"""

        mtf_instruction = ""
        if image2_base64:
            mtf_instruction = """
═══════════════════════════════════
קיבלת 2 תמונות — Multi-Timeframe:
תמונה 1 = גרף ראשי (כיוון כללי)
תמונה 2 = גרף כניסה (כניסה מדויקת)

כלל חובה: אם תמונה 1 מראה מגמה יורדת — אל תמליץ LONG בתמונה 2.
אם תמונה 1 מראה מגמה עולה — אל תמליץ SHORT בתמונה 2.
הכיוון של תמונה 1 שולט תמיד.
═══════════════════════════════════
"""

        prompt_body = trade_instruction + mtf_instruction + """אתה מנתח טכני מקצועי. נתח את הגרף ותן המלצת מסחר.

זהה:
1. טיקר + Timeframe + מחיר נוכחי + סוג (DAY_TRADE/SWING_TRADE)
2. האסטרטגיה הדומיננטית: Bull Flag, Bear Flag, VWAP Reclaim, Breakout, Breakdown, Support Bounce, Resistance Rejection, Gap Fill, 3 Bar Play, Flat Top, Cup & Handle, Double Bottom/Top, Mean Reversion, Order Block, FVG, CHoCH, Liquidity Sweep, Pin Bar, Compression/Squeeze, Rubber Band
3. דפוסי נרות: Hammer, Engulfing, Doji, Morning/Evening Star, Marubozu, Harami, Inside Bar, Abandoned Baby, Kicker, Belt Hold, Tasuki Gap, Three Methods, Deliberation, Rejection Wick
4. Market Structure: HH/HL עולה / LH/LL יורד / BOS / FVG / CHoCH / Liquidity Sweep
5. נפח: גבוה/נמוך/Climax/Dry Up
6. VWAP: מעל/מתחת/Reclaim (גורם אישור בלבד — לא עיקרי)
7. EMA/MA: מאשר/מתנגד
8. Order Blocks + Breaker Blocks + Liquidity Pools אם נראים

שלב ראשון — זהה Market Regime:
לפני כל החלטה, קבע באיזה מצב השוק:

TRENDING: HH/HL ברורים (עלייה) או LH/LL ברורים (ירידה) + נפח תומך + נרות בכיוון אחד
→ כנס עם המגמה, Setup ברור

BREAKOUT: מניה יצאה מאזור דחיסה עם נפח פי 1.5+ מהממוצע
→ כנס בכיוון הפריצה, SL מתחת לאזור הדחיסה

CHOPPY: תנועה ללא כיוון, נרות קטנים, נפח נמוך, כל עלייה נמחקת
→ NEUTRAL — אבל רק אם הגרף שלפניך Choppy. ב-Day Trade: Daily Choppy לא מונע עסקה.

סקאלת Confidence (1-10) — השתמש בה בעקביות:
9-10: Setup מושלם — כל הפקטורים מאשרים, כניסה מיידית מומלצת
7-8: Setup טוב — 3-4 פקטורים מאשרים, כניסה מומלצת בגודל מלא
5-6: Setup בינוני — כניסה בגודל מצומצם בלבד (50% גודל רגיל)
3-4: Setup חלש — אין edge ברור, דלג על העסקה
1-2: לא ברור / נגד מגמה — NEUTRAL בכל מקרה

חשוב ביותר — גישת מסחר:
- 80% מה-Day Trades נכשלים. עדיף לדלג על 10 עסקאות בינוניות מאשר להפסיד בהן.
- NEUTRAL הוא ברירת המחדל. LONG/SHORT רק כשה-Setup מדבר בעד עצמו ללא ספק.
- אם אתה מהסס בין LONG ל-NEUTRAL — תמיד בחר NEUTRAL.

כללי ברזל (אין יוצאי דופן):
1. R/R: מתחת ל-1.5 ב-DAY_TRADE / מתחת ל-1.5 ב-SWING → NEUTRAL חובה, אל תציע עסקה
2. Confidence: מתחת ל-7/10 ב-DAY_TRADE / מתחת ל-6/10 ב-SWING → NEUTRAL חובה
3. win_probability: פחות מ-65% → NEUTRAL חובה. היה שמרן — 65% אומר שסביר להניח העסקה נכשלת.
4. Confluence Score: מתחת ל-6 ב-DAY_TRADE → NEUTRAL חובה (4-5 לא מספיק)
5. אם הגרף לא ברור ואין setup מוגדר → NEUTRAL. pattern בינוני = NEUTRAL.
6. Setup שמצריך wishful thinking כדי להצדיק כניסה → NEUTRAL.
7. השתמש בכל הגרף להבנת הקשר ומגמה, אבל קבל החלטת כניסה לפי הנרות האחרונים בצד ימין
8. עדיף לסחור עם המגמה: LONG במגמת עלייה (HH/HL), SHORT במגמת ירידה (LH/LL)
9. היפוך מגמה אפשרי רק כשיש CHoCH ברור + אישור נפח + דפוס נרות — אחרת NEUTRAL
10. Entry Timing: המתן לסגירת נר מעל/מתחת הרמה לפני כניסה — מונע כניסות על פריצות מזויפות
11. Higher Timeframe Bias:
    ב-DAY TRADE: אין כלל Higher Timeframe. נתח את הגרף שלפניך בלבד. Daily לא רלוונטי.
    ב-SWING TRADE:
    - מגמה יומית עולה → עדיפות ל-LONG
    - מגמה יומית יורדת → עדיפות ל-SHORT
    - Choppy יומי → בדוק את הגרף הנוכחי. Setup חד וברור עם נפח = כנס. ספק = NEUTRAL.

כניסות תקפות ל-Day Trade (רק אם עומדות בכל כללי הברזל לעיל):
- Bounce ברור מתמיכה עם wick דחייה + אישור נפח
- Pullback ל-VWAP/EMA עם נר היפוך + נפח עולה
- Breakout מ-Consolidation עם נפח פי 1.5+ מהממוצע
- Gap Continuation אחרי 30 דקות ראשונות עם Momentum ברור

סדר חישוב SL/TP: קודם זהה TP ריאלי (התנגדות/FVG/OB), אחר כך קבע SL קטן ממנו.

Psychology of Price Action:
- זהה איפה SL של Longs (מתחת שפלים) ו-Shorts (מעל שיאים) - Smart Money יצוד אותם
- Retail Trap: פריצה ברורה ואז היפוך מיידי = מלכודת לקהל
- Fear Zone: אחרי ירידה חדה = הזדמנות קנייה. Greed Zone: אחרי עלייה חדה = זמן לצאת

Confluence Score:
- כל גורם מאשר = +1, גורמים חזקים (OB/FVG/CHoCH/VWAP Reclaim) = +2
- אל תספור גורמים מתואמים: מעל MA50 + מעל MA200 + מעל VWAP = 1 נקודה בלבד, לא 3
- 8+ = כניסה חזקה | 6-7 = טובה | מתחת ל-6 ב-Day Trade = NEUTRAL (לא מספיק)

2 תרחישי כניסה:
Conservative: SL קרוב, TP קרוב, R/R 1.5 (לסוחרים זהירים)
Standard: SL בינוני, TP בינוני, R/R 2.0 (המלצה רגילה)

What Could Go Wrong:
- ציין מחיר/תנאי שיבטל את ה-Setup לחלוטין
- מה הסימן הראשון שהעסקה נכשלת

ענה JSON בלבד:
{
  "ticker": "טיקר או null",
  "trade_type": "DAY_TRADE או SWING_TRADE",
  "timeframe": "1m/5m/15m/1h/4h/Daily",
  "current_price": מחיר,
  "signal": "LONG או SHORT או NEUTRAL",
  "confidence": 1-10,
  "confidence_breakdown": {
    "pattern": 0-2,
    "volume": 0-2,
    "market_structure": 0-2,
    "strategy": 0-2,
    "trend": 0-1,
    "vwap": 0-1,
    "ema_ma": 0-1,
    "no_resistance": 0-1,
    "total": 0-12
  },
  "entry": מחיר,
  "entry_timing": "תיאור מתי להיכנס",
  "tp": מחיר TP1,
  "tp2": מחיר TP2 או null,
  "sl": מחיר SL,
  "tp_pct": אחוז,
  "sl_pct": אחוז,
  "rr_ratio": יחס,
  "strategy": "שם האסטרטגיה",
  "entry_type": "Breakout/Pullback/Momentum/Confirmation",
  "patterns": ["דפוס1"],
  "trend": "תיאור",
  "market_structure": "HH/HL או LH/LL או BOS או FVG או CHoCH",
  "volume_analysis": "ניתוח נפח",
  "market_context": "הקשר כללי",
  "support": מחיר,
  "resistance": מחיר,
  "vwap": מחיר או null,
  "vwap_position": "above/below/null",
  "ema9": מחיר או null,
  "ema20": מחיר או null,
  "opening_range_high": מחיר או null,
  "opening_range_low": מחיר או null,
  "partial_exit": "הוראת יציאה חלקית",
  "breakeven_stop": "מתי להזיז SL לכניסה",
  "key_levels": ["רמה1","רמה2"],
  "holding": "זמן החזקה",
  "reasoning": "הסבר מפורט בעברית",
  "warnings": ["אזהרה אם יש"],
  "confluence_score": מספר 0-15,
  "confluence_factors": ["גורם1", "גורם2"],
  "psychology": "מה הסוחרים האחרים חושבים ואיפה ה-SL שלהם",
  "scenario_conservative": {"entry": מחיר, "tp": מחיר, "sl": מחיר, "rr": יחס},
  "scenario_standard":     {"entry": מחיר, "tp": מחיר, "sl": מחיר, "rr": יחס},
  "invalidation": "מחיר/תנאי שיבטל את ה-Setup",
  "first_warning_sign": "הסימן הראשון שהעסקה נכשלת",
  "post_entry_scenarios": {
    "bull_case": "מה קורה אם הכניסה עובדת — תיאור קצר של מה המחיר עושה, כמה זמן, target ראשון",
    "bear_case": "מה קורה אם הכניסה נכשלת — איפה המחיר שובר, מתי לצאת מיידית",
    "neutral_case": "consolidation/sideways — מה לחכות, איפה נקודת ההחלטה הבאה"
  },
  "timing_note": "מתי הכי טוב להיכנס — candle close? breakout confirm? pull-back? limit order?",
  "win_probability": 0,
  "max_adverse_excursion": 0.0,
  "ideal_hold_time": "15m/30m/1h/2h",
  "skip_reason": ""
}

הערות לשדות החדשים:
- win_probability: מספר 0-100 בלבד (ללא %). כמה % מהמקרים דומים הצליחו לפי ניסיון.
- max_adverse_excursion: כמה % המחיר יכול לנוע נגדנו לפני שה-SL נפגע (0.0-5.0).
- ideal_hold_time: זמן החזקה אידאלי לפי הסטאפ — 15m/30m/1h/2h.
- skip_reason: אם העסקה לא ראויה — כתוב סיבה קצרה. אם כדאי לסחור — השאר ריק "".
"""
        full_prompt = context_text + "\n\n" + prompt_body
        
        url = "https://api.anthropic.com/v1/messages"
        # בנה content — תמונה אחת או שתיים
        content_parts = []
        if image2_base64:
            content_parts.append({
                "type": "text",
                "text": "תמונה 1 — גרף ראשי (יומי/שעתי — כיוון כללי):"
            })
        content_parts.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": image_base64,
            }
        })
        if image2_base64:
            content_parts.append({
                "type": "text",
                "text": "תמונה 2 — גרף כניסה (5 דקות/15 דקות — כניסה מדויקת):"
            })
            content_parts.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type2,
                    "data": image2_base64,
                }
            })
        content_parts.append({
            "type": "text",
            "text": full_prompt
        })

        payload = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 3000,
            "messages": [{
                "role": "user",
                "content": content_parts
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
        # שלוף JSON — תחילה נסה regex מדויק, אחר כך fallback לחיפוש { }
        import re as _re
        _json_match = _re.search(r'\{[\s\S]*\}', text)
        if _json_match:
            text = _json_match.group(0)
        else:
            # fallback: הסר markdown backticks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
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
        # נרמל שדות חדשים — מנע type mismatches מ-Claude
        try:
            wp = result.get("win_probability", 0)
            result["win_probability"] = max(0, min(100, int(str(wp).replace("%","").strip()))) if wp else 0
        except:
            result["win_probability"] = 0
        try:
            mae = result.get("max_adverse_excursion", 0)
            result["max_adverse_excursion"] = round(float(mae), 2) if mae else 0.0
        except:
            result["max_adverse_excursion"] = 0.0
        if not isinstance(result.get("ideal_hold_time", ""), str):
            result["ideal_hold_time"] = ""
        if not isinstance(result.get("skip_reason", ""), str):
            result["skip_reason"] = ""

        # ולידציה שרת — חסם סיגנלים חלשים (threshold שונה לפי trade_type)
        result = validate_and_filter_signal(result, market_ctx, trade_type=trade_type)
        # חשב quality_score בצד שרת
        sig_final = result.get("signal", "NEUTRAL")
        if sig_final in ("LONG", "SHORT"):
            rr_q   = result.get("rr_ratio", 1) or 1
            conf_q = result.get("confidence", 5) or 5
            confs_q = result.get("confluence_score", 5) or 5
            rr_base  = 1.5  # שניהם דורשים 1.5 מינימום
            rr_bonus = min(20, int((rr_q - rr_base) * 10)) if rr_q >= rr_base else 0
            wp_q = result.get("win_probability", 50) or 50
            wp_bonus = max(-20, min(15, int((wp_q - 65) * 0.6)))  # -20 עד +15 לפי win_prob
            result["quality_score"] = min(100, max(0, int(conf_q * 4 + confs_q * 3 + rr_bonus + wp_bonus)))
        else:
            result["quality_score"] = 0
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


def get_market_regime():
    """מחזיר מצב השוק הכללי: Bull/Bear/Choppy + SPY vs MA50/MA200 + VIX level"""
    import datetime
    try:
        spy_q = fh("/quote?symbol=SPY")
        spy_price = spy_q.get("c", 0)
        spy_dp    = round(spy_q.get("dp", 0), 2)

        vix_q = fh("/quote?symbol=VIX")
        vix   = round(vix_q.get("c", 0), 1)

        # SPY candles לחישוב MA50/MA200
        to  = int(time.time())
        frm = to - 250 * 86400
        d = fh(f"/stock/candle?symbol=SPY&resolution=D&from={frm}&to={to}")
        closes = d.get("c", []) if d.get("s") == "ok" else []

        ma50  = round(sum(closes[-50:]) / 50, 2)  if len(closes) >= 50  else 0
        ma200 = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else 0

        above_ma50  = spy_price > ma50  if ma50  > 0 else True
        above_ma200 = spy_price > ma200 if ma200 > 0 else True

        # קבע Regime
        if above_ma50 and above_ma200 and spy_dp > -1:
            regime = "bull"
            label  = "שוק שורי"
            color  = "green"
        elif not above_ma50 and not above_ma200:
            regime = "bear"
            label  = "שוק דובי"
            color  = "red"
        elif not above_ma50 and above_ma200:
            regime = "choppy"
            label  = "תיקון / Choppy"
            color  = "amber"
        else:
            regime = "recovery"
            label  = "התאוששות"
            color  = "blue"

        # VIX level
        if vix > 30:
            vix_level = "פחד גבוה"
            vix_color = "red"
        elif vix > 20:
            vix_level = "זהירות"
            vix_color = "amber"
        else:
            vix_level = "רגוע"
            vix_color = "green"

        # long_penalty: כמה % להפחית מ-confidence של LONG signals
        long_penalty = 0
        if regime == "bear":   long_penalty = 25
        elif regime == "choppy": long_penalty = 12
        if vix > 30:           long_penalty += 10
        elif vix > 25:         long_penalty += 5

        return {
            "regime":       regime,
            "label":        label,
            "color":        color,
            "spyPrice":     round(spy_price, 2),
            "spyDp":        spy_dp,
            "ma50":         ma50,
            "ma200":        ma200,
            "aboveMa50":    above_ma50,
            "aboveMa200":   above_ma200,
            "vix":          vix,
            "vixLevel":     vix_level,
            "vixColor":     vix_color,
            "longPenalty":  long_penalty,
        }
    except:
        return {
            "regime": "unknown", "label": "לא זמין", "color": "text3",
            "spyPrice": 0, "spyDp": 0, "ma50": 0, "ma200": 0,
            "aboveMa50": True, "aboveMa200": True,
            "vix": 0, "vixLevel": "—", "vixColor": "text3",
            "longPenalty": 0,
        }


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
                image2_b64 = body.get("image2", None)
                media_type2 = body.get("mediaType2", "image/jpeg")
                trade_type  = body.get("tradeType", "day").lower().strip()

                if not image_b64:
                    self._json({"error": "חסרה תמונה"}, 400)
                    return

                # שלב 1 — אם אין טיקר, זהה קודם (מהיר)
                if not ticker:
                    ticker = identify_ticker_from_chart(image_b64, media_type)

                # שלב 2 — ניתוח מלא עם context + חדשות
                result = analyze_chart_image(image_b64, media_type, ticker, image2_b64, media_type2, trade_type)

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
            self._json({"key": FINNHUB_KEY})
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
                elif endpoint=="regime":     data = get_market_regime()
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
