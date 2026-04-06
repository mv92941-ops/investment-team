"""
投資團隊儀表板後端伺服器
執行後開啟瀏覽器：http://localhost:5678
"""
import json, math
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import yfinance as yf
import webbrowser, threading, os

PORT = 5678

def safe(v):
    if v is None: return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f): return None
        return f
    except: return None

def get_stock_data(code):
    code = code.strip()
    sym  = f"{code}.TW" if code.isdigit() else code.upper()

    tk   = yf.Ticker(sym)
    info = tk.info or {}

    # 歷史價格（1年）
    hist = tk.history(period="1y", interval="1d", auto_adjust=True)
    closes = [safe(v) for v in hist["Close"].tolist()] if not hist.empty else []
    highs  = [safe(v) for v in hist["High"].tolist()]  if not hist.empty else []
    lows   = [safe(v) for v in hist["Low"].tolist()]   if not hist.empty else []
    vols   = [safe(v) for v in hist["Volume"].tolist()]if not hist.empty else []

    # 財報
    try:
        inc = tk.financials          # 損益表（年度），columns = 日期
        inc_rows = []
        for col in list(inc.columns)[:3]:
            yr  = col.year if hasattr(col,'year') else 0
            rev = safe(inc.loc["Total Revenue", col])   if "Total Revenue"   in inc.index else None
            gp  = safe(inc.loc["Gross Profit", col])    if "Gross Profit"    in inc.index else None
            ni  = safe(inc.loc["Net Income", col])      if "Net Income"      in inc.index else None
            inc_rows.append({"yr": yr, "rev": rev, "gp": gp, "ni": ni})
    except: inc_rows = []

    try:
        cf   = tk.cashflow
        opcf = safe(cf.loc["Operating Cash Flow", cf.columns[0]]) if not cf.empty and "Operating Cash Flow" in cf.index else None
        capx = safe(cf.loc["Capital Expenditure",  cf.columns[0]]) if not cf.empty and "Capital Expenditure"  in cf.index else None
        fcf  = (opcf + capx) if opcf is not None and capx is not None else opcf
    except: opcf=capx=fcf=None

    try:
        bs    = tk.balance_sheet
        ar0   = safe(bs.loc["Accounts Receivable", bs.columns[0]]) if not bs.empty and "Accounts Receivable" in bs.index else None
        ar1   = safe(bs.loc["Accounts Receivable", bs.columns[1]]) if not bs.empty and len(bs.columns)>1 and "Accounts Receivable" in bs.index else None
        inv0  = safe(bs.loc["Inventory", bs.columns[0]]) if not bs.empty and "Inventory" in bs.index else None
        inv1  = safe(bs.loc["Inventory", bs.columns[1]]) if not bs.empty and len(bs.columns)>1 and "Inventory" in bs.index else None
    except: ar0=ar1=inv0=inv1=None

    isTW = sym.endswith(".TW")

    data = {
        "sym": sym, "code": code, "isTW": isTW,
        "name":        info.get("longName") or info.get("shortName") or code,
        "sector":      info.get("sector",""),
        "industry":    info.get("industry",""),
        "cur":         safe(info.get("currentPrice")) or safe(info.get("regularMarketPrice")),
        "prev":        safe(info.get("previousClose")) or safe(info.get("regularMarketPreviousClose")),
        "high52":      safe(info.get("fiftyTwoWeekHigh")),
        "low52":       safe(info.get("fiftyTwoWeekLow")),
        "mktcap":      safe(info.get("marketCap")),
        "beta":        safe(info.get("beta")),
        "volume":      safe(info.get("volume")) or safe(info.get("regularMarketVolume")),
        "avgVol20":    safe(info.get("averageVolume")),
        # Valuation
        "pe":          safe(info.get("trailingPE")),
        "fpe":         safe(info.get("forwardPE")),
        "pb":          safe(info.get("priceToBook")),
        "ps":          safe(info.get("priceToSalesTrailing12Months")),
        "evEb":        safe(info.get("enterpriseToEbitda")),
        "divY":        safe(info.get("dividendYield")) or safe(info.get("trailingAnnualDividendYield")),
        "tgtPrice":    safe(info.get("targetMeanPrice")),
        # Fundamentals
        "revGrow":     safe(info.get("revenueGrowth")),
        "earGrow":     safe(info.get("earningsGrowth")),
        "grossMarg":   safe(info.get("grossMargins")),
        "profitMarg":  safe(info.get("profitMargins")),
        "roe":         safe(info.get("returnOnEquity")),
        "de":          safe(info.get("debtToEquity")),
        "cr":          safe(info.get("currentRatio")),
        "totalCash":   safe(info.get("totalCash")),
        "totalDebt":   safe(info.get("totalDebt")),
        "freeCF":      fcf,
        # AR / Inventory
        "ar0": ar0, "ar1": ar1, "inv0": inv0, "inv1": inv1,
        # 財報
        "incRows": inc_rows,
        # Price history
        "closes": closes, "highs": highs, "lows": lows, "vols": vols,
    }
    return data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass  # 靜音

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path == "/" or path == "/index.html":
            self.serve_file("stock_dashboard.html", "text/html; charset=utf-8")

        elif path == "/api/stock":
            qs   = parse_qs(parsed.query)
            code = qs.get("code", [""])[0].strip()
            if not code:
                self.send_json({"error": "請輸入股票代號"}, 400)
                return
            try:
                data = get_stock_data(code)
                self.send_json(data)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        else:
            self.send_response(404); self.end_headers()

    def serve_file(self, filename, ct):
        here = os.path.dirname(os.path.abspath(__file__))
        fp   = os.path.join(here, filename)
        try:
            with open(fp, "rb") as f: body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


def open_browser():
    import time; time.sleep(1)
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == "__main__":
    print("=" * 45)
    print("  📊 投資團隊儀表板伺服器")
    print(f"  http://localhost:{PORT}")
    print("  關閉此視窗即停止")
    print("=" * 45)
    threading.Thread(target=open_browser, daemon=True).start()
    HTTPServer(("", PORT), Handler).serve_forever()
