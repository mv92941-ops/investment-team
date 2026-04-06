"""
每日排程 LINE 通知
  07:30 財經重點新聞
  08:20 台指期盤前提醒
  08:30 持股監控早報 + 鎖股掃描
  13:35 加權指數收盤報告
  14:00 台股選股報告
週一~週五，扣除台灣國定假日
資料來源：dashboard_data.json / watchlist_data.json
"""

import os
import json
import time
import requests
import holidays
import yfinance as yf
import pandas as pd
import feedparser
import warnings
import hashlib
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

# ── 台灣股票中文名稱快取 ─────────────────────────────────────────────
_TW_NAMES: dict = {}
_TW_NAMES_LOADED = False

def load_tw_names():
    global _TW_NAMES, _TW_NAMES_LOADED
    if _TW_NAMES_LOADED:
        return
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                         timeout=10, verify=False)
        if r.status_code == 200:
            for item in r.json():
                _TW_NAMES[item["Code"]] = item["Name"]
    except Exception:
        pass
    try:
        r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
                         timeout=10, verify=False)
        if r.status_code == 200:
            for item in r.json():
                code = item.get("SecuritiesCompanyCode", "")
                name = item.get("CompanyName", "")
                if code and name:
                    _TW_NAMES[code] = name
    except Exception:
        pass
    _TW_NAMES_LOADED = True

def get_tw_name(code: str, fallback: str) -> str:
    load_tw_names()
    if code in _TW_NAMES:
        return _TW_NAMES[code]
    try:
        r = requests.get(
            f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?stockNo={code}&response=json",
            timeout=6, verify=False)
        if r.status_code == 200:
            title = r.json().get("title", "")
            if code in title:
                part = title.split(code)[-1].split("各日")[0].strip()
                if part:
                    _TW_NAMES[code] = part
                    return part
    except Exception:
        pass
    return fallback

load_dotenv()
warnings.filterwarnings("ignore")

LINE_CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_TOKEN", "")
LINE_USER_IDS = [
    uid.strip()
    for uid in os.getenv("LINE_USER_IDS", os.getenv("LINE_USER_ID", "")).split(",")
    if uid.strip()
]

DATA_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_data.json")
WATCH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist_data.json")
TW_HOLIDAYS = holidays.TW()

# ── 排程時間 ─────────────────────────────────────────────────────────
NEWS_HOUR,    NEWS_MIN    = 7,  30   # 財經重點新聞
PREFUT_HOUR,  PREFUT_MIN  = 8,  20   # 台指期盤前提醒
SEND_HOUR,    SEND_MIN    = 8,  30   # 早報 + 鎖股掃描
CLOSE_HOUR,   CLOSE_MIN   = 13, 35   # 收盤報告
PICK_HOUR,    PICK_MIN    = 14, 0    # 台股選股報告


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in TW_HOLIDAYS


def send_line(msg: str):
    if not LINE_CHANNEL_TOKEN or not LINE_USER_IDS:
        print("未設定 LINE Token，改為印出：")
        print(msg)
        return
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
    }
    for uid in LINE_USER_IDS:
        payload = {"to": uid, "messages": [{"type": "text", "text": msg}]}
        try:
            r = requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers=headers, json=payload, timeout=10,
            )
            status = "OK" if r.status_code == 200 else f"FAIL {r.status_code}"
            print(f"LINE {status}")
        except Exception as e:
            print(f"LINE error: {e}")


# ════════════════════════════════════════════
# 07:30  財經重點新聞
# ════════════════════════════════════════════
def build_finance_news() -> str:
    """抓取台灣財經重點新聞"""
    now_str = datetime.now().strftime("%m/%d %H:%M")
    lines   = [f"⏰ 財經重點新聞  {now_str}"]

    queries = [
        ("台股 今日", "台股"),
        ("台指期 今日", "台指期"),
        ("美股 道瓊 那斯達克", "美股"),
        ("Fed 聯準會 利率", "Fed/利率"),
    ]
    NOISE = ["券商分點", "主力買賣", "當沖", "三大法人統計", "技術分析圖", "K線", "成交量排行"]
    seen   = set()
    cutoff = datetime.now() - timedelta(hours=18)

    for query, label in queries:
        url  = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        count = 0
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                if " - " in title:
                    title = title.rsplit(" - ", 1)[0].strip()
                if any(kw in title for kw in NOISE):
                    continue
                key = hashlib.md5(title[:20].encode()).hexdigest()
                if key in seen:
                    continue
                pub = entry.get("published_parsed")
                if pub and datetime(*pub[:6]) < cutoff:
                    continue
                seen.add(key)
                lines.append(f"・[{label}] {title}")
                count += 1
                if count >= 2:
                    break
        except Exception:
            pass

    if len(lines) == 1:
        lines.append("・今日暫無最新財經消息")

    return "\n".join(lines)


# ════════════════════════════════════════════
# 08:20  台指期盤前提醒
# ════════════════════════════════════════════
def build_prefutures_reminder() -> str:
    """台指期盤前重點提醒（夜盤收盤 + 開盤策略提示）"""
    now_str = datetime.now().strftime("%m/%d %H:%M")
    lines   = [f"⏰ 台指期盤前提醒  {now_str}"]

    # 嘗試抓夜盤收盤（用 ^TWII 日K 前一日收盤作參考）
    try:
        ticker = yf.Ticker("^TWII")
        hist   = ticker.history(period="5d", interval="1d")
        if not hist.empty:
            if isinstance(hist.columns, pd.MultiIndex):
                hist.columns = hist.columns.get_level_values(0)
            prev_close = float(hist["Close"].iloc[-1])
            lines.append(f"加權指數前收  {prev_close:,.0f} 點（參考）")
    except Exception:
        pass

    lines.append("")
    lines.append("📋 盤前確認清單：")
    lines.append("  □ 昨日持倉是否留倉？出場計畫確認")
    lines.append("  □ 今日操作方向（偏多/偏空/觀望）")
    lines.append("  □ 進場點位是否設好停損？")
    lines.append("  □ 風報比 ≥ 1:2 才進場")
    lines.append("  □ 連續獲利 >3 筆 → 維持 1 口，勿加碼")
    lines.append("")
    lines.append("🧠 策略王：開盤前確認 60分K 方向，不追漲殺跌")

    return "\n".join(lines)


# ════════════════════════════════════════════
# 14:00  台股選股報告
# ════════════════════════════════════════════
def build_stock_pick_report() -> str:
    """掃描鎖股區，產出今日選股報告"""
    now_str = datetime.now().strftime("%m/%d %H:%M")
    lines   = [f"⏰ 台股選股報告  {now_str}"]

    watches = load_watchlist()
    if not watches:
        lines.append("鎖股區目前無標的")
        return "\n".join(lines)

    ready   = []
    near    = []
    waiting = []

    for w in watches:
        code      = w["code"]
        entry     = w.get("entry", 0)
        w20ma     = w.get("w20ma", 0)
        condition = w.get("condition", "").strip()

        current, name = get_current_price(code)
        if current <= 0 or entry <= 0:
            waiting.append(f"  {code} {name}（價格取得失敗）")
            continue

        dist_pct = (entry - current) / entry * 100

        if current >= entry:
            ready.append((code, name, current, entry, w20ma, condition))
        elif dist_pct <= 5:
            near.append((code, name, current, entry, dist_pct, condition))
        else:
            waiting.append(f"  {code} {name}  現價 {current:.2f}  目標 {entry:.2f}（差 {dist_pct:.1f}%）")

    if ready:
        lines.append("\n✅ 今日符合進場條件：")
        for code, name, current, entry, w20ma, condition in ready:
            lines.append(f"  {code} {name}  現價 {current:.2f}  目標 {entry:.2f}")
            if w20ma > 0:
                above = "站上週20MA ✓" if current >= w20ma else f"週20MA {w20ma:.2f}（尚未站上）"
                lines.append(f"    {above}")
            if condition:
                lines.append(f"    進場條件：{condition}")

    if near:
        lines.append("\n🔔 接近進場價（5%以內），明日留意：")
        for code, name, current, entry, dist_pct, condition in near:
            lines.append(f"  {code} {name}  現價 {current:.2f}  目標 {entry:.2f}（差 {dist_pct:.1f}%）")
            if condition:
                lines.append(f"    條件：{condition}")

    if waiting:
        lines.append(f"\n⏳ 尚未到位（{len(waiting)} 檔）：")
        for w in waiting:
            lines.append(w)

    if not ready and not near:
        lines.append("\n今日無標的符合進場條件，繼續等待。")

    lines.append("\n🛡️ 風控師：進場前確認停損點位！")
    return "\n".join(lines)


# ════════════════════════════════════════════
# 持股監控早報
# ════════════════════════════════════════════
def get_current_price(code: str) -> tuple[float, str]:
    for suffix in [".TW", ".TWO", ""]:
        try:
            ticker = yf.Ticker(code + suffix)
            hist   = ticker.history(period="5d", interval="1d")
            if not hist.empty:
                if isinstance(hist.columns, pd.MultiIndex):
                    hist.columns = hist.columns.get_level_values(0)
                current = float(hist["Close"].iloc[-1])
                yf_name = (ticker.info or {}).get("shortName") or code
                name    = get_tw_name(code, yf_name)
                return current, name
        except Exception:
            pass
    return 0, code


def get_stock_data(code: str, entry: float, shares: float, w20ma: float, edate: str) -> dict:
    for suffix in [".TW", ".TWO", ""]:
        ticker = yf.Ticker(code + suffix)
        hist   = ticker.history(period="5d", interval="1d")
        if not hist.empty:
            break
    if hist.empty:
        return None

    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.get_level_values(0)

    current = float(hist["Close"].iloc[-1])
    volume  = float(hist["Volume"].iloc[-1])

    if w20ma == 0:
        whist = ticker.history(
            start=(datetime.now() - timedelta(days=700)).strftime("%Y-%m-%d"),
            interval="1wk"
        )
        if isinstance(whist.columns, pd.MultiIndex):
            whist.columns = whist.columns.get_level_values(0)
        if not whist.empty and len(whist) >= 20:
            w20ma = float(whist["Close"].rolling(20).mean().iloc[-1])

    pnl_pct   = (current - entry) / entry * 100 if entry > 0 else 0
    pnl_total = (current - entry) * shares * 1000 if entry > 0 and shares > 0 else 0
    dist_sl   = (current - w20ma) / current * 100 if w20ma > 0 else 0
    tp1       = entry * 1.20 if entry > 0 else 0
    tp1_hit   = current >= tp1 if tp1 > 0 else False

    hist_high    = 0
    trail_active = False
    trail_stop   = 0
    trail_hit    = False
    if edate:
        try:
            yhist = ticker.history(start=edate, interval="1d")
            if isinstance(yhist.columns, pd.MultiIndex):
                yhist.columns = yhist.columns.get_level_values(0)
            if not yhist.empty:
                hist_high    = float(yhist["High"].max())
                trail_active = hist_high >= tp1 > 0
                trail_stop   = hist_high * 0.85 if trail_active else 0
                trail_hit    = trail_active and current <= trail_stop
        except Exception:
            pass

    if (w20ma > 0 and current < w20ma) or trail_hit:
        status = "danger"
    elif w20ma > 0 and current <= w20ma + 2:
        status = "warn"
    else:
        status = "safe"

    info    = ticker.info
    yf_name = info.get("shortName") or code
    name    = get_tw_name(code, yf_name)

    return {
        "code": code, "name": name, "current": current, "volume": volume,
        "entry": entry, "shares": shares, "w20ma": w20ma,
        "pnl_pct": pnl_pct, "pnl_total": pnl_total,
        "dist_sl": dist_sl, "tp1": tp1, "tp1_hit": tp1_hit,
        "trail_active": trail_active, "trail_stop": trail_stop, "trail_hit": trail_hit,
        "hist_high": hist_high, "status": status,
    }


MAX_AGE_HOURS = 24

def fetch_stock_news(code: str, name: str, max_items: int = 2) -> list[str]:
    query = f"{name} {code} 股票"
    url   = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    NOISE = ["券商分點", "進出買賣", "主力買賣", "當沖", "三大法人統計",
             "技術分析圖", "K線", "成交量排行", "盤後資訊"]
    seen    = set()
    results = []
    cutoff  = datetime.now() - timedelta(hours=MAX_AGE_HOURS)
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            if " - " in title:
                title = title.rsplit(" - ", 1)[0].strip()
            if any(kw in title for kw in NOISE):
                continue
            key = hashlib.md5(title[:20].encode()).hexdigest()
            if key in seen:
                continue
            pub = entry.get("published_parsed")
            if pub and datetime(*pub[:6]) < cutoff:
                continue
            seen.add(key)
            results.append(title)
            if len(results) >= max_items:
                break
    except Exception:
        pass
    return results


def format_stock_block(d: dict) -> str:
    lines = []
    status_icon = {"danger": "【危險】", "warn": "【警戒】", "safe": "【正常】"}.get(d["status"], "")
    lines.append(f"{status_icon} {d['code']} {d['name']}")
    lines.append(f"現價 {d['current']:.2f}  損益 {d['pnl_pct']:+.1f}%（{d['pnl_total']:+,.0f}元）")

    if d["status"] == "danger":
        reason = "已觸發移動停利" if d["trail_hit"] else "已跌破週20MA"
        lines.append(f"停損線 {d['w20ma']:.2f}  {reason}  按規則出場！")
    elif d["status"] == "warn":
        lines.append(f"停損線 {d['w20ma']:.2f}  距停損僅 {d['dist_sl']:.1f}%  高度警戒")
    else:
        stop_line = f"停損線 {d['w20ma']:.2f}（距 {d['dist_sl']:.1f}%）"
        if d["trail_active"]:
            stop_line += f"  移動停利 {d['trail_stop']:.2f}"
        elif d["tp1"] > 0:
            tp_note = "已達！應出場50%" if d["tp1_hit"] else f"停利目標 {d['tp1']:.2f}"
            stop_line += f"  {tp_note}"
        lines.append(stop_line)

    news = fetch_stock_news(d["code"], d["name"])
    if news:
        for n in news:
            lines.append(f"・{n}")
    else:
        lines.append("・無特別消息")

    return "\n".join(lines)


def load_watchlist() -> list:
    if not os.path.exists(WATCH_FILE):
        return []
    with open(WATCH_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [w for w in data if w and w.get("code")]


def build_watchlist_section() -> str:
    watches = load_watchlist()
    if not watches:
        return ""

    ready   = []
    near    = []
    waiting = 0

    for w in watches:
        code      = w["code"]
        entry     = w.get("entry", 0)
        w20ma     = w.get("w20ma", 0)
        condition = w.get("condition", "").strip()
        current, name = get_current_price(code)
        if current <= 0 or entry <= 0:
            waiting += 1
            continue
        dist_pct = (entry - current) / entry * 100
        if current >= entry:
            ready.append((code, name, current, entry, w20ma, condition))
        elif dist_pct <= 5:
            near.append((code, name, current, entry, dist_pct, condition))
        else:
            waiting += 1

    if not ready and not near and waiting == 0:
        return ""

    lines = ["", "────── 鎖股區掃描 ──────"]
    if ready:
        lines.append("✅ 已達進場價，可考慮進場：")
        for code, name, current, entry, w20ma, condition in ready:
            line = f"  {code} {name}  現價 {current:.2f}  目標 {entry:.2f}"
            if w20ma > 0:
                line += f"  週20MA {w20ma:.2f}"
            if condition:
                line += f"\n  條件：{condition}"
            lines.append(line)
    if near:
        lines.append("🔔 距進場價 5% 以內，接近到位：")
        for code, name, current, entry, dist_pct, condition in near:
            line = f"  {code} {name}  現價 {current:.2f}  目標 {entry:.2f}（差 {dist_pct:.1f}%）"
            if condition:
                line += f"\n  條件：{condition}"
            lines.append(line)
    if waiting > 0:
        lines.append(f"⏳ 尚未到位：{waiting} 檔繼續等待")

    return "\n".join(lines)


def build_message(holdings: list) -> str:
    now_str  = datetime.now().strftime("%m/%d %H:%M")
    blocks   = [f"投資團隊早報  {now_str}"]
    all_data = []

    for h in holdings:
        d = get_stock_data(h["code"], h.get("entry", 0), h.get("shares", 0),
                           h.get("w20ma", 0), h.get("edate", ""))
        if d:
            all_data.append(d)

    all_data.sort(key=lambda x: {"danger": 0, "warn": 1, "safe": 2}[x["status"]])

    if not all_data:
        blocks.append("目前無持倉資料")
    else:
        for d in all_data:
            blocks.append("............")
            blocks.append(format_stock_block(d))

    blocks.append("............")
    blocks.append("停損掛單了嗎？")

    watch_section = build_watchlist_section()
    if watch_section:
        blocks.append(watch_section)

    return "\n".join(blocks)


# ════════════════════════════════════════════
# 13:35  收盤報告
# ════════════════════════════════════════════
def get_twii_today() -> dict | None:
    try:
        ticker = yf.Ticker("^TWII")
        hist   = ticker.history(period="5d", interval="1d")
        if hist.empty:
            return None
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        last_date = hist.index[-1]
        if hasattr(last_date, "date"):
            last_date = last_date.date()
        if last_date != date.today():
            print(f"加權指數資料非今日（{last_date}），略過收盤報告")
            return None
        row = hist.iloc[-1]
        return {
            "open":  float(row["Open"]),
            "high":  float(row["High"]),
            "low":   float(row["Low"]),
            "close": float(row["Close"]),
        }
    except Exception as e:
        print(f"取得加權指數資料失敗：{e}")
        return None


def build_closing_message() -> str | None:
    twii = get_twii_today()
    if twii is None:
        return None
    chg     = twii["close"] - twii["open"]
    chg_pct = chg / twii["open"] * 100 if twii["open"] else 0
    arrow   = "▲" if chg >= 0 else "▼"
    now_str = datetime.now().strftime("%m/%d %H:%M")
    return "\n".join([
        f"投資團隊收盤報告  {now_str}",
        f"加權指數  {twii['close']:,.0f} 點",
        f"{arrow} {abs(chg):,.0f} 點（{abs(chg_pct):.2f}%）",
        f"今日區間  {twii['low']:,.0f} ～ {twii['high']:,.0f}",
    ])


# ════════════════════════════════════════════
# 盤中偵測
# ════════════════════════════════════════════
INTRADAY_INTERVAL = 30
INTRADAY_START    = (9, 0)
INTRADAY_END      = (13, 30)
_prev_status: dict = {}
_prev_price:  dict = {}


def is_trading_hours() -> bool:
    now = datetime.now()
    if not is_trading_day(now.date()):
        return False
    t = (now.hour, now.minute)
    return INTRADAY_START <= t <= INTRADAY_END


def format_team_discussion(d: dict, event: str) -> str:
    now_str = datetime.now().strftime("%m/%d %H:%M")
    lines   = [f"【投資團隊緊急討論】{now_str}"]
    lines.append(f"{d['code']} {d['name']}")
    lines.append(f"現價 {d['current']:.2f}  損益 {d['pnl_pct']:+.1f}%（{d['pnl_total']:+,.0f}元）")
    lines.append("")
    if event == "danger_ma":
        lines.append(f"🛡️ 風控師：已跌破週20MA（{d['w20ma']:.2f}），停損觸發！按規則立即出場")
        lines.append(f"💰 財務長：現在出場損益 {d['pnl_pct']:+.1f}%，再拖損失更大")
        lines.append(f"🧠 策略王：趨勢轉弱，不要等「以為會回來」，執行計畫")
    elif event == "danger_trail":
        lines.append(f"🛡️ 風控師：從高點回落超過15%，移動停利觸發（停利線 {d['trail_stop']:.2f}）")
        lines.append(f"💰 財務長：獲利保護機制啟動，第二批應全數出場")
        lines.append(f"🧠 策略王：已完成波段，鎖定獲利，不要貪心")
    elif event == "warn":
        lines.append(f"🛡️ 風控師：進入黃色警戒區，距週20MA（{d['w20ma']:.2f}）不足2元")
        lines.append(f"💰 財務長：距停損僅 {d['dist_sl']:.1f}%，確認停損單是否已掛")
        lines.append(f"🧠 策略王：持續觀察，不要在警戒區加碼")
    elif event == "tp1":
        lines.append(f"💰 財務長：已達停利目標 {d['tp1']:.2f}（+20%），第一批50%應出場！")
        lines.append(f"🛡️ 風控師：停利單掛了嗎？不要手動等，讓系統幫你執行")
        lines.append(f"🧠 策略王：剩餘50%繼續持有，移動停利線設好")
    elif event == "big_drop":
        lines.append(f"🛡️ 風控師：今日單日跌幅超過3%，注意是否接近停損線")
        lines.append(f"💰 財務長：停損線 {d['w20ma']:.2f}，距離 {d['dist_sl']:.1f}%")
        lines.append(f"🧠 策略王：確認是市場整體下跌還是個股利空，再決定是否提早出場")
    elif event == "big_rise":
        lines.append(f"💰 財務長：今日單日漲幅超過3%，強勢表現")
        lines.append(f"🛡️ 風控師：停損線拉高到 {d['w20ma']:.2f}，結構安全")
        lines.append(f"🧠 策略王：注意停利目標 {d['tp1']:.2f}，到了不要捨不得出")
    news = fetch_stock_news(d["code"], d["name"], max_items=1)
    if news:
        lines.append(f"・{news[0]}")
    return "\n".join(lines)


def check_intraday(holdings: list):
    alerts = []
    for h in holdings:
        code = h["code"]
        d    = get_stock_data(h["code"], h.get("entry", 0), h.get("shares", 0),
                               h.get("w20ma", 0), h.get("edate", ""))
        if not d:
            continue
        prev_status = _prev_status.get(code)
        prev_price  = _prev_price.get(code, d["current"])
        price_chg   = (d["current"] - prev_price) / prev_price * 100 if prev_price else 0
        event = None
        if d["status"] == "danger" and prev_status != "danger":
            event = "danger_trail" if d["trail_hit"] else "danger_ma"
        elif d["status"] == "warn" and prev_status == "safe":
            event = "warn"
        elif d["tp1_hit"] and not (_prev_status.get(code + "_tp1")):
            event = "tp1"
            _prev_status[code + "_tp1"] = True
        elif price_chg <= -3:
            event = "big_drop"
        elif price_chg >= 3:
            event = "big_rise"
        if event:
            alerts.append(format_team_discussion(d, event))
        _prev_status[code] = d["status"]
        _prev_price[code]  = d["current"]
    return alerts


# ════════════════════════════════════════════
# 資料載入
# ════════════════════════════════════════════
def load_data() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        holdings = json.load(f)
    return [h for h in holdings if h and h.get("code")]


# ════════════════════════════════════════════
# 補傳判斷：當天時間已過某個排程，且尚未發送
# ════════════════════════════════════════════
def past_time(hour: int, minute: int) -> bool:
    now = datetime.now()
    return now.hour > hour or (now.hour == hour and now.minute >= minute)


def catchup(label: str, hour: int, minute: int, sent_today, build_fn, *args) -> bool:
    """
    若今天尚未發送，且當前時間已過排程時間 → 立即補發。
    回傳 True 表示有補發。
    """
    today = date.today()
    if sent_today == today:
        return False
    if not past_time(hour, minute):
        return False
    print(f"補傳 {label}（當前 {datetime.now().strftime('%H:%M')}）...")
    msg = build_fn(*args) if args else build_fn()
    if msg:
        send_line(msg)
    return True


# ════════════════════════════════════════════
# 主程式
# ════════════════════════════════════════════
def main():
    print("股票監控排程啟動")

    news_sent_today    = None
    prefut_sent_today  = None
    morning_sent_today = None
    closing_sent_today = None
    pick_sent_today    = None

    # ── 啟動通知 ──────────────────────────────────────────────────────
    send_line("📊 投資團隊監控程式已啟動")

    # ── 啟動補傳：當天已過的排程，逐一補發 ──────────────────────────────
    today = date.today()
    if is_trading_day(today):
        if catchup("財經重點新聞", NEWS_HOUR, NEWS_MIN, news_sent_today, build_finance_news):
            news_sent_today = today

        if catchup("台指期盤前提醒", PREFUT_HOUR, PREFUT_MIN, prefut_sent_today, build_prefutures_reminder):
            prefut_sent_today = today

        holdings = load_data()
        if holdings and catchup("早報", SEND_HOUR, SEND_MIN, morning_sent_today,
                                build_message, holdings):
            morning_sent_today = today

        # 收盤報告補傳（資料須是今日才發）
        if past_time(CLOSE_HOUR, CLOSE_MIN) and closing_sent_today != today:
            msg = build_closing_message()
            if msg:
                print("補傳收盤報告...")
                send_line(msg)
            closing_sent_today = today

        if catchup("台股選股報告", PICK_HOUR, PICK_MIN, pick_sent_today, build_stock_pick_report):
            pick_sent_today = today

    # ── 主循環 ────────────────────────────────────────────────────────
    while True:
        now   = datetime.now()
        today = now.date()

        if not is_trading_day(today):
            time.sleep(60)
            continue

        h, m = now.hour, now.minute

        # 07:30 財經重點新聞
        if h == NEWS_HOUR and m == NEWS_MIN and news_sent_today != today:
            send_line(build_finance_news())
            news_sent_today = today
            time.sleep(61)
            continue

        # 08:20 台指期盤前提醒
        if h == PREFUT_HOUR and m == PREFUT_MIN and prefut_sent_today != today:
            send_line(build_prefutures_reminder())
            prefut_sent_today = today
            time.sleep(61)
            continue

        # 08:30 早報 + 鎖股掃描
        if h == SEND_HOUR and m == SEND_MIN and morning_sent_today != today:
            holdings = load_data()
            if holdings:
                print(f"發送早報（{len(holdings)} 檔持股 + 鎖股掃描）...")
                send_line(build_message(holdings))
            morning_sent_today = today
            time.sleep(61)
            continue

        # 13:35 收盤報告
        if h == CLOSE_HOUR and m == CLOSE_MIN and closing_sent_today != today:
            msg = build_closing_message()
            if msg:
                send_line(msg)
                print("收盤報告已發送")
            else:
                print("加權指數資料未更新，略過收盤報告")
            closing_sent_today = today
            time.sleep(61)
            continue

        # 14:00 台股選股報告
        if h == PICK_HOUR and m == PICK_MIN and pick_sent_today != today:
            send_line(build_stock_pick_report())
            pick_sent_today = today
            time.sleep(61)
            continue

        # 盤中偵測（每 INTRADAY_INTERVAL 分鐘）
        if is_trading_hours() and m % INTRADAY_INTERVAL == 0:
            holdings = load_data()
            if holdings:
                print(f"盤中偵測 {now.strftime('%H:%M')}...")
                alerts = check_intraday(holdings)
                for msg in alerts:
                    send_line(msg)
                    time.sleep(3)

        time.sleep(60)


if __name__ == "__main__":
    main()
