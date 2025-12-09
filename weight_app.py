import streamlit as st
import pandas as pd
import gspread
import google.generativeai as genai
from datetime import datetime, date, time
from PIL import Image
import pytz

# --- 設定區 ---
SHEET_ID = 'My Weight Data'
WEIGHT_SHEET_NAME = 'Weight Log'
FOOD_SHEET_NAME = 'Food Log'
WATER_SHEET_NAME = 'Water Log'
CONFIG_SHEET_NAME = 'Config' # 新增配置分頁

# 設定時區
TAIPEI_TZ = pytz.timezone('Asia/Taipei')

# --- 1. 連接 Google Sheets (最終修復版) ---
@st.cache_resource
def get_google_sheet(sheet_name):
    """取得 Google Sheet 分頁並進行標題修復"""
    credentials = st.secrets["service_account_info"]
    gc = gspread.service_account_from_dict(credentials)
    sh = gc.open(SHEET_ID)
    
    # 定義標準標題
    HEADERS = {
        FOOD_SHEET_NAME: ['日期', '時間', '食物名稱', '熱量', '蛋白質', '碳水', '脂肪'],
        WATER_SHEET_NAME: ['日期', '時間', '水量(ml)'],
        WEIGHT_SHEET_NAME: ['日期', '身高', '體重', 'BMI'],
        CONFIG_SHEET_NAME: ['Key', 'Value'] # 新增配置標題
    }
    
    try:
        ws = sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        # 建立新分頁
        cols = len(HEADERS.get(sheet_name, [])) + 1 # 確保有足夠的欄位
        ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=cols)
    
    # --- 智慧檢查與修復標題 ---
    if sheet_name in HEADERS:
        expected_header = HEADERS[sheet_name]
        try:
            first_row = ws.row_values(1)
            
            if not first_row or first_row != expected_header:
                if not first_row or len(first_row) < len(expected_header) or first_row[0] not in expected_header:
                    # 情況 A/B: 完全空白或標題不符，強制在頂部插入標題
                    ws.insert_row(expected_header, index=1)
                    st.cache_data.clear()
        except Exception as e:
            # 處理 Sheet 讀取錯誤，例如網路問題
            print(f"Error checking header for {sheet_name}: {e}")
            
    return ws

# --- 讀取配置 (用於目標體重) ---
@st.cache_data
def get_config():
    ws = get_google_sheet(CONFIG_SHEET_NAME)
    records = ws.get_all_records()
    config = {r['Key']: r['Value'] for r in records if 'Key' in r and 'Value' in r}
    
    # 設定預設值
    if 'target_weight' not in config: config['target_weight'] = 75 
    if 'target_water' not in config: config['target_water'] = 2400
    return config

# --- 核心邏輯函式 ---

def analyze_food_with_ai(image_data, text_input):
    """呼叫 Gemini 進行飲食分析"""
    if "gemini_api_key" not in st.secrets:
        st.error("❌ Gemini API Key 尚未設定！")
        return None
        
    model_name = 'gemini-2.5-flash'
    model = genai.GenerativeModel(model_name)
    now_dt = datetime.now(TAIPEI_TZ)
    current_time_str = now_dt.strftime("%Y-%m-%d %H:%M")
    
    prompt = f"""
    你是一個專業營養師。現在的時間是：{current_time_str} (GMT+8 台北時間)。
    請分析這份飲食，並根據使用者的文字描述推斷「進食時間」。
    任務：
    1. 估算營養：熱量(kcal), 蛋白質(g), 碳水(g), 脂肪(g)
    2. 推斷時間：如果使用者說 "早上8點吃的"，請推算 date (YYYY-MM-DD) 和 time (HH:MM)。
    
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
        st.toast("📡 AI 分析中...", icon="🕒")
        response = model.generate_content(inputs)
        clean_json = response.text.replace('```json', '').replace('```', '').strip()
        st.toast("✅ AI 分析完成！", icon="✨")
        return eval(clean_json)
    except Exception as e:
        st.error(f"❌ 錯誤：AI 無法解析回應，請檢查輸入或稍後再試。詳細錯誤：{e}")
        return None

# --- 資料讀寫與計算 ---

def save_config(key, value):
    ws = get_google_sheet(CONFIG_SHEET_NAME)
    records = ws.get_all_records()
    
    found = False
    for i, r in enumerate(records):
        if r.get('Key') == key:
            ws.update_cell(i + 2, 2, value) # +2 是因為有標題列，且 gspread 從 1 開始
            found = True
            break
    if not found:
        ws.append_row([key, value])
    st.cache_data.clear() # 清除配置快取

def save_weight_data(d, h, w, b):
    ws = get_google_sheet(WEIGHT_SHEET_NAME)
    ws.append_row([str(d), h, w, b])
    st.cache_data.clear()

def save_food_data(date_str, time_str, food, cal, prot, carb, fat):
    ws = get_google_sheet(FOOD_SHEET_NAME)
    ws.append_row([str(date_str), str(time_str), food, cal, prot, carb, fat])
    st.cache_data.clear()

def save_water_data(vol): 
    ws = get_google_sheet(WATER_SHEET_NAME)
    now_date = datetime.now(TAIPEI_TZ).date()
    now_time = datetime.now(TAIPEI_TZ).strftime("%H:%M")
    ws.append_row([str(now_date), str(now_time), vol])
    st.cache_data.clear()

def load_data(sheet_name):
    ws = get_google_sheet(sheet_name)
    try:
        records = ws.get_all_records()
        if not records: return pd.DataFrame()
        # 將 '日期' 列轉換為字串格式，避免 Pandas 讀取錯誤
        df = pd.DataFrame(records)
        if '日期' in df.columns:
            df['日期'] = df['日期'].astype(str)
        return df
    except Exception:
        return pd.DataFrame()

def calculate_daily_summary(target_date):
    """計算指定日期的總營養攝取"""
    target_date_str = str(target_date)
    totals = {'cal': 0, 'prot': 0, 'carb': 0, 'fat': 0, 'water': 0}
    
    # 1. 計算食物
    try:
        df_food = load_data(FOOD_SHEET_NAME)
        if not df_food.empty and '日期' in df_food.columns:
            df_target = df_food[df_food['日期'] == target_date_str]
            for col, key in [('熱量', 'cal'), ('蛋白質', 'prot'), ('碳水', 'carb'), ('脂肪', 'fat')]:
                if col in df_target.columns:
                    totals[key] = pd.to_numeric(df_target[col], errors='coerce').fillna(0).sum()
    except Exception: pass

    # 2. 計算飲水
    try:
        df_water = load_data(WATER_SHEET_NAME)
        if not df_water.empty and '日期' in df_water.columns:
            df_target_water = df_water[df_water['日期'] == target_date_str]
            water_col = '水量(ml)' if '水量(ml)' in df_target_water.columns else ('水量' if '水量' in df_target_water.columns else None)
            
            if water_col:
                totals['water'] = pd.to_numeric(df_target_water[water_col], errors='coerce').fillna(0).sum()
    except Exception: pass
        
    return totals

# ================= 介面開始 =================
st.set_page_config(layout="wide", page_title="健康管家 AI")
st.title('🥗 健康管家 AI')
config = get_config()
target_water = config.get('target_water', 2400)
target_weight = config.get('target_weight', 75)

# --- 儀表板 ---
st.markdown("### 📅 每日攝取總覽")

col_date, col_empty = st.columns([1, 2])
with col_date:
    default_today = datetime.now(TAIPEI_TZ).date()
    view_date = st.date_input("🔍 選擇檢視日期", default_today)

with st.spinner(f"正在讀取 {view_date} 的資料..."):
    daily_stats = calculate_daily_summary(view_date)

water_delta = f"目標 {target_water}"
if daily_stats['water'] < target_water:
    water_delta = f"↓ 尚缺 {target_water - daily_stats['water']} ml"
elif daily_stats['water'] > target_water:
    water_delta = f"↑ 超出 {daily_stats['water'] - target_water} ml"

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("💧 飲水", f"{int(daily_stats['water'])} ml", delta=water_delta)
col2.metric("🔥 熱量", f"{int(daily_stats['cal'])} kcal")
col3.metric("🥩 蛋白質", f"{int(daily_stats['prot'])} g")
col4.metric("🍚 碳水", f"{int(daily_stats['carb'])} g")
col5.metric("🥑 脂肪", f"{int(daily_stats['fat'])} g")
st.divider()

# --- 分頁區 ---
tab1, tab2, tab3, tab4 = st.tabs(["⚖️ 體重 & 目標", "📸 飲食分析", "💧 飲水", "⚙️ 設定"])

# --- Tab 1: 體重 & 目標 ---
with tab1:
    st.subheader("體重趨勢與目標追蹤")
    col_w1, col_w2 = st.columns([1, 2])
    with col_w1:
        st.markdown("#### 紀錄體重")
        default_date_tw = datetime.now(TAIPEI_TZ).date()
        w_date = st.date_input("日期", default_date_tw, key="w_input_date")
        w_height = st.number_input("身高 (cm)", 100.0, 250.0, 170.0)
        w_weight = st.number_input("體重 (kg)", 0.0, 200.0, step=0.1, format="%.1f")
        if w_height > 0:
            bmi = w_weight / ((w_height / 100) ** 2)
            st.caption(f"BMI: {bmi:.1f}")
        if st.button("紀錄體重"):
            save_weight_data(w_date, w_height, w_weight, round(bmi, 1))
            st.success("✅ 紀錄成功！")
            st.rerun()

    with col_w2:
        try:
            df_weight = load_data(WEIGHT_SHEET_NAME)
            if not df_weight.empty and '體重' in df_weight.columns:
                df_weight['日期'] = pd.to_datetime(df_weight['日期'])
                
                # 繪製目標線
                df_plot = df_weight.set_index('日期')['體重']
                
                import altair as alt # 引入 altair 繪圖
                
                # 建立主趨勢圖
                chart_base = alt.Chart(df_plot.reset_index()).encode(
                    x=alt.X('日期:T', title="日期"), 
                    y=alt.Y('體重:Q', title="體重 (kg)")
                )
                
                # 體重折線
                line = chart_base.mark_line(point=True).encode(
                    tooltip=['日期:T', '體重:Q']
                )

                # 目標虛線
                goal_line = alt.Chart(pd.DataFrame({'目標體重': [target_weight]})).mark_rule(color='red', strokeDash=[5, 5]).encode(
                    y='目標體重'
                )

                st.altair_chart(line + goal_line, use_container_width=True)
                st.dataframe(df_weight.sort_values(by='日期', ascending=False), use_container_width=True)
            else: st.info("尚無體重資料")
        except: st.info("尚無體重資料或數據格式錯誤")

# --- Tab 2: 飲食 ---
with tab2:
    st.subheader("AI 視覺化飲食紀錄")
    st.info("💡 提示：輸入「昨天中午吃的」，AI 會自動推算時間！")
    col_f1, col_f2 = st.columns([1, 2])
    with col_f1:
        uploaded_file = st.file_uploader("📸 上傳食物照片", type=["jpg", "png", "jpeg"])
        image = None
        if uploaded_file:
            image = Image.open(uploaded_file)
            st.image(image, caption='預覽', use_container_width=True)
        
        food_input = st.text_input("文字補充", placeholder="例如：這是昨天晚上 7 點吃的牛肉麵")
        
        if st.button("🍱 AI 分析"):
            if uploaded_file or food_input:
                res = analyze_food_with_ai(image, food_input)
                if res: st.session_state['last_result'] = res

    with col_f2:
        if 'last_result' in st.session_state:
            res = st.session_state['last_result']
            st.markdown("#### 🍽️ 分析結果確認")
            
            # 嘗試解析 AI 推算的日期時間
            default_date = datetime.now(TAIPEI_TZ).date()
            default_time = datetime.now(TAIPEI_TZ).time()
            
            if res.get('date'):
                try: default_date = datetime.strptime(res['date'], "%Y-%m-%d").date()
                except: pass
            if res.get('time'):
                try: default_time = datetime.strptime(res['time'], "%H:%M").time()
                except: pass

            c_date, c_time = st.columns(2)
            sel_date = c_date.date_input("進食日期", default_date, key="f_input_date")
            sel_time = c_time.time_input("進食時間", default_time)

            st.markdown(f"**辨識：** {res['food_name']}")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("熱量", res['calories'])
            c2.metric("蛋白質", res['protein'])
            c3.metric("碳水", res['carbs'])
            c4.metric("脂肪", res.get('fat', 0))
            
            if st.button(f"📥 確認儲存"):
                save_food_data(sel_date, sel_time.strftime("%H:%M"), res['food_name'], 
                                 res['calories'], res['protein'], res['carbs'], res.get('fat', 0))
                st.success(f"✅ 已儲存！")
                del st.session_state['last_result']
                st.rerun()
        else:
            st.info("請上傳圖片或輸入文字進行分析。")

    st.divider()
    df_food = load_data(FOOD_SHEET_NAME)
    if not df_food.empty:
        st.dataframe(df_food.sort_values(by=['日期', '時間'], ascending=False), use_container_width=True)

# --- Tab 3: 飲水 ---
with tab3:
    st.subheader("💧 飲水紀錄")
    b1, b2, b3, b4 = st.columns(4)
    add_val = 0
    
    st.markdown(f"**今日目標:** {target_water} ml")
    
    if b1.button("+ 100ml"): add_val = 100
    if b2.button("+ 300ml"): add_val = 300
    if b3.button("+ 500ml"): add_val = 500
    if b4.button("+ 700ml"): add_val = 700
    
    st.caption("--- 或 ---")
    water_input = st.number_input("手動輸入 (ml)", 0, 2000, 0, step=50, key="manual_water_input")
    if st.button("紀錄手動輸入"): add_val = water_input
    
    if add_val > 0:
        save_water_data(add_val)
        st.success(f"已紀錄 {add_val} ml")
        st.rerun()

    st.divider()
    df_w = load_data(WATER_SHEET_NAME)
    if not df_w.empty:
        st.dataframe(df_w.sort_values(by=['日期', '時間'], ascending=False), use_container_width=True)

# --- Tab 4: 設定 ---
with tab4:
    st.subheader("⚙️ 應用程式設定")
    st.markdown("設定你的健康追蹤目標")
    
    new_target_weight = st.number_input("目標體重 (kg)", 30.0, 150.0, float(target_weight), key="set_target_w")
    new_target_water = st.number_input("每日飲水目標 (ml)", 1000, 5000, int(target_water), step=100, key="set_target_h")
    
    if st.button("儲存目標設定"):
        save_config('target_weight', new_target_weight)
        save_config('target_water', new_target_water)
        st.success("✅ 設定已儲存！請重新整理網頁查看效果。")
