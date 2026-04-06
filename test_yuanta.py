"""
元大 API 連線測試
測試登入並訂閱微型台指即時報價
"""

import sys
import time
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 元大 API DLL 路徑
YUANTA_DLL_PATH = r"C:\Users\mv929\Downloads\YuantaOneAPI_Python"
sys.path.append(YUANTA_DLL_PATH)

# 載入 pythonnet
import clr
clr.AddReference('System.Collections')
sys.path.append(YUANTA_DLL_PATH)
clr.AddReference("YuantaOneAPI")

from YuantaOneAPI import (
    YuantaOneAPITrader,
    enumEnvironmentMode,
    OnResponseEventHandler,
    YuantaDataHelper,
    enumLangType,
    enumLogType,
    FiveTickA
)
from System.Collections.Generic import List

# 帳號資訊
ACCOUNT   = os.getenv("YUANTA_ACCOUNT", "")
PASSWORD  = os.getenv("YUANTA_PASSWORD", "")

# 目前抓到的價格
current_price = None

def on_response(intMark, dwIndex, strIndex, objHandle, objValue):
    global current_price

    # 所有回應都印出來偵錯
    print(f"[回應] mark={intMark} index={strIndex} value={str(objValue)[:80]}")

    # 系統訊息
    if intMark == 0:
        print(f"[系統] {objValue}")

    # 登入回應
    elif intMark == 1 and strIndex == 'Login':
        dataGetter = YuantaDataHelper(enumLangType.NORMAL)
        dataGetter.OutMsgLoad(objValue)
        try:
            code = dataGetter.GetStr(5)
            msg  = dataGetter.GetStr(50)
            print(f"[登入] 代碼:{code} 訊息:{msg}")
            if code in ('0001', '00001'):
                print("✅ 登入成功！開始訂閱微型台指報價...")
                subscribe_tmff(api)
        except Exception as e:
            print(f"[登入解析錯誤] {e}")

    # 五檔報價回應
    elif intMark == 2 and strIndex == '210.10.60.10':
        dataGetter = YuantaDataHelper(enumLangType.NORMAL)
        dataGetter.OutMsgLoad(objValue)
        try:
            dataGetter.GetStr(22)   # key
            market = dataGetter.GetByte()
            code   = dataGetter.GetStr(12)
            flag   = dataGetter.GetByte()

            if str(flag) == '50':
                buy1  = dataGetter.GetInt()
                dataGetter.GetInt(); dataGetter.GetInt()
                dataGetter.GetInt(); dataGetter.GetInt()
                # 跳過買量5個
                for _ in range(5): dataGetter.GetInt()
                sell1 = dataGetter.GetInt()

                current_price = buy1
                print(f"[{code}] 買一:{buy1}  賣一:{sell1}  ← 現價參考")

        except Exception as e:
            print(f"[報價解析錯誤] {e}")

def subscribe_tmff(yuanta):
    """訂閱微型台指近月五檔報價"""
    lst = List[FiveTickA]()

    # 微型台指近月（TMFFM1）
    tmff = FiveTickA()
    tmff.MarketNo  = 3
    tmff.StockCode = 'TMFFM1'
    lst.Add(tmff)

    # 也訂閱大台指確認連線正常
    txf = FiveTickA()
    txf.MarketNo  = 3
    txf.StockCode = 'TXFPM1'
    lst.Add(txf)

    yuanta.SubscribeFiveTickA(lst)
    print("[訂閱] 已送出訂閱請求：TMFFM1（微型台指）、TXFPM1（台指期）")

# 初始化 API
print("="*40)
print("元大 API 連線測試")
print("="*40)

api = YuantaOneAPITrader()
api.OnResponse += OnResponseEventHandler(on_response)
api.SetLogType(enumLogType.COMMON)

print("[1] 開啟連線...")
api.Open(enumEnvironmentMode.PROD)
time.sleep(3)

print(f"[2] 登入帳號：{ACCOUNT}")
api.Login(ACCOUNT, PASSWORD)
time.sleep(5)

print("[3] 等待報價回應（60秒）...")
for i in range(60):
    time.sleep(1)
    if current_price:
        print(f"\n✅ 成功取得微型台指現價：{current_price}")
        break

if not current_price:
    print("\n⚠️ 未收到報價，可能原因：")
    print("  - 帳號格式不對（期貨帳號格式不同）")
    print("  - 商品代碼需要確認")
    print("  - 目前非交易時段")

api.LogOut()
