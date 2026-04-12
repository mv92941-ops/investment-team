"""
投資團隊 - 一鍵啟動所有排程
同時執行：
  ① 台指期點位提醒 + 08:20 盤前提醒   (price_alert.py)
  ② 07:30 財經早報                     (morning_news.py)
  ③ 14:00 台股選股報告                 (stock_screener.py)
  ④ 盤中即時重大新聞通知               (news_realtime.py)
"""

import threading
import time
from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ── 匯入各模組排程函式 ──────────────────────────────────────────────

def run_price_alert():
    try:
        from price_alert import main
        log("✅ 台指期監控 啟動")
        main()
    except Exception as e:
        log(f"❌ 台指期監控 錯誤：{e}")

def run_morning_news():
    try:
        from morning_news import run_scheduled
        log("✅ 財經早報排程 啟動（每日 07:30）")
        run_scheduled()
    except Exception as e:
        log(f"❌ 財經早報 錯誤：{e}")

def run_stock_screener():
    try:
        from stock_screener import run_scheduled
        log("✅ 台股選股排程 啟動（每日 14:00）")
        run_scheduled()
    except Exception as e:
        log(f"❌ 台股選股 錯誤：{e}")

def run_news_realtime():
    try:
        from news_realtime import run_realtime
        log("✅ 即時新聞監控 啟動（盤中每 5 分鐘掃描）")
        run_realtime()
    except Exception as e:
        log(f"❌ 即時新聞監控 錯誤：{e}")

def run_trump_monitor():
    try:
        from trump_monitor import run_trump_monitor as _run
        log("✅ 川普貼文監控 啟動（全天每 3 分鐘掃描）")
        _run()
    except Exception as e:
        log(f"❌ 川普貼文監控 錯誤：{e}")

# ── 主程序 ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  📊 投資團隊系統啟動")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    print()
    print("  ⏰ 07:30  財經重點新聞")
    print("  ⏰ 08:20  台指期盤前提醒")
    print("  ⏰ 14:00  台股選股報告")
    print("  🔔 全天   台指期關鍵點位即時通知")
    print("  📡 盤中   重大財經新聞即時通知（每 5 分鐘）")
  print("  🇺🇸 全天   川普 Truth Social 新貼文即時翻譯（每 3 分鐘）")
    print()
    print("  關閉此視窗即停止所有功能")
    print("=" * 50)
    print()

    threads = [
        threading.Thread(target=run_price_alert,    daemon=True, name="price_alert"),
        threading.Thread(target=run_morning_news,   daemon=True, name="morning_news"),
        threading.Thread(target=run_stock_screener, daemon=True, name="stock_screener"),
        threading.Thread(target=run_news_realtime,  daemon=True, name="news_realtime"),
        threading.Thread(target=run_trump_monitor,  daemon=True, name="trump_monitor"),
    ]

    for t in threads:
        t.start()
        time.sleep(1)   # 錯開啟動，避免同時搶資源

    # 主執行緒保持存活，等待所有子執行緒
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[系統] 使用者中止，關閉所有排程。")
