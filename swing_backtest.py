"""
波段輔助指標 仿老貓版 — Python 回測
=====================================
策略：用波段高低點方向作為 ORB 進場濾網
  多頭結構（低點墊高、高點墊高）→ 只做多
  空頭結構（高點降低、低點降低）→ 只做空
  無明確結構 → 不操作

執行：python swing_backtest.py
"""

import yfinance as yf
import pandas as pd
import numpy as np
import warnings
import os
warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════
# 策略參數
# ═══════════════════════════════════════════
ORB_PARAMS = {
    "buffer_pts":     5,
    "stop_max_pts":   40,
    "tp1_ratio":      0.8,
    "tp2_trail_pct":  0.25,
    "max_daily_stop": 2,
    "orb_min_pts":    20,
    "orb_max_pts":    120,
    "ema_period":     20,
    "mtx_per_pt":     10,
    "n_contracts":    1,
}

SWING_PARAMS = {
    "swing_period": 50,   # 波段週期
    "speed":        20,   # 速度調整（ATR週期）
    "vol_adj":      True, # 開啟波動調整
    "vol_dev":      10,   # 波動偏差
}

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


# ═══════════════════════════════════════════
# 波段高低點偵測（仿老貓邏輯）
# ═══════════════════════════════════════════
def detect_swings(df, sp):
    """
    自適應 ZigZag 波段偵測
    回傳：swing_high[], swing_low[], swing_trend[]
    """
    highs  = df["High"].values.astype(float)
    lows   = df["Low"].values.astype(float)
    closes = df["Close"].values.astype(float)
    n      = len(df)

    lb = sp["swing_period"] // 2   # 左側確認 bars
    rb = sp["speed"] // 2          # 右側確認 bars（速度調整）

    # ATR
    tr = np.maximum(highs - lows,
         np.maximum(np.abs(highs - np.roll(closes, 1)),
                    np.abs(lows  - np.roll(closes, 1))))
    tr[0] = highs[0] - lows[0]
    atr = pd.Series(tr).rolling(sp["speed"]).mean().values

    # 最小波動門檻
    min_move = atr * (sp["vol_dev"] / 10.0) if sp["vol_adj"] else np.full(n, sp["vol_dev"])

    swing_h   = np.full(n, np.nan)
    swing_l   = np.full(n, np.nan)
    swing_trend = np.zeros(n, dtype=int)  # 1=多頭, -1=空頭, 0=不明

    last_ph = last_pl = np.nan
    last_ph_i = last_pl_i = -1

    for i in range(lb, n - rb):
        window_h = highs[i - lb: i + rb + 1]
        window_l = lows [i - lb: i + rb + 1]

        # 波段高點
        if highs[i] >= np.max(window_h):
            if np.isnan(last_pl) or (highs[i] - last_pl) >= min_move[i]:
                swing_h[i] = highs[i]
                last_ph    = highs[i]
                last_ph_i  = i

        # 波段低點
        if lows[i] <= np.min(window_l):
            if np.isnan(last_ph) or (last_ph - lows[i]) >= min_move[i]:
                swing_l[i] = lows[i]
                last_pl    = lows[i]
                last_pl_i  = i

    # ── 趨勢判斷：最近兩個波段高低點的序列 ────────
    ph_list = [(i, v) for i, v in enumerate(swing_h) if not np.isnan(v)]
    pl_list = [(i, v) for i, v in enumerate(swing_l) if not np.isnan(v)]

    current_trend = 0
    pi = pi2 = 0  # pointer for ph_list, pl_list

    for i in range(n):
        # 多頭：高點墊高 + 低點墊高
        if len(ph_list) >= 2 and len(pl_list) >= 2:
            # 最近兩個高點和低點
            recent_ph = [v for idx, v in ph_list if idx <= i][-2:] if len([v for idx, v in ph_list if idx <= i]) >= 2 else []
            recent_pl = [v for idx, v in pl_list if idx <= i][-2:] if len([v for idx, v in pl_list if idx <= i]) >= 2 else []

            if len(recent_ph) == 2 and len(recent_pl) == 2:
                if recent_ph[1] > recent_ph[0] and recent_pl[1] > recent_pl[0]:
                    current_trend = 1
                elif recent_ph[1] < recent_ph[0] and recent_pl[1] < recent_pl[0]:
                    current_trend = -1

        swing_trend[i] = current_trend

    return swing_h, swing_l, swing_trend


# ═══════════════════════════════════════════
# ORB 日內回測（含波段濾網）
# ═══════════════════════════════════════════
def run_orb_with_swing(df, p, swing_trend):
    """
    逐日 ORB 回測，進場前加上波段趨勢確認
    """
    trades = []

    for date, day_df in df.groupby("date"):
        bars = list(day_df.sort_index().itertuples())
        if len(bars) < 2:
            continue

        orb     = bars[0]
        orb_rng = orb.High - orb.Low
        if not (p["orb_min_pts"] <= orb_rng <= p["orb_max_pts"]):
            continue

        orb_mid     = (orb.High + orb.Low) / 2
        long_entry  = orb.High + p["buffer_pts"]
        short_entry = orb.Low  - p["buffer_pts"]
        tp1_pts     = orb_rng * p["tp1_ratio"]

        long_stop   = max(orb_mid, long_entry  - p["stop_max_pts"])
        short_stop  = min(orb_mid, short_entry + p["stop_max_pts"])
        long_tp1    = long_entry  + tp1_pts
        short_tp1   = short_entry - tp1_pts

        # 取第一根 K 棒的波段趨勢
        bar0_idx     = day_df.index[0]
        df_idx       = df.index.get_loc(bar0_idx)
        day_trend    = swing_trend[df_idx] if df_idx < len(swing_trend) else 0
        ema_trend    = "up" if orb.Close > orb.ema else "down"

        position     = None
        entry_price  = 0.0
        stop_price   = 0.0
        tp1_price    = 0.0
        tp1_hit      = False
        trail_ext    = 0.0
        stop_count   = 0

        for bar in bars[1:]:
            if stop_count >= p["max_daily_stop"]:
                break

            # ── 進場：ORB 方向 + EMA 趨勢 + 波段結構三重確認 ──
            if position is None:
                long_ok  = (bar.High >= long_entry  and ema_trend == "up"
                            and (day_trend == 1 or day_trend == 0))
                short_ok = (bar.Low  <= short_entry and ema_trend == "down"
                            and (day_trend == -1 or day_trend == 0))

                if long_ok:
                    position    = "long"
                    entry_price = long_entry
                    stop_price  = long_stop
                    tp1_price   = long_tp1
                    trail_ext   = entry_price
                    tp1_hit     = False
                elif short_ok:
                    position    = "short"
                    entry_price = short_entry
                    stop_price  = short_stop
                    tp1_price   = short_tp1
                    trail_ext   = entry_price
                    tp1_hit     = False

            elif position == "long":
                trail_ext = max(trail_ext, bar.High)
                if bar.Low <= stop_price:
                    pnl = (stop_price - entry_price) * p["mtx_per_pt"]
                    trades.append({"dir": "long", "entry": entry_price, "exit": stop_price,
                                   "pnl": pnl, "result": "stop", "trend": day_trend})
                    position = None; stop_count += 1
                elif not tp1_hit and bar.High >= tp1_price:
                    pnl = tp1_pts * p["mtx_per_pt"] * 0.5
                    trades.append({"dir": "long", "entry": entry_price, "exit": tp1_price,
                                   "pnl": pnl, "result": "tp1", "trend": day_trend})
                    tp1_hit = True; stop_price = entry_price
                elif tp1_hit and bar.Low <= trail_ext * (1 - p["tp2_trail_pct"]):
                    tp2_exit = trail_ext * (1 - p["tp2_trail_pct"])
                    pnl = (tp2_exit - entry_price) * p["mtx_per_pt"] * 0.5
                    trades.append({"dir": "long", "entry": entry_price, "exit": tp2_exit,
                                   "pnl": pnl, "result": "tp2", "trend": day_trend})
                    position = None

            elif position == "short":
                trail_ext = min(trail_ext, bar.Low)
                if bar.High >= stop_price:
                    pnl = (entry_price - stop_price) * p["mtx_per_pt"]
                    trades.append({"dir": "short", "entry": entry_price, "exit": stop_price,
                                   "pnl": pnl, "result": "stop", "trend": day_trend})
                    position = None; stop_count += 1
                elif not tp1_hit and bar.Low <= tp1_price:
                    pnl = tp1_pts * p["mtx_per_pt"] * 0.5
                    trades.append({"dir": "short", "entry": entry_price, "exit": tp1_price,
                                   "pnl": pnl, "result": "tp1", "trend": day_trend})
                    tp1_hit = True; stop_price = entry_price
                elif tp1_hit and bar.High >= trail_ext * (1 + p["tp2_trail_pct"]):
                    tp2_exit = trail_ext * (1 + p["tp2_trail_pct"])
                    pnl = (entry_price - tp2_exit) * p["mtx_per_pt"] * 0.5
                    trades.append({"dir": "short", "entry": entry_price, "exit": tp2_exit,
                                   "pnl": pnl, "result": "tp2", "trend": day_trend})
                    position = None

        # 收盤強制平倉
        if position is not None:
            lc   = bars[-1].Close
            size = 0.5 if tp1_hit else 1.0
            pnl  = (lc - entry_price if position == "long" else entry_price - lc)
            pnl *= p["mtx_per_pt"] * size
            trades.append({"dir": position, "entry": entry_price, "exit": lc,
                           "pnl": pnl, "result": "close", "trend": day_trend})

    return trades


# ═══════════════════════════════════════════
# 主程式
# ═══════════════════════════════════════════
def main():
    print("=" * 55)
    print("  波段濾網 × ORB 組合策略回測")
    print("=" * 55)

    print("\n[1] 下載資料...")
    df = yf.Ticker("^TWII").history(period="2y", interval="1h")
    if df.empty:
        print("❌ 無法下載資料"); return
    if hasattr(df.index, "tz") and df.index.tz:
        df.index = df.index.tz_localize(None)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df["ema"] = df["Close"].ewm(span=ORB_PARAMS["ema_period"], adjust=False).mean()
    df["date"] = df.index.date
    print(f"   {df.index[0].date()} ~ {df.index[-1].date()}，{len(df['date'].unique())} 交易日")

    print("\n[2] 計算波段高低點...")
    swing_h, swing_l, swing_trend = detect_swings(df, SWING_PARAMS)
    ph_count = int(np.sum(~np.isnan(swing_h)))
    pl_count = int(np.sum(~np.isnan(swing_l)))
    bull_days = int(np.sum(swing_trend == 1))
    bear_days = int(np.sum(swing_trend == -1))
    print(f"   波段高點：{ph_count} 個，波段低點：{pl_count} 個")
    print(f"   多頭結構：{bull_days} 根K棒，空頭結構：{bear_days} 根K棒")

    print("\n[3] 執行ORB+波段組合回測...")
    trades = run_orb_with_swing(df, ORB_PARAMS, swing_trend)

    print("\n[4] 比較結果（純ORB vs 加波段濾網）...")

    # 純 ORB 回測（無波段濾網，供對比）
    import importlib.util
    spec = importlib.util.spec_from_file_location("orb", os.path.join(OUTPUT_DIR, "orb_backtest.py"))
    orb_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(orb_mod)
    df2 = orb_mod.add_trend_filter(df.copy(), ORB_PARAMS["ema_period"])
    base_trades = []
    for date, day_df in df2.groupby("date"):
        base_trades.extend(orb_mod.run_daily(date, day_df, ORB_PARAMS))

    def stats(t_list, label):
        if not t_list:
            print(f"  {label}: 無交易"); return
        rdf = pd.DataFrame(t_list)
        total = len(rdf)
        wins  = rdf[rdf.pnl > 0]
        losses = rdf[rdf.pnl < 0]
        wr = len(wins) / total * 100
        pf = abs(wins.pnl.sum() / losses.pnl.sum()) if losses.pnl.sum() != 0 else 9.99
        total_pnl = rdf.pnl.sum()
        rdf["cum"] = rdf.pnl.cumsum()
        maxdd = (rdf.cum - rdf.cum.cummax()).min()
        print(f"\n  ── {label} ──")
        print(f"  交易筆數：{total}  勝率：{wr:.1f}%  獲利因子：{pf:.2f}")
        print(f"  總損益：NT${total_pnl:,.0f}  最大回撤：NT${maxdd:,.0f}")

    stats(base_trades, "純 ORB（原版）")
    stats(trades,      "ORB × 波段濾網（新版）")

    # 儲存明細
    if trades:
        pd.DataFrame(trades).to_csv(
            os.path.join(OUTPUT_DIR, "swing_result.csv"),
            index=False, encoding="utf-8-sig")
        print(f"\n  明細已儲存：swing_result.csv")

    print("\n" + "=" * 55)


if __name__ == "__main__":
    main()
