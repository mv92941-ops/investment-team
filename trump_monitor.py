"""
川普 Truth Social 即時監控
每 3 分鐘掃一次，發現新貼文立即翻譯成繁體中文推播 LINE
全天 24 小時運作（川普隨時發文）
"""

import os
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from deep_translator import GoogleTranslator

load_dotenv()

TZ = ZoneInfo("Asia/Taipei")
def now_tw(): return datetime.now(TZ)

LINE_CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_TOKEN", "")
LINE_USER_IDS = [
    uid.strip()
    for uid in os.getenv("LINE_USER_IDS", os.getenv("LINE_USER_ID", "")).split(",")
    if uid.strip()
]

CHECK_INTERVAL = 3   # 每幾分鐘掃一次
TRUMP_ACCOUNT_ID = "107780257626128497"  # @realDonaldTrump on Truth Social


def get_latest_posts(last_id: str | None) -> list[dict]:
    """從 Truth Social Mastodon API 抓最新貼文"""
    url = f"https://truthsocial.com/api/v1/accounts/{TRUMP_ACCOUNT_ID}/statuses"
    params = {"limit": 5, "exclude_replies": "true", "exclude_reblogs": "true"}
    if last_id:
        params["since_id"] = last_id
    try:
        r = requests.get(url, params=params, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            return r.json()
        else:
            print(f"  Truth Social API 回傳 {r.status_code}")
            return []
    except Exception as e:
        print(f"  Truth Social 抓取失敗: {e}")
        return []


def translate_post(raw_text: str) -> str:
    """用 Google Translate（免費）翻譯成繁體中文"""
    try:
        translated = GoogleTranslator(source="auto", target="zh-TW").translate(raw_text)
        return translated or raw_text
    except Exception as e:
        print(f"  翻譯失敗: {e}，改發原文")
        return raw_text


def strip_html(text: str) -> str:
    """簡單去除 HTML 標籤"""
    import re
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def send_line(msg: str):
    if not LINE_CHANNEL_TOKEN or not LINE_USER_IDS:
        print("⚠️ 未設定 LINE Token")
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
            print(f"  LINE {status}")
        except Exception as e:
            print(f"  LINE 例外: {e}")


INVEST_KEYWORDS = [
    "tariff", "關稅", "china", "中國", "taiwan", "台灣",
    "fed", "interest rate", "利率", "inflation", "通膨",
    "stock", "market", "nasdaq", "dow", "s&p",
    "nvidia", "tsmc", "apple", "trade", "sanction", "制裁",
]

def format_message(post: dict, translated: str, raw_text: str) -> str:
    created_at = post.get("created_at", "")
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        dt_tw = dt.astimezone(TZ)
        time_str = dt_tw.strftime("%m/%d %H:%M")
    except Exception:
        time_str = created_at[:16]

    # 偵測是否涉及投資相關內容
    combined = (raw_text + translated).lower()
    is_invest = any(kw in combined for kw in INVEST_KEYWORDS)
    tag = "📈 投資相關！" if is_invest else ""

    lines = [
        f"🇺🇸 川普剛在 Truth Social 發文 {tag}",
        "━━━━━━━━━━━━",
        translated,
        "━━━━━━━━━━━━",
        f"發文時間：{time_str}（台灣時間）",
    ]
    return "\n".join(lines)


def run_trump_monitor():
    print("🇺🇸 川普 Truth Social 監控啟動")
    print(f"   掃描間隔：{CHECK_INTERVAL} 分鐘（全天 24 小時）")

    last_id: str | None = None

    # 初始化：抓最新一篇 ID 作為基準，不發送舊文
    init_posts = get_latest_posts(None)
    if init_posts:
        last_id = init_posts[0]["id"]
        print(f"   基準貼文 ID：{last_id}（不發送，只偵測之後的新文）")

    while True:
        time.sleep(CHECK_INTERVAL * 60)
        now = now_tw()
        print(f"[{now.strftime('%H:%M')}] 掃描川普新貼文...")

        new_posts = get_latest_posts(last_id)
        if not new_posts:
            print("  無新貼文")
            continue

        # API 回傳為新→舊，倒序處理讓舊的先發
        for post in reversed(new_posts):
            raw = strip_html(post.get("content", ""))
            if not raw:
                continue

            post_id = post["id"]
            print(f"  新貼文（ID {post_id}）：{raw[:60]}...")

            translated = translate_post(raw)
            msg = format_message(post, translated, raw)
            send_line(msg)
            last_id = post_id
            time.sleep(2)   # 連續多篇時稍微錯開


if __name__ == "__main__":
    run_trump_monitor()
