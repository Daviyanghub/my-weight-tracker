import streamlit as st
import pandas as pd
import gspread
import google.generativeai as genai
from datetime import datetime, date, time
from PIL import Image
import pytz
import json  # ✨ [新增] 用於安全解析 JSON
import altair as alt # ✨ [移動] 移到最上方

# --- 設定區 ---
SHEET_ID = 'My Weight Data'
WEIGHT_SHEET_NAME = 'Weight Log'
FOOD_SHEET_NAME = 'Food Log'
WATER_SHEET_NAME = 'Water Log'
CONFIG_SHEET_NAME = 'Config'

# 設定時區
TAIPEI_TZ = pytz.timezone('Asia/Taipei')

# --- 1. 連接 Google Sheets ---
@st.cache_resource
def get_google_sheet(sheet_name):
    """取得 Google Sheet 分頁並進行標題修復"""
    credentials = st.secrets["service_account_info"]
    gc = gspread.service_account_from_dict(credentials)
    sh = gc.open(SHEET_ID)
    
    HEADERS = {
        FOOD_SHEET_NAME: ['日期', '時間', '食物名稱', '熱量', '蛋白質', '碳水', '脂肪'],
        WATER_SHEET_NAME: ['日期', '時間', '水量(ml)'],
        WEIGHT_SHEET_NAME: ['日期', '身高', '體重', 'BMI'],
        CONFIG_SHEET_NAME: ['Key', 'Value']
    }
    
    try:
        ws = sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        cols = len(HEADERS.get(sheet_name, [])) + 2 # ✨ [優化] 多預留一點空間
        ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=cols)
    
    # 智慧檢查與修復標題
    if sheet_name in HEADERS:
        expected_header = HEADERS[sheet_name]
        try:
            first_row = ws.row_values(1)
            # ✨ [優化] 增加判斷：如果第一格是日期格式(例如 2025-...)，代表標題遺失
            is_data_in_header = False
            if first_row and len(first_row) > 0:
                # 簡單檢查：如果第一格包含 "-" 且長度像日期，或者是數字
                if "-" in str(first_row[0]) or str(first_row[0]).isdigit():
                    is_data_in_header = True

            if not first_row or first_row != expected_header or is_data_in_header:
                # 若原本有資料但沒標題，插入標題
                if first_row and first_row != expected_header:
                     ws.insert_row(expected_header, index=1)
                # 若完全空白，附加標題
                else:
                     ws.append_row(expected_header)
                st.cache_data.clear()
        except Exception as e:
            print(f"Error checking header for {sheet_name}: {e}")
            
    return ws

# --- 讀取配置 ---
@st.cache_data
def get_config():
    ws = get_google_sheet(CONFIG_SHEET_NAME)
    records = ws.get_all_records()
    # ✨ [優化] 強制轉換 Value 為 float/int，避免字串計算錯誤
    config = {}
    for r in records:
        key = r.get('Key')
        val = r.get('Value')
        if key and val is not None:
            try:
                # 嘗試轉為數字
                if float(val).is_integer():
                    config[key] = int(val)
                else:
                    config[key] = float(val)
            except ValueError:
                config[key] = val # 保持原樣 (如果是字串設定)

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
    
    請直接回傳標準 JSON 格式，不要包含 ```json 或 markdown 標記：
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
        text_resp = response.text
        
        # ✨ [優化] 清理字串並使用 json.loads 取代 eval
        clean_json = text_resp.replace('```json', '').replace('```', '').strip()
        # 有時候 AI 會回傳 ```python ... ```，一併清理
        clean_json = clean_json.replace('```python', '').replace('```', '').strip()
        
        st.toast("✅ AI 分析完成！", icon="✨")
        return json.loads(clean_json) # ⚠️ [安全性修正]
    except json.JSONDecodeError:
        st.error("❌ 錯誤：AI 回傳格式不正確 (JSON Error)")
        return None
    except Exception as e:
        st.error(f"❌ 系統錯誤：{e}")
        return None

# --- 資料讀寫與計算 ---

def save_config(key, value):
    ws = get_google_sheet(CONFIG_SHEET_NAME)
    # 尋找是否已存在 Key
    try:
        cell = ws.find(key)
        ws.update_cell(cell.row, 2, value)
    except gspread.CellNotFound:
        ws.append_row([key, value])
    except Exception:
        # 如果 find 失敗的備用方案 (遍歷)
        records = ws.get_all_records()
        found = False
        for i, r in enumerate(records):
            if r.get('Key') == key:
                ws.update_cell(i + 2, 2, value)
                found = True
                break
        if not found:
            ws.append_row([key, value])
            
    st.cache_data.clear()

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
        df = pd.DataFrame(records)
        if '日期' in df.columns:
            # ✨ [優化] 統一轉成 datetime 後再轉 str，確保格式一致
            df['日期'] = pd.to_datetime(df['日期'], errors='coerce').dt.strftime('%Y-%m-%d')
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
            # ✨ [優化] 確保比對時都是字串
            df_target = df_food[df_food['日期'].astype(str) == target_date_str]
            for col, key in [('熱量', 'cal'), ('蛋白質', 'prot'), ('碳水', 'carb'), ('脂肪', 'fat')]:
                if col in df_target.columns:
                    totals[key] = pd.to_numeric(df_target[col], errors='coerce').fillna(0).sum()
    except Exception: pass

    # 2. 計算飲水
    try:
        df_water = load_data(WATER_SHEET_NAME)
        if not df_water.empty and '日期' in df_water.columns:
            df_target_water = df_water[df_water['日期'].astype(str) == target_date_str]
            # 兼容舊標題
            water_col = '水量(ml)' if '水量(ml)' in df_target_water.columns else ('水量' if '水量' in df_target_water.columns else None)
            
            if water_col:
                totals['water'] = pd.to_numeric(df_target_water[water_col], errors='coerce').fillna(0).sum()
    except Exception: pass
        
    return totals

# ================= 介面開始 =================
st.set_page_config(layout="wide", page_title="健康管家 AI")
st.title('🥗 健康管家 AI')

# 讀取設定
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
        
        bmi = 0
        if w_height > 0:
            bmi = w_weight / ((w_height / 100) ** 2)
            st.caption(f"BMI: {bmi:.1f}")
            
        if st.button("紀錄體重"):
            save_weight_data(w_date, w_height, w_weight, round(bmi, 1))
            st.success("✅ 紀錄成功！")
            st.rerun()

    with col_w2:
        df_weight = load_data(WEIGHT_SHEET_NAME)
        if not df_weight.empty and '體重' in df_weight.columns:
            df_weight['日期'] = pd.to_datetime(df_weight['日期'])
            
            # 繪製圖表
            chart_base = alt.Chart(df_weight).encode(
                x=alt.X('日期:T', title="日期"), 
                y=alt.Y('體重:Q', title="體重 (kg)", scale=alt.Scale(zero=False)) # ✨ [優化] zero=False 讓曲線變化更明顯
            )
            line = chart_base.mark_line(point=True).encode(tooltip=['日期:T', '體重:Q'])
            
            # 目標線
            goal_line = alt.Chart(pd.DataFrame({'目標體重': [target_weight]})).mark_rule(color='red', strokeDash=[5, 5]).encode(y='目標體重')

            st.altair_chart(
