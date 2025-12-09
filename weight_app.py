import streamlit as st
import pandas as pd
import gspread
import google.generativeai as genai
from datetime import datetime, date, time, timedelta
from PIL import Image
import pytz 

# --- 設定區 ---
SHEET_ID = 'My Weight Data' 
WEIGHT_SHEET_NAME = 'Weight Log'
FOOD_SHEET_NAME = 'Food Log'
WATER_SHEET_NAME = 'Water Log' 

# 設定時區 (全域強制使用台北時間 GMT+8)
TAIPEI_TZ = pytz.timezone('Asia/Taipei')

# --- 1. 連接 Google Sheets (核心修復區) ---
@st.cache_resource
def get_google_sheet(sheet_name):
    """
    取得 Google Sheet 分頁。
    修復邏輯：如果分頁存在但內容是空的(標題遺失)，會自動補上標題。
    """
    credentials = st.secrets["service_account_info"]
    gc = gspread.service_account_from_dict(credentials)
    sh = gc.open(SHEET_ID)
    
    # 定義各個分頁該有的標題
    HEADERS = {
        FOOD_SHEET_NAME: ['日期', '時間', '食物名稱', '熱量', '蛋白質', '碳水', '脂肪'],
        WATER_SHEET_NAME: ['日期', '時間', '水量(ml)'],
        WEIGHT_SHEET_NAME: ['日期', '身高', '體重', 'BMI']
    }
    
    try:
        ws = sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        # 找不到就新建
        ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=10)
    
    # --- 自動修復檢查 ---
    # 檢查第一列是否有資料，如果沒有，代表是空表，必須補上標題
    existing_data = ws.get_values('A1:Z1') 
    if not existing_data and sheet_name in HEADERS:
        ws.append_row(HEADERS[sheet_name])
        print(f"已自動修復 {sheet_name} 的標題列")
        
    return ws

# --- 2. 設定 Google AI ---
if "gemini_api_key" in st.secrets:
    genai.configure(api_key=st.secrets["gemini_api_key"])

# --- 3. 核心邏輯函式 ---

def analyze_food_with_ai(image_data, text_input):
    model_name = 'gemini-2.5-flash'
    model = genai.GenerativeModel(model_name)
    
    now_dt = datetime.now(TAIPEI_TZ)
    current_time_str = now_dt.strftime("%Y-%m-%d %H:%M")
    
    prompt = f"""
    你是一個專業營養師。現在的時間是：{current_time_str} (GMT+8 台北時間)。
    請分析這份飲食，並根據使用者的文字描述推斷「進食時間」。
    
    任務：
    1. 估算營養：熱量(kcal), 蛋白質(g), 碳水(g), 脂肪(g)
    2. 推斷時間：如果使用者說 "早上8點吃的" 或 "昨天晚餐"，請根據現在時間推算出正確的 date (YYYY-MM-DD) 和 time (HH:MM)。
       如果使用者沒提時間，就回傳 null，我會預設為現在。
    
    請直接回傳 JSON 格式：
    {{
        "food_name": "食物簡稱",
        "calories": 數字,
        "protein": 數字,
        "carbs": 數字,
        "fat": 數字,
        "date": "YYYY-MM-DD" 或 null,
        "time": "HH:MM" 或 null
    }}
    """
    if text_input: prompt += f"\n使用者補充說明：{text_input}"
    
    inputs = [prompt]
    if image_data: inputs.append(image_data)
    
    try:
        st.toast("📡 AI 正在分析照片與時間...", icon="🕒")
        response = model.generate_content(inputs)
        clean_json = response.text.replace('```json', '').replace('```', '').strip()
        return eval(clean_json)
    except Exception as e:
        st.error(f"錯誤：{e}")
        return None

# --- 資料讀寫與計算 ---

def save_weight_data(d, h, w, b):
    ws = get_google_sheet(WEIGHT_SHEET_NAME)
    ws.append_row([str(d), h, w, b])

def save_food_data(date_str, time_str, food, cal, prot, carb, fat):
    ws = get_google_sheet(FOOD_SHEET_NAME)
    ws.append_row([str(date_str), str(time_str), food, cal, prot, carb, fat])

def save_water_data(vol): 
    ws = get_google_sheet(WATER_SHEET_NAME)
    now_date = datetime.now(TAIPEI_TZ).date()
    now_time = datetime.now(TAIPEI_TZ).strftime("%H:%M")
    ws.append_row([str(now_date), str(now_time), vol])

def load_data(sheet_name):
    """安全讀取資料，防止空表報錯"""
    ws = get_google_sheet(sheet_name)
    try:
        records = ws.get_all_records()
        if not records: return pd.DataFrame()
        return pd.DataFrame(records)
    except Exception:
        # 如果發生 API 錯誤（例如全空），回傳空 DataFrame
        return pd.DataFrame()

def calculate_daily_summary(target_date):
    """
    計算「指定日期」的總營養攝取
    """
    target_date_str = str(target_date)
    totals = {'cal': 0, 'prot': 0, 'carb': 0, 'fat': 0, 'water': 0}
    
    # 1. 計算食物
    try:
        df_food = load_data(FOOD_SHEET_NAME)
        if not df_food.empty and '日期' in df_food.columns:
            df_target = df_food[df_food['日期'].astype(str) == target_date_str]
            for col, key in [('熱量', 'cal'), ('蛋白質', 'prot'), ('碳水', 'carb'), ('脂肪', 'fat')]:
                if col in df_target.columns:
                    totals[key] = pd.to_numeric(df_target[col], errors='coerce').fillna(0).sum()
    except Exception as e:
        print(f"Food log error: {e}")

    # 2. 計算飲水
    try:
        df_water = load_data(WATER_SHEET_NAME)
        if not df_water.empty and '日期' in df_water.columns:
            df_target_water = df_water[df_water['日期'].astype(str) == target_date_str]
            
            # 自動尋找水量欄位 (相容舊版或新版名稱)
            water_col = None
            if '水量(ml)' in df_target_water.columns:
                water_col = '水量(ml)'
            elif '水量' in df_target_water.columns:
                water_col = '水量'
            
            if water_col:
                totals['water'] = pd.to_numeric(df_target_water[water_col], errors='coerce').fillna(0).sum()
                
    except Exception as e:
        print(f"Water log error: {e}")
        
    return totals

# ================= 介面開始 =================
st.title('🥗 健康管家 AI')

# --- 儀表板 ---
st.markdown("### 📅 每日攝取總覽")

col_date, col_empty = st.columns([1, 2])
with col_date:
    default_today = datetime.now(TAIPEI_TZ).date()
    view_date = st.date_input("🔍 選擇檢視日期", default_today)

with st.spinner(f"正在讀取 {view_date} 的資料..."):
    daily_stats = calculate_daily_summary(view_date)

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("💧 飲水", f"{int(daily_stats['water'])}", delta="目標 2400")
col2.metric("🔥 熱量", f"{int(daily_stats['cal'])}")
col3.metric("🥩 蛋白質", f"{int(daily_stats['prot'])}")
col4.metric("🍚 碳水", f"{int(daily_stats['carb'])}")
col5.metric("🥑 脂肪", f"{int(daily_stats['fat'])}")
st.divider()

# --- 分頁區 ---
tab1, tab2, tab3 = st.tabs(["⚖️ 體重", "📸 飲食 (自動時間)", "💧 飲水"])

# --- Tab 1: 體重 ---
with tab1:
    col_w1, col_w2 = st.columns([1, 2])
    with col_w1:
        st.subheader("新增體重")
        default_date_tw = datetime.now(TAIPEI_TZ).date()
        w_date = st.date_input("日期", default_date_tw, key="w_input_date")
        w_height = st.number_input("身高 (cm)", 100.0, 250.0, 170.0)
        w_weight = st.number_input("體重 (kg)", 0.0, 200.0, step=0.1, format="%.1f")
        if w_height > 0:
            bmi = w_weight / ((w_height / 100) ** 2)
            st.caption(f"BMI: {bmi:.1f}")
        if st.button("紀錄體重"):
            save_weight_data(w_date, w_height, w_weight, round(bmi, 1))
            st.success("已紀錄！")
            st.cache_data.clear() 

    with col_w2:
        try:
            df_weight = load_data(WEIGHT_SHEET_NAME)
            if not df_weight.empty and '體重' in df_weight.columns:
                st.line_chart(df_weight.set_index('日期')['體重'])
            else:
                st.info("尚無體重資料")
        except Exception:
            st.info("尚無體重資料，請先輸入。")

# --- Tab 2: 飲食 ---
with tab2:
    st.info("💡 提示：輸入「昨天中午吃的」，AI 會自動推算時間 (GMT+8)！")
    
    uploaded_file = st.file_uploader("上傳食物照片", type=["jpg", "png", "jpeg"])
    image = None
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption='預覽', width=300)
    
    food_input = st.text_input("文字補充", placeholder="例如：這是昨天晚上 7 點吃的牛肉麵")
    
    if st.button("AI 分析"):
        if uploaded_file or food_input:
            res = analyze_food_with_ai(image, food_input)
            if res:
                st.session_state['last_result'] = res

    if 'last_result' in st.session_state:
        res = st.session_state['last_result']
        st.markdown("#### 🍽️ 分析結果")
        
        default_date = datetime.now(TAIPEI_TZ).date()
        default_time = datetime.now(TAIPEI_TZ).time()
        
        if res.get('date'):
            try:
                default_date = datetime.strptime(res['date'], "%Y-%m-%d").date()
                st.toast(f"📅 AI 偵測到日期：{res['date']}", icon="✅")
            except: pass
            
        if res.get('time'):
            try:
                t_str = res['time']
                if len(t_str) == 5:
                    default_time = datetime.strptime(t_str, "%H:%M").time()
                    st.toast(f"⏰ AI 偵測到時間：{res['time']}", icon="✅")
            except: pass

        c_date, c_time = st.columns(2)
        sel_date = c_date.date_input("進食日期", default_date, key="f_input_date")
        sel_time = c_time.time_input("進食時間", default_time)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("熱量", res['calories'])
        c2.metric("蛋白質", res['protein'])
        c3.metric("碳水", res['carbs'])
        c4.metric("脂肪", res.get('fat', 0))
        
        st.write(f"**辨識內容：** {res['food_name']}")
        
        if st.button(f"📥 確認儲存"):
            final_time_str = sel_time.strftime("%H:%M")
            save_food_data(sel_date, final_time_str, res['food_name'], 
                           res['calories'], res['protein'], res['carbs'], res.get('fat', 0))
            st.success(f"已儲存於 {sel_date} {final_time_str}")
            del st.session_state['last_result']
            st.rerun()

# --- Tab 3: 飲水 ---
with tab3:
    st.subheader("💧 新增飲水")
    b1, b2, b3, b4 = st.columns(4)
    add_val = 0
    if b1.button("+ 100ml"): add_val = 100
    if b2.button("+ 300ml"): add_val = 300
    if b3.button("+ 500ml"): add_val = 500
    if b4.button("+ 700ml"): add_val = 700
    
    water_input = st.number_input("手動輸入 (ml)", 0, 2000, 0, step=50)
    
    final_water = 0
    if add_val > 0: final_water = add_val
    elif st.button("紀錄") and water_input > 0: final_water = water_input
        
    if final_water > 0:
        save_water_data(final_water)
        st.success(f"已紀錄 {final_water} ml")
        st.rerun()

    st.divider()
    
    df_w = load_data(WATER_SHEET_NAME)
    if not df_w.empty and '日期' in df_w.columns:
        view_date_str = str(view_date)
        st.caption(f"📅 {view_date_str} 的飲水明細：")
        st.dataframe(df_w[df_w['日期'].astype(str) == view_date_str], use_container_width=True)
