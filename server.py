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


def analyze_chart_image(image_base64, media_type="image/jpeg"):
    """ניתוח גרף מתמונה עם Claude Vision"""
    anthropic_key = os.environ.get("ANTHROPIC_KEY", "")
    if not anthropic_key:
        return {"error": "ANTHROPIC_KEY לא מוגדר"}
    try:
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": "claude-opus-4-5",
            "max_tokens": 1024,
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
                        "text": """אתה מנתח טכני מקצועי ברמה של Hedge Fund, מתמחה ב-Day Trading וב-Swing Trading.

═══════════════════════════════════
שלב 1 — זיהוי מידע בסיסי מהגרף
═══════════════════════════════════
1. זהה את שם/טיקר המניה (מוצג בדרך כלל בפינה שמאל עליון ב-TradingView — למשל AAPL, NVDA, SPY)
2. זהה את ה-Timeframe (1m/2m/3m/5m/10m/15m/30m/1h/2h/4h/D/W)
3. זהה את המחיר הנוכחי (המחיר האחרון הנראה בגרף)
4. קבע סוג מסחר: DAY_TRADE (דקות) או SWING_TRADE (שעה ומעלה)

═══════════════════════════════════
שלב 2 — ניתוח Day Trading (נרות דקות)
═══════════════════════════════════
בדוק כל אחד מהגורמים הבאים וציין אם הוא BULLISH/BEARISH/NEUTRAL:

VWAP (משקל גבוה):
- מחיר מעל VWAP = bullish
- מחיר מתחת VWAP = bearish
- VWAP Reclaim (ירד מתחת וחזר מעל) = כניסה חזקה

Opening Range (משקל גבוה):
- פריצה מעל OR High = bullish breakout
- שבירה מתחת OR Low = bearish breakdown
- מחיר בתוך OR = דשדוש

אסטרטגיות Day Trade:
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

נפח (חשוב מאוד):
- נפח גבוה + תנועה = אמיתי
- נפח נמוך + תנועה = לא אמין

═══════════════════════════════════
שלב 3 — ניתוח Swing Trading (שעה ומעלה)
═══════════════════════════════════
- מגמה: MA50/MA200 אם מוצגים
- תמיכה/התנגדות: רמות ברורות בגרף
- דפוסי היפוך: Morning Star, Evening Star, Head & Shoulders, Double Top/Bottom
- Fibonacci: רמות 38.2%, 50%, 61.8%
- RSI/MACD: אם מוצגים
- Trend Pullback: נסיגה לממוצע נע → כניסה
- Breakout: פריצת התנגדות עם נפח

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
שלב 5 — SL/TP מדויקים
═══════════════════════════════════
Day Trade:
- SL: מתחת לשפל הנר האחרון / מתחת ל-VWAP / מתחת ל-OR Low
- TP: שיא קודם / רמת התנגדות / R/R 2:1 מינימום

Swing Trade:
- SL: מתחת לתמיכה / מתחת ל-MA50
- TP: התנגדות הבאה / Fibonacci extension

═══════════════════════════════════
ענה בפורמט JSON בלבד — ללא טקסט לפני או אחרי:
═══════════════════════════════════
{
  "ticker": "שם המניה שזוהה מהגרף (למשל AAPL) או null אם לא נראה",
  "trade_type": "DAY_TRADE" או "SWING_TRADE",
  "timeframe": "הזמן שזוהה (1m/5m/15m/1h/4h/Daily)",
  "current_price": המחיר הנוכחי הנראה בגרף,
  "signal": "LONG" או "SHORT" או "NEUTRAL",
  "confidence": מספר 1-10,
  "confidence_breakdown": {
    "pattern": 0-2,
    "vwap": 0-2,
    "volume": 0-2,
    "trend": 0-2,
    "rr": 0-2,
    "opening_range": 0-2,
    "ema_ma": 0-2,
    "no_resistance": 0-2,
    "total": 0-16
  },
  "entry": מחיר כניסה מדויק,
  "entry_timing": "תיאור מדויק מתי להיכנס (למשל: על פריצת $X / על סגירה מעל VWAP / על pullback ל-EMA9)",
  "tp": מחיר TP,
  "tp2": מחיר TP שני אם רלוונטי או null,
  "sl": מחיר SL,
  "tp_pct": אחוז רווח,
  "sl_pct": אחוז הפסד,
  "rr_ratio": יחס R/R,
  "strategy": "שם האסטרטגיה המדויקת",
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
        with urlopen(req, timeout=30) as r:
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
                if not image_b64:
                    self._json({"error": "חסרה תמונה"}, 400)
                    return
                result = analyze_chart_image(image_b64, media_type)
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
