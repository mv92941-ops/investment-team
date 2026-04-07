"""
盤中即時財經新聞監控
每 CHECK_INTERVAL 分鐘掃描一次 RSS
發現新文章（30分鐘內）且符合重大關鍵字，立即發 LINE
監控時段：平日 07:00 ~ 17:00，週末 08:00 ~ 14:00
"""

import os
import time
import requests
import urllib3
import feedparser
import holidays
from datetime import datetime, date, timezone, timedelta
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
def now_tw(): return datetime.now(TZ)

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LINE_CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_TOKEN", "")
LINE_USER_IDS = [
    uid.strip()
    for uid in os.getenv("LINE_USER_IDS", os.getenv("LINE_USER_ID", "")).split(",")
    if uid.strip()
]

CHECK_INTERVAL = 5        # 每幾分鐘掃一次
NEWS_MAX_AGE_MIN = 30     # 只看幾分鐘內的新文章
COOLDOWN_MIN = 10         # 兩則通知之間最少間隔（分鐘）

# ── 新聞來源 ──────────────────────────────────────────────────────────
NEWS_SOURCES = [
    {
        "name": "Google財經",
        "url": "https://news.google.com/rss/search?q=台股+財經&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    },
    {
        "name": "Google美股",
        "url": "https://news.google.com/rss/search?q=美股+那斯達克+道瓊&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    },
    {
        "name": "Google總經",
        "url": "https://news.google.com/rss/search?q=聯準會+Fed+升息+降息+通膨&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    },
    {
        "name": "鉅亨財經",
        "url": "https://feeds.feedburner.com/cnyes",
    },
    {
        "name": "Yahoo財經",
        "url": "https://tw.finance.yahoo.com/news/rss",
    },
]

# ── 重大事件關鍵字（觸發即時通知）────────────────────────────────────
BREAKING_KEYWORDS = [
    # 總經重大
    "升息", "降息", "暴跌", "崩盤", "熔斷", "停市",
    "Fed", "聯準會", "非農", "CPI", "PCE", "GDP",
    # 台股大盤
    "台股大漲", "台股大跌", "加權指數", "外資大買", "外資大賣",
    "三大法人", "融資斷頭",
    # 期貨相關
    "台指期", "結算", "逼倉",
    # 地緣政治 / 黑天鵝
    "關稅", "制裁", "戰爭", "地震", "停電",
    # 個股重大
    "台積電", "TSMC", "輝達", "NVIDIA", "財報", "EPS",
    "法說會", "獲利預警", "股利",
]

# ── 排除（避免雜訊）──────────────────────────────────────────────────
EXCLUDE_KEYWORDS = [
    "房地產", "房價", "藝人", "明星", "娛樂", "電影", "音樂",
    "球賽", "足球", "棒球", "籃球", "旅遊", "美食", "健康",
    "醫療", "減肥", "政治", "選舉", "罷免",
]


# ── 工具函式 ──────────────────────────────────────────────────────────

def is_breaking(title: str) -> bool:
    if any(kw in title for kw in EXCLUDE_KEYWORDS):
        return False
    return any(kw in title for kw in BREAKING_KEYWORDS)


def is_active_hours() -> bool:
    """是否在監控時段內"""
    now = now_tw()
    wd = now.weekday()
    h = now.hour
    if wd < 5:          # 平日 07:00 ~ 17:00
        return 7 <= h < 17
    else:               # 週末 08:00 ~ 14:00（美股盤前/外電仍有消息）
        return 8 <= h < 14


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


def format_alert(news_list: list[dict]) -> str:
    now_str = now_tw().strftime("%m/%d %H:%M")
    lines = [
        f"🔔 即時財經快訊  {now_str}",
        f"━━━━━━━━━━━━",
        f"共 {len(news_list)} 則新訊息",
        "",
    ]
    for i, item in enumerate(news_list, 1):
        lines.append(f"{i}. {item['title']}")
        lines.append(f"   [{item['source']}]")
        if item.get("link"):
            lines.append(f"   {item['link']}")
        lines.append("")
    lines += [
        "━━━━━━━━━━━━",
        "🧠 策略王：注意消息面對盤勢的影響",
        "🛡️ 風控師：重大消息出現，先縮部位再觀察",
    ]
    return "\n".join(lines)


def fetch_breaking_news(seen_titles: set) -> list[dict]:
    """抓取所有來源，回傳未見過且在時間內的重大新聞"""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=NEWS_MAX_AGE_MIN)
    new_breaking = []

    for source in NEWS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                if not title or title in seen_titles:
                    continue
                if not is_breaking(title):
                    continue
                # 時間過濾
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published:
                    pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue
                seen_titles.add(title)
                link = entry.get("link", "")
                new_breaking.append({"title": title, "source": source["name"], "link": link})
        except Exception as e:
            print(f"  [{source['name']} 抓取失敗] {e}")

    return new_breaking


# ── 主排程 ────────────────────────────────────────────────────────────

def run_realtime():
    print("📡 即時新聞監控啟動")
    print(f"   掃描間隔：{CHECK_INTERVAL} 分鐘")
    print(f"   新聞時效：最近 {NEWS_MAX_AGE_MIN} 分鐘")
    print(f"   通知冷卻：{COOLDOWN_MIN} 分鐘")
    print(f"   監控時段：平日 07-17，週末 08-14")

    seen_titles: set = set()
    last_sent: datetime | None = None

    while True:
        now = now_tw()

        if not is_active_hours():
            print(f"[{now.strftime('%H:%M')}] 非監控時段，跳過")
            time.sleep(CHECK_INTERVAL * 60)
            continue

        print(f"[{now.strftime('%H:%M')}] 掃描即時新聞...")
        breaking = fetch_breaking_news(seen_titles)

        if breaking:
            # 冷卻時間檢查
            if last_sent and (now - last_sent).total_seconds() < COOLDOWN_MIN * 60:
                remaining = COOLDOWN_MIN - int((now - last_sent).total_seconds() / 60)
                print(f"  冷卻中（還剩 {remaining} 分鐘），暫不發送")
            else:
                print(f"  發現 {len(breaking)} 則重大新聞，發送中...")
                for item in breaking:
                    print(f"    - {item['title']}")
                msg = format_alert(breaking)
                send_line(msg)
                last_sent = now
        else:
            print(f"  無新重大訊息")

        time.sleep(CHECK_INTERVAL * 60)


if __name__ == "__main__":
    run_realtime()
