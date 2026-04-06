"""
投資團隊 - 台指期關鍵價位 LINE 提醒系統
當台指接近關鍵點位時，自動發送 LINE 通知
"""

import os
import json
import time
import requests
import yfinance as yf
from datetime import datetime, date
from pathlib import Path
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
def now_tw(): return datetime.now(TZ)

load_dotenv()

LINE_CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_TOKEN", "")
LINE_USER_IDS = [
    uid.strip()
    for uid in os.getenv("LINE_USER_IDS", os.getenv("LINE_USER_ID", "")).split(",")
    if uid.strip()
]

LEVELS_FILE = Path(__file__).parent / "levels.json"
CHECK_INTERVAL = 300  # 每5分鐘檢查一次

AGENT_COMMENTS = {
    "策略王":  "🧠 策略王：確認方向後再進場，不要搶第一根K棒。",
    "風控師":  "🛡️ 風控師：確認15分K棒收盤位置，停損設好再進。",
    "資料酷":  "📊 資料酷：注意量能是否配合，量縮慎進。",
    "財務長":  "💰 財務長：記得計算成本，微型台指每點$10。",
    "強心臟":  "💪 強心臟：心態穩了嗎？進場理由是技術面還是情緒？"
}

def now_str():
    return now_tw().strftime("%H:%M:%S")

def load_levels():
    if LEVELS_FILE.exists():
        with open(LEVELS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"levels": [], "alert_range": 30, "cooldown_minutes": 30}

def get_taiwan_index_price():
    """取得台灣加權指數作為台指期參考價（與台指期高度相關）"""
    try:
        ticker = yf.Ticker("^TWII")
        hist = ticker.history(period="1d", interval="1m")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            return price
    except Exception as e:
        print(f"[{now_str()}] yfinance 取價失敗: {e}")
    return None

def send_line(msg: str):
    if not LINE_CHANNEL_TOKEN or not LINE_USER_IDS:
        print(f"[{now_str()}] ⚠️ 未設定 LINE Token")
        return
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}"
    }
    for uid in LINE_USER_IDS:
        payload = {"to": uid, "messages": [{"type": "text", "text": msg}]}
        try:
            r = requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers=headers, json=payload, timeout=10
            )
            if r.status_code == 200:
                print(f"[{now_str()}] ✅ LINE 通知已發送")
            else:
                print(f"[{now_str()}] ❌ LINE 發送失敗: {r.status_code}")
        except Exception as e:
            print(f"[{now_str()}] ❌ LINE 例外: {e}")

def build_alert_message(price: float, level: dict, diff: float) -> str:
    name = level["name"]
    point = level["price"]
    direction = level["direction"]
    owner = level.get("owner", "風控師")

    if price >= point:
        position = f"現價高於關鍵價 {price - point:.0f} 點"
        arrow = "🔺"
    else:
        position = f"現價低於關鍵價 {point - price:.0f} 點"
        arrow = "🔻"

    comment = AGENT_COMMENTS.get(owner, f"🛡️ 風控師：請確認後再行動。")

    return (
        f"📊 投資團隊提醒\n"
        f"━━━━━━━━━━━━\n"
        f"{arrow} 接近關鍵點位！\n"
        f"\n"
        f"現價：{price:.0f}\n"
        f"關鍵點：{point}（{name}）\n"
        f"距離：{diff:.0f} 點\n"
        f"方向提示：{direction}\n"
        f"\n"
        f"{comment}\n"
        f"━━━━━━━━━━━━\n"
        f"⏰ {now_tw().strftime('%m/%d %H:%M')}"
    )

def check_levels(price: float, levels_data: dict, alerted: dict):
    levels = levels_data.get("levels", [])
    alert_range = levels_data.get("alert_range", 30)
    cooldown = levels_data.get("cooldown_minutes", 30)
    now = now_tw()

    for level in levels:
        name = level["name"]
        point = level["price"]
        diff = abs(price - point)

        if diff <= alert_range:
            # 冷卻時間檢查
            if name in alerted:
                elapsed = (now - alerted[name]).total_seconds() / 60
                if elapsed < cooldown:
                    continue

            msg = build_alert_message(price, level, diff)
            send_line(msg)
            alerted[name] = now
            print(f"[{now_str()}] 🔔 提醒發送：{name}（{point}），現價 {price:.0f}，距離 {diff:.0f}點")

def send_morning_reminder():
    msg = (
        "📊 投資團隊 早安！\n"
        "━━━━━━━━━━━━\n"
        "請回傳今日盤前資訊：\n"
        "\n"
        "1️⃣ 有無持倉\n"
        "   （幾口、多空、成本點位）\n"
        "\n"
        "2️⃣ 昨天收盤點位\n"
        "\n"
        "3️⃣ 今天看法\n"
        "   （偏多 / 偏空 / 中性）\n"
        "\n"
        "4️⃣ 貼上日線或60分圖\n"
        "\n"
        "📩 回傳後來 Claude Code\n"
        "   讓五人團隊幫你分析！"
    )
    send_line(msg)
    print(f"[{now_str()}] ✅ 早安盤前提醒已發送")

def main():
    print("="*40)
    print("📊 投資團隊 - 監控系統啟動")
    print(f"每日 08:20 發送盤前提醒")
    print(f"每 {CHECK_INTERVAL//60} 分鐘檢查關鍵點位")
    print("="*40)

    alerted = {}
    morning_sent_date = None
    last_price = None
    same_price_count = 0

    # 啟動時發送上線通知
    send_line(
        "📊 投資團隊監控已啟動\n"
        "━━━━━━━━━━━━\n"
        "每日 08:20 會提醒你回傳盤前資訊\n"
        "接近關鍵點位時也會通知你\n"
        "🛡️ 風控師：停損設好，紀律執行。"
    )

    while True:
        now = now_tw()
        today = date.today()

        # 每日 08:20 發送盤前提醒（只發一次；若晚開機則補發）
        past_820 = (now.hour > 8) or (now.hour == 8 and now.minute >= 20)
        if past_820 and morning_sent_date != today:
            send_morning_reminder()
            morning_sent_date = today

        # 關鍵點位監控
        price = get_taiwan_index_price()
        if price:
            # 價格沒有更新（夜盤抓到舊資料）就跳過
            if price == last_price:
                same_price_count += 1
                print(f"[{now_str()}] 價格未更新（{same_price_count}次），跳過監控")
                time.sleep(CHECK_INTERVAL)
                continue
            else:
                same_price_count = 0
                last_price = price

            print(f"[{now_str()}] 台指參考價：{price:.0f}")
            levels_data = load_levels()
            check_levels(price, levels_data, alerted)
        else:
            print(f"[{now_str()}] ⚠️ 無法取得價格（可能非交易時段）")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
