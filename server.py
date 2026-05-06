#!/usr/bin/env python3
"""
DULITRADE - Python Backend Server
Finnhub = מחירים real-time
Yahoo Finance = פונדמנטלים + נרות + חדשות
"""
import json, re, time, os, gzip
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request
from urllib.error import HTTPError

PORT = int(os.environ.get("PORT", 8000))
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
FH  = "https://finnhub.io/api/v1"
YH1 = "https://query1.finance.yahoo.com"
YH2 = "https://query2.finance.yahoo.com"

HEADERS_OUT = {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
}

# Headers שמחקים דפדפן אמיתי
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://finance.yahoo.com/",
    "Origin": "https://finance.yahoo.com",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Cache-Control": "no-cache",
}

def fetch_url(url, extra={}):
    headers = {**BROWSER_HEADERS, **extra}
    for attempt in range(3):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=10) as r:
                raw = r.read()
                # handle gzip
                if r.info().get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8", errors="ignore"))
        except HTTPError as e:
            if e.code == 429:
                time.sleep(1)
                continue
            break
        except Exception:
            break
    return {}

def yh_fetch(path):
    """Yahoo Finance with query1/query2 fallback"""
    for base in [YH1, YH2]:
        try:
            d = fetch_url(f"{base}{path}")
            if d:
                return d
        except:
            continue
    return {}

def fh_fetch(path):
    if not FINNHUB_KEY:
        return {}
    sep = "&" if "?" in path else "?"
    return fetch_url(f"{FH}{path}{sep}token={FINNHUB_KEY}", {
        "Accept": "application/json",
        "Referer": "https://finnhub.io/",
    })

# ── QUOTE — Finnhub real-time ────────────────────────────
def get_quote(symbol):
    if FINNHUB_KEY:
        d = fh_fetch(f"/quote?symbol={symbol}")
        if d.get("c", 0) > 0:
            return {
                "c":  round(d["c"], 2),
                "pc": round(d.get("pc", d["c"]), 2),
                "h":  round(d.get("h", d["c"]), 2),
                "l":  round(d.get("l", d["c"]), 2),
                "o":  round(d.get("o", d["c"]), 2),
                "dp": round(d.get("dp", 0), 2),
                "source": "finnhub"
            }
    # Fallback Yahoo
    d = yh_fetch(f"/v8/finance/chart/{symbol}?interval=1d&range=2d")
    meta = (d.get("chart",{}).get("result") or [{}])[0].get("meta",{})
    price = meta.get("regularMarketPrice", 0)
    prev  = meta.get("chartPreviousClose", price) or price
    return {
        "c":  round(price, 2), "pc": round(prev, 2),
        "h":  round(meta.get("regularMarketDayHigh", price), 2),
        "l":  round(meta.get("regularMarketDayLow",  price), 2),
        "o":  round(meta.get("regularMarketOpen",    price), 2),
        "dp": round((price-prev)/prev*100, 2) if prev else 0,
        "source": "yahoo"
    }

# ── CANDLES ──────────────────────────────────────────────
def get_candles(symbol):
    to   = int(time.time())
    frm  = to - 90 * 86400
    d = yh_fetch(f"/v8/finance/chart/{symbol}?interval=1d&period1={frm}&period2={to}")
    res = (d.get("chart",{}).get("result") or [None])[0]
    if not res:
        return {"c":[],"o":[],"h":[],"l":[],"v":[],"t":[],"s":"no_data"}
    q  = (res.get("indicators",{}).get("quote") or [{}])[0]
    ts = res.get("timestamp", [])
    closes  = q.get("close",  []) or []
    opens   = q.get("open",   []) or []
    highs   = q.get("high",   []) or []
    lows    = q.get("low",    []) or []
    volumes = q.get("volume", []) or []
    idx = [i for i in range(len(ts))
           if i < len(closes) and closes[i] is not None
           and i < len(opens)  and opens[i]  is not None]
    return {
        "c": [round(closes[i],  2) for i in idx],
        "o": [round(opens[i],   2) for i in idx],
        "h": [round(highs[i],   2) for i in idx if i<len(highs)],
        "l": [round(lows[i],    2) for i in idx if i<len(lows)],
        "v": [volumes[i] or 0      for i in idx if i<len(volumes)],
        "t": [ts[i]                for i in idx],
        "s": "ok"
    }

# ── PROFILE — פונדמנטלים ─────────────────────────────────
def get_profile(symbol):
    def raw(obj, key):
        v = obj.get(key)
        return v.get("raw") if isinstance(v, dict) else v

    # נסה quoteSummary מלא
    d = yh_fetch(f"/v10/finance/quoteSummary/{symbol}?modules=assetProfile,price,defaultKeyStatistics,financialData,summaryDetail")
    res = (d.get("quoteSummary",{}).get("result") or [None])[0]

    if not res:
        # Fallback: נסה v7/finance/quote שפחות מוגן
        d2 = yh_fetch(f"/v7/finance/quote?symbols={symbol}&fields=longName,shortName,regularMarketPrice,trailingPE,forwardPE,priceToBook,marketCap,52WeekHigh,52WeekLow,beta")
        r2 = (d2.get("quoteResponse",{}).get("result") or [{}])[0]
        if r2:
            return {
                "name":     r2.get("longName") or r2.get("shortName") or symbol,
                "sector":   r2.get("sector","—"),
                "industry": r2.get("industry","—"),
                "marketCapitalization": r2.get("marketCap"),
                "pe":        r2.get("trailingPE"),
                "forwardPE": r2.get("forwardPE"),
                "pb":        r2.get("priceToBook"),
                "ps": None, "revenueGrowth": None, "earningsGrowth": None,
                "profitMargin": None, "operatingMargin": None,
                "roe": None, "roa": None, "debtToEquity": None,
                "currentRatio": None, "beta": r2.get("beta"),
                "shortRatio": None, "targetMeanPrice": r2.get("targetMeanPrice"),
                "recommendationKey": r2.get("recommendationKey"),
                "numberOfAnalysts": r2.get("numberOfAnalystOpinions"),
                "dividendYield": r2.get("dividendYield"),
                "fiftyTwoWeekHigh": r2.get("fiftyTwoWeekHigh"),
                "fiftyTwoWeekLow":  r2.get("fiftyTwoWeekLow"),
                "_partial": True
            }
        return {"name": symbol, "sector":"—", "industry":"—", "_missing": True}

    ap = res.get("assetProfile", {})
    pr = res.get("price", {})
    ks = res.get("defaultKeyStatistics", {})
    fd = res.get("financialData", {})
    sd = res.get("summaryDetail", {})
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

# ── NEWS ─────────────────────────────────────────────────
def get_news(symbol):
    try:
        d = yh_fetch(f"/v1/finance/search?q={symbol}&newsCount=15&quotesCount=0")
        return [{"headline":n.get("title",""),"url":n.get("link","#"),"source":"Yahoo Finance"}
                for n in d.get("news",[]) if n.get("title")]
    except:
        return []

# ── EARNINGS SURPRISE ─────────────────────────────────────
def get_earnings(symbol):
    try:
        d = yh_fetch(f"/v10/finance/quoteSummary/{symbol}?modules=earningsHistory,earningsTrend")
        res = (d.get("quoteSummary",{}).get("result") or [None])[0]
        if not res:
            return {"available": False}

        def raw(obj,key):
            v=obj.get(key); return v.get("raw") if isinstance(v,dict) else v

        history = res.get("earningsHistory",{}).get("history",[])
        surprises = []
        for h in history[-4:]:  # 4 רבעונים אחרונים
            actual   = raw(h,"epsActual")
            estimate = raw(h,"epsEstimate")
            surprise = raw(h,"surprisePercent")
            period   = h.get("period","")
            if actual is not None and estimate is not None:
                surprises.append({
                    "period":   period,
                    "actual":   round(actual,2),
                    "estimate": round(estimate,2),
                    "surprise": round(surprise*100,1) if surprise else None,
                    "beat":     actual > estimate
                })

        # מגמת EPS
        trend = res.get("earningsTrend",{}).get("trend",[])
        nextEps = None
        for t in trend:
            if t.get("period") == "0q":
                nextEps = raw(t.get("earningsEstimate",{}),"avg")
                break

        beats = sum(1 for s in surprises if s.get("beat"))
        lastSurprise = surprises[-1].get("surprise") if surprises else None

        return {
            "available": True,
            "surprises": surprises,
            "beats": beats,
            "total": len(surprises),
            "lastSurprisePct": lastSurprise,
            "nextEpsEstimate": round(nextEps,2) if nextEps else None,
            "consistent": beats >= 3  # הפתיע חיובי 3+ רבעונים
        }
    except Exception as e:
        return {"available": False, "error": str(e)}

# ── INSIDER BUYING — SEC EDGAR ────────────────────────────
def get_insider(symbol):
    try:
        # חפש CIK של החברה ב-EDGAR
        search_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&dateRange=custom&startdt=2024-01-01&forms=4"
        req = Request(search_url, headers={"User-Agent":"dulitrade@example.com","Accept":"application/json"})
        with urlopen(req, timeout=8) as r:
            data = json.loads(r.read())

        hits = data.get("hits",{}).get("hits",[])[:10]
        buys, sells = 0, 0
        transactions = []

        for hit in hits:
            src = hit.get("_source",{})
            trans_type = src.get("transaction_type","")
            shares = src.get("shares","0")
            name   = src.get("entity_name","")
            date   = src.get("period_of_report","")

            try: shares_n = float(str(shares).replace(",",""))
            except: shares_n = 0

            if "P" in str(trans_type):  # Purchase
                buys += 1
                transactions.append({"type":"קנייה","name":name,"date":date,"shares":shares_n})
            elif "S" in str(trans_type):  # Sale
                sells += 1
                transactions.append({"type":"מכירה","name":name,"date":date,"shares":shares_n})

        return {
            "available": True,
            "buys": buys,
            "sells": sells,
            "net": buys - sells,
            "bullish": buys > sells,
            "transactions": transactions[:5]
        }
    except:
        return {"available": False}

# ── SECTOR ROTATION ───────────────────────────────────────
SECTOR_ETFS = {
    "Technology":    "XLK",
    "Healthcare":    "XLV",
    "Financials":    "XLF",
    "Energy":        "XLE",
    "Consumer Cyclical": "XLY",
    "Communication": "XLC",
    "Industrials":   "XLI",
    "Materials":     "XLB",
    "Real Estate":   "XLRE",
    "Utilities":     "XLU",
    "Consumer Defensive": "XLP",
}

def get_sector(sector_name):
    try:
        etf = SECTOR_ETFS.get(sector_name)
        if not etf:
            # נסה לזהות לפי שם חלקי
            for k,v in SECTOR_ETFS.items():
                if any(w in sector_name for w in k.split()):
                    etf = v
                    break
        if not etf:
            return {"available": False}

        # מחיר ETF + S&P 500 ל-4 שבועות
        spyQ = get_quote("SPY")
        etfQ = get_quote(etf)

        # candles ל-20 ימי מסחר (חודש)
        to  = int(time.time())
        frm = to - 30*86400
        etfC = yh_fetch(f"/v8/finance/chart/{etf}?interval=1d&period1={frm}&period2={to}")
        spyC = yh_fetch(f"/v8/finance/chart/SPY?interval=1d&period1={frm}&period2={to}")

        def monthly_chg(d):
            c = (d.get("chart",{}).get("result") or [{}])[0]
            q = (c.get("indicators",{}).get("quote") or [{}])[0]
            closes = [x for x in (q.get("close",[]) or []) if x]
            if len(closes) < 2: return 0
            return round((closes[-1]/closes[0]-1)*100,2)

        etf_chg  = monthly_chg(etfC)
        spy_chg  = monthly_chg(spyC)
        relative = round(etf_chg - spy_chg, 2)

        return {
            "available":  True,
            "sector":     sector_name,
            "etf":        etf,
            "etfChg1M":   etf_chg,
            "spyChg1M":   spy_chg,
            "relative":   relative,
            "leading":    relative > 1.0,   # מוביל את השוק
            "lagging":    relative < -1.0,  # פיגור אחרי השוק
        }
    except Exception as e:
        return {"available": False}

# ── EXTERNAL NEWS ─────────────────────────────────────────
def get_extnews(symbol):
    sym = symbol.upper()
    sources = [
        ("CNBC","https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
        ("MarketWatch","https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
        ("Investing.com","https://www.investing.com/rss/news_301.rss"),
    ]
    results = []
    for name, url in sources:
        try:
            req = Request(url, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]})
            with urlopen(req, timeout=6) as r:
                xml = r.read().decode("utf-8", errors="ignore")
            for m in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)[:40]:
                tm = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>",m) or re.search(r"<title>(.*?)</title>",m)
                lm = re.search(r"<link>(.*?)</link>",m) or re.search(r"<guid>(.*?)</guid>",m)
                if not tm: continue
                title = tm.group(1)
                if sym not in title.upper(): continue
                link = lm.group(1).strip() if lm else "#"
                sent = ("positive" if re.search(r"beat|surge|rise|gain|strong|record|buy|upgrade|rally|soar|jump",title,re.I)
                        else "negative" if re.search(r"miss|drop|fall|loss|weak|sell|warn|cut|downgrade|crash|plunge",title,re.I)
                        else "neutral")
                results.append({"source":name,"title":title.replace("&amp;","&").strip(),"url":link,"sentiment":sent})
        except:
            continue
    return results[:8]

# ── COMPETITORS ───────────────────────────────────────────
COMP_MAP = {
    "AAPL":["MSFT","GOOGL","META"],"NVDA":["AMD","INTC","QCOM"],
    "TSLA":["F","GM","RIVN"],"MSFT":["AAPL","GOOGL","AMZN"],
    "AMZN":["MSFT","GOOGL","WMT"],"META":["SNAP","PINS","GOOGL"],
    "GOOGL":["META","MSFT","AMZN"],"AMD":["NVDA","INTC","QCOM"],
    "NFLX":["DIS","PARA","WBD"],"TEVA":["MRK","PFE","AMGN"],
    "CHKP":["PANW","CRWD","FTNT"],"MNDY":["CRM","NOW","WDAY"],
    "JPM":["BAC","WFC","GS"],"V":["MA","AXP","PYPL"],
    "SOFI":["SQ","HOOD","AFRM"],"COIN":["HOOD","IBKR","CME"],
    "PLTR":["AI","BB","SOUN"],
}

def get_competitors(symbol):
    peers = COMP_MAP.get(symbol.upper(), [])
    results = []
    for peer in peers[:3]:
        try:
            q = get_quote(peer)
            price, prev = q["c"], q["pc"]
            d2 = yh_fetch(f"/v10/finance/quoteSummary/{peer}?modules=price,summaryDetail,financialData,defaultKeyStatistics")
            res2 = (d2.get("quoteSummary",{}).get("result") or [{}])[0]
            def raw2(obj,key):
                v=obj.get(key); return v.get("raw") if isinstance(v,dict) else v
            pr2=res2.get("price",{}); sd2=res2.get("summaryDetail",{})
            fd2=res2.get("financialData",{}); ks2=res2.get("defaultKeyStatistics",{})
            mc2=raw2(pr2,"marketCap")
            results.append({
                "symbol":peer,"name":pr2.get("shortName",peer),
                "price":round(price,2),
                "changePct":round((price-prev)/prev*100,2) if prev else 0,
                "pe":raw2(sd2,"trailingPE"),"pb":raw2(ks2,"priceToBook"),
                "revenueGrowth":raw2(fd2,"revenueGrowth"),
                "profitMargin":raw2(fd2,"profitMargins"),
                "beta":raw2(sd2,"beta"),
                "marketCap":round(mc2/1e9,1) if mc2 else None,
            })
        except:
            continue
    return results

# ── MACRO ─────────────────────────────────────────────────
def get_macro():
    tickers = ["^VIX","^TNX","^DXY","^GSPC"]
    results = {}
    for t in tickers:
        try:
            if FINNHUB_KEY:
                fh_sym = t.replace("^","")
                d = fh_fetch(f"/quote?symbol={fh_sym}")
                if d.get("c",0) > 0:
                    results[t] = {"price":d["c"],"prev":d.get("pc",0)}
                    continue
            from urllib.parse import quote as urlquote
            d = yh_fetch(f"/v8/finance/chart/{urlquote(t)}?interval=1d&range=2d")
            meta = (d.get("chart",{}).get("result") or [{}])[0].get("meta",{})
            results[t] = {"price":meta.get("regularMarketPrice",0),"prev":meta.get("chartPreviousClose",0)}
        except:
            results[t] = {"price":0,"prev":0}
    return results

# ── HTTP HANDLER ──────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_OPTIONS(self):
        self.send_response(200)
        for k,v in HEADERS_OUT.items(): self.send_header(k,v)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path in ("/","/index.html"):
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
                elif endpoint=="earnings":    data=get_earnings(symbol)
                elif endpoint=="insider":     data=get_insider(symbol)
                elif endpoint=="sector":
                    sector = qs.get("sector",[""])[0]
                    data=get_sector(sector)
                else: data={"error":"endpoint לא תקין"}
            except Exception as e:
                data={"error":str(e)}
            self._json(data)
            return

        self.send_response(404); self.end_headers()

    def _json(self, data, code=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            for k,v in HEADERS_OUT.items(): self.send_header(k,v)
            self.send_header("Content-Length",len(body))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def handle_error(self, request, client_address):
        pass  # מדכא שגיאות BrokenPipe מה-log

from socketserver import ThreadingMixIn
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

if __name__=="__main__":
    import signal, sys
    def shutdown(sig, frame):
        print("Shutting down..."); sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"DULITRADE on port {PORT} | Finnhub: {'✓' if FINNHUB_KEY else '✗'}")
    server.serve_forever()
