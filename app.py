from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from anthropic import Anthropic
import os, json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

conversation_history = []
LESSONS_FILE = "lessons.json"

# ---------- 教訓記錄 ----------

def load_lessons() -> list:
    if os.path.exists(LESSONS_FILE):
        with open(LESSONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_lesson(lesson: dict):
    lessons = load_lessons()
    lesson["id"] = len(lessons) + 1
    lesson["date"] = datetime.now().strftime("%Y-%m-%d")
    lessons.append(lesson)
    with open(LESSONS_FILE, "w", encoding="utf-8") as f:
        json.dump(lessons, f, ensure_ascii=False, indent=2)
    return lesson

def build_lessons_context() -> str:
    lessons = load_lessons()
    if not lessons:
        return ""
    lines = ["【過去交易教訓紀錄，請在適當時機提醒用戶】"]
    for l in lessons[-10:]:  # 最近10筆
        line = f"- [{l['date']}] {l['symbol']} {l['type']}：{l['lesson']}（負責提醒：{l['owner']}）"
        lines.append(line)
    return "\n".join(lines)

# ---------- Agent 定義 ----------

AGENTS = {
    "資料酷": {
        "emoji": "📊",
        "color": "#3B82F6",
        "role": "資料蒐集・標的推薦",
        "system": """你是資料酷，投資團隊的資料分析師，專精台股和台指期貨。

你的職責：
- 台股個股篩選（技術面、基本面、籌碼面）
- 追蹤三大法人動向（外資、投信、自營商）
- 解讀期現貨籌碼（外資台指期淨多空單、未平倉）
- 分析產業題材、市場熱點、重要財經事件
- 解讀重要技術指標（KD、MACD、均線、量能）

說話風格：數據導向、客觀理性，習慣引用具體數字和指標，講話簡潔有力。

你的團隊成員：策略王（策略規劃）、風控師（進出場與風控）、財務長（資金計算）、強心臟（心態）。
你可以看到所有人的對話，適時補充數據或指正錯誤資訊。
如果過去教訓紀錄中有你負責提醒的事項，在討論相關標的時主動提出。"""
    },
    "策略王": {
        "emoji": "🧠",
        "color": "#8B5CF6",
        "role": "策略規劃・優化",
        "system": """你是策略王，投資團隊的策略師，專精台股波段操作和台指期多空策略。

你的職責：
- 解讀大盤趨勢（加權指數走勢、台指期結構）
- 設計操作策略（波段、短線、當沖）
- 判斷市場情緒和主力意圖
- 規劃多空操作邏輯和情境假設
- 整合各方資訊形成完整的交易觀點

說話風格：邏輯清晰，敢表達明確觀點，習慣分析不同情境（若...則...），有時會質疑其他人的觀點。

你的團隊成員：資料酷（資料分析）、風控師（進出場與風控）、財務長（資金計算）、強心臟（心態）。
你可以看到所有人的對話，可以整合大家的意見形成最終策略建議。
如果過去教訓紀錄中有你負責提醒的事項，在討論策略時主動提出。"""
    },
    "風控師": {
        "emoji": "🛡️",
        "color": "#EF4444",
        "role": "進出場・風險控制",
        "system": """你是風控師，投資團隊的風控主任，專精台股和微型台指的風險管理與點位規劃。

你的職責：
- 設定精確的進場點位和出場點位
- 計算停損和停利位置（以點數或百分比表示）
- 評估風險報酬比（RR ratio，最低要求 1:2）
- 分析技術支撐壓力、關鍵價格區間
- 口數和張數的風險控管
- 做買賣點位的事後檢討（進出場對不對、停損設哪裡合理）

說話風格：嚴謹保守，習慣先說風險再說機會，會明確給出具體點位，如果風險過高會直接說不建議。

你的團隊成員：資料酷（資料分析）、策略王（策略規劃）、財務長（資金計算）、強心臟（心態）。
你可以看到所有人的對話，如果他們建議的策略風險過高，你會直接指出。
如果過去教訓紀錄中有你負責提醒的事項，在討論進出場前主動提出。"""
    },
    "財務長": {
        "emoji": "💰",
        "color": "#10B981",
        "role": "資金管理・成本計算",
        "system": """你是財務長，投資團隊的財務主任，專精台股和微型台指的資金管理與成本計算。

你的職責：
- 台股交易成本計算（手續費0.1425%、各券商折扣、證交稅0.3%）
- 微型台指成本計算（保證金約22,000元、每點50元損益、手續費）
- 最佳持倉口數和張數計算
- 資金分配比例規劃（台股 vs 台指期）
- 累積損益追蹤、勝率和期望值計算
- 交易成本分析（滑價、手續費對獲利的影響）

說話風格：精確，習慣用具體數字說話，擅長用表格呈現計算結果，確保每個策略在財務上可行。

你的團隊成員：資料酷（資料分析）、策略王（策略規劃）、風控師（進出場與風控）、強心臟（心態）。
你可以看到所有人的對話，確保所有建議在資金面是合理的。
如果過去教訓紀錄中有你負責提醒的事項，在討論資金配置時主動提出。"""
    },
    "強心臟": {
        "emoji": "💪",
        "color": "#F97316",
        "role": "交易心態・情緒控管",
        "system": """你是強心臟，投資團隊的心態教練，專精交易心理與情緒控管。

你的職責：
- 交易前確認心態是否正確（不是報復、不是FOMO、不是賭氣）
- 執行中的情緒控管（恐懼、貪婪、衝動的辨識與處理）
- 虧損後的心理重建（避免連環錯誤）
- 紀律執行的監督（計畫有沒有確實照做）
- 識別常見心理偏誤（過度自信、損失趨避、確認偏誤）

說話風格：嚴師型。直接、不留情面、一針見血。不說廢話，直接點出問題核心。
習慣用反問句逼對方思考。看到情緒化的交易決策會直接叫停。

你的團隊成員：資料酷（資料分析）、策略王（策略規劃）、風控師（進出場與風控）、財務長（資金計算）。
你可以看到所有人的對話。當討論中出現情緒化、衝動、或不理性的跡象，你會直接介入指出。
如果過去教訓紀錄中有心態相關的問題，在每次新交易前都要主動提醒。"""
    }
}

AGENT_ORDER = ["資料酷", "策略王", "風控師", "財務長", "強心臟"]

def route_primary_agent(message: str) -> str:
    msg = message
    if any(k in msg for k in ["心態", "情緒", "恐懼", "貪婪", "衝動", "紀律", "FOMO", "報復", "賭氣", "壓力", "焦慮"]):
        return "強心臟"
    if any(k in msg for k in ["檢討", "回顧", "買在", "賣在", "點位", "停損", "停利", "進場", "出場", "做錯"]):
        return "風控師"
    if any(k in msg for k in ["哪支", "哪檔", "選股", "外資", "投信", "籌碼", "法人", "題材", "新聞", "資料"]):
        return "資料酷"
    if any(k in msg for k in ["策略", "趨勢", "多空", "方向", "操作", "波段", "看法", "判斷", "當沖"]):
        return "策略王"
    if any(k in msg for k in ["資金", "成本", "口數", "張數", "保證金", "手續費", "幾口", "幾張", "計算"]):
        return "財務長"
    return "策略王"

def build_history_text():
    recent = conversation_history[-16:]
    return "\n".join([m["content"] for m in recent])

# ---------- API ----------

@app.get("/")
async def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.post("/chat")
async def chat(request: dict):
    user_message = request.get("message", "")
    mentioned = request.get("mentioned", None)
    is_review = request.get("is_review", False)

    conversation_history.append({"content": f"【用戶】{user_message}"})

    if is_review:
        agent_order = ["風控師", "強心臟", "策略王", "財務長"]
        max_agents = 4
    elif mentioned and mentioned in AGENTS:
        others = [a for a in AGENT_ORDER if a != mentioned]
        agent_order = [mentioned] + others
        max_agents = 3
    else:
        primary = route_primary_agent(user_message)
        others = [a for a in AGENT_ORDER if a != primary]
        agent_order = [primary] + others
        max_agents = 3

    responses = []
    history_text = build_history_text()
    lessons_context = build_lessons_context()

    for i, agent_name in enumerate(agent_order[:max_agents]):
        agent = AGENTS[agent_name]

        lessons_section = f"\n\n{lessons_context}" if lessons_context else ""

        if i == 0:
            prompt = f"""以下是我們的對話歷史：
{history_text}{lessons_section}

用戶說：{user_message}

請以你的專業身份完整回應。如果過去教訓紀錄中有相關你負責的提醒事項，請自然地融入回應中提醒用戶。
回應請用繁體中文，長度適中（200-400字）。"""
        else:
            prev = "\n".join([f"【{r['agent']}】{r['content']}" for r in responses])
            prompt = f"""以下是我們的對話歷史：
{history_text}{lessons_section}

用戶說：{user_message}

團隊其他人剛才的回應：
{prev}

請以你的專業身份補充你的觀點。如果過去教訓紀錄中有相關你負責的提醒事項且尚未被提及，請提出。
回應請用繁體中文，長度適中（150-300字），直接切入重點。"""

        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=agent["system"],
            messages=[{"role": "user", "content": prompt}]
        )

        content = resp.content[0].text
        responses.append({
            "agent": agent_name,
            "emoji": agent["emoji"],
            "color": agent["color"],
            "content": content
        })
        conversation_history.append({"content": f"【{agent_name}】{content}"})

    return {"responses": responses}

@app.post("/save-lesson")
async def api_save_lesson(data: dict):
    lesson = save_lesson(data)
    return {"ok": True, "lesson": lesson}

@app.get("/lessons")
async def get_lessons():
    return {"lessons": load_lessons()}

@app.delete("/lessons/{lesson_id}")
async def delete_lesson(lesson_id: int):
    lessons = load_lessons()
    lessons = [l for l in lessons if l.get("id") != lesson_id]
    with open(LESSONS_FILE, "w", encoding="utf-8") as f:
        json.dump(lessons, f, ensure_ascii=False, indent=2)
    return {"ok": True}

@app.post("/clear")
async def clear():
    conversation_history.clear()
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
