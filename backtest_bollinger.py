"""
布林通道策略回測
策略A：均值回歸 — 碰下軌做多、碰上軌做空，回到中軌停利
策略B：突破     — 收盤突破上軌做多、跌破下軌做空，固定停損停利
共同過濾：日K SMA20 方向過濾大方向
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

# ── 微型台指規格 ──────────────────────────────────────────
POINT_VALUE = 10
COMMISSION  = 120
TAX_RATE    = 0.00002

# ── 布林通道參數 ──────────────────────────────────────────
BB_PERIOD = 20
BB_STD    = 2.0


# ── 資料下載 ──────────────────────────────────────────────
def download_data(years: int = 2):
    ticker = "^TWII"
    end    = datetime.now()
    start  = end - timedelta(days=365 * years)
    h_start = end - timedelta(days=700)

    print(f"下載日K（{years}年）...")
    daily = yf.download(ticker, start=start, end=end,
                        interval="1d", auto_adjust=True, progress=False)
    print(f"下載60分K（700天）...")
    hourly = yf.download(ticker, start=h_start, end=end,
                         interval="1h", auto_adjust=True, progress=False)

    for df in [daily, hourly]:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

    daily  = daily.dropna()
    hourly = hourly.dropna()
    print(f"日K：{len(daily)} 根  |  60分K：{len(hourly)} 根\n")
    return daily, hourly


# ── 指標計算 ──────────────────────────────────────────────
def add_indicators(daily, hourly):
    # 日K SMA20 方向
    daily["SMA20_D"] = daily["Close"].rolling(20).mean()
    daily["trend"]   = np.where(
        daily["SMA20_D"] > daily["SMA20_D"].shift(1), 1,
        np.where(daily["SMA20_D"] < daily["SMA20_D"].shift(1), -1, 0)
    )

    # 60分K 布林通道
    hourly["BB_mid"]   = hourly["Close"].rolling(BB_PERIOD).mean()
    hourly["BB_std"]   = hourly["Close"].rolling(BB_PERIOD).std()
    hourly["BB_upper"] = hourly["BB_mid"] + BB_STD * hourly["BB_std"]
    hourly["BB_lower"] = hourly["BB_mid"] - BB_STD * hourly["BB_std"]
    hourly["BB_width"] = hourly["BB_upper"] - hourly["BB_lower"]

    return daily, hourly


# ── 績效計算 ──────────────────────────────────────────────
def calc_stats(trades: pd.DataFrame, label: str):
    if trades.empty:
        print(f"  {label}：無交易紀錄")
        return {}
    wins  = trades[trades["損益金額"] > 0]
    loses = trades[trades["損益金額"] <= 0]
    wr    = len(wins) / len(trades) * 100
    total = trades["損益金額"].sum()
    aw    = wins["損益金額"].mean()  if len(wins)  else 0
    al    = loses["損益金額"].mean() if len(loses) else 0
    pf    = abs(wins["損益金額"].sum() / loses["損益金額"].sum()) \
            if loses["損益金額"].sum() != 0 else float("inf")
    pnl   = trades["損益金額"].tolist()
    mdd = cur = 0
    for p in pnl:
        cur = cur + p if p < 0 else 0
        mdd = min(mdd, cur)
    return {"label": label, "筆數": len(trades), "勝率": wr,
            "總損益": total, "平均獲利": aw, "平均虧損": al,
            "獲利因子": pf, "最大連虧": mdd}


# ── 策略A：均值回歸 ───────────────────────────────────────
def run_mean_reversion(daily, hourly, sl=30) -> pd.DataFrame:
    """
    進場：價格碰到/穿越布林下軌→做多；碰到/穿越布林上軌→做空
    停利：價格回到布林中軌
    停損：固定 sl 點
    過濾：日K SMA20 方向一致
    """
    trades = []
    in_pos = False
    direction = entry_price = stop_loss = take_profit = 0
    entry_time = None

    for i, (ts, row) in enumerate(hourly.iterrows()):
        if pd.isna(row["BB_mid"]):
            continue
        date     = ts.date()
        d_row    = daily[daily.index.date == date]
        if d_row.empty:
            continue
        d_trend  = int(d_row["trend"].iloc[-1])
        close    = float(row["Close"])
        bb_upper = float(row["BB_upper"])
        bb_lower = float(row["BB_lower"])
        bb_mid   = float(row["BB_mid"])

        # 出場
        if in_pos:
            hit_sl = (direction == 1  and close <= stop_loss) or \
                     (direction == -1 and close >= stop_loss)
            hit_tp = (direction == 1  and close >= take_profit) or \
                     (direction == -1 and close <= take_profit)

            if hit_sl or hit_tp:
                ep = stop_loss if hit_sl else take_profit
                pts = (ep - entry_price) * direction
                net = pts * POINT_VALUE - COMMISSION - ep * POINT_VALUE * TAX_RATE
                trades.append({
                    "進場時間": entry_time, "出場時間": ts,
                    "方向": "多" if direction == 1 else "空",
                    "進場價": entry_price, "出場價": ep,
                    "損益點數": round(pts, 1), "損益金額": round(net, 0),
                    "出場原因": "停損" if hit_sl else "停利（中軌）",
                })
                in_pos = False

        # 進場（加入K棒顏色確認：碰下軌需紅K，碰上軌需黑K）
        open_price = float(row["Open"]) if "Open" in row else close
        is_green = close > open_price   # 紅K（收盤 > 開盤）
        is_red   = close < open_price   # 黑K

        if not in_pos:
            if close <= bb_lower and d_trend >= 0 and is_green:   # 碰下軌 + 紅K → 做多
                direction   = 1
                entry_price = close
                stop_loss   = close - sl
                take_profit = bb_mid
                in_pos      = True
                entry_time  = ts
            elif close >= bb_upper and d_trend <= 0 and is_red:   # 碰上軌 + 黑K → 做空
                direction   = -1
                entry_price = close
                stop_loss   = close + sl
                take_profit = bb_mid
                in_pos      = True
                entry_time  = ts

    return pd.DataFrame(trades)


# ── 策略B：突破 ────────────────────────────────────────────
def run_breakout(daily, hourly, sl=20, tp=40) -> pd.DataFrame:
    """
    進場：收盤突破布林上軌→做多；收盤跌破布林下軌→做空
    停利：固定 tp 點
    停損：固定 sl 點
    過濾：日K SMA20 方向一致
    額外過濾：布林通道寬度 > 100（避免擠壓假突破）
    """
    trades = []
    in_pos = False
    direction = entry_price = stop_loss = take_profit = 0
    entry_time = None

    for i, (ts, row) in enumerate(hourly.iterrows()):
        if pd.isna(row["BB_mid"]):
            continue
        date     = ts.date()
        d_row    = daily[daily.index.date == date]
        if d_row.empty:
            continue
        d_trend  = int(d_row["trend"].iloc[-1])
        close    = float(row["Close"])
        bb_upper = float(row["BB_upper"])
        bb_lower = float(row["BB_lower"])
        bb_width = float(row["BB_width"])
        prev_close = float(hourly["Close"].iloc[i-1]) if i > 0 else close

        # 出場
        if in_pos:
            hit_sl = (direction == 1  and close <= stop_loss) or \
                     (direction == -1 and close >= stop_loss)
            hit_tp = (direction == 1  and close >= take_profit) or \
                     (direction == -1 and close <= take_profit)

            if hit_sl or hit_tp:
                ep = stop_loss if hit_sl else take_profit
                pts = (ep - entry_price) * direction
                net = pts * POINT_VALUE - COMMISSION - ep * POINT_VALUE * TAX_RATE
                trades.append({
                    "進場時間": entry_time, "出場時間": ts,
                    "方向": "多" if direction == 1 else "空",
                    "進場價": entry_price, "出場價": ep,
                    "損益點數": round(pts, 1), "損益金額": round(net, 0),
                    "出場原因": "停損" if hit_sl else "停利",
                })
                in_pos = False

        # 進場（需收盤突破，不只是碰到）
        if not in_pos and bb_width > 100:
            if prev_close <= bb_upper and close > bb_upper and d_trend > 0:
                direction   = 1
                entry_price = close
                stop_loss   = close - sl
                take_profit = close + tp
                in_pos      = True
                entry_time  = ts
            elif prev_close >= bb_lower and close < bb_lower and d_trend < 0:
                direction   = -1
                entry_price = close
                stop_loss   = close + sl
                take_profit = close - tp
                in_pos      = True
                entry_time  = ts

    return pd.DataFrame(trades)


# ── 主程式 ────────────────────────────────────────────────
if __name__ == "__main__":
    daily, hourly = download_data(years=2)
    daily, hourly = add_indicators(daily, hourly)

    # 策略A：均值回歸，測試不同停損
    print("═" * 55)
    print("策略A：均值回歸（碰下軌紅K做多 / 碰上軌黑K做空，中軌停利）")
    print("═" * 55)
    print(f"{'停損':>6} {'筆數':>6} {'勝率':>7} {'總損益':>10} {'獲利因子':>9} {'最大連虧':>10}")
    print("─" * 55)
    a_results = []
    for sl in [20, 30, 40, 50, 60, 80, 100]:
        t = run_mean_reversion(daily, hourly, sl=sl)
        s = calc_stats(t, f"A sl={sl}")
        if s:
            print(f"{sl:>6} {s['筆數']:>6} {s['勝率']:>6.1f}% "
                  f"{s['總損益']:>10,.0f} {s['獲利因子']:>9.2f} {s['最大連虧']:>10,.0f}")
            a_results.append(s)
    print("═" * 55)

    # 策略B：突破，測試不同停損/停利
    print(f"\n{'═'*55}")
    print("策略B：突破（收盤穿越布林通道，固定停損停利）")
    print("═" * 55)
    print(f"{'停損':>6} {'停利':>6} {'筆數':>6} {'勝率':>7} {'總損益':>10} {'獲利因子':>9} {'最大連虧':>10}")
    print("─" * 55)
    b_results = []
    for sl, tp in [(15,30),(20,40),(25,50),(30,60),(40,80)]:
        t = run_breakout(daily, hourly, sl=sl, tp=tp)
        s = calc_stats(t, f"B sl={sl} tp={tp}")
        if s:
            print(f"{sl:>6} {tp:>6} {s['筆數']:>6} {s['勝率']:>6.1f}% "
                  f"{s['總損益']:>10,.0f} {s['獲利因子']:>9.2f} {s['最大連虧']:>10,.0f}")
            b_results.append(s)
    print("═" * 55)

    # 總結比較
    all_r = a_results + b_results
    if all_r:
        best = max(all_r, key=lambda x: x["總損益"])
        print(f"\n{'★'*55}")
        print(f"★  整體最佳：{best['label']}")
        print(f"★  筆數 {best['筆數']}  勝率 {best['勝率']:.1f}%  "
              f"總損益 ${best['總損益']:,.0f}  最大連虧 ${best['最大連虧']:,.0f}")
        print(f"{'★'*55}")

    # 輸出最佳組合（策略A 停損100）的交易明細
    best_trades = run_mean_reversion(daily, hourly, sl=100)
    if not best_trades.empty:
        best_trades["進場時間_台灣"] = pd.to_datetime(best_trades["進場時間"]).dt.tz_convert("Asia/Taipei")
        best_trades["出場時間_台灣"] = pd.to_datetime(best_trades["出場時間"]).dt.tz_convert("Asia/Taipei")
        print(f"\n{'─'*75}")
        print("策略A（停損100，紅K確認）交易明細：")
        print(f"{'─'*75}")
        cols = ["進場時間_台灣","出場時間_台灣","方向","進場價","出場價","損益點數","損益金額","出場原因"]
        print(best_trades[cols].to_string(index=False))
        best_trades[cols].to_csv("backtest_bollinger_result.csv", index=False, encoding="utf-8-sig")
        print(f"\n明細已儲存至 backtest_bollinger_result.csv")
