import streamlit as st
import pandas as pd
import gspread
import google.generativeai as genai
from datetime import datetime, date

# --- 設定區 ---
SHEET_ID = 'My Weight Data'  # 你的試算表名稱
WEIGHT_SHEET_NAME = '工作表1' # ⚠️注意：如果你改過體重分頁的名字，請這裡也要改 (預設通常是 "工作表1" 或 "Sheet1")
FOOD_SHEET_NAME = 'Food Log'

# --- 1. 連接 Google Sheets ---
@st.cache_resource
def get_google_sheet(sheet_name):
    credentials = st.secrets["service_account_info"]
    gc = gspread.service_account_from_dict(credentials)
    sh = gc.open(SHEET_ID)
    try:
        return sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        # 如果找不到分頁，就自動創一個 (防呆機制)
        new_sheet = sh.add_worksheet(title=sheet_name, rows=1000, cols=6)
        if sheet_name == FOOD_SHEET_NAME:
            new_sheet.append_row(['日期', '時間', '食物名稱', '熱量', '蛋白質', '碳水'])
        return new_sheet

# --- 2. 設定 Google AI (Gemini) ---
if "gemini_api_key" in st.secrets:
    genai.configure(api_key=st.secrets["gemini_api_key"])
else:
    st.error("⚠️ 尚未設定 Gemini API Key！請去 Secrets 貼上。")

def analyze_food_with_ai(text_input):
    """叫 AI 幫我們估算營養素"""
    model = genai.GenerativeModel('gemini-1.5-flash') # 使用最新的輕量模型，速度快
    
    prompt = f"""
    你是一個專業營養師。請分析這段飲食描述："{text_input}"。
    請估算它的：1.熱量(大卡), 2.蛋白質(克), 3.碳水化合物(克)。
    
    請直接回傳一個 JSON 格式，不要有markdown標記，格式如下：
    {{
        "food_name": "食物簡稱",
        "calories": 數字,
        "protein": 數字,
        "carbs": 數字
    }}
    如果無法辨識或不是食物，所有數字回傳 0。
    """
    try:
        response = model.generate_content(prompt)
        # 清理一下 AI 回傳的文字，確保是純 JSON
        clean_json = response.text.replace('```json', '').replace('```', '').strip()
        return eval(clean_json) # 把文字變成 Python 字典
    except Exception as e:
        return None

# --- 3. 讀寫資料函式 ---
def save_weight_data(d, h, w, b):
    ws = get_google_sheet(WEIGHT_SHEET_NAME)
    ws.append_row([str(d), h, w, b])

def save_food_data(date_str, time_str, food, cal, prot, carb):
    ws = get_google_sheet(FOOD_SHEET_NAME)
    ws.append_row([str(date_str), str(time_str), food, cal, prot, carb])

def load_data(sheet_name):
    ws = get_google_sheet(sheet_name)
    records = ws.get_all_records()
    return pd.DataFrame(records)

# ================= 介面開始 =================
st.title('🥗 健康管家 & 體重追蹤')

# 建立兩個分頁
tab1, tab2 = st.tabs(["⚖️ 體重紀錄", "🍎 飲食紀錄 (AI辨識)"])

# --- 分頁 1: 體重 ---
with tab1:
    col1, col2 = st.columns([1, 2])
    with col1:
        st.subheader("新增體重")
        w_date = st.date_input("日期", date.today(), key='w_date')
        w_height = st.number_input("身高 (cm)", 100.0, 250.0, 170.0, 0.1, key='w_h')
        w_weight = st.number_input("體重 (kg)", 0.0, 200.0, step=0.1, format="%.1f", key='w_w')
        
        if w_height > 0:
            bmi = w_weight / ((w_height / 100) ** 2)
            st.caption(f"BMI: {bmi:.1f}")

        if st.button("紀錄體重"):
            with st.spinner('上傳中...'):
                save_weight_data(w_date, w_height, w_weight, round(bmi, 1))
            st.success("✅ 體重已紀錄！")
            st.cache_data.clear() # 清除快取以顯示最新資料

    with col2:
        try:
            df_weight = load_data(WEIGHT_SHEET_NAME)
            if not df_weight.empty:
                st.subheader("📊 體重趨勢")
                st.line_chart(df_weight.set_index('日期')['體重'])
                with st.expander("詳細數據"):
                    st.dataframe(df_weight.sort_values('日期', ascending=False))
        except Exception as e:
            st.info("👈 尚無資料，請先輸入第一筆！")

# --- 分頁 2: 飲食 (AI 功能) ---
with tab2:
    st.info("💡 試試輸入：『早餐吃了一個火腿蛋吐司和大冰奶』")
    
    food_input = st.text_input("今天吃了什麼？(支援中文/語音輸入轉文字)", placeholder="例如：排骨便當去飯、一杯無糖綠茶")
    
    if st.button("🍱 AI 幫我算熱量"):
        if food_input:
            with st.spinner('AI 營養師正在分析中...'):
                result = analyze_food_with_ai(food_input)
            
            if result and result['calories'] > 0:
                # 顯示 AI 分析結果卡片
                c1, c2, c3 = st.columns(3)
                c1.metric("🔥 熱量", f"{result['calories']} kcal")
                c2.metric("🥩 蛋白質", f"{result['protein']} g")
                c3.metric("🍚 碳水", f"{result['carbs']} g")
                
                # 確認按鈕
                st.write(f"**辨識結果：** {result['food_name']}")
                if st.button("✅ 確認並儲存到雲端"):
                    now_time = datetime.now().strftime("%H:%M")
                    save_food_data(date.today(), now_time, result['food_name'], 
                                  result['calories'], result['protein'], result['carbs'])
                    st.success(f"已紀錄：{result['food_name']} ({result['calories']} kcal)")
                    st.cache_data.clear()
            else:
                st.error("AI 看不懂這是什麼食物，請換個說法試試看！(例如：1碗白飯)")
        else:
            st.warning("請先輸入文字喔！")

    st.divider()
    
    # 顯示飲食紀錄表
    try:
        df_food = load_data(FOOD_SHEET_NAME)
        if not df_food.empty:
            st.subheader("📝 近期飲食紀錄")
            st.dataframe(df_food.sort_values('日期', ascending=False))
    except:
        st.write("目前還沒有飲食資料")