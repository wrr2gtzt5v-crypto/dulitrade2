#!/usr/bin/env python3
"""DULITRADE - Server with yfinance"""
import json, time, re, os, gzip
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from socketserver import ThreadingMixIn

PORT = int(os.environ.get("PORT", 10000))
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")

try:
    import yfinance as yf
    YF_OK = True
except ImportError:
    YF_OK = False

from urllib.request import urlopen, Request
from urllib.error import HTTPError

HEADERS_OUT = {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
}

def fh_fetch(path):
    if not FINNHUB_KEY: return {}
    try:
        url = f"https://finnhub.io/api/v1{path}&token={FINNHUB_KEY}" if "?" in path else f"https://finnhub.io/api/v1{path}?token={FINNHUB_KEY}"
        req = Request(url, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
        with urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except: return {}

def get_quote(symbol):
    # Finnhub real-time
    if FINNHUB_KEY:
        d = fh_fetch(f"/quote?symbol={symbol}")
        if d.get("c", 0) > 0:
            return {"c": round(d["c"],2), "pc": round(d.get("pc",d["c"]),2),
                    "h": round(d.get("h",d["c"]),2), "l": round(d.get("l",d["c"]),2),
                    "o": round(d.get("o",d["c"]),2), "dp": round(d.get("dp",0),2), "source":"finnhub"}
    # yfinance fallback
    if YF_OK:
        try:
            t = yf.Ticker(symbol)
            h = t.history(period="2d")
            if not h.empty:
                closes = h["Close"].tolist()
                price = round(closes[-1], 2)
                prev  = round(closes[-2], 2) if len(closes)>1 else price
                hi = round(h["High"].iloc[-1], 2)
                lo = round(h["Low"].iloc[-1], 2)
                op = round(h["Open"].iloc[-1], 2)
                return {"c":price,"pc":prev,"h":hi,"l":lo,"o":op,
                        "dp":round((price-prev)/prev*100,2) if prev else 0,"source":"yfinance"}
        except: pass
    return {"c":0,"pc":0,"h":0,"l":0,"o":0,"dp":0,"source":"error"}

def get_candles(symbol):
    if YF_OK:
        try:
            t = yf.Ticker(symbol)
            h = t.history(period="90d")
            if not h.empty:
                return {
                    "c": [round(x,2) for x in h["Close"].tolist()],
                    "o": [round(x,2) for x in h["Open"].tolist()],
                    "h": [round(x,2) for x in h["High"].tolist()],
                    "l": [round(x,2) for x in h["Low"].tolist()],
                    "v": [int(x) for x in h["Volume"].tolist()],
                    "t": [int(ts.timestamp()) for ts in h.index],
                    "s": "ok"
                }
        except: pass
    return {"c":[],"o":[],"h":[],"l":[],"v":[],"t":[],"s":"no_data"}

def get_profile(symbol):
    if YF_OK:
        try:
            info = yf.Ticker(symbol).info
            mc = info.get("marketCap")
            return {
                "name": info.get("longName") or info.get("shortName") or symbol,
                "sector": info.get("sector","—"), "industry": info.get("industry","—"),
                "marketCapitalization": mc/1e6 if mc else None,
                "pe": info.get("trailingPE"), "forwardPE": info.get("forwardPE"),
                "pb": info.get("priceToBook"), "ps": info.get("priceToSalesTrailing12Months"),
                "revenueGrowth": info.get("revenueGrowth"), "earningsGrowth": info.get("earningsGrowth"),
                "profitMargin": info.get("profitMargins"), "operatingMargin": info.get("operatingMargins"),
                "roe": info.get("returnOnEquity"), "roa": info.get("returnOnAssets"),
                "debtToEquity": info.get("debtToEquity"), "currentRatio": info.get("currentRatio"),
                "beta": info.get("beta"), "shortRatio": info.get("shortRatio"),
                "targetMeanPrice": info.get("targetMeanPrice"),
                "recommendationKey": info.get("recommendationKey"),
                "numberOfAnalysts": info.get("numberOfAnalystOpinions"),
                "dividendYield": info.get("dividendYield"),
                "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
                "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
            }
        except: pass
    return {"name": symbol, "sector":"—", "industry":"—"}

def get_news(symbol):
    if YF_OK:
        try:
            items = yf.Ticker(symbol).news or []
            return [{"headline": n.get("content",{}).get("title") or n.get("title",""),
                     "url": n.get("content",{}).get("canonicalUrl",{}).get("url") or n.get("link","#"),
                     "source":"Yahoo Finance"} for n in items[:15] if n.get("content",{}).get("title") or n.get("title")]
        except: pass
    return []

def get_extnews(symbol):
    sym = symbol.upper()
    sources = [
        ("CNBC","https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
        ("MarketWatch","https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
    ]
    results = []
    for name, url in sources:
        try:
            req = Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urlopen(req, timeout=6) as r: xml = r.read().decode("utf-8","ignore")
            for m in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)[:40]:
                tm = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>",m) or re.search(r"<title>(.*?)</title>",m)
                lm = re.search(r"<link>(.*?)</link>",m) or re.search(r"<guid>(.*?)</guid>",m)
                if not tm: continue
                title = tm.group(1)
                if sym not in title.upper(): continue
                link = lm.group(1).strip() if lm else "#"
                sent = "positive" if re.search(r"beat|surge|rise|gain|strong|record|buy|upgrade|rally|soar",title,re.I) \
                    else "negative" if re.search(r"miss|drop|fall|loss|weak|sell|warn|cut|downgrade|crash",title,re.I) else "neutral"
                results.append({"source":name,"title":title.replace("&amp;","&").strip(),"url":link,"sentiment":sent})
        except: continue
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
            mc = p.get("marketCapitalization")
            results.append({
                "symbol":peer,"name":p.get("name",peer),"price":q["c"],
                "changePct":q["dp"],"pe":p.get("pe"),"pb":p.get("pb"),
                "revenueGrowth":p.get("revenueGrowth"),"profitMargin":p.get("profitMargin"),
                "beta":p.get("beta"),"marketCap":round(mc/1000,1) if mc else None,
            })
        except: continue
    return results

def get_macro():
    tickers = {"^VIX":"^VIX","^TNX":"^TNX","^DXY":"DX-Y.NYB","^GSPC":"^GSPC"}
    results = {}
    for key, sym in tickers.items():
        try:
            if FINNHUB_KEY:
                fh_sym = key.replace("^","")
                d = fh_fetch(f"/quote?symbol={fh_sym}")
                if d.get("c",0)>0:
                    results[key]={"price":d["c"],"prev":d.get("pc",0)}
                    continue
            if YF_OK:
                h = yf.Ticker(sym).history(period="2d")
                if not h.empty:
                    c = h["Close"].tolist()
                    results[key]={"price":round(c[-1],2),"prev":round(c[-2],2) if len(c)>1 else round(c[-1],2)}
                    continue
        except: pass
        results[key]={"price":0,"prev":0}
    return results

def get_earnings(symbol):
    if YF_OK:
        try:
            t = yf.Ticker(symbol)
            cal = t.calendar
            hist = t.earnings_history
            surprise = None
            quarter = None
            if hist is not None and not hist.empty:
                last = hist.iloc[-1]
                actual = last.get("epsActual") or last.get("Reported EPS")
                estimate = last.get("epsEstimate") or last.get("Estimated EPS")
                if actual and estimate and estimate!=0:
                    surprise = round((actual-estimate)/abs(estimate)*100, 1)
                    quarter = str(last.name) if hasattr(last,'name') else "אחרון"
            return {"available": True, "surprise": surprise, "quarter": quarter}
        except: pass
    return {"available": False}

def get_insider(symbol):
    try:
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&dateRange=custom&startdt={time.strftime('%Y-%m-%d', time.gmtime(time.time()-90*86400))}&enddt={time.strftime('%Y-%m-%d')}&forms=4"
        req = Request(url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
        with urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        hits = d.get("hits",{}).get("hits",[])
        buys=0; sells=0; transactions=[]
        for h in hits[:20]:
            src = h.get("_source",{})
            trans = src.get("period_of_report","")
            name = src.get("display_names","")
            form = src.get("form_type","")
            if "4" not in form: continue
            # פשוט ספור
            buys += 1
        return {"available":True,"buys":buys,"sells":sells,"net":buys-sells,"bullish":buys>sells,"transactions":[]}
    except:
        return {"available":False}

def get_sector(sector_name):
    SECTOR_ETFS = {
        "Technology":"XLK","Healthcare":"XLV","Financials":"XLF","Energy":"XLE",
        "Consumer Cyclical":"XLY","Communication":"XLC","Industrials":"XLI",
        "Materials":"XLB","Real Estate":"XLRE","Utilities":"XLU","Consumer Defensive":"XLP",
    }
    etf = SECTOR_ETFS.get(sector_name)
    if not etf:
        for k,v in SECTOR_ETFS.items():
            if any(w in sector_name for w in k.split()):
                etf=v; break
    if not etf: return {"available":False}
    try:
        if YF_OK:
            etf_h = yf.Ticker(etf).history(period="1mo")
            spy_h = yf.Ticker("SPY").history(period="1mo")
            def chg(h):
                c=h["Close"].tolist()
                return round((c[-1]/c[0]-1)*100,2) if len(c)>1 else 0
            ec=chg(etf_h); sc=chg(spy_h); rel=round(ec-sc,2)
            return {"available":True,"sector":sector_name,"etf":etf,"etfChg1M":ec,"spyChg1M":sc,"relative":rel,"leading":rel>1,"lagging":rel<-1}
    except: pass
    return {"available":False}

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

        if parsed.path in ("/","/index.html"):
            try:
                with open("index.html","rb") as f: body=f.read()
                self.send_response(200)
                self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin","*")
                self.end_headers(); self.wfile.write(body)
            except:
                self.send_response(404); self.end_headers()
            return

        if parsed.path == "/api/stock":
            symbol = qs.get("symbol",[""])[0].upper().strip()
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
            self._json(data); return

        self.send_response(404); self.end_headers()

    def _json(self, data, code=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            for k,v in HEADERS_OUT.items(): self.send_header(k,v)
            self.send_header("Content-Length",len(body))
            self.end_headers(); self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

if __name__=="__main__":
    print(f"DULITRADE on port {PORT} | yfinance: {'✓' if YF_OK else '✗'} | Finnhub: {'✓' if FINNHUB_KEY else '✗'}", flush=True)
    ThreadedHTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
