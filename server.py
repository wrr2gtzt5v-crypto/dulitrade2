#!/usr/bin/env python3
"""DULITRADE - Finnhub + Stooq Server"""
import json, time, re, os, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request
from socketserver import ThreadingMixIn

PORT = int(os.environ.get("PORT", 10000))
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
FH = "https://finnhub.io/api/v1"

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
        return {"c":round(c,2),"pc":round(d.get("pc",c),2),
                "h":round(d.get("h",c),2),"l":round(d.get("l",c),2),
                "o":round(d.get("o",c),2),"dp":round(d.get("dp",0),2)}
    return {"c":0,"pc":0,"h":0,"l":0,"o":0,"dp":0}

def get_candles(symbol):
    # Stooq — CSV ללא API key
    try:
        url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
        req = Request(url, headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urlopen(req, timeout=12) as r:
            csv = r.read().decode("utf-8","ignore")
        lines = [l.strip() for l in csv.strip().split("\n") if l.strip() and not l.startswith("Date") and not l.startswith("No")]
        if len(lines) >= 5:
            lines = lines[-90:]
            c,o,h,l,v,t = [],[],[],[],[],[]
            for line in lines:
                p = line.split(",")
                if len(p) < 5: continue
                try:
                    dt = datetime.datetime.strptime(p[0].strip(), "%Y-%m-%d")
                    o.append(round(float(p[1]),2))
                    h.append(round(float(p[2]),2))
                    l.append(round(float(p[3]),2))
                    c.append(round(float(p[4]),2))
                    v.append(int(float(p[5])) if len(p)>5 and p[5].strip() else 0)
                    t.append(int(dt.timestamp()))
                except: continue
            if len(c) >= 5:
                print(f"[Stooq {symbol}] {len(c)} candles OK", flush=True)
                return {"c":c,"o":o,"h":h,"l":l,"v":v,"t":t,"s":"ok"}
    except Exception as e:
        print(f"[Stooq {symbol}] error: {e}", flush=True)

    # Fallback Finnhub
    to = int(time.time())
    frm = to - 365*86400
    d = fh(f"/stock/candle?symbol={symbol}&resolution=D&from={frm}&to={to}")
    if d.get("s")=="ok" and len(d.get("c",[]))>=5:
        return {"c":[round(x,2) for x in d["c"]],"o":[round(x,2) for x in d["o"]],
                "h":[round(x,2) for x in d["h"]],"l":[round(x,2) for x in d["l"]],
                "v":[int(x) for x in d["v"]],"t":d["t"],"s":"ok"}

    return {"c":[],"o":[],"h":[],"l":[],"v":[],"t":[],"s":"no_data"}

def get_profile(symbol):
    p = fh(f"/stock/profile2?symbol={symbol}")
    m = fh(f"/stock/metric?symbol={symbol}&metric=all")
    metric = m.get("metric", {})
    mc = p.get("marketCapitalization")
    return {
        "name": p.get("name", symbol),
        "sector": p.get("finnhubIndustry","—"),
        "industry": p.get("finnhubIndustry","—"),
        "marketCapitalization": mc,
        "pe": metric.get("peNormalizedAnnual") or metric.get("peTTM"),
        "forwardPE": None,
        "pb": metric.get("pbAnnual") or metric.get("pb"),
        "ps": None,
        "revenueGrowth": metric.get("revenueGrowthTTMYoy"),
        "earningsGrowth": metric.get("epsGrowthTTMYoy"),
        "profitMargin": metric.get("netProfitMarginTTM"),
        "operatingMargin": metric.get("operatingMarginTTM"),
        "roe": metric.get("roeTTM"),
        "roa": metric.get("roaTTM"),
        "debtToEquity": metric.get("totalDebt/totalEquityAnnual"),
        "currentRatio": metric.get("currentRatioAnnual"),
        "beta": metric.get("beta"),
        "shortRatio": None,
        "targetMeanPrice": None,
        "recommendationKey": None,
        "numberOfAnalysts": None,
        "dividendYield": metric.get("dividendYieldIndicatedAnnual"),
        "fiftyTwoWeekHigh": metric.get("52WeekHigh"),
        "fiftyTwoWeekLow": metric.get("52WeekLow"),
    }

def get_news(symbol):
    today = time.strftime("%Y-%m-%d")
    month_ago = time.strftime("%Y-%m-%d", time.gmtime(time.time()-30*86400))
    items = fh(f"/company-news?symbol={symbol}&from={month_ago}&to={today}")
    if not isinstance(items, list): return []
    return [{"headline":n.get("headline",""),"url":n.get("url","#"),"source":n.get("source","Finnhub")}
            for n in items[:15] if n.get("headline")]

def get_extnews(symbol):
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
            mc = p.get("marketCapitalization")
            results.append({"symbol":peer,"name":p.get("name",peer),"price":q["c"],
                "changePct":q["dp"],"pe":p.get("pe"),"pb":p.get("pb"),
                "revenueGrowth":p.get("revenueGrowth"),"profitMargin":p.get("profitMargin"),
                "beta":p.get("beta"),"marketCap":round(mc/1000,1) if mc else None})
        except: continue
    return results

def get_macro():
    mapping = {"^VIX":"VIX","^TNX":"TNX","^DXY":"DXY","^GSPC":"SPY"}
    results = {}
    for key, sym in mapping.items():
        try:
            d = fh(f"/quote?symbol={sym}")
            c = d.get("c",0)
            results[key] = {"price":round(c,2),"prev":round(d.get("pc",c),2)} if c>0 else {"price":0,"prev":0}
        except:
            results[key] = {"price":0,"prev":0}
    return results

def get_earnings(symbol):
    try:
        items = fh(f"/stock/earnings?symbol={symbol}&limit=4")
        if not isinstance(items,list) or not items: return {"available":False}
        last = items[0]
        actual, estimate = last.get("actual"), last.get("estimate")
        surprise = round((actual-estimate)/abs(estimate)*100,1) if actual is not None and estimate and estimate!=0 else None
        return {"available":True,"surprise":surprise,"quarter":last.get("period","")}
    except: return {"available":False}

def get_insider(symbol):
    try:
        items = fh(f"/stock/insider-transactions?symbol={symbol}")
        txs = items.get("data",[]) if isinstance(items,dict) else []
        buys  = sum(1 for t in txs if t.get("transactionType","") in ("P - Purchase","Buy"))
        sells = sum(1 for t in txs if t.get("transactionType","") in ("S - Sale","Sell"))
        return {"available":True,"buys":buys,"sells":sells,"net":buys-sells,
                "transactions":[{"name":t.get("name",""),"date":t.get("transactionDate","")} for t in txs[:3]]}
    except: return {"available":False}

def get_indicators(symbol):
    return fh(f"/scan/technical-indicator?symbol={symbol}&resolution=D")

def get_sector(sector_name):
    SECTOR_ETFS = {"Technology":"XLK","Healthcare":"XLV","Financials":"XLF","Energy":"XLE",
        "Consumer Cyclical":"XLY","Communication Services":"XLC","Industrials":"XLI",
        "Materials":"XLB","Real Estate":"XLRE","Utilities":"XLU","Consumer Defensive":"XLP"}
    etf = SECTOR_ETFS.get(sector_name)
    if not etf:
        for k,v in SECTOR_ETFS.items():
            if any(w in sector_name for w in k.split()): etf=v; break
    if not etf: return {"available":False}
    try:
        qe = fh(f"/quote?symbol={etf}")
        qs = fh(f"/quote?symbol=SPY")
        ec = round(qe.get("dp",0),2)
        sc = round(qs.get("dp",0),2)
        rel = round(ec-sc,2)
        return {"available":True,"sector":sector_name,"etf":etf,"etfChg1M":ec,
                "spyChg1M":sc,"relative":rel,"leading":rel>0.5,"lagging":rel<-0.5}
    except: return {"available":False}

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
            except: self.send_response(404); self.end_headers()
            return

        if parsed.path == "/api/stock":
            symbol = qs.get("symbol",[""])[0].upper().strip()
            endpoint = qs.get("endpoint",[""])[0]
            if not symbol: self._json({"error":"חסר סימבול"},400); return
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
                elif endpoint=="indicators":  data=get_indicators(symbol)
                elif endpoint=="sector":
                    data=get_sector(qs.get("sector",[""])[0])
                else: data={"error":"endpoint לא תקין"}
            except Exception as e: data={"error":str(e)}
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
    print(f"DULITRADE | Stooq+Finnhub | port {PORT} | key: {'✓' if FINNHUB_KEY else '✗'}", flush=True)
    ThreadedHTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
