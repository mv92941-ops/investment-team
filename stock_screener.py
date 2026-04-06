"""
台股選股系統 - 每日篩選符合條件的台股
條件：
1. 日線站上日20MA
2. 周線站上周20MA
3. 當天成交量 ≥ 1萬張
4. 成交量 > 5日均量（放量突破加分）
5. 近期下降趨勢線突破（近似演算法）
"""

import os
import time
import requests
import urllib3
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
def now_tw(): return datetime.now(TZ)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

LINE_CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_TOKEN", "")
LINE_USER_IDS = [
    uid.strip()
    for uid in os.getenv("LINE_USER_IDS", os.getenv("LINE_USER_ID", "")).split(",")
    if uid.strip()
]

MIN_VOLUME = 10000  # 最低成交量（張）


# ── 1. 取今日上市股成交量 ────────────────────────────────────────────

def get_twse_stocks_today():
    """從 TWSE 取得今日所有上市股代碼 + 成交量（張）"""
    date_str = now_tw().strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=json&date={date_str}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=30, verify=False)
        r.raise_for_status()
        data = r.json()
        # TWSE 回傳格式：data["fields"] 是欄位名，data["data"] 是資料列
        fields = data.get("fields", [])
        rows   = data.get("data", [])
        if not rows:
            print("    ⚠️ TWSE 回傳無資料（今日可能非交易日）")
            return []

        # 找欄位索引
        try:
            idx_code = fields.index("證券代號")
            idx_name = fields.index("證券名稱")
            idx_vol  = fields.index("成交股數")
        except ValueError:
            # 欄位名稱備用對應（有時會不同）
            idx_code, idx_name, idx_vol = 0, 1, 2

        stocks = []
        for row in rows:
            code = str(row[idx_code]).strip()
            name = str(row[idx_name]).strip()
            vol_str = str(row[idx_vol]).replace(",", "")
            if not (len(code) == 4 and code.isdigit()):
                continue
            try:
                vol_lots = int(vol_str) // 1000   # 股 → 張
            except ValueError:
                vol_lots = 0
            stocks.append({"code": code, "name": name, "volume": vol_lots})
        return stocks
    except Exception as e:
        print(f"[TWSE API 錯誤] {e}")
        return []


# ── 2. 趨勢線突破偵測（簡化版） ──────────────────────────────────────

def detect_downtrend_breakout(df, lookback=60):
    """
    在過去 lookback 個交易日找波段高點，
    確認是下降序列後，外推至今日，
    若收盤價突破趨勢線則返回 True。
    """
    if len(df) < lookback + 5:
        return False

    # 取「不含今日」的 lookback 根 K 棒
    window = df.iloc[-(lookback + 1):-1]
    highs = window["High"].values
    n = len(highs)

    # 找波段高點（左右各 5 根以上）
    swing_highs = []
    for i in range(5, n - 5):
        if highs[i] >= max(highs[i - 5:i + 6]):
            swing_highs.append((i, highs[i]))

    if len(swing_highs) < 2:
        return False

    # 從最新波段高點往回找，找第一組「後低於前」的組合
    today_close = float(df["Close"].iloc[-1])
    for j in range(len(swing_highs) - 1, 0, -1):
        x2, y2 = swing_highs[j]
        for k in range(j - 1, -1, -1):
            x1, y1 = swing_highs[k]
            if y2 >= y1:
                continue  # 不是下降趨勢
            # 外推到今日（今日 index = n，緊接在 window 之後）
            slope = (y2 - y1) / (x2 - x1)
            trend_today = y2 + slope * (n - x2)
            return today_close > trend_today
    return False


# ── 3. 指標計算 ──────────────────────────────────────────────────────

def analyze_stock(code):
    """
    回傳 dict 或 None（資料不足 / 例外）。
    """
    try:
        ticker = yf.Ticker(f"{code}.TW")

        # 日線（取 150 天，確保有足夠資料算 20MA）
        daily = ticker.history(period="150d", interval="1d")
        if len(daily) < 25:
            return None

        daily["MA20"] = daily["Close"].rolling(20).mean()
        close     = float(daily["Close"].iloc[-1])
        d_ma20    = float(daily["MA20"].iloc[-1])
        yf_vol    = int(daily["Volume"].iloc[-1])          # 股
        vol_lots  = yf_vol // 1000                         # 張
        avg5_lots = int(daily["Volume"].iloc[-6:-1].mean()) // 1000

        daily_ok = close > d_ma20

        # 周線（取 35 週）
        weekly = ticker.history(period="245d", interval="1wk")
        if len(weekly) < 22:
            return None

        weekly["MA20"] = weekly["Close"].rolling(20).mean()
        w_ma20   = float(weekly["MA20"].iloc[-1])
        weekly_ok = close > w_ma20

        vol_ok = (vol_lots >= MIN_VOLUME) and (vol_lots > avg5_lots)

        tl_break = detect_downtrend_breakout(daily)

        # 距周20MA 距離（%）
        dist_pct = (close - w_ma20) / w_ma20 * 100 if w_ma20 else 0

        return {
            "daily_ok":   daily_ok,
            "weekly_ok":  weekly_ok,
            "vol_ok":     vol_ok,
            "tl_break":   tl_break,
            "close":      close,
            "d_ma20":     d_ma20,
            "w_ma20":     w_ma20,
            "vol_lots":   vol_lots,
            "avg5_lots":  avg5_lots,
            "dist_pct":   dist_pct,
        }
    except Exception:
        return None


# ── 4. LINE 發送 ─────────────────────────────────────────────────────

def send_line(msg: str):
    if not LINE_CHANNEL_TOKEN or not LINE_USER_IDS:
        print("⚠️ 未設定 LINE Token，改為終端機輸出。")
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
            status = "✅" if r.status_code == 200 else f"❌ {r.status_code}"
            print(f"LINE 發送 {status}")
        except Exception as e:
            print(f"LINE 例外: {e}")


def format_message(results, scan_dt: str, total_candidates: int) -> str:
    vol_ratio = lambda r: r["vol_lots"] / r["avg5_lots"] if r["avg5_lots"] > 0 else 0

    header = (
        f"📊 投資團隊選股報告\n"
        f"━━━━━━━━━━━━\n"
        f"🗓 {scan_dt}\n"
        f"掃描量≥1萬張：{total_candidates} 支\n"
    )

    if not results:
        return (
            header
            + "\n今日無符合條件個股\n\n"
            "🛡️ 風控師：耐心等待，不是每天都有機會。"
        )

    lines = [header, f"✅ 符合：{len(results)} 支\n"]

    # 排序：趨勢線突破優先，再按距周20MA距離由近到遠
    results.sort(key=lambda r: (not r["tl_break"], r["dist_pct"]))

    for i, r in enumerate(results[:15], 1):   # 最多顯示 15 支
        tl_tag   = " ⚡趨勢線突破" if r["tl_break"] else ""
        vr       = vol_ratio(r)
        max_loss = (r["close"] - r["w_ma20"]) * 1000
        lines.append(
            f"{i}. {r['code']} {r['name']}{tl_tag}\n"
            f"   收盤 {r['close']:.1f}  "
            f"量 {r['vol_lots']:,}張（{vr:.1f}x）\n"
            f"   日MA20 {r['d_ma20']:.1f}  "
            f"周MA20 {r['w_ma20']:.1f}（{r['dist_pct']:+.1f}%）\n"
            f"   💸 最大停損：${max_loss:,.0f}／張\n"
        )

    if len(results) > 15:
        lines.append(f"...另有 {len(results)-15} 支符合，距周MA較遠\n")

    lines += [
        "━━━━━━━━━━━━",
        "🧠 策略王：⚡標記者優先觀察",
        "🛡️ 風控師：對照K線確認後再進，停損日MA20下方",
    ]
    return "\n".join(lines)


# ── 5. 主程序 ────────────────────────────────────────────────────────

def main():
    scan_dt = now_tw().strftime("%Y-%m-%d %H:%M")
    print("=" * 45)
    print(f"台股選股系統  {scan_dt}")
    print("=" * 45)

    # Step 1: TWSE 量能初篩
    print("\n[1] 抓取 TWSE 今日成交量...")
    all_stocks = get_twse_stocks_today()
    candidates = [s for s in all_stocks if s["volume"] >= MIN_VOLUME]
    print(f"    上市股總數：{len(all_stocks)} 支")
    print(f"    量≥{MIN_VOLUME//10000}萬張：{len(candidates)} 支")

    if not candidates:
        send_line("⚠️ 無法取得今日成交量資料（可能非交易日或 API 異常）")
        return

    # Step 2: MA + 趨勢線分析
    print(f"\n[2] 分析 {len(candidates)} 支候選股（每支約 0.5 秒）...")
    results = []

    for i, stock in enumerate(candidates, 1):
        code, name, vol = stock["code"], stock["name"], stock["volume"]
        print(f"  [{i:3d}/{len(candidates)}] {code} {name:<8}", end=" ")

        data = analyze_stock(code)
        if data is None:
            print("資料不足")
            time.sleep(0.3)
            continue

        data["code"] = code
        data["name"] = name

        if data["daily_ok"] and data["weekly_ok"] and data["vol_ok"]:
            tag = "⚡ 趨勢線突破" if data["tl_break"] else "✅ 均線符合"
            print(tag)
            results.append(data)
        else:
            reasons = []
            if not data["daily_ok"]:  reasons.append("日MA❌")
            if not data["weekly_ok"]: reasons.append("周MA❌")
            if not data["vol_ok"]:    reasons.append("量能❌")
            print(" ".join(reasons))

        time.sleep(0.4)   # 避免被 yfinance rate limit

    # Step 3: 整理輸出
    print(f"\n[3] 結果：{len(results)} 支符合")
    msg = format_message(results, scan_dt, len(candidates))
    print("\n" + "─" * 45)
    print(msg)
    print("─" * 45)

    # Step 4: 發 LINE
    send_line(msg)
    print("\n完成！")


def run_scheduled():
    """每個交易日 14:00 自動執行一次選股"""
    print("📅 排程模式啟動，每日 14:00 執行選股...")
    ran_today = None

    while True:
        now = now_tw()
        today = now.date()

        # 週一到週五，14:00 執行（只執行一次；若晚開機則補發）
        past_1400 = (now.hour > 14) or (now.hour == 14 and now.minute >= 0)
        if (now.weekday() < 5
                and past_1400
                and ran_today != today):
            print(f"\n⏰ 觸發每日選股 {now.strftime('%Y-%m-%d %H:%M')}")
            try:
                main()
            except Exception as e:
                print(f"[選股錯誤] {e}")
                send_line(f"⚠️ 台股選股系統發生錯誤：{e}")
            ran_today = today

        time.sleep(30)   # 每 30 秒檢查一次時間


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--now":
        # python stock_screener.py --now  → 立即執行一次
        main()
    else:
        # python stock_screener.py        → 排程模式（每日14:00）
        run_scheduled()
