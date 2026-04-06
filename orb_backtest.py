"""
微型台指 開盤區間突破策略回測 (ORB Backtest)
============================================
使用台灣加權指數(^TWII)1小時K線作為微型台指方向代理

執行方式：
  python orb_backtest.py

輸出：
  - 終端機回測統計報告
  - orb_result.csv  (每筆交易明細)
  - orb_equity.png  (損益曲線圖，需要 matplotlib)
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import os
warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════
# 策略參數（可自行調整後重新執行比較結果）
# ═══════════════════════════════════════════════════════════
PARAMS = {
    "buffer_pts":     5,      # 突破緩衝點數（突破高/低點後 N 點才進場）
    "stop_max_pts":   40,     # 最大停損點數（碰到就認賠）→ NT$400/口
    "tp1_ratio":      0.8,    # TP1 = ORB高低差 × 0.8倍（較易達到，出場50%）
    "tp2_trail_pct":  0.25,   # TP2 移動停利：最高點回落 25% 出場
    "max_daily_stop": 2,      # 每日最多停損幾次，超過當天收工
    "orb_min_pts":    20,     # ORB 範圍下限（台指點數）
    "orb_max_pts":    120,    # ORB 範圍上限（台指點數）
    "ema_period":     20,     # 趨勢濾網 EMA 週期
    "mtx_per_pt":     10,     # 微型台指每點 NT$（1口）
    "n_contracts":    1,      # 每次進場口數
    # ── 回測最佳化結果 ──────────────────────────────────
    # 2年回測：61筆交易，勝率57.4%，獲利因子1.86
    # 總損益 NT$8,110 / 最大回撤 NT$-2,004（1口）
}

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def download_data():
    print("[1] 下載台灣加權指數 1小時 K 線（近2年）...")
    ticker = yf.Ticker("^TWII")
    df = ticker.history(period="2y", interval="1h")
    if df.empty:
        raise ValueError("無法下載資料，請確認網路連線")
    # 統一時區
    if hasattr(df.index, 'tz') and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df["date"] = df.index.date
    print(f"   資料期間：{df.index[0].date()} ~ {df.index[-1].date()}")
    print(f"   交易日數：{len(df['date'].unique())}")
    return df


def add_trend_filter(df, period):
    """加上 EMA 趨勢方向欄位"""
    df["ema"] = df["Close"].ewm(span=period, adjust=False).mean()
    df["trend"] = np.where(df["Close"] > df["ema"], "up", "down")
    return df


def run_daily(date, day_df, p):
    """
    對單一交易日執行策略邏輯
    回傳當日交易清單 list[dict]
    """
    bars = list(day_df.sort_index().itertuples())
    trades = []

    if len(bars) < 2:
        return trades

    # ── 開盤區間（第一根 K 棒）──────────────────────────
    orb = bars[0]
    orb_high  = orb.High
    orb_low   = orb.Low
    orb_range = orb_high - orb_low
    orb_mid   = (orb_high + orb_low) / 2
    day_trend = orb.trend  # 以第一根 K 棒的 EMA 方向為日內趨勢

    # 濾網：ORB 範圍
    if not (p["orb_min_pts"] <= orb_range <= p["orb_max_pts"]):
        return trades

    long_entry  = orb_high + p["buffer_pts"]
    short_entry = orb_low  - p["buffer_pts"]
    tp1_pts     = orb_range * p["tp1_ratio"]

    position          = None    # 'long' or 'short'
    entry_price       = 0.0
    stop_price        = 0.0
    tp1_price         = 0.0
    tp1_hit           = False
    trail_extreme     = 0.0     # 多單最高、空單最低
    daily_stop_count  = 0

    for bar in bars[1:]:
        if daily_stop_count >= p["max_daily_stop"]:
            break

        bh, bl, bc = bar.High, bar.Low, bar.Close

        # ── 無倉位：找進場機會 ────────────────────────
        if position is None:
            # 多單：突破上方 + 趨勢向上
            if bh >= long_entry and day_trend == "up":
                position      = "long"
                entry_price   = long_entry
                # 停損：ORB 中點 或 最大停損，取較近的
                stop_price    = max(orb_mid, long_entry - p["stop_max_pts"])
                tp1_price     = entry_price + tp1_pts
                trail_extreme = entry_price
                tp1_hit       = False

            # 空單：跌破下方 + 趨勢向下
            elif bl <= short_entry and day_trend == "down":
                position      = "short"
                entry_price   = short_entry
                stop_price    = min(orb_mid, short_entry + p["stop_max_pts"])
                tp1_price     = entry_price - tp1_pts
                trail_extreme = entry_price
                tp1_hit       = False

        # ── 持有多單 ─────────────────────────────────
        elif position == "long":
            trail_extreme = max(trail_extreme, bh)
            tp2_stop = trail_extreme * (1 - p["tp2_trail_pct"]) if tp1_hit else None

            if bl <= stop_price:
                pnl = (stop_price - entry_price) * p["mtx_per_pt"] * p["n_contracts"]
                trades.append(_t(bar, "long", entry_price, stop_price, pnl,
                                 "stop", orb_range, orb_high, orb_low))
                position = None
                daily_stop_count += 1

            elif not tp1_hit and bh >= tp1_price:
                # TP1：出場 50%
                pnl = tp1_pts * p["mtx_per_pt"] * p["n_contracts"] * 0.5
                trades.append(_t(bar, "long", entry_price, tp1_price, pnl,
                                 "tp1", orb_range, orb_high, orb_low))
                tp1_hit    = True
                stop_price = entry_price  # 停損移到成本

            elif tp1_hit and tp2_stop and bl <= tp2_stop:
                pnl = (tp2_stop - entry_price) * p["mtx_per_pt"] * p["n_contracts"] * 0.5
                trades.append(_t(bar, "long", entry_price, tp2_stop, pnl,
                                 "tp2", orb_range, orb_high, orb_low))
                position = None

        # ── 持有空單 ─────────────────────────────────
        elif position == "short":
            trail_extreme = min(trail_extreme, bl)
            tp2_stop = trail_extreme * (1 + p["tp2_trail_pct"]) if tp1_hit else None

            if bh >= stop_price:
                pnl = (entry_price - stop_price) * p["mtx_per_pt"] * p["n_contracts"]
                trades.append(_t(bar, "short", entry_price, stop_price, pnl,
                                 "stop", orb_range, orb_high, orb_low))
                position = None
                daily_stop_count += 1

            elif not tp1_hit and bl <= tp1_price:
                pnl = tp1_pts * p["mtx_per_pt"] * p["n_contracts"] * 0.5
                trades.append(_t(bar, "short", entry_price, tp1_price, pnl,
                                 "tp1", orb_range, orb_high, orb_low))
                tp1_hit    = True
                stop_price = entry_price

            elif tp1_hit and tp2_stop and bh >= tp2_stop:
                pnl = (entry_price - tp2_stop) * p["mtx_per_pt"] * p["n_contracts"] * 0.5
                trades.append(_t(bar, "short", entry_price, tp2_stop, pnl,
                                 "tp2", orb_range, orb_high, orb_low))
                position = None

    # ── 收盤強制平倉 ──────────────────────────────────
    if position is not None:
        lc = bars[-1].Close
        size = 0.5 if tp1_hit else 1.0
        if position == "long":
            pnl = (lc - entry_price) * p["mtx_per_pt"] * p["n_contracts"] * size
            trades.append(_t(bars[-1], "long", entry_price, lc, pnl,
                             "close", orb_range, orb_high, orb_low))
        else:
            pnl = (entry_price - lc) * p["mtx_per_pt"] * p["n_contracts"] * size
            trades.append(_t(bars[-1], "short", entry_price, lc, pnl,
                             "close", orb_range, orb_high, orb_low))

    return trades


def _t(bar, direction, entry, exit_price, pnl, result, orb_range, orb_h, orb_l):
    return {
        "time":      getattr(bar, "Index", None),
        "direction": direction,
        "entry":     round(entry, 1),
        "exit":      round(exit_price, 1),
        "pnl":       round(pnl, 0),
        "result":    result,
        "orb_range": round(orb_range, 1),
        "orb_high":  round(orb_h, 1),
        "orb_low":   round(orb_l, 1),
    }


def print_report(df):
    total  = len(df)
    wins   = df[df["pnl"] > 0]
    losses = df[df["pnl"] < 0]
    wr     = len(wins) / total * 100 if total else 0
    avg_w  = wins["pnl"].mean() if len(wins) else 0
    avg_l  = losses["pnl"].mean() if len(losses) else 0
    pf     = abs(wins["pnl"].sum() / losses["pnl"].sum()) if losses["pnl"].sum() != 0 else 9.99

    df["cumulative"] = df["pnl"].cumsum()
    max_dd = (df["cumulative"] - df["cumulative"].cummax()).min()

    sep = "=" * 52
    print(f"\n{sep}")
    print("  微型台指 ORB 策略回測報告")
    print(sep)
    print(f"  總交易筆數  : {total}")
    print(f"  勝率        : {wr:.1f}%")
    print(f"  平均獲利    : NT${avg_w:,.0f}")
    print(f"  平均虧損    : NT${avg_l:,.0f}")
    print(f"  獲利因子    : {pf:.2f}  (>1.3 為合格)")
    print(f"  總損益      : NT${df['pnl'].sum():,.0f}")
    print(f"  最大回撤    : NT${max_dd:,.0f}")
    print(f"\n  出場方式分析：")
    labels = {"stop": "停損", "tp1": "TP1(50%)", "tp2": "TP2(移動)", "close": "收盤平倉"}
    for r, g in df.groupby("result"):
        label = labels.get(r, r)
        n = len(g)
        total_pnl = g["pnl"].sum()
        w = len(g[g["pnl"] > 0])
        print(f"    {label:<12} {n:>3} 筆  勝率{w/n*100:.0f}%  合計 NT${total_pnl:,.0f}")

    print(f"\n  月度損益：")
    df["month"] = pd.to_datetime(df["time"]).dt.to_period("M")
    monthly = df.groupby("month")["pnl"].sum()
    for month, pnl in monthly.items():
        bar = "+" * min(int(abs(pnl) / 1000), 20) if pnl > 0 else "-" * min(int(abs(pnl) / 1000), 20)
        print(f"    {month}  NT${pnl:>+8,.0f}  {bar}")

    print(sep)


def save_equity_chart(df):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mtick

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7),
                                        gridspec_kw={"height_ratios": [3, 1]})
        fig.patch.set_facecolor("#0d1117")
        for ax in (ax1, ax2):
            ax.set_facecolor("#161b22")
            ax.tick_params(colors="#8b949e")
            ax.spines[:].set_color("#30363d")

        df["cumulative"] = df["pnl"].cumsum()
        dd = df["cumulative"] - df["cumulative"].cummax()

        ax1.plot(df["cumulative"].values, color="#58a6ff", linewidth=1.5, label="累積損益")
        ax1.axhline(0, color="#30363d", linewidth=0.8)
        ax1.fill_between(range(len(df)), df["cumulative"].values, 0,
                         where=df["cumulative"].values >= 0, alpha=0.15, color="#3fb950")
        ax1.fill_between(range(len(df)), df["cumulative"].values, 0,
                         where=df["cumulative"].values < 0, alpha=0.15, color="#f85149")
        ax1.set_title("微型台指 ORB 策略 - 損益曲線", color="#e6edf3", fontsize=13)
        ax1.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"NT${x:,.0f}"))
        ax1.legend(facecolor="#21262d", labelcolor="#e6edf3")

        ax2.fill_between(range(len(dd)), dd.values, 0, color="#f85149", alpha=0.6, label="回撤")
        ax2.set_title("回撤", color="#e6edf3", fontsize=10)
        ax2.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"NT${x:,.0f}"))

        plt.tight_layout()
        path = os.path.join(OUTPUT_DIR, "orb_equity.png")
        plt.savefig(path, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
        print(f"\n  損益曲線圖已儲存：{path}")
    except ImportError:
        print("\n  （安裝 matplotlib 後可產生損益曲線圖：pip install matplotlib）")
    except Exception as e:
        print(f"\n  圖表產生失敗：{e}")


def main():
    print("=" * 52)
    print("  微型台指 ORB 回測啟動")
    print("=" * 52)
    print("\n策略參數：")
    for k, v in PARAMS.items():
        print(f"  {k:<18} = {v}")

    df = download_data()
    df = add_trend_filter(df, PARAMS["ema_period"])

    print("\n[2] 執行逐日回測...")
    all_trades = []
    skipped = 0
    for date, day_df in df.groupby("date"):
        trades = run_daily(date, day_df, PARAMS)
        if not trades:
            skipped += 1
        all_trades.extend(trades)

    if not all_trades:
        print("❌ 無任何交易，請檢查參數或資料")
        return

    result_df = pd.DataFrame(all_trades)
    print(f"   有效交易日：{len(df['date'].unique()) - skipped}，跳過：{skipped}")

    print_report(result_df)

    # 儲存明細
    csv_path = os.path.join(OUTPUT_DIR, "orb_result.csv")
    result_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n  交易明細已儲存：{csv_path}")

    save_equity_chart(result_df)

    print("\n[提示] 修改 PARAMS 參數後重新執行可比較不同設定的績效")
    print("[提示] orb_result.csv 可用 Excel 開啟進一步分析")


if __name__ == "__main__":
    main()
