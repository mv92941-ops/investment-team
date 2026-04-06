"""
股票監控儀表板
執行：python stock_dashboard.py
開啟：http://localhost:8100
"""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import uvicorn
import warnings
import json
import os
import requests as _req
warnings.filterwarnings("ignore")

DATA_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_data.json")
WATCH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist_data.json")

def load_holdings() -> list:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_holdings(data: list):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── 台灣股票中文名稱快取 ──────────────────────────────────────────────
_TW_NAMES: dict = {}
_TW_NAMES_LOADED = False

def load_tw_names():
    global _TW_NAMES, _TW_NAMES_LOADED
    if _TW_NAMES_LOADED:
        return
    try:
        r = _req.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                     timeout=10, verify=False)
        if r.status_code == 200:
            for item in r.json():
                _TW_NAMES[item["Code"]] = item["Name"]
    except Exception:
        pass
    try:
        r = _req.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
                     timeout=10, verify=False)
        if r.status_code == 200:
            for item in r.json():
                code = item.get("SecuritiesCompanyCode", "")
                name = item.get("CompanyName", "")
                if code and name:
                    _TW_NAMES[code] = name
    except Exception:
        pass
    _TW_NAMES_LOADED = True

def get_tw_name(code: str, fallback: str) -> str:
    load_tw_names()
    if code in _TW_NAMES:
        return _TW_NAMES[code]
    # 個別查詢（處理槓桿/反向ETF等未在批次清單中的代號）
    try:
        r = _req.get(
            f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?stockNo={code}&response=json",
            timeout=6, verify=False)
        if r.status_code == 200:
            title = r.json().get("title", "")
            # title 格式：「115年03月 00631L 元大台灣50正2    各日成交資訊」
            if code in title:
                part = title.split(code)[-1].split("各日")[0].strip()
                if part:
                    _TW_NAMES[code] = part
                    return part
    except Exception:
        pass
    return fallback

app = FastAPI()

HTML = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>投資團隊股票儀表板</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d1117; color: #e6edf3; font-family: 'Segoe UI', sans-serif; padding: 20px; }
  h1 { text-align: center; margin-bottom: 12px; font-size: 22px; color: #58a6ff; letter-spacing: 2px; }
  .toolbar { text-align: center; margin-bottom: 20px; }
  .btn-add { background: #21262d; border: 1px solid #30363d; border-radius: 8px; color: #e6edf3;
             padding: 8px 20px; cursor: pointer; font-size: 14px; margin: 0 6px; }
  .btn-add:hover { background: #30363d; }
  .btn-clear { background: #3d1f1f; border: 1px solid #da3633; border-radius: 8px; color: #f85149;
               padding: 8px 20px; cursor: pointer; font-size: 14px; margin: 0 6px; }
  .btn-clear:hover { background: #4a2020; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }

  @keyframes flash-danger {
    0%, 100% { border-color: #27ae60; box-shadow: 0 0 0px #27ae60; }
    50%       { border-color: #2ecc71; box-shadow: 0 0 14px #2ecc71; }
  }
  @keyframes flash-warn {
    0%, 100% { border-color: #d29922; box-shadow: 0 0 0px #d29922; }
    50%       { border-color: #f0b429; box-shadow: 0 0 14px #f0b429; }
  }

  .card {
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 16px; transition: border-color 0.3s;
  }
  .card.safe   { border-color: #c0392b; background: #1a0a09; }
  .card.warn   { background: #1a1500; animation: flash-warn 1.2s ease-in-out infinite; }
  .card.danger { background: #091a0e; animation: flash-danger 1.2s ease-in-out infinite; }

  .card-header { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
  .card-header input {
    flex: 1; background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    color: #e6edf3; padding: 6px 10px; font-size: 14px;
  }
  .card-header button {
    background: #238636; border: none; border-radius: 6px; color: white;
    padding: 6px 14px; cursor: pointer; font-size: 13px;
  }
  .card-header button:hover { background: #2ea043; }

  .params { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 12px; }
  .params label { font-size: 11px; color: #8b949e; }
  .params input {
    width: 100%; background: #0d1117; border: 1px solid #21262d; border-radius: 4px;
    color: #e6edf3; padding: 4px 8px; font-size: 13px;
  }

  .price-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .price-big { font-size: 26px; font-weight: bold; }
  .price-big.up   { color: #f85149; }
  .price-big.down { color: #3fb950; }
  .company-name { font-size: 12px; color: #8b949e; margin-bottom: 4px; }
  .pnl { font-size: 14px; font-weight: bold; }
  .pnl.pos { color: #f85149; }
  .pnl.neg { color: #3fb950; }

  .levels { border-top: 1px solid #21262d; padding-top: 8px; margin-bottom: 10px; }
  .level-row {
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 8px;
    padding: 5px 0;
    border-bottom: 1px solid #161b22;
  }
  .level-row:last-child { border-bottom: none; }
  .level-row .label { color: #8b949e; font-size: 12px; }
  .level-row .right { text-align: right; white-space: nowrap; }
  .level-row .val   { font-weight: bold; font-size: 13px; }
  .level-row .dist  { font-size: 11px; color: #8b949e; margin-left: 6px; }

  .tag { display: inline-block; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-right: 4px; }
  .tag.green  { background: #1a4731; color: #3fb950; }
  .tag.red    { background: #3d1f1f; color: #f85149; }
  .tag.yellow { background: #3d2f00; color: #d29922; }

  .advice { border-top: 1px solid #21262d; padding-top: 10px; font-size: 12px; line-height: 1.7; }
  .advice .line { margin-bottom: 4px; }

  .loading { text-align: center; color: #8b949e; padding: 20px; }
  .error   { color: #f85149; font-size: 12px; padding: 10px; }

  .volume-row { font-size: 12px; color: #8b949e; margin-bottom: 6px; }
  .volume-ok   { color: #3fb950; }
  .volume-warn { color: #d29922; }

  .section-divider {
    margin: 36px 0 16px;
    border-top: 2px solid #30363d;
    padding-top: 20px;
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;
  }
  .section-divider h2 { font-size: 18px; color: #79c0ff; letter-spacing: 2px; }
  /* 鎖股區獨立顏色邏輯 */
  .watch-card                { border-color: #1f6feb; }
  .watch-card.watch-wait     { background: #0a1028; border-color: #1f6feb; }
  .watch-card.watch-near     { background: #1a1500; animation: flash-warn 1.2s ease-in-out infinite; }
  .watch-card.watch-ready    { background: #0a1a0e; border-color: #3fb950; }
  .watch-badge { font-size: 10px; background: #1f6feb33; color: #79c0ff;
                 padding: 2px 7px; border-radius: 10px; margin-left: 6px; }
  .params.full { grid-template-columns: 1fr; }
  .params input[type="text"] { font-size: 12px; }
</style>
</head>
<body>
<h1>投資團隊股票儀表板</h1>
<div class="toolbar">
  <button class="btn-add" onclick="addCard('hold')">＋ 新增持股</button>
  <button class="btn-add" onclick="refreshAll()">↻ 全部刷新</button>
  <button class="btn-clear" onclick="clearAll('hold')">✕ 清除持股</button>
</div>
<div class="grid" id="grid"></div>

<div class="section-divider">
  <h2>鎖股區 <span class="watch-badge">觀察中</span></h2>
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
    <div style="display:flex;align-items:center;gap:8px;">
      <label style="font-size:12px;color:#8b949e;white-space:nowrap;">總資產（元）</label>
      <input id="watch-total" type="number" placeholder="如 3000000"
        style="background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:6px 10px;font-size:14px;width:150px;"
        onchange="saveBudgetSettings()">
    </div>
    <button class="btn-add" onclick="addCard('watch')">＋ 新增觀察股</button>
    <button class="btn-add" onclick="refreshWatch()">↻ 刷新觀察</button>
    <button class="btn-clear" onclick="clearAll('watch')">✕ 清除觀察</button>
  </div>
</div>
<div class="grid" id="watch-grid"></div>

<script>
const HOLD_KEY  = 'stock_dashboard_v4';
const WATCH_KEY = 'stock_dashboard_watch_v1';

// ── 通用存取 ────────────────────────────────────────────────────────
function loadData(section) {
  const key = section === 'watch' ? WATCH_KEY : HOLD_KEY;
  try {
    const d = JSON.parse(localStorage.getItem(key)) || [];
    if (section !== 'watch') {
      const v3 = JSON.parse(localStorage.getItem('stock_dashboard_v3')) || [];
      return d.length >= v3.length ? d : v3;
    }
    return d;
  } catch { return []; }
}
function saveData(slots, section) {
  const key = section === 'watch' ? WATCH_KEY : HOLD_KEY;
  const ep  = section === 'watch' ? '/saved-watch' : '/saved';
  localStorage.setItem(key, JSON.stringify(slots));
  fetch(ep, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(slots) }).catch(() => {});
}

// 掃描整個 grid 重建資料陣列並存檔
function autoSave(section) {
  const sec    = section || 'hold';
  const isWatch = sec === 'watch';
  const gridId = isWatch ? 'watch-grid' : 'grid';
  const slots  = [];
  document.getElementById(gridId).querySelectorAll('.card').forEach(card => {
    const id     = card.id.replace('card-', '');
    const codeEl = document.getElementById(`code-${id}`);
    if (!codeEl || !codeEl.value.trim()) return;
    const base = {
      code:    codeEl.value.trim().toUpperCase(),
      entry:   parseFloat(document.getElementById(`entry-${id}`)?.value)  || 0,
      shares:  parseFloat(document.getElementById(`shares-${id}`)?.value) || 0,
      w20ma:   parseFloat(document.getElementById(`w20ma-${id}`)?.value)  || 0,
      edate:   document.getElementById(`edate-${id}`)?.value || '',
      section: sec
    };
    if (isWatch) {
      base.budget    = parseFloat(document.getElementById(`budget-${id}`)?.value) || 0;
      base.condition = document.getElementById(`cond-${id}`)?.value || '';
    }
    slots.push(base);
  });
  saveData(slots, sec);
}

// 儲存總資產設定
function saveBudgetSettings() {
  const total = document.getElementById('watch-total')?.value || '';
  localStorage.setItem('watch_total_assets', total);
}
function loadBudgetSettings() {
  const total = localStorage.getItem('watch_total_assets') || '';
  const el = document.getElementById('watch-total');
  if (el && total) el.value = total;
}

function createCard(index, saved, section) {
  const d = saved || {};
  const sec = section || d.section || 'hold';
  const isWatch = sec === 'watch';
  const extraClass = isWatch ? 'watch-card watch-wait' : '';

  const holdParams = `
    <div class="params">
      <div>
        <label>進場價格</label>
        <input id="entry-${index}" type="number" step="0.01" placeholder="0.00" value="${d.entry||''}" onchange="autoSave('${sec}')">
      </div>
      <div>
        <label>持有張數</label>
        <input id="shares-${index}" type="number" placeholder="0" value="${d.shares||''}" onchange="autoSave('${sec}')">
      </div>
      <div>
        <label>週20MA（手動覆蓋）</label>
        <input id="w20ma-${index}" type="number" step="0.01" placeholder="自動計算" value="${d.w20ma||''}" onchange="autoSave('${sec}')">
      </div>
      <div>
        <label>進場日期</label>
        <input id="edate-${index}" type="date" value="${d.edate||''}" onchange="autoSave('${sec}')">
      </div>
    </div>`;

  const watchParams = `
    <div class="params">
      <div>
        <label>預計買入價（自動帶入）</label>
        <input id="entry-${index}" type="number" step="0.01" placeholder="查詢後自動帶入" value="${d.entry||''}" onchange="autoSave('${sec}')">
      </div>
      <div>
        <label>可投入金額（元）</label>
        <input id="budget-${index}" type="number" placeholder="如 100000" value="${d.budget||''}" onchange="autoSave('${sec}')">
      </div>
      <div>
        <label>週20MA（手動覆蓋）</label>
        <input id="w20ma-${index}" type="number" step="0.01" placeholder="自動計算" value="${d.w20ma||''}" onchange="autoSave('${sec}')">
      </div>
      <div>
        <label>目標買入日</label>
        <input id="edate-${index}" type="date" value="${d.edate||''}" onchange="autoSave('${sec}')">
      </div>
    </div>
    <div class="params full" style="margin-top:-4px;">
      <div>
        <label>進場條件（自訂）</label>
        <input id="cond-${index}" type="text" placeholder="如：站回週20MA且爆量突破" value="${d.condition||''}" onchange="autoSave('${sec}')">
      </div>
    </div>
    <input id="shares-${index}" type="hidden" value="${d.shares||''}">`;

  return `
  <div class="card ${extraClass}" id="card-${index}" data-section="${sec}">
    <div class="card-header">
      <input id="code-${index}" placeholder="股票/ETF代號（如 2330）" value="${d.code||''}"
        onchange="autoSave('${sec}')"
        onkeydown="if(event.key==='Enter')fetchStock('${index}','${sec}')">
      <button onclick="fetchStock('${index}','${sec}')">查詢</button>
      <button onclick="removeCard('${index}','${sec}')" style="background:#3d1f1f;color:#f85149;">✕</button>
    </div>
    ${isWatch ? watchParams : holdParams}
    <div id="result-${index}" class="loading" style="display:none"></div>
  </div>`;
}

function addCard(section) {
  const sec = section || 'hold';
  const ts = Date.now();
  const index = sec === 'watch' ? 'w' + ts : ts;
  const gridId = sec === 'watch' ? 'watch-grid' : 'grid';
  const grid = document.getElementById(gridId);
  const div = document.createElement('div');
  div.innerHTML = createCard(index, {}, sec);
  grid.appendChild(div.firstElementChild);
}

function removeCard(index, section) {
  const sec = section || 'hold';
  const card = document.getElementById(`card-${index}`);
  if (card) card.remove();
  autoSave(sec);
}

function clearAll(section) {
  const sec = section || 'hold';
  const label = sec === 'watch' ? '觀察清單' : '持股';
  if (!confirm(`確定清除所有${label}？`)) return;
  const key = sec === 'watch' ? WATCH_KEY : HOLD_KEY;
  localStorage.removeItem(key);
  if (sec === 'watch') renderWatch(); else renderGrid();
}

function refreshAll() {
  document.querySelectorAll('.card').forEach(card => {
    const id = card.id.replace('card-', '');
    const sec = card.dataset.section || 'hold';
    const codeEl = document.getElementById(`code-${id}`);
    if (codeEl && codeEl.value.trim()) fetchStock(id, sec, true);
  });
}
function refreshWatch() {
  document.querySelectorAll('.watch-card').forEach(card => {
    const id = card.id.replace('card-', '');
    const codeEl = document.getElementById(`code-${id}`);
    if (codeEl && codeEl.value.trim()) fetchStock(id, 'watch', true);
  });
}

async function renderSection(section) {
  const isWatch = section === 'watch';
  const ep      = isWatch ? '/saved-watch' : '/saved';
  const gridId  = isWatch ? 'watch-grid' : 'grid';
  const minCards = isWatch ? 3 : 5;
  const prefix  = isWatch ? 'w' : '';

  const local = loadData(section);
  let server = [];
  try {
    const resp = await fetch(ep);
    if (resp.ok) server = await resp.json();
  } catch {}

  let saved = (local.length >= server.length) ? local : server;
  if (local.length > server.length && saved.length > 0)
    fetch(ep, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(saved) }).catch(()=>{});
  if (server.length > local.length && server.length > 0)
    localStorage.setItem(isWatch ? WATCH_KEY : HOLD_KEY, JSON.stringify(server));

  const grid = document.getElementById(gridId);
  grid.innerHTML = '';
  const count = Math.max(saved.length, minCards);
  for (let i = 0; i < count; i++)
    grid.innerHTML += createCard(prefix + i, saved[i], section);
  saved.forEach((d, i) => { if (d && d.code) fetchStock(prefix + i, section, true); });
}

async function renderGrid()  { await renderSection('hold'); }
async function renderWatch() { await renderSection('watch'); }

async function fetchStock(index, section, silent=false) {
  const sec    = section || 'hold';
  const code   = document.getElementById(`code-${index}`)?.value.trim().toUpperCase();
  const entry  = parseFloat(document.getElementById(`entry-${index}`)?.value)  || 0;
  const shares = parseFloat(document.getElementById(`shares-${index}`)?.value) || 0;
  const w20ma  = parseFloat(document.getElementById(`w20ma-${index}`)?.value)  || 0;
  const edate  = document.getElementById(`edate-${index}`)?.value || '';

  if (!code) return;

  autoSave(sec);

  const result = document.getElementById(`result-${index}`);
  result.style.display = 'block';
  if (!silent) result.innerHTML = '<div class="loading">查詢中...</div>';

  try {
    const resp = await fetch(`/stock/${code}?entry=${entry}&shares=${shares}&w20ma=${w20ma}&edate=${edate}`);
    const data = await resp.json();
    if (data.error) { result.innerHTML = `<div class="error">❌ ${data.error}</div>`; return; }
    renderResult(index, data, sec);
  } catch(e) {
    result.innerHTML = `<div class="error">❌ 連線失敗：${e}</div>`;
  }
}

function renderResult(index, d, section) {
  const card   = document.getElementById(`card-${index}`);
  const result = document.getElementById(`result-${index}`);
  const sec    = section || card.dataset.section || 'hold';
  const isWatch = sec === 'watch';

  // 卡片顏色
  const watchClass = isWatch ? ' watch-card' : '';
  card.className = 'card ' + d.status_class + watchClass;

  // 鎖股區：獨立顏色邏輯 + 自動帶入收盤價
  if (isWatch) {
    // 自動帶入收盤價
    const entryEl = document.getElementById(`entry-${index}`);
    if (entryEl && (!entryEl.value || parseFloat(entryEl.value) === 0)) {
      entryEl.value = d.current.toFixed(2);
    }

    // 顏色邏輯：ready=站上20MA, near=距20MA<5%, wait=等待
    const ma = d.stop_loss;
    let watchStatus = 'watch-wait';
    if (ma > 0) {
      if (d.current >= ma) watchStatus = 'watch-ready';
      else if (d.current >= ma * 0.95) watchStatus = 'watch-near';
    }
    card.className = `card watch-card ${watchStatus}`;

    autoSave('watch');
  }

  const pnlSign   = d.pnl_pct >= 0 ? '+' : '';
  const pnlClass  = d.pnl_pct >= 0 ? 'pos' : 'neg';
  const priceClass = d.pnl_pct >= 0 ? 'up' : 'down';
  const volClass  = d.vol_ok ? 'volume-ok' : 'volume-warn';
  const volIcon   = d.vol_ok ? '✅' : '⚠️';

  const slDist = d.dist_sl_pct > 0 ? `-${d.dist_sl_pct.toFixed(1)}%` : '<span class="tag green">已跌破</span>';
  const warnTag = d.status_class === 'warn' ? '<span class="tag yellow">警戒中</span>' : '';

  let levelsHtml = `
    <div class="level-row">
      <span class="label">🛡️ 停損線（週20MA）</span>
      <span class="right"><span class="val">$${d.stop_loss.toFixed(2)}</span><span class="dist">${slDist}</span></span>
    </div>
    <div class="level-row">
      <span class="label">⚠️ 黃色警戒（MA+2）</span>
      <span class="right"><span class="val">$${(d.stop_loss + 2).toFixed(2)}</span><span class="dist">${warnTag}</span></span>
    </div>`;

  if (d.tp1 > 0) {
    const tp1Tag = d.tp1_hit ? '<span class="tag green">已達目標</span>' : `+${d.dist_tp1_pct.toFixed(1)}%`;
    levelsHtml += `
    <div class="level-row">
      <span class="label">💰 停利一（+20% 賣50%）</span>
      <span class="right"><span class="val">$${d.tp1.toFixed(2)}</span><span class="dist">${tp1Tag}</span></span>
    </div>`;
    if (d.trail_active) {
      const trailTag = d.trail_hit ? '<span class="tag green">已觸發</span>' : `高點$${d.hist_high.toFixed(2)}`;
      const trailColor = d.trail_hit ? 'color:#3fb950' : '';
      levelsHtml += `
    <div class="level-row">
      <span class="label">📉 移動停利（高點-15%）</span>
      <span class="right"><span class="val" style="${trailColor}">$${d.trail_stop.toFixed(2)}</span><span class="dist">${trailTag}</span></span>
    </div>`;
    } else {
      levelsHtml += `
    <div class="level-row">
      <span class="label">💰 停利二（跌破週20MA）</span>
      <span class="right"><span class="val">$${d.stop_loss.toFixed(2)}</span><span class="dist">同停損線</span></span>
    </div>`;
    }
  }

  if (d.shares > 0 && d.entry > 0) {
    levelsHtml += `
    <div class="level-row">
      <span class="label">📦 持倉損益（${d.shares}張）</span>
      <span class="right"><span class="val pnl ${pnlClass}">${pnlSign}$${d.pnl_total.toFixed(0)}</span><span class="dist">${pnlSign}${d.pnl_pct.toFixed(1)}%</span></span>
    </div>`;
    if (d.max_loss !== 0) {
      levelsHtml += `
    <div class="level-row">
      <span class="label">🔻 最大虧損（觸停損）</span>
      <span class="right"><span class="val" style="color:#3fb950">-$${Math.abs(d.max_loss).toFixed(0)}</span><span class="dist">${d.shares}張×${(d.entry - d.stop_loss).toFixed(2)}×1000</span></span>
    </div>`;
    }
  }

  // 鎖股區：完整資訊計算
  let buyHtml = '';
  if (isWatch) {
    const ma      = d.stop_loss;
    const cur     = d.current;

    // 距週20MA
    if (ma > 0) {
      const gap    = cur - ma;
      const gapPct = (gap / cur * 100).toFixed(1);
      const gapColor = gap >= 0 ? '#3fb950' : '#d29922';
      const gapText  = gap >= 0
        ? `已站上 MA，領先 $${gap.toFixed(2)}（+${gapPct}%）`
        : `距站上還差 $${Math.abs(gap).toFixed(2)}（${gapPct}%）`;
      buyHtml += `<div class="level-row"><span class="label">📍 距週20MA</span><span class="right"><span class="val" style="color:${gapColor}">${gapText}</span></span></div>`;
    }

    // 可投入金額 → 可買張數
    const budget  = parseFloat(document.getElementById(`budget-${index}`)?.value) || 0;
    const total   = parseFloat(document.getElementById('watch-total')?.value) || 0;
    if (budget > 0 && cur > 0) {
      const lotCost = cur * 1000;
      const lots    = Math.floor(budget / lotCost);
      if (lots > 0) {
        const spent = lots * lotCost;
        const left  = budget - spent;
        buyHtml += `<div class="level-row"><span class="label">💰 可買張數</span><span class="right"><span class="val" style="color:#58a6ff">${lots} 張</span><span class="dist">花 $${spent.toLocaleString()}，剩 $${Math.round(left).toLocaleString()}</span></span></div>`;
      } else {
        const frac = Math.floor(budget / cur);
        buyHtml += `<div class="level-row"><span class="label">💰 預算不足一張</span><span class="right"><span class="val" style="color:#d29922">零股 ${frac} 股</span><span class="dist">每張需 $${Math.round(cur*1000).toLocaleString()}</span></span></div>`;
      }
      // 佔總倉位 %
      if (total > 0) {
        const pct = (budget / total * 100).toFixed(1);
        const pctColor = pct > 20 ? '#f85149' : pct > 10 ? '#d29922' : '#3fb950';
        buyHtml += `<div class="level-row"><span class="label">📊 佔總倉位</span><span class="right"><span class="val" style="color:${pctColor}">${pct}%</span><span class="dist">$${budget.toLocaleString()} / $${total.toLocaleString()}</span></span></div>`;
      }
    }

    // 進場條件
    const cond = document.getElementById(`cond-${index}`)?.value?.trim();
    if (cond) {
      buyHtml += `<div class="level-row" style="grid-template-columns:1fr;"><span class="label">🎯 進場條件：<span style="color:#e6edf3;font-size:12px;">${cond}</span></span></div>`;
    }
  }

  result.innerHTML = `
    <div class="company-name">${d.name}</div>
    <div class="price-row">
      <span class="price-big ${priceClass}">$${d.current.toFixed(2)}</span>
      <span class="pnl ${pnlClass}">${d.entry > 0 ? pnlSign + d.pnl_pct.toFixed(1) + '%' : ''}</span>
    </div>
    <div class="volume-row">
      成交量：<span class="${volClass}">${volIcon} ${(d.volume/1000).toFixed(0)}張</span>
      ${d.vol_ok ? '' : '（未達1萬張門檻）'}
    </div>
    <div class="levels">${levelsHtml}${buyHtml}</div>
    <div class="advice">${d.advice}</div>
  `;
}

loadBudgetSettings();
renderGrid();
renderWatch();

// 每5分鐘自動刷新
setInterval(() => {
  document.querySelectorAll('.card').forEach(card => {
    const id = card.id.replace('card-', '');
    const sec = card.dataset.section || 'hold';
    const codeEl = document.getElementById(`code-${id}`);
    if (codeEl && codeEl.value.trim()) fetchStock(id, sec, true);
  });
}, 5 * 60 * 1000);
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


@app.get("/saved")
def get_saved():
    return load_holdings()


@app.post("/saved")
async def post_saved(request: Request):
    data = await request.json()
    save_holdings(data)
    return {"ok": True}


@app.get("/saved-watch")
def get_saved_watch():
    if os.path.exists(WATCH_FILE):
        with open(WATCH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


@app.post("/saved-watch")
async def post_saved_watch(request: Request):
    data = await request.json()
    with open(WATCH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"ok": True}


@app.get("/stock/{code}")
def get_stock(code: str, entry: float = 0, shares: float = 0, w20ma: float = 0, edate: str = ""):
    try:
        # 嘗試台股代號
        for suffix in [".TW", ".TWO", ""]:
            ticker = yf.Ticker(code + suffix)
            hist = ticker.history(period="5d", interval="1d")
            if not hist.empty:
                break

        if hist.empty:
            return {"error": f"找不到 {code}，請確認代號"}

        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)

        current = float(hist["Close"].iloc[-1])
        volume  = float(hist["Volume"].iloc[-1])
        info    = ticker.info
        yf_name = info.get("longName") or info.get("shortName") or code
        name    = get_tw_name(code, yf_name)

        # 計算週20MA（如果使用者沒填）
        if w20ma == 0:
            whist = ticker.history(start=(datetime.now()-timedelta(days=700)).strftime("%Y-%m-%d"),
                                   interval="1wk")
            if isinstance(whist.columns, pd.MultiIndex):
                whist.columns = whist.columns.get_level_values(0)
            if not whist.empty and len(whist) >= 20:
                w20ma = float(whist["Close"].rolling(20).mean().iloc[-1])

        # 抓進場日期後的最高價（用於移動停利）
        hist_high = 0
        if edate:
            try:
                yhist = ticker.history(start=edate, interval="1d")
                if isinstance(yhist.columns, pd.MultiIndex):
                    yhist.columns = yhist.columns.get_level_values(0)
                if not yhist.empty:
                    hist_high = float(yhist["High"].max())
            except Exception:
                pass

        # 計算各數值
        stop_loss    = w20ma
        tp1          = entry * 1.20 if entry > 0 else 0
        pnl_pct      = (current - entry) / entry * 100 if entry > 0 else 0
        pnl_total    = (current - entry) * shares * 1000 if entry > 0 and shares > 0 else 0
        dist_sl_pct  = (current - stop_loss) / current * 100 if stop_loss > 0 else 0
        dist_tp1_pct = (tp1 - current) / current * 100 if tp1 > 0 else 0
        tp1_hit      = current >= tp1 if tp1 > 0 else False
        vol_ok       = volume >= 10_000_000  # 1萬張
        max_loss     = (entry - stop_loss) * shares * 1000 if entry > 0 and stop_loss > 0 and shares > 0 else 0

        # 移動停利：只在股價曾達到進場+20%後才啟動
        trail_active = entry > 0 and hist_high >= tp1 > 0
        trail_stop   = hist_high * 0.85 if trail_active else 0
        trail_hit    = trail_active and current <= trail_stop

        # 狀態判斷（危險：跌破週20MA 或 觸發移動停利；警戒：週20MA ~ 週20MA+2）
        if (stop_loss > 0 and current < stop_loss) or trail_hit:
            status_class = "danger"
        elif stop_loss > 0 and current <= stop_loss + 2:
            status_class = "warn"
        else:
            status_class = "safe"

        # 投資團隊建議
        advice_lines = []

        # 📊 資料酷
        if not vol_ok:
            advice_lines.append(f"📊 資料酷：今日量 {volume/1000:.0f}張，未達1萬張門檻，流動性偏低")
        else:
            advice_lines.append(f"📊 資料酷：成交量正常（{volume/1000:.0f}張）")

        # 🛡️ 風控師
        if stop_loss > 0:
            if current < stop_loss:
                advice_lines.append(f"🛡️ 風控師：⚠️ 已跌破週20MA（${stop_loss:.2f}），按規則應立即出場！")
            elif current <= stop_loss + 2:
                advice_lines.append(f"🛡️ 風控師：⚠️ 進入警戒區（距週20MA不足2元），高度警戒，隨時準備執行停損")
            elif dist_sl_pct < 5:
                advice_lines.append(f"🛡️ 風控師：距停損僅 {dist_sl_pct:.1f}%，留意走勢")
            else:
                advice_lines.append(f"🛡️ 風控師：停損線 ${stop_loss:.2f}，距離 {dist_sl_pct:.1f}%，結構安全")

        # 💰 財務長
        if trail_hit:
            advice_lines.append(f"💰 財務長：⚠️ 從高點（${hist_high:.2f}）回落超過15%，第二批應全數出場！")
        elif tp1_hit:
            advice_lines.append(f"💰 財務長：已達停利目標 ${tp1:.2f}，應執行第一批（50%）出場！")
            if trail_active:
                advice_lines.append(f"💰 財務長：移動停利線 ${trail_stop:.2f}（高點 ${hist_high:.2f} × 85%），第二批持續追蹤")
        elif tp1 > 0:
            advice_lines.append(f"💰 財務長：停利目標 ${tp1:.2f}，距離 +{dist_tp1_pct:.1f}%")

        if shares > 0 and entry > 0:
            sign = "+" if pnl_total >= 0 else ""
            advice_lines.append(f"💰 財務長：{shares:.0f}張持倉損益 {sign}${pnl_total:,.0f}")

        # 🧠 策略王
        if stop_loss > 0 and entry > 0:
            if trail_hit:
                advice_lines.append("🧠 策略王：移動停利已觸發，鎖定獲利出場，勿猶豫")
            elif current < w20ma:
                advice_lines.append("🧠 策略王：現價在週20MA以下，趨勢轉弱，嚴格執行停損規則")
            elif current > w20ma * 1.10:
                advice_lines.append("🧠 策略王：現價已高於週20MA逾10%，注意追高風險，留意回落訊號")
            else:
                advice_lines.append("🧠 策略王：趨勢結構正常，按計畫持有")

        advice_html = "".join(f'<div class="line">{l}</div>' for l in advice_lines)

        return {
            "name": name, "current": current, "volume": volume,
            "entry": entry, "shares": shares, "w20ma": w20ma,
            "stop_loss": stop_loss, "tp1": tp1,
            "hist_high": hist_high, "trail_stop": trail_stop, "trail_active": trail_active, "trail_hit": trail_hit,
            "pnl_pct": pnl_pct, "pnl_total": pnl_total, "max_loss": max_loss,
            "dist_sl_pct": dist_sl_pct, "dist_tp1_pct": dist_tp1_pct,
            "tp1_hit": tp1_hit, "vol_ok": vol_ok,
            "status_class": status_class, "advice": advice_html,
        }

    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8100))
    print(f"Starting on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
