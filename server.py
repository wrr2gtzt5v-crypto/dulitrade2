#!/usr/bin/env python3
"""
DULITRADE - Python Backend Server
Runs on Render.com
"""
import json
import re
import asyncio
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request
from urllib.error import URLError
import os

PORT = int(os.environ.get("PORT", 8000))
HEADERS_OUT = {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
}
YH = "https://query1.finance.yahoo.com"
YH2 = "https://query2.finance.yahoo.com"

def yh_fetch(url):
    """Fetch from Yahoo Finance with fallback"""
    for base in [url, url.replace("query1", "query2")]:
        try:
            req = Request(base, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            })
            with urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode())
        except Exception:
            continue
    return {}

def get_quote(symbol):
    d = yh_fetch(f"{YH}/v8/finance/chart/{symbol}?interval=1d&range=2d")
    meta = (d.get("chart", {}).get("result") or [{}])[0].get("meta", {})
    price = meta.get("regularMarketPrice", 0)
    prev  = meta.get("chartPreviousClose", price)
    return {
        "c": price, "pc": prev,
        "h": meta.get("regularMarketDayHigh", price),
        "l": meta.get("regularMarketDayLow", price),
        "o": meta.get("regularMarketOpen", price),
    }

def get_candles(symbol):
    to   = int(time.time())
    frm  = to - 90 * 86400
    d = yh_fetch(f"{YH}/v8/finance/chart/{symbol}?interval=1d&period1={frm}&period2={to}")
    res = (d.get("chart", {}).get("result") or [None])[0]
    if not res:
        return {"c": [], "o": [], "h": [], "l": [], "v": [], "t": []}
    q  = (res.get("indicators", {}).get("quote") or [{}])[0]
    ts = res.get("timestamp", [])
    closes  = q.get("close",  [])
    opens   = q.get("open",   [])
    highs   = q.get("high",   [])
    lows    = q.get("low",    [])
    volumes = q.get("volume", [])
    idx = [i for i in range(len(ts)) if closes[i] is not None and opens[i] is not None]
    return {
        "c": [round(closes[i],  2) for i in idx],
        "o": [round(opens[i],   2) for i in idx],
        "h": [round(highs[i],   2) for i in idx],
        "l": [round(lows[i],    2) for i in idx],
        "v": [volumes[i] or 0      for i in idx],
        "t": [ts[i]                for i in idx],
        "s": "ok"
    }

def get_profile(symbol):
    d = yh_fetch(f"{YH}/v10/finance/quoteSummary/{symbol}?modules=assetProfile,price,defaultKeyStatistics,financialData,summaryDetail")
    res = (d.get("quoteSummary", {}).get("result") or [{}])[0]
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
        "sector":   ap.get("sector") or raw(pr, "sector") or "—",
        "industry": ap.get("industry") or "—",
        "marketCapitalization": mc / 1e6 if mc else None,
        "pe":             raw(sd, "trailingPE") or raw(ks, "trailingPE"),
        "forwardPE":      raw(sd, "forwardPE")  or raw(ks, "forwardPE"),
        "pb":             raw(ks, "priceToBook"),
        "ps":             raw(ks, "priceToSalesTrailing12Months"),
        "revenueGrowth":  raw(fd, "revenueGrowth"),
        "earningsGrowth": raw(fd, "earningsGrowth"),
        "profitMargin":   raw(fd, "profitMargins"),
        "operatingMargin":raw(fd, "operatingMargins"),
        "roe":            raw(fd, "returnOnEquity"),
        "roa":            raw(fd, "returnOnAssets"),
        "debtToEquity":   raw(fd, "debtToEquity"),
        "currentRatio":   raw(fd, "currentRatio"),
        "beta":           raw(sd, "beta") or raw(ks, "beta"),
        "shortRatio":     raw(ks, "shortRatio"),
        "targetMeanPrice":     raw(fd, "targetMeanPrice"),
        "recommendationKey":   fd.get("recommendationKey"),
        "numberOfAnalysts":    raw(fd, "numberOfAnalystOpinions"),
        "dividendYield":       raw(sd, "dividendYield"),
        "fiftyTwoWeekHigh":    raw(sd, "fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow":     raw(sd, "fiftyTwoWeekLow"),
    }

def get_news(symbol):
    try:
        d = yh_fetch(f"{YH}/v1/finance/search?q={symbol}&newsCount=15&quotesCount=0")
        items = d.get("news", [])
        return [{"headline": n.get("title",""), "url": n.get("link","#"), "source": "Yahoo Finance"}
                for n in items if n.get("title")]
    except Exception:
        return []

def get_extnews(symbol):
    sym = symbol.upper()
    sources = [
        ("CNBC",          "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
        ("MarketWatch",   "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
        ("Investing.com", "https://www.investing.com/rss/news_301.rss"),
    ]
    results = []
    for name, url in sources:
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=6) as r:
                xml = r.read().decode("utf-8", errors="ignore")
            items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
            for item in items[:40]:
                title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", item) or re.search(r"<title>(.*?)</title>", item)
                link_m  = re.search(r"<link>(.*?)</link>", item) or re.search(r"<guid>(.*?)</guid>", item)
                desc_m  = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>", item) or re.search(r"<description>(.*?)</description>", item)
                title = title_m.group(1) if title_m else ""
                link  = link_m.group(1)  if link_m  else "#"
                desc  = desc_m.group(1)  if desc_m  else ""
                if sym not in (title + desc).upper():
                    continue
                sentiment = "positive" if re.search(r"beat|surge|rise|gain|strong|record|buy|upgrade|rally|soar|jump", title, re.I) \
                    else "negative" if re.search(r"miss|drop|fall|loss|weak|sell|warn|cut|downgrade|crash|plunge", title, re.I) \
                    else "neutral"
                results.append({
                    "source": name,
                    "title": title.replace("&amp;","&").strip(),
                    "url": link.strip(),
                    "sentiment": sentiment
                })
        except Exception:
            continue
    return results[:8]

COMP_MAP = {
    "AAPL":["MSFT","GOOGL","META"], "NVDA":["AMD","INTC","QCOM"], "TSLA":["F","GM","RIVN"],
    "MSFT":["AAPL","GOOGL","AMZN"], "AMZN":["MSFT","GOOGL","WMT"], "META":["SNAP","PINS","GOOGL"],
    "GOOGL":["META","MSFT","AMZN"], "AMD":["NVDA","INTC","QCOM"], "NFLX":["DIS","PARA","WBD"],
    "TEVA":["MRK","PFE","AMGN"],   "CHKP":["PANW","CRWD","FTNT"], "MNDY":["CRM","NOW","WDAY"],
    "JPM":["BAC","WFC","GS"],       "V":["MA","AXP","PYPL"],       "SOFI":["SQ","HOOD","AFRM"],
    "COIN":["HOOD","IBKR","CME"],   "PLTR":["AI","BB","SOUN"],
}

def get_competitors(symbol):
    peers = COMP_MAP.get(symbol.upper(), [])
    results = []
    for peer in peers[:3]:
        try:
            q = get_quote(peer)
            price, prev = q["c"], q["pc"]
            d2 = yh_fetch(f"{YH}/v10/finance/quoteSummary/{peer}?modules=price,summaryDetail,financialData,defaultKeyStatistics")
            res2 = (d2.get("quoteSummary", {}).get("result") or [{}])[0]
            pr2 = res2.get("price", {})
            sd2 = res2.get("summaryDetail", {})
            fd2 = res2.get("financialData", {})
            ks2 = res2.get("defaultKeyStatistics", {})
            def raw2(obj, key):
                v = obj.get(key); return v.get("raw") if isinstance(v, dict) else v
            mc2 = raw2(pr2, "marketCap")
            results.append({
                "symbol": peer,
                "name": pr2.get("shortName", peer),
                "price": round(price, 2),
                "changePct": round((price-prev)/prev*100, 2) if prev else 0,
                "pe": raw2(sd2,"trailingPE"),
                "pb": raw2(ks2,"priceToBook"),
                "revenueGrowth": raw2(fd2,"revenueGrowth"),
                "profitMargin":  raw2(fd2,"profitMargins"),
                "beta": raw2(sd2,"beta"),
                "marketCap": round(mc2/1e9,1) if mc2 else None,
            })
        except Exception:
            continue
    return results

def get_macro():
    tickers = ["^VIX","^TNX","^DXY","^GSPC"]
    results = {}
    for t in tickers:
        try:
            from urllib.parse import quote
            d = yh_fetch(f"{YH}/v8/finance/chart/{quote(t)}?interval=1d&range=2d")
            meta = (d.get("chart",{}).get("result") or [{}])[0].get("meta",{})
            results[t] = {"price": meta.get("regularMarketPrice",0), "prev": meta.get("chartPreviousClose",0)}
        except Exception:
            results[t] = {"price":0,"prev":0}
    return results


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_OPTIONS(self):
        self.send_response(200)
        for k,v in HEADERS_OUT.items():
            self.send_header(k,v)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)

        # Serve index.html
        if parsed.path in ("/", "/index.html"):
            try:
                with open("index.html","rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin","*")
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"index.html not found")
            return

        # API endpoint
        if parsed.path == "/api/stock":
            symbol   = qs.get("symbol",[""])[0].upper().strip()
            endpoint = qs.get("endpoint",[""])[0]

            if not symbol:
                self._json({"error":"חסר סימבול"}, 400); return

            try:
                if endpoint == "quote":       data = get_quote(symbol)
                elif endpoint == "candle":    data = get_candles(symbol)
                elif endpoint == "profile":   data = get_profile(symbol)
                elif endpoint == "news":      data = get_news(symbol)
                elif endpoint == "extnews":   data = get_extnews(symbol)
                elif endpoint == "competitors": data = get_competitors(symbol)
                elif endpoint == "macro":     data = get_macro()
                else: data = {"error":"endpoint לא תקין"}
            except Exception as e:
                data = {"error": str(e)}

            self._json(data)
            return

        self.send_response(404)
        self.end_headers()

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        for k,v in HEADERS_OUT.items():
            self.send_header(k,v)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"DULITRADE server running on port {PORT}")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
