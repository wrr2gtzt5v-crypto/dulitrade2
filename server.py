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
        with urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except: return {}

def get_quote(symbol):
    d = fh(f"/quote?symbol={symbol}")
    c = d.get("c", 0)
    if c > 0:
        return {
            "c":  round(c, 2),
            "pc": round(d.get("pc", c), 2),
            "h":  round(d.get("h", c), 2),
            "l":  round(d.get("l", c), 2),
            "o":  round(d.get("o", c), 2),
            "dp": round(d.get("dp", 0), 2),
        }
    return {"c":0,"pc":0,"h":0,"l":0,"o":0,"dp":0}

def pg(path):
    """Polygon.io API"""
    if not POLYGON_KEY: return {}
    try:
        sep = "&" if "?" in path else "?"
        url = f"{PG}{path}{sep}apiKey={POLYGON_KEY}"
        req = Request(url, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
        with urlopen(req, timeout=12) as r:
            return json.loads(r.read())
    except: return {}

def get_candles(symbol):
    """נרות יומיים — Polygon ראשי, Finnhub fallback"""
    import datetime

    # ── Polygon ──────────────────────────────────────────────
    if POLYGON_KEY:
        try:
            to_dt  = datetime.date.today().isoformat()
            frm_dt = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()
            url_path = f"/v2/aggs/ticker/{symbol}/range/1/day/{frm_dt}/{to_dt}?adjusted=true&sort=asc&limit=365"
            d = pg(url_path)
            results = d.get("results", [])
            if results and len(results) >= 5:
                c = [round(r["c"], 2) for r in results]
                o = [round(r["o"], 2) for r in results]
                h = [round(r["h"], 2) for r in results]
                l = [round(r["l"], 2) for r in results]
                v = [int(r.get("v", 0)) for r in results]
                t = [int(r["t"] // 1000) for r in results]  # ms → seconds
                return {"c":c,"o":o,"h":h,"l":l,"v":v,"t":t,"s":"ok","source":"polygon"}
        except: pass

    # ── Finnhub fallback ─────────────────────────────────────
    to  = int(time.time())
    frm = to - 365 * 86400
    d = fh(f"/stock/candle?symbol={symbol}&resolution=D&from={frm}&to={to}")
    if d.get("s") == "ok" and d.get("c") and len(d["c"]) >= 5:
        return {"c":[round(x,2) for x in d["c"]],"o":[round(x,2) for x in d["o"]],
                "h":[round(x,2) for x in d["h"]],"l":[round(x,2) for x in d["l"]],
                "v":[int(x) for x in d["v"]],"t":d["t"],"s":"ok","source":"finnhub"}

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
        "roe":  metric.get("roeTTM"),
        "roa":  metric.get("roaTTM"),
        "debtToEquity":  metric.get("totalDebt/totalEquityAnnual"),
        "currentRatio":  metric.get("currentRatioAnnual"),
        "beta": beta, "shortRatio": None,
        "targetMeanPrice": None, "recommendationKey": None, "numberOfAnalysts": None,
        "dividendYield":    metric.get("dividendYieldIndicatedAnnual"),
        "fiftyTwoWeekHigh": metric.get("52WeekHigh"),
        "fiftyTwoWeekLow":  metric.get("52WeekLow"),
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
        return {
            "available": True,
            "surprise": surprise,
            "quarter": last.get("period",""),
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

    def do_OPTIONS(self):
        self.send_response(200)
        for k,v in HEADERS_OUT.items(): self.send_header(k,v)
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
