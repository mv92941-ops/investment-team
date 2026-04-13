import requests
import pandas as pd
import numpy as np
import yfinance as yf
import sys
import warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')

POINT_VALUE = 10
COMMISSION = 120  # 來回手續費

def get_tx_daily():
    url = 'https://api.finmindtrade.com/api/v4/data'
    r = requests.get(url, params={
        'dataset': 'TaiwanFuturesDaily', 'data_id': 'TX', 'start_date': '2020-01-01'
    }, timeout=15)
    df = pd.DataFrame(r.json()['data'])
    df['date'] = pd.to_datetime(df['date'])
    df = df[df['contract_date'].str.match(r'^\d{6}$')]
    df['contract_date'] = df['contract_date'].astype(int)
    df = df.sort_values(['date', 'contract_date'])
    df = df.groupby(['date', 'trading_session']).first().reset_index()
    day = df[df['trading_session'] == 'position'].set_index('date')[['open','max','min','close','volume']].copy()
    day.columns = ['open','high','low','close','volume']
    day = day.sort_index()
    return day

def get_yf(interval='60m', period='2y'):
    df = yf.download('^TWII', period=period, interval=interval, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df = df.rename(columns={'Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'})
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df[['open','high','low','close','volume']].dropna()

def calc_atr(df, n=14):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def calc_bollinger(df, n=20, std_mult=2.0):
    mid = df['close'].rolling(n).mean()
    std = df['close'].rolling(n).std()
    return mid, mid + std_mult*std, mid - std_mult*std

def supertrend_dir(df, atr_mult=3.0, atr_period=10):
    atr = calc_atr(df, atr_period)
    hl2 = (df['high'] + df['low']) / 2
    upper = hl2 + atr_mult * atr
    lower = hl2 - atr_mult * atr
    direction = pd.Series(1, index=df.index)
    for i in range(1, len(df)):
        if df['close'].iloc[i] > upper.iloc[i-1]:
            direction.iloc[i] = 1
        elif df['close'].iloc[i] < lower.iloc[i-1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
    return direction, atr * atr_mult

def backtest(signals_df):
    trades = []
    in_trade = False
    entry_price = sl = tp = direction = 0
    entry_date = None
    for i, row in signals_df.iterrows():
        if in_trade:
            hit_sl = (direction == 1 and row['low'] <= sl) or (direction == -1 and row['high'] >= sl)
            hit_tp = (direction == 1 and row['high'] >= tp) or (direction == -1 and row['low'] <= tp)
            exit_price = None
            if hit_sl:
                exit_price = sl
            elif hit_tp:
                exit_price = tp
            if exit_price:
                pnl_pts = (exit_price - entry_price) * direction
                pnl_twd = pnl_pts * POINT_VALUE - COMMISSION
                trades.append({
                    'entry_date': entry_date, 'exit_date': i,
                    'direction': direction, 'entry': entry_price,
                    'exit': exit_price, 'pnl_pts': pnl_pts,
                    'pnl_twd': pnl_twd, 'win': pnl_twd > 0
                })
                in_trade = False
        if not in_trade and row['signal'] != 0:
            in_trade = True
            direction = int(row['signal'])
            entry_price = row['close']
            sl = row['sl']
            tp = row['tp']
            entry_date = i
    return pd.DataFrame(trades) if trades else pd.DataFrame()

def summarize(trades, name):
    if trades.empty or len(trades) < 3:
        return {'strategy': name, 'trades': len(trades) if not trades.empty else 0,
                'winrate': '0%', 'pf': 0.0, 'total_twd': 0, 'max_dd': 0, 'avg_win': 0, 'avg_loss': 0}
    wins = trades[trades['win']]
    losses = trades[~trades['win']]
    gp = wins['pnl_twd'].sum()
    gl = losses['pnl_twd'].sum()
    pf = round(gp / abs(gl), 2) if gl != 0 else 999.0
    cumsum = trades['pnl_twd'].cumsum()
    dd = int((cumsum - cumsum.cummax()).min())
    return {
        'strategy': name, 'trades': len(trades),
        'winrate': f"{wins.shape[0]/len(trades)*100:.1f}%",
        'pf': pf, 'total_twd': int(trades['pnl_twd'].sum()), 'max_dd': dd,
        'avg_win': int(wins['pnl_twd'].mean()) if len(wins) else 0,
        'avg_loss': int(losses['pnl_twd'].mean()) if len(losses) else 0
    }

# ---- 策略1: 前日高低點突破 ----
def strategy_prev_day_breakout(df, sl_pts=100, rr=1.5):
    df = df.copy().dropna()
    rows = []
    for i in range(2, len(df)):
        c = df['close'].iloc[i]
        ph = df['high'].iloc[i-1]
        pl = df['low'].iloc[i-1]
        if c > ph:
            rows.append({'signal':1,'close':c,'sl':c-sl_pts,'tp':c+sl_pts*rr,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        elif c < pl:
            rows.append({'signal':-1,'close':c,'sl':c+sl_pts,'tp':c-sl_pts*rr,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        else:
            rows.append({'signal':0,'close':c,'sl':0,'tp':0,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
    return backtest(pd.DataFrame(rows, index=df.index[2:]))

# ---- 策略2: 前兩日高低點突破 ----
def strategy_prev2_day_breakout(df, sl_pts=100, rr=1.5):
    df = df.copy().dropna()
    rows = []
    for i in range(3, len(df)):
        c = df['close'].iloc[i]
        ph = max(df['high'].iloc[i-2], df['high'].iloc[i-1])
        pl = min(df['low'].iloc[i-2], df['low'].iloc[i-1])
        if c > ph:
            rows.append({'signal':1,'close':c,'sl':c-sl_pts,'tp':c+sl_pts*rr,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        elif c < pl:
            rows.append({'signal':-1,'close':c,'sl':c+sl_pts,'tp':c-sl_pts*rr,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        else:
            rows.append({'signal':0,'close':c,'sl':0,'tp':0,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
    return backtest(pd.DataFrame(rows, index=df.index[3:]))

# ---- 策略3: 布林均值回歸 + K棒確認 ----
def strategy_bollinger_reversion(df, n=20, std=2.0, sl_pts=100, rr=1.5):
    df = df.copy().dropna()
    mid, upper, lower = calc_bollinger(df, n, std)
    rows = []
    for i in range(n+1, len(df)):
        c = df['close'].iloc[i]
        red = c > df['open'].iloc[i]
        blk = c < df['open'].iloc[i]
        if df['low'].iloc[i] <= lower.iloc[i] and red and (mid.iloc[i]-c) >= sl_pts*0.8:
            rows.append({'signal':1,'close':c,'sl':c-sl_pts,'tp':mid.iloc[i],'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        elif df['high'].iloc[i] >= upper.iloc[i] and blk and (c-mid.iloc[i]) >= sl_pts*0.8:
            rows.append({'signal':-1,'close':c,'sl':c+sl_pts,'tp':mid.iloc[i],'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        else:
            rows.append({'signal':0,'close':c,'sl':0,'tp':0,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
    return backtest(pd.DataFrame(rows, index=df.index[n+1:]))

# ---- 策略4: ATR Supertrend 翻轉 ----
def strategy_supertrend(df, atr_mult=3.0, atr_period=10, rr=1.5):
    df = df.copy().dropna()
    direction, atr_sl = supertrend_dir(df, atr_mult, atr_period)
    rows = []
    for i in range(atr_period+1, len(df)):
        pd_ = direction.iloc[i-1]
        cd_ = direction.iloc[i]
        c = df['close'].iloc[i]
        sp = atr_sl.iloc[i]
        if pd_ == -1 and cd_ == 1:
            rows.append({'signal':1,'close':c,'sl':c-sp,'tp':c+sp*rr,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        elif pd_ == 1 and cd_ == -1:
            rows.append({'signal':-1,'close':c,'sl':c+sp,'tp':c-sp*rr,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        else:
            rows.append({'signal':0,'close':c,'sl':0,'tp':0,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
    return backtest(pd.DataFrame(rows, index=df.index[atr_period+1:]))

# ---- 策略5: 布林突破 + EMA趨勢過濾 ----
def strategy_boll_breakout_ema(df, n=20, std=2.0, ema_n=50, sl_pts=80, rr=1.5):
    df = df.copy().dropna()
    mid, upper, lower = calc_bollinger(df, n, std)
    ema = df['close'].ewm(span=ema_n).mean()
    rows = []
    start = max(n, ema_n)+1
    for i in range(start, len(df)):
        c = df['close'].iloc[i]
        pc = df['close'].iloc[i-1]
        bup = pc < upper.iloc[i-1] and c > upper.iloc[i]
        bdn = pc > lower.iloc[i-1] and c < lower.iloc[i]
        if bup and c > ema.iloc[i]:
            rows.append({'signal':1,'close':c,'sl':c-sl_pts,'tp':c+sl_pts*rr,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        elif bdn and c < ema.iloc[i]:
            rows.append({'signal':-1,'close':c,'sl':c+sl_pts,'tp':c-sl_pts*rr,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        else:
            rows.append({'signal':0,'close':c,'sl':0,'tp':0,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
    return backtest(pd.DataFrame(rows, index=df.index[start:]))

# ---- 策略6: Supertrend + 布林確認組合 ----
def strategy_super_boll_combo(df, atr_mult=3.0, atr_period=10, n=20, std=2.0, rr=1.5):
    df = df.copy().dropna()
    direction, atr_sl = supertrend_dir(df, atr_mult, atr_period)
    mid, upper, lower = calc_bollinger(df, n, std)
    rows = []
    start = max(atr_period, n)+1
    for i in range(start, len(df)):
        pd_ = direction.iloc[i-1]
        cd_ = direction.iloc[i]
        c = df['close'].iloc[i]
        sp = atr_sl.iloc[i]
        near_low = df['low'].iloc[i] <= lower.iloc[i] * 1.005
        near_up  = df['high'].iloc[i] >= upper.iloc[i] * 0.995
        if pd_ == -1 and cd_ == 1 and near_low:
            rows.append({'signal':1,'close':c,'sl':c-sp,'tp':c+sp*rr,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        elif pd_ == 1 and cd_ == -1 and near_up:
            rows.append({'signal':-1,'close':c,'sl':c+sp,'tp':c-sp*rr,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
        else:
            rows.append({'signal':0,'close':c,'sl':0,'tp':0,'low':df['low'].iloc[i],'high':df['high'].iloc[i]})
    return backtest(pd.DataFrame(rows, index=df.index[start:]))

# ============================================================
# 主程式
# ============================================================
print("=" * 65)
print("投資團隊策略研究報告")
print("=" * 65)
print("\n[載入資料...]")
tx_daily = get_tx_daily()
df60 = get_yf('60m', '2y')
df15 = get_yf('15m', '60d')
print(f"TX日K (FinMind真實期貨): {len(tx_daily)} 筆 | {tx_daily.index[0].date()} ~ {tx_daily.index[-1].date()}")
print(f"TWII 60分K (proxy): {len(df60)} 筆")
print(f"TWII 15分K (proxy): {len(df15)} 筆")

results = []

print("\n[跑策略中，請稍候...]")

# Daily TX - 前日/前兩日突破
for sl in [80, 100, 120, 150]:
    for rr in [1.5, 2.0, 2.5]:
        for fn, name in [(strategy_prev_day_breakout,'前日突破'), (strategy_prev2_day_breakout,'前兩日突破')]:
            t = fn(tx_daily, sl_pts=sl, rr=rr)
            r = summarize(t, f'{name} sl={sl} rr={rr}')
            r['timeframe'] = 'Daily-TX'
            results.append(r)

# Daily TX - 布林回歸
for sl in [80, 100, 120, 150]:
    for rr in [1.5, 2.0]:
        t = strategy_bollinger_reversion(tx_daily, sl_pts=sl, rr=rr)
        r = summarize(t, f'布林回歸 sl={sl} rr={rr}')
        r['timeframe'] = 'Daily-TX'
        results.append(r)

# Daily TX - Supertrend
for mult in [2.5, 3.0, 3.5]:
    for rr in [1.5, 2.0, 2.5]:
        t = strategy_supertrend(tx_daily, atr_mult=mult, rr=rr)
        r = summarize(t, f'Supertrend mult={mult} rr={rr}')
        r['timeframe'] = 'Daily-TX'
        results.append(r)

# Daily TX - 布林突破EMA + 組合
for sl in [80, 100]:
    for rr in [1.5, 2.0]:
        t = strategy_boll_breakout_ema(tx_daily, sl_pts=sl, rr=rr)
        r = summarize(t, f'布林突破EMA sl={sl} rr={rr}')
        r['timeframe'] = 'Daily-TX'
        results.append(r)

for mult in [2.5, 3.0]:
    for rr in [1.5, 2.0]:
        t = strategy_super_boll_combo(tx_daily, atr_mult=mult, rr=rr)
        r = summarize(t, f'Super+Boll mult={mult} rr={rr}')
        r['timeframe'] = 'Daily-TX'
        results.append(r)

# 60m TWII
for sl in [50, 80, 100]:
    for rr in [1.5, 2.0]:
        t = strategy_bollinger_reversion(df60, sl_pts=sl, rr=rr)
        r = summarize(t, f'布林回歸 sl={sl} rr={rr}')
        r['timeframe'] = '60m-TWII'
        results.append(r)

        t = strategy_boll_breakout_ema(df60, sl_pts=sl, rr=rr)
        r = summarize(t, f'布林突破EMA sl={sl} rr={rr}')
        r['timeframe'] = '60m-TWII'
        results.append(r)

for mult in [2.5, 3.0, 3.5]:
    for rr in [1.5, 2.0, 2.5]:
        t = strategy_supertrend(df60, atr_mult=mult, rr=rr)
        r = summarize(t, f'Supertrend mult={mult} rr={rr}')
        r['timeframe'] = '60m-TWII'
        results.append(r)

for mult in [2.5, 3.0]:
    for rr in [1.5, 2.0]:
        t = strategy_super_boll_combo(df60, atr_mult=mult, rr=rr)
        r = summarize(t, f'Super+Boll mult={mult} rr={rr}')
        r['timeframe'] = '60m-TWII'
        results.append(r)

# 15m TWII
for sl in [30, 50, 80]:
    for rr in [1.5, 2.0]:
        t = strategy_bollinger_reversion(df15, sl_pts=sl, rr=rr)
        r = summarize(t, f'布林回歸 sl={sl} rr={rr}')
        r['timeframe'] = '15m-TWII'
        results.append(r)

for mult in [2.5, 3.0, 3.5]:
    for rr in [1.5, 2.0, 2.5]:
        t = strategy_supertrend(df15, atr_mult=mult, rr=rr)
        r = summarize(t, f'Supertrend mult={mult} rr={rr}')
        r['timeframe'] = '15m-TWII'
        results.append(r)

# ============================================================
# 輸出報告
# ============================================================
rdf = pd.DataFrame(results)
rdf.to_csv('c:/Users/mv929/OneDrive/AI專案/投資團隊/strategy_research_results.csv', index=False, encoding='utf-8-sig')

valid = rdf[rdf['trades'] >= 5].copy()
valid['pf'] = valid['pf'].astype(float)
valid = valid.sort_values('pf', ascending=False)

print("\n" + "=" * 85)
print("全部有效策略（>=5筆，按獲利因子排序，前30名）")
print("=" * 85)
print(f"{'策略':<32} {'框架':<14} {'筆':>4} {'勝率':>7} {'PF':>5} {'總損益TWD':>11} {'最大回撤':>10}")
print("-" * 85)
for _, row in valid.head(30).iterrows():
    print(f"{row['strategy']:<32} {row['timeframe']:<14} {row['trades']:>4} {row['winrate']:>7} {float(row['pf']):>5.2f} {row['total_twd']:>11,} {row['max_dd']:>10,}")

top = valid[valid['pf'] >= 1.5]
print(f"\n\n[投資團隊推薦] 獲利因子>=1.5 共 {len(top)} 個策略")
print("\n" + "=" * 65)
print("TOP 5 策略詳細分析")
print("=" * 65)
for idx, (_, row) in enumerate(top.head(5).iterrows(), 1):
    print(f"\n#{idx} {row['strategy']} [{row['timeframe']}]")
    print(f"  交易筆數: {row['trades']} | 勝率: {row['winrate']} | 獲利因子(PF): {float(row['pf']):.2f}")
    print(f"  總損益: NT${row['total_twd']:,} | 最大回撤: NT${row['max_dd']:,}")
    print(f"  平均獲利: NT${row['avg_win']:,} | 平均虧損: NT${row['avg_loss']:,}")

if not top.empty:
    best = top.iloc[0]
    print(f"\n\n[策略王推薦最佳策略]")
    print(f"  {best['strategy']} [{best['timeframe']}]")
    print(f"  PF={float(best['pf']):.2f}, 勝率={best['winrate']}, 總損益=NT${best['total_twd']:,}")

print("\n\n完整結果已存: c:/Users/mv929/OneDrive/AI專案/投資團隊/strategy_research_results.csv")
