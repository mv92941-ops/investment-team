"""
每日 07:30 財經重點新聞 LINE 通知
來源：Google News RSS、Yahoo Finance RSS、鉅亨網 RSS
只留與台股/美股/總經/期貨相關的新聞
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

# ── 關鍵字過濾 ────────────────────────────────────────────────────────
INCLUDE_KEYWORDS = [
    "台股", "加權", "大盤", "外資", "法人", "投信", "自營",
    "台指期", "期貨", "選擇權", "微型",
    "美股", "那斯達克", "道瓊", "S&P", "標普",
    "聯準會", "Fed", "升息", "降息", "利率", "通膨", "CPI", "PPI",
    "半導體", "輝達", "台積電", "NVIDIA", "AI", "人工智慧",
    "ETF", "0050", "00878",
    "匯率", "美元", "新台幣",
    "財報", "EPS", "獲利", "營收",
]

EXCLUDE_KEYWORDS = [
    "房地產", "房價", "藝人", "明星", "娛樂", "電影", "音樂",
    "球賽", "足球", "棒球", "籃球", "奧運",
    "旅遊", "美食", "健康", "醫療", "減肥",
    "政治", "選舉", "罷免",
]

MAX_NEWS = 8      # 最多顯示幾則
MAX_AGE_DAYS = 1  # 只顯示 1 天內的新聞（避免舊新聞）


# ── 工具函式 ──────────────────────────────────────────────────────────

def is_relevant(title: str) -> bool:
    title_lower = title.lower()
    if any(kw in title for kw in EXCLUDE_KEYWORDS):
        return False
    return any(kw in title for kw in INCLUDE_KEYWORDS)


def fetch_news() -> list[dict]:
    """抓取所有來源的新聞，回傳去重後的相關新聞列表"""
    seen_titles = set()
    all_news = []

    for source in NEWS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            for entry in feed.entries[:20]:
                title = entry.get("title", "").strip()
                link  = entry.get("link", "")
                if not title or title in seen_titles:
                    continue
                if not is_relevant(title):
                    continue
                # 日期過濾：只保留 MAX_AGE_DAYS 天內的新聞
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published:
                    pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - pub_dt > timedelta(days=MAX_AGE_DAYS):
                        continue
                seen_titles.add(title)
                all_news.append({"title": title, "link": link, "source": source["name"]})
        except Exception as e:
            print(f"[{source['name']} 抓取失敗] {e}")

    return all_news[:MAX_NEWS]


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
            print(f"LINE 發送 {status}")
        except Exception as e:
            print(f"LINE 例外: {e}")


def format_message(news_list: list[dict]) -> str:
    now_str = now_tw().strftime("%m/%d %H:%M")

    if not news_list:
        return (
            f"📰 投資團隊早報  {now_str}\n"
            f"━━━━━━━━━━━━\n"
            f"今日暫無相關財經新聞\n"
            f"📊 資料酷：開盤前請自行確認大盤方向。"
        )

    lines = [
        f"📰 投資團隊早報  {now_str}",
        f"━━━━━━━━━━━━",
        f"今日財經重點（{len(news_list)} 則）",
        f"",
    ]

    for i, item in enumerate(news_list, 1):
        lines.append(f"{i}. {item['title']}")
        lines.append(f"   🔗 {item['link']}")
        lines.append("")

    lines += [
        "━━━━━━━━━━━━",
        "🧠 策略王：注意總經消息對盤勢的影響",
        "🛡️ 風控師：有重大消息時，縮小部位或等待",
    ]
    return "\n".join(lines)


def main():
    print(f"[{now_tw().strftime('%H:%M:%S')}] 抓取財經新聞...")
    news = fetch_news()
    print(f"    找到 {len(news)} 則相關新聞")
    msg = format_message(news)
    print("\n" + "─" * 45)
    print(msg)
    print("─" * 45 + "\n")
    send_line(msg)


def is_rest_day(d: date) -> bool:
    """判斷是否為休息日（週末或台灣國定假日）"""
    if d.weekday() >= 5:  # 週六、週日
        return True
    tw_holidays = holidays.Taiwan(years=d.year)
    return d in tw_holidays


def get_send_time(d: date) -> tuple[int, int]:
    """回傳當天發送時間 (hour, minute)"""
    if is_rest_day(d):
        return 9, 0   # 週末或國定假日 09:00
    return 7, 30      # 平日 07:30


def run_scheduled():
    """每日自動執行：平日 07:30，週末與國定假日 09:00"""
    print("📅 新聞排程模式啟動（平日 07:30，週末/國定假日 09:00）...")
    sent_today = None

    while True:
        now = now_tw()
        today = now.date()
        send_h, send_m = get_send_time(today)

        # 補發：若今天排程時間已過且尚未發送（晚開機補發）
        already_past = (now.hour > send_h) or (now.hour == send_h and now.minute >= send_m)
        if (already_past and sent_today != today):
            rest = is_rest_day(today)
            label = "假日早報" if rest else "早報"
            print(f"\n⏰ 觸發{label} {now.strftime('%Y-%m-%d %H:%M')}")
            try:
                main()
            except Exception as e:
                print(f"[早報錯誤] {e}")
                send_line(f"⚠️ 早報系統發生錯誤：{e}")
            sent_today = today

        time.sleep(30)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--now":
        main()
    else:
        run_scheduled()
