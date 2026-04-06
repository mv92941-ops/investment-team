"""
台指期均線策略回測
策略邏輯：
  - 日K SMA20：過濾大方向
  - 60分K SMA20 + SMA100：確認趨勢、日內留倉判斷
  - 進場：60分K SMA20 穿越 SMA100（黃金/死亡交叉）且日K方向一致
  - 停損：進場後跌破 60分K SMA100（多單）/ 突破 SMA100（空單）
  - 停利：RR 1:2
  - 日內不留倉規則：SMA20 - SMA100 ≤ 100點 時強制當日出場
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

# ── 微型台指規格 ──────────────────────────────────────────
POINT_VALUE    = 10       # 每點 $10
COMMISSION     = 120      # 來回手續費
TAX_RATE       = 0.00002  # 期交稅率
BREAKEVEN_PTS  = 13       # 損益兩平最少需超過的點數
RR_RATIO       = 2.0      # 最低風報比
INTRADAY_GAP   = 100      # SMA20-SMA100 ≤ 此值時強制日內
FIXED_SL       = 20       # 固定停損點數
FIXED_TP       = 40       # 固定停利點數（1:2）


# ── 資料下載 ──────────────────────────────────────────────
def download_data(years: int = 2) -> tuple[pd.DataFrame, pd.DataFrame]:
    ticker = "^TWII"
    end    = datetime.now()
    start  = end - timedelta(days=365 * years)

    print(f"下載 {ticker} 日K（{years}年）...")
    daily = yf.download(ticker, start=start, end=end, interval="1d",
                        auto_adjust=True, progress=False)

    print(f"下載 {ticker} 60分K（最多700天）...")
    h_start = end - timedelta(days=700)
    hourly = yf.download(ticker, start=h_start, end=end, interval="1h",
                         auto_adjust=True, progress=False)

    # 展平多層欄位
    if isinstance(daily.columns, pd.MultiIndex):
        daily.columns  = daily.columns.get_level_values(0)
    if isinstance(hourly.columns, pd.MultiIndex):
        hourly.columns = hourly.columns.get_level_values(0)

    daily  = daily.dropna()
    hourly = hourly.dropna()
    print(f"日K：{len(daily)} 根  |  60分K：{len(hourly)} 根")
    return daily, hourly


# ── 指標計算 ──────────────────────────────────────────────
def add_indicators(daily: pd.DataFrame, hourly: pd.DataFrame):
    # 日K
    daily["SMA20_D"] = daily["Close"].rolling(20).mean()
    daily["trend"]   = np.where(
        daily["SMA20_D"] > daily["SMA20_D"].shift(1), 1,   # 上升
        np.where(daily["SMA20_D"] < daily["SMA20_D"].shift(1), -1, 0)  # 下降
    )

    # 60分K
    hourly["SMA20_H"]  = hourly["Close"].rolling(20).mean()
    hourly["SMA100_H"] = hourly["Close"].rolling(100).mean()
    hourly["gap"]      = hourly["SMA20_H"] - hourly["SMA100_H"]

    # 黃金/死亡交叉
    hourly["cross"] = np.where(
        (hourly["SMA20_H"] > hourly["SMA100_H"]) &
        (hourly["SMA20_H"].shift(1) <= hourly["SMA100_H"].shift(1)), 1,
        np.where(
            (hourly["SMA20_H"] < hourly["SMA100_H"]) &
            (hourly["SMA20_H"].shift(1) >= hourly["SMA100_H"].shift(1)), -1, 0
        )
    )
    return daily, hourly


# ── 回測引擎 ──────────────────────────────────────────────
def run_backtest(daily: pd.DataFrame, hourly: pd.DataFrame,
                 sl: int = None, tp: int = None,
                 entry_hour: int = None,
                 gap_threshold: int = 0,
                 price_confirm: bool = False,
                 ma_align: bool = False) -> pd.DataFrame:
    """
    gap_threshold : A. 趨勢強度過濾，SMA20-SMA100 需 > 此值才進場
    price_confirm : B. 價格位置確認，多單需 close > SMA20，空單需 close < SMA20
    entry_hour    : C. 時間過濾（UTC小時，台灣09:30 = UTC 01）
    ma_align      : D. 均線方向一致，SMA20 和 SMA100 需同向才進場
    """
    sl = sl if sl is not None else FIXED_SL
    tp = tp if tp is not None else FIXED_TP
    trades = []
    in_position = False
    entry_price = stop_loss = take_profit = 0
    direction   = 0
    entry_time  = None
    is_intraday = False

    for i, (ts, row) in enumerate(hourly.iterrows()):
        if pd.isna(row["SMA20_H"]) or pd.isna(row["SMA100_H"]):
            continue

        # 取當日日K趨勢
        date = ts.date()
        daily_row = daily[daily.index.date == date]
        if daily_row.empty:
            continue
        d_trend = int(daily_row["trend"].iloc[-1])

        close = float(row["Close"])
        sma20 = float(row["SMA20_H"])
        sma100 = float(row["SMA100_H"])
        gap   = float(row["gap"])

        # ── 出場邏輯 ─────────────────────────────────────
        if in_position:
            hit_sl = hit_tp = force_exit = False

            if direction == 1:   # 多單
                hit_sl = close <= stop_loss
                hit_tp = close >= take_profit
            else:                # 空單
                hit_sl = close >= stop_loss
                hit_tp = close <= take_profit

            # 日內強制出場（尾盤最後一根）
            if is_intraday:
                next_rows = hourly.iloc[i+1:i+3] if i+1 < len(hourly) else pd.DataFrame()
                if next_rows.empty or next_rows.index[0].date() != date:
                    force_exit = True

            exit_price = None
            exit_reason = ""
            if hit_sl:
                exit_price  = stop_loss
                exit_reason = "停損"
            elif hit_tp:
                exit_price  = take_profit
                exit_reason = "停利"
            elif force_exit:
                exit_price  = close
                exit_reason = "日內強制出場"

            if exit_price:
                pts   = (exit_price - entry_price) * direction
                tax   = exit_price * POINT_VALUE * TAX_RATE
                gross = pts * POINT_VALUE
                net   = gross - COMMISSION - tax
                trades.append({
                    "進場時間": entry_time,
                    "出場時間": ts,
                    "方向":    "多" if direction == 1 else "空",
                    "進場價":  entry_price,
                    "出場價":  exit_price,
                    "損益點數": round(pts, 1),
                    "損益金額": round(net, 0),
                    "出場原因": exit_reason,
                    "日內":    is_intraday,
                })
                in_position = False

        # ── 進場邏輯 ─────────────────────────────────────
        if entry_hour is not None and ts.hour != entry_hour:
            continue

        if not in_position and row["cross"] != 0:
            signal = int(row["cross"])

            # 日K方向過濾
            if signal == 1 and d_trend < 0:
                continue
            if signal == -1 and d_trend > 0:
                continue

            # A. 趨勢強度過濾
            if abs(gap) < gap_threshold:
                continue

            # B. 價格位置確認
            if price_confirm:
                if signal == 1 and close < sma20:
                    continue
                if signal == -1 and close > sma20:
                    continue

            # D. 均線方向一致
            if ma_align:
                sma20_up  = sma20  > float(hourly["SMA20_H"].shift(3).iloc[i])  if i >= 3 else False
                sma100_up = sma100 > float(hourly["SMA100_H"].shift(3).iloc[i]) if i >= 3 else False
                if signal == 1 and not (sma20_up and sma100_up):
                    continue
                if signal == -1 and (sma20_up or sma100_up):
                    continue

            if signal == 1:   # 做多
                stop_loss    = close - sl
                take_profit  = close + tp
            else:             # 做空
                stop_loss    = close + sl
                take_profit  = close - tp

            in_position = True
            direction   = signal
            entry_price = close
            entry_time  = ts
            is_intraday = abs(gap) <= INTRADAY_GAP

    return pd.DataFrame(trades)


# ── 績效報表 ──────────────────────────────────────────────
def performance_report(trades: pd.DataFrame):
    if trades.empty:
        print("無交易紀錄")
        return

    wins  = trades[trades["損益金額"] > 0]
    loses = trades[trades["損益金額"] <= 0]

    total_net   = trades["損益金額"].sum()
    win_rate    = len(wins) / len(trades) * 100
    avg_win     = wins["損益金額"].mean() if len(wins) else 0
    avg_loss    = loses["損益金額"].mean() if len(loses) else 0
    profit_factor = abs(wins["損益金額"].sum() / loses["損益金額"].sum()) \
                    if loses["損益金額"].sum() != 0 else float("inf")

    # 最大連續虧損
    pnl = trades["損益金額"].tolist()
    max_dd = cur_dd = 0
    for p in pnl:
        if p < 0:
            cur_dd += p
            max_dd = min(max_dd, cur_dd)
        else:
            cur_dd = 0

    print("\n" + "═" * 45)
    print("📊  均線策略回測結果")
    print("═" * 45)
    print(f"  總交易筆數   : {len(trades)}")
    print(f"  勝率         : {win_rate:.1f}%  ({len(wins)}勝 {len(loses)}敗)")
    print(f"  總淨損益     : ${total_net:,.0f}")
    print(f"  平均獲利     : ${avg_win:,.0f}")
    print(f"  平均虧損     : ${avg_loss:,.0f}")
    print(f"  獲利因子     : {profit_factor:.2f}")
    print(f"  最大連續虧損 : ${max_dd:,.0f}")
    print("─" * 45)

    by_reason = trades.groupby("出場原因")["損益金額"].agg(["count", "sum", "mean"])
    print("\n出場原因分析：")
    print(by_reason.to_string())

    intra = trades[trades["日內"] == True]
    swing = trades[trades["日內"] == False]
    print(f"\n  日內交易筆數 : {len(intra)}  淨損益 ${intra['損益金額'].sum():,.0f}")
    print(f"  波段交易筆數 : {len(swing)}  淨損益 ${swing['損益金額'].sum():,.0f}")
    print("═" * 45)

    return trades


# ── 主程式 ────────────────────────────────────────────────
if __name__ == "__main__":
    daily, hourly = download_data(years=2)
    daily, hourly = add_indicators(daily, hourly)

    # 測試不同停損/停利組合
    combos = [
        (15, 30),
        (20, 40),
        (25, 50),
        (30, 60),
        (40, 80),
        (50, 100),
    ]

    def scan(label, **kwargs):
        print(f"\n{'═'*62}")
        print(f"🔍  {label}")
        print(f"{'═'*62}")
        print(f"{'停損':>6} {'停利':>6} {'筆數':>6} {'勝率':>7} {'總損益':>10} {'平均獲利':>9} {'平均虧損':>9} {'最大連虧':>10}")
        print("─" * 62)
        results = []
        for sl, tp in combos:
            t = run_backtest(daily, hourly, sl=sl, tp=tp, **kwargs)
            if t.empty:
                continue
            wins  = t[t["損益金額"] > 0]
            loses = t[t["損益金額"] <= 0]
            wr    = len(wins) / len(t) * 100
            total = t["損益金額"].sum()
            aw    = wins["損益金額"].mean() if len(wins) else 0
            al    = loses["損益金額"].mean() if len(loses) else 0
            pnl   = t["損益金額"].tolist()
            mdd = cur = 0
            for p in pnl:
                cur = cur + p if p < 0 else 0
                mdd = min(mdd, cur)
            print(f"{sl:>6} {tp:>6} {len(t):>6} {wr:>6.1f}% {total:>10,.0f} {aw:>9,.0f} {al:>9,.0f} {mdd:>10,.0f}")
            results.append({"label": label, "停損": sl, "停利": tp, "勝率": wr, "總損益": total, "最大連虧": mdd, "kwargs": kwargs})
        print("═" * 62)
        if results:
            best = max(results, key=lambda x: x["總損益"])
            print(f"最佳：停損 {best['停損']}點 / 停利 {best['停利']}點  →  總損益 ${best['總損益']:,.0f}  勝率 {best['勝率']:.1f}%")
        return results

    all_results = []
    all_results += scan("基準（無過濾）")
    all_results += scan("A. 趨勢強度（gap>50）", gap_threshold=50)
    all_results += scan("B. 價格位置確認", price_confirm=True)
    all_results += scan("C. 09:30進場", entry_hour=1)
    all_results += scan("D. 均線方向一致", ma_align=True)
    all_results += scan("全部過濾條件（A+B+C+D）",
                        gap_threshold=50, price_confirm=True,
                        entry_hour=1, ma_align=True)

    # 全部結果中找最佳
    if all_results:
        champion = max(all_results, key=lambda x: x["總損益"])
        print(f"\n{'★'*62}")
        print(f"★  整體最佳：{champion['label']}")
        print(f"★  停損 {champion['停損']}點 / 停利 {champion['停利']}點")
        print(f"★  總損益 ${champion['總損益']:,.0f}  勝率 {champion['勝率']:.1f}%  最大連虧 ${champion['最大連虧']:,.0f}")
        print(f"{'★'*62}")

        # 儲存最佳明細
        best_t = run_backtest(daily, hourly, sl=champion["停損"],
                              tp=champion["停利"], **champion["kwargs"])
        if not best_t.empty:
            best_t.to_csv("backtest_result.csv", index=False, encoding="utf-8-sig")
            print("最佳組合交易明細已儲存至 backtest_result.csv")
