#!/usr/bin/env python3
"""
DULITRADE - Python Backend Server
Finnhub = מחירים real-time
Yahoo Finance = פונדמנטלים + נרות + חדשות
"""
import json, re, time, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import urlopen, Request

PORT = int(os.environ.get("PORT", 8000))
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
FH  = "https://finnhub.io/api/v1"
YH  = "https://query1.finance.yahoo.com"
YH2 = "https://query2.finance.yahoo.com"

HEADERS_OUT = {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0"

def fetch(url, extra_headers={}):
    headers = {"User-Agent": UA, "Accept": "application/json", **extra_headers}
    for u in [url]:
        try:
            req = Request(u, headers=headers)
            with urlopen(req, timeout=9) as r:
                return json.loads(r.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            continue
    return {}

def fh_fetch(path):
    """Finnhub — real-time data"""
    if not FINNHUB_KEY:
        return {}
    sep = "&" if "?" in path else "?"
    return fetch(f"{FH}{path}{sep}token={FINNHUB_KEY}")

def yh_fetch(url):
    """Yahoo Finance — fundamentals, candles, news"""
    for base in [url, url.replace("query1","query2")]:
        try:
            req = Request(base, headers={
                "User-Agent": UA,
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://finance.yahoo.com/",
            })
            with urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode("utf-8", errors="ignore"))
        except Exception:
            continue
    return {}

# ── QUOTE — Finnhub real-time ────────────────────────────
def get_quote(symbol):
    if FINNHUB_KEY:
        d = fh_fetch(f"/quote?symbol={symbol}")
        if d.get("c", 0) > 0:
            return {
                "c":  d.get("c", 0),
                "pc": d.get("pc", 0),
                "h":  d.get("h", 0),
                "l":  d.get("l", 0),
                "o":  d.get("o", 0),
                "dp": d.get("dp", 0),  # % change
                "source": "finnhub"
            }
    # fallback Yahoo
    d = yh_fetch(f"{YH}/v8/finance/chart/{symbol}?interval=1d&range=2d")
    meta = (d.get("chart",{}).get("result") or [{}])[0].get("meta",{})
    price = meta.get("regularMarketPrice", 0)
    prev  = meta.get("chartPreviousClose", price)
    return {
        "c":  price, "pc": prev,
        "h":  meta.get("regularMarketDayHigh", price),
        "l":  meta.get("regularMarketDayLow", price),
        "o":  meta.get("regularMarketOpen", price),
        "dp": round((price-prev)/prev*100, 2) if prev else 0,
        "source": "yahoo"
    }

# ── CANDLES — Yahoo Finance ──────────────────────────────
def get_candles(symbol):
    to   = int(time.time())
    frm  = to - 90 * 86400
    d = yh_fetch(f"{YH}/v8/finance/chart/{symbol}?interval=1d&period1={frm}&period2={to}")
    res = (d.get("chart",{}).get("result") or [None])[0]
    if not res:
        return {"c":[],"o":[],"h":[],"l":[],"v":[],"t":[],"s":"no_data"}
    q  = (res.get("indicators",{}).get("quote") or [{}])[0]
    ts = res.get("timestamp", [])
    closes  = q.get("close",  [])
    opens   = q.get("open",   [])
    highs   = q.get("high",   [])
    lows    = q.get("low",    [])
    volumes = q.get("volume", [])
    idx = [i for i in range(len(ts))
           if i < len(closes) and closes[i] is not None
           and i < len(opens)  and opens[i]  is not None]
    return {
        "c": [round(closes[i], 2)  for i in idx],
        "o": [round(opens[i],  2)  for i in idx],
        "h": [round(highs[i],  2)  for i in idx],
        "l": [round(lows[i],   2)  for i in idx],
        "v": [volumes[i] or 0      for i in idx],
        "t": [ts[i]                for i in idx],
        "s": "ok"
    }

# ── PROFILE — Yahoo Finance ──────────────────────────────
def get_profile(symbol):
    d = yh_fetch(f"{YH}/v10/finance/quoteSummary/{symbol}"
                 f"?modules=assetProfile,price,defaultKeyStatistics,financialData,summaryDetail")
    res = (d.get("quoteSummary",{}).get("result") or [{}])[0]
    ap = res.get("assetProfile", {})
    pr = res.get("price", {})
    ks = res.get("defaultKeyStatistics", {})
    fd = res.get("financialData", {})
    sd = res.get("summaryDetail", {})
    def raw(obj, key):
        v = obj.get(key)
        return v.get("raw") if isinstance(v, dict) else v
    mc = raw(pr, "marketCap")
    return {
        "name":     pr.get("longName") or pr.get("shortName") or symbol,
        "sector":   ap.get("sector") or raw(pr,"sector") or "—",
        "industry": ap.get("industry") or "—",
        "marketCapitalization": mc/1e6 if mc else None,
        "pe":             raw(sd,"trailingPE") or raw(ks,"trailingPE"),
        "forwardPE":      raw(sd,"forwardPE")  or raw(ks,"forwardPE"),
        "pb":             raw(ks,"priceToBook"),
        "ps":             raw(ks,"priceToSalesTrailing12Months"),
        "revenueGrowth":  raw(fd,"revenueGrowth"),
        "earningsGrowth": raw(fd,"earningsGrowth"),
        "profitMargin":   raw(fd,"profitMargins"),
        "operatingMargin":raw(fd,"operatingMargins"),
        "roe":            raw(fd,"returnOnEquity"),
        "roa":            raw(fd,"returnOnAssets"),
        "debtToEquity":   raw(fd,"debtToEquity"),
        "currentRatio":   raw(fd,"currentRatio"),
        "beta":           raw(sd,"beta") or raw(ks,"beta"),
        "shortRatio":     raw(ks,"shortRatio"),
        "targetMeanPrice":     raw(fd,"targetMeanPrice"),
        "recommendationKey":   fd.get("recommendationKey"),
        "numberOfAnalysts":    raw(fd,"numberOfAnalystOpinions"),
        "dividendYield":       raw(sd,"dividendYield"),
        "fiftyTwoWeekHigh":    raw(sd,"fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow":     raw(sd,"fiftyTwoWeekLow"),
    }

# ── NEWS — Yahoo Finance ─────────────────────────────────
def get_news(symbol):
    try:
        d = yh_fetch(f"{YH}/v1/finance/search?q={symbol}&newsCount=15&quotesCount=0")
        items = d.get("news", [])
        return [{"headline": n.get("title",""), "url": n.get("link","#"), "source":"Yahoo Finance"}
                for n in items if n.get("title")]
    except:
        return []

# ── EXTERNAL NEWS — RSS ──────────────────────────────────
def get_extnews(symbol):
    sym = symbol.upper()
    sources = [
        ("CNBC",         "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
        ("MarketWatch",  "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
        ("Investing.com","https://www.investing.com/rss/news_301.rss"),
    ]
    results = []
    for name, url in sources:
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=6) as r:
                xml = r.read().decode("utf-8", errors="ignore")
            items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
            for item in items[:40]:
                tm = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", item) or re.search(r"<title>(.*?)</title>", item)
                lm = re.search(r"<link>(.*?)</link>", item) or re.search(r"<guid>(.*?)</guid>", item)
                dm = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>", item) or re.search(r"<description>(.*?)</description>", item)
                title = tm.group(1) if tm else ""
                link  = lm.group(1) if lm else "#"
                desc  = dm.group(1) if dm else ""
                if sym not in (title+desc).upper():
                    continue
                sentiment = ("positive" if re.search(r"beat|surge|rise|gain|strong|record|buy|upgrade|rally|soar|jump", title, re.I)
                             else "negative" if re.search(r"miss|drop|fall|loss|weak|sell|warn|cut|downgrade|crash|plunge", title, re.I)
                             else "neutral")
                results.append({"source":name, "title":title.replace("&amp;","&").strip(), "url":link.strip(), "sentiment":sentiment})
        except:
            continue
    return results[:8]

# ── COMPETITORS ──────────────────────────────────────────
COMP_MAP = {
    "AAPL":["MSFT","GOOGL","META"], "NVDA":["AMD","INTC","QCOM"],
    "TSLA":["F","GM","RIVN"],       "MSFT":["AAPL","GOOGL","AMZN"],
    "AMZN":["MSFT","GOOGL","WMT"],  "META":["SNAP","PINS","GOOGL"],
    "GOOGL":["META","MSFT","AMZN"], "AMD":["NVDA","INTC","QCOM"],
    "NFLX":["DIS","PARA","WBD"],    "TEVA":["MRK","PFE","AMGN"],
    "CHKP":["PANW","CRWD","FTNT"],  "MNDY":["CRM","NOW","WDAY"],
    "JPM":["BAC","WFC","GS"],        "V":["MA","AXP","PYPL"],
    "SOFI":["SQ","HOOD","AFRM"],    "COIN":["HOOD","IBKR","CME"],
    "PLTR":["AI","BB","SOUN"],
}

def get_competitors(symbol):
    peers = COMP_MAP.get(symbol.upper(), [])
    results = []
    for peer in peers[:3]:
        try:
            q = get_quote(peer)
            price, prev = q["c"], q["pc"]
            d2 = yh_fetch(f"{YH}/v10/finance/quoteSummary/{peer}"
                          f"?modules=price,summaryDetail,financialData,defaultKeyStatistics")
            res2 = (d2.get("quoteSummary",{}).get("result") or [{}])[0]
            pr2 = res2.get("price",{})
            sd2 = res2.get("summaryDetail",{})
            fd2 = res2.get("financialData",{})
            ks2 = res2.get("defaultKeyStatistics",{})
            def r2(obj, key):
                v = obj.get(key); return v.get("raw") if isinstance(v,dict) else v
            mc2 = r2(pr2,"marketCap")
            results.append({
                "symbol": peer,
                "name": pr2.get("shortName", peer),
                "price": round(price, 2),
                "changePct": round((price-prev)/prev*100,2) if prev else 0,
                "pe": r2(sd2,"trailingPE"),
                "pb": r2(ks2,"priceToBook"),
                "revenueGrowth": r2(fd2,"revenueGrowth"),
                "profitMargin":  r2(fd2,"profitMargins"),
                "beta": r2(sd2,"beta"),
                "marketCap": round(mc2/1e9,1) if mc2 else None,
            })
        except:
            continue
    return results

# ── MACRO — Finnhub real-time ────────────────────────────
def get_macro():
    tickers = {"^VIX":"VIX", "^TNX":"TNX", "^DXY":"DXY", "^GSPC":"GSPC"}
    results = {}
    for t, key in tickers.items():
        try:
            if FINNHUB_KEY:
                fh_sym = t.replace("^","")
                d = fh_fetch(f"/quote?symbol={fh_sym}")
                if d.get("c",0) > 0:
                    results[t] = {"price": d.get("c",0), "prev": d.get("pc",0)}
                    continue
            # fallback Yahoo
            d = yh_fetch(f"{YH}/v8/finance/chart/{t.replace('^','%5E')}?interval=1d&range=2d")
            meta = (d.get("chart",{}).get("result") or [{}])[0].get("meta",{})
            results[t] = {"price": meta.get("regularMarketPrice",0), "prev": meta.get("chartPreviousClose",0)}
        except:
            results[t] = {"price":0,"prev":0}
    return results

# ── INDICES ──────────────────────────────────────────────
def get_indices():
    """SPY, QQQ, DIA, GLD, TLT — real-time via Finnhub"""
    symbols = ["SPY","QQQ","DIA","GLD","TLT"]
    results = {}
    for sym in symbols:
        try:
            results[sym] = get_quote(sym)
        except:
            results[sym] = {"c":0,"pc":0,"h":0,"l":0}
    return results

# ── HTTP HANDLER ─────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_OPTIONS(self):
        self.send_response(200)
        for k,v in HEADERS_OUT.items(): self.send_header(k,v)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            try:
                with open("index.html","rb") as f: body=f.read()
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
                self._json({"error":"חסר סימבול"},400); return
            try:
                if   endpoint=="quote":       data=get_quote(symbol)
                elif endpoint=="candle":      data=get_candles(symbol)
                elif endpoint=="profile":     data=get_profile(symbol)
                elif endpoint=="news":        data=get_news(symbol)
                elif endpoint=="extnews":     data=get_extnews(symbol)
                elif endpoint=="competitors": data=get_competitors(symbol)
                elif endpoint=="macro":       data=get_macro()
                elif endpoint=="indices":     data=get_indices()
                else: data={"error":"endpoint לא תקין"}
            except Exception as e:
                data={"error":str(e)}
            self._json(data)
            return

        self.send_response(404); self.end_headers()

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        for k,v in HEADERS_OUT.items(): self.send_header(k,v)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    print(f"DULITRADE server on port {PORT} | Finnhub: {'✓' if FINNHUB_KEY else '✗ (Yahoo only)'}")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
