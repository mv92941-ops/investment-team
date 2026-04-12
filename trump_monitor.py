"""
川普言論即時監控
Truth Social API 已封鎖伺服器存取，改用 Google News 監控報導川普言論的新聞
路透社、AP、Yahoo財經等媒體通常 5~15 分鐘內就會報導重大貼文
全天 24 小時運作
"""

import os
import time
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("Asia/Taipei")
def now_tw(): return datetime.now(TZ)

LINE_CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_TOKEN", "")
LINE_USER_IDS = [
    uid.strip()
    for uid in os.getenv("LINE_USER_IDS", os.getenv("LINE_USER_ID", "")).split(",")
    if uid.strip()
]

CHECK_INTERVAL   = 5    # 每幾分鐘掃一次
NEWS_MAX_AGE_MIN = 30   # 只看 30 分鐘內的新聞
COOLDOWN_MIN     = 15   # 兩則川普通知之間最少間隔（分鐘）

# ── 川普專屬新聞來源 ──────────────────────────────────────────────────
TRUMP_SOURCES = [
    {
        "name": "Google-川普英文",
        "url": "https://news.google.com/rss/search?q=Trump+tariff+OR+trade+OR+China+OR+Taiwan+OR+Fed+OR+market&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google-川普中文",
        "url": "https://news.google.com/rss/search?q=川普+關稅+OR+制裁+OR+台灣+OR+中國+OR+貿易+OR+美股&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    },
    {
        "name": "Google-Truth Social",
        "url": "https://news.google.com/rss/search?q=Trump+site:truthsocial.com&hl=en-US&gl=US&ceid=US:en",
    },
]

# ── 川普相關觸發關鍵字（英文+中文）──────────────────────────────────
TRUMP_KEYWORDS = [
    # 人名
    "Trump", "川普", "Donald Trump",
    # 政策
    "tariff", "關稅", "trade war", "貿易戰", "sanction", "制裁",
    "executive order", "行政命令",
    # 地緣
    "China", "中國", "Taiwan", "台灣", "Taiwan Strait", "台海",
    "North Korea", "北韓", "Russia", "俄羅斯", "Ukraine", "烏克蘭",
    # 總經
    "Fed", "interest rate", "利率", "inflation", "通膨",
    "recession", "衰退", "debt ceiling", "debt limit",
    # 市場
    "stock market", "美股", "Nasdaq", "S&P", "Dow Jones",
    "NVIDIA", "Apple", "TSMC", "台積電",
]

# ── 排除無關雜訊 ─────────────────────────────────────────────────────
EXCLUDE_KEYWORDS = [
    "golf", "高爾夫", "hair", "lawsuit", "訴訟", "trial", "審判",
    "TV show", "reality", "celebrity", "名人", "entertainment",
]


def is_trump_related(title: str) -> bool:
    title_lower = title.lower()
    if any(kw.lower() in title_lower for kw in EXCLUDE_KEYWORDS):
        return False
    return any(kw.lower() in title_lower for kw in TRUMP_KEYWORDS)


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


def format_trump_alert(news_list: list[dict]) -> str:
    now_str = now_tw().strftime("%m/%d %H:%M")
    lines = [
        f"🇺🇸 川普相關重大消息  {now_str}",
        "━━━━━━━━━━━━",
        f"共 {len(news_list)} 則新報導",
        "",
    ]
    for i, item in enumerate(news_list, 1):
        lines.append(f"{i}. {item['title']}")
        lines.append(f"   [{item['source']}]")
        lines.append("")
    lines += [
        "━━━━━━━━━━━━",
        "🧠 策略王：確認對台股/美股的影響方向",
        "🛡️ 風控師：消息面震盪，注意停損點",
    ]
    return "\n".join(lines)


def fetch_trump_news(seen_titles: set) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=NEWS_MAX_AGE_MIN)
    results = []

    for source in TRUMP_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            for entry in feed.entries[:20]:
                title = entry.get("title", "").strip()
                if not title or title in seen_titles:
                    continue
                if not is_trump_related(title):
                    continue
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published:
                    pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue
                seen_titles.add(title)
                results.append({"title": title, "source": source["name"]})
        except Exception as e:
            print(f"  [{source['name']} 抓取失敗] {e}")

    return results


def run_trump_monitor():
    print("🇺🇸 川普言論監控啟動（Google News，全天 24 小時）")
    print(f"   掃描間隔：{CHECK_INTERVAL} 分鐘")
    print(f"   新聞時效：最近 {NEWS_MAX_AGE_MIN} 分鐘")
    print(f"   通知冷卻：{COOLDOWN_MIN} 分鐘")

    seen_titles: set = set()
    last_sent: datetime | None = None

    # 初始化：先掃一次填充 seen_titles，避免啟動時發舊新聞
    print("  初始化掃描（不發送）...")
    fetch_trump_news(seen_titles)
    print(f"  已記錄 {len(seen_titles)} 則既有標題，開始監控新消息")

    while True:
        time.sleep(CHECK_INTERVAL * 60)
        now = now_tw()
        print(f"[{now.strftime('%H:%M')}] 掃描川普相關新聞...")

        news = fetch_trump_news(seen_titles)

        if not news:
            print("  無新消息")
            continue

        if last_sent and (now - last_sent).total_seconds() < COOLDOWN_MIN * 60:
            remaining = COOLDOWN_MIN - int((now - last_sent).total_seconds() / 60)
            print(f"  冷卻中（還剩 {remaining} 分鐘），暫不發送")
            continue

        print(f"  發現 {len(news)} 則新川普相關報導，發送中...")
        for item in news:
            print(f"    - {item['title']}")
        msg = format_trump_alert(news)
        send_line(msg)
        last_sent = now


if __name__ == "__main__":
    run_trump_monitor()
