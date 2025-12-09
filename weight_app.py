import streamlit as st
import pandas as pd
import gspread
import google.generativeai as genai
from datetime import datetime, date, time
from PIL import Image
import pytz
import json # 引入 json 庫，用於安全解析
import altair as alt # 引入 altair 繪圖庫

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
        cols = len(HEADERS.get(sheet_name, [])) + 2
        ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=cols)
    
    # 智慧檢查與修復標題
    if sheet_name in HEADERS:
        expected_header = HEADERS[sheet_name]
        try:
            first_row = ws.row_values(1)
            is_data_in_header = False
            if first_row and len(first_row) > 0:
                # 簡單檢查：如果第一格包含 "-" 且長度像日期，或者是數字
                if "-" in str(first_row[0]) or str(first_row[0]).replace('.', '', 1).isdigit():
                    is_data_in_header = True

            if not first_row or first_row != expected_header or is_data_in_header:
                if first_row and first_row != expected_header:
                     ws.insert_row(expected_header, index=1)
                else:
                     ws.append_row(expected_header)
                st.cache_data.clear()
        except Exception as e:
            print(f"Error checking header for {sheet_name}: {e}")
            
    return ws

# --- 讀取配置 (目標) ---
@st.cache_data
def get_config():
    ws = get_google_sheet(CONFIG_SHEET_NAME)
    records = ws.get_all_records()
    config = {}
    for r in records:
        key = r.get('Key')
        val = r.get('Value')
        if key and val is not None:
            try:
                if float(val).is_integer():
                    config[key] = int(val)
                else:
                    config[key] = float(val)
            except ValueError:
                config[key] = val

    # 設定預設值 (針對衝刺計畫)
    if 'target_weight' not in config: config['target_weight'] = 75 
    if 'target_water' not in config: config['target_water'] = 2400
    # ✨ 新增營養目標預設值
    if 'target_cal' not in config: config['target_cal'] = 2200 
    if 'target_protein' not in config: config['target_protein'] = 140
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
        
        clean_json = text_resp.replace('```json', '').replace('```', '').strip()
        clean_json = clean_json.replace('```python', '').replace('```', '').strip()
        
        st.toast("✅ AI 分析完成！", icon="✨")
        return json.loads(clean_json) # 使用 json.loads 提升安全性
    except json.JSONDecodeError:
        st.error("❌ 錯誤：AI 回傳格式不正確 (JSON Decode Error)")
        return None
    except Exception as e:
        st.error(f"❌ 系統錯誤：{e}")
        return None

# --- 資料讀寫與計算 (簡化) ---
# ... save_config, save_weight_data, save_food_data, save_water_data 函式保持不變 ...
def save_config(key, value):
    ws = get_google_sheet(CONFIG_SHEET_NAME)
    try:
        cell = ws.find(key)
        ws.update_cell(cell.row, 2, value)
    except gspread.CellNotFound:
        ws.append_row([key, value])
    except Exception:
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
            water_col = '水量(ml)' if '水量(ml)' in df_target_water.columns else ('水量' if '水量' in df_target_water.columns else None)
            
            if water_col:
                totals['water'] = pd.to_numeric(df_target_water[water_col], errors='coerce').fillna(0).sum()
    except Exception: pass
        
    return totals

def calculate_daily_macros_goal(daily_stats, config):
    """計算並回傳今日營養目標達成狀況及建議"""
    
    target_cal = config.get('target_cal', 2200)
    target_protein = config.get('target_protein', 140)
    
    # 計算今日達成率
    cal_percent = (daily_stats['cal'] / target_cal) * 100 if target_cal > 0 else 0
    prot_percent = (daily_stats['prot'] / target_protein) * 100 if target_protein > 0 else 0
    
    # 計算宏量營養素比例 (Macros Ratio)
    total_g = daily_stats['prot'] + daily_stats['carb'] + daily_stats['fat']
    macros_data = pd.DataFrame({
        'Nutrient': ['蛋白質', '碳水化合物', '脂肪'],
        'Grams': [daily_stats['prot'], daily_stats['carb'], daily_stats['fat']]
    })
    macros_data['Percentage'] = (macros_data['Grams'] / total_g) * 100 if total_g > 0 else 0
    
    alerts = []
    if daily_stats['cal'] > target_cal * 1.1:
        alerts.append(("🔥 熱量超標", "今日熱量已超出目標 10%。建議控制下一餐攝取。", "red"))
    elif daily_stats['prot'] < target_protein * 0.8:
        alerts.append(("🥩 蛋白質不足", f"蛋白質攝取尚缺 {target_protein - daily_stats['prot']:.0f}g，請在睡前補充。", "orange"))
    
    return {
        'cal_percent': cal_percent,
        'prot_percent': prot_percent,
        'macros_data': macros_data,
        'alerts': alerts
    }


# ================= 介面開始 =================
st.set_page_config(layout="wide", page_title="健康管家 AI")
st.title('🥗 健康管家 AI')

# 讀取設定
config = get_config()
target_water = config.get('target_water', 2400)
target_weight = config.get('target_weight', 75)
target_cal = config.get('target_cal', 2200)
target_protein = config.get('target_protein', 140)


# --- 儀表板 ---
st.markdown("### 📅 每日攝取總覽")

col_date, col_empty = st.columns([1, 2])
with col_date:
    default_today = datetime.now(TAIPEI_TZ).date()
    view_date = st.date_input("🔍 選擇檢視日期", default_today)

with st.spinner(f"正在讀取 {view_date} 的資料..."):
    daily_stats = calculate_daily_summary(view_date)
    analysis = calculate_daily_macros_goal(daily_stats, config)

# 飲水 Delta
water_delta = f"目標 {target_water}"
if daily_stats['water'] < target_water * 0.9:
    water_delta = f"↓ 尚缺 {target_water - daily_stats['water']} ml"
elif daily_stats['water'] > target_water * 1.1:
    water_delta = f"↑ 超出 {daily_stats['water'] - target_water} ml"

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("💧 飲水", f"{int(daily_stats['water'])} ml", delta=water_delta)
col2.metric("🔥 熱量", f"{int(daily_stats['cal'])} kcal", delta=f"目標 {target_cal}")
col3.metric("🥩 蛋白質", f"{int(daily_stats['prot'])} g", delta=f"目標 {target_protein}")
col4.metric("🍚 碳水", f"{int(daily_stats['carb'])} g")
col5.metric("🥑 脂肪", f"{int(daily_stats['fat'])} g")
st.divider()

# --- 新增：目標達成與警示區 ---
st.markdown("### 🎯 衝刺計畫追蹤與警示")

if analysis['alerts']:
    for alert, message, color in analysis['alerts']:
        st.error(f"⚠️ {alert}: {message}")

col_p1, col_p2, col_p3 = st.columns(3)

# 1. 蛋白質達成率
col_p1.metric("蛋白質達成率", f"{analysis['prot_percent']:.1f} %", delta=f"目標 {target_protein}g")
col_p1.progress(min(analysis['prot_percent'] / 100, 1.0))

# 2. 熱量達成率
col_p2.metric("熱量達成率", f"{analysis['cal_percent']:.1f} %", delta=f"目標 {target_cal} kcal")
cal_progress_color = 'red' if analysis['cal_percent'] > 100 else 'green'
col_p2.progress(min(analysis['cal_percent'] / 100, 1.0)) # 顯示進度條

# 3. 宏量營養素圓餅圖
if not analysis['macros_data'].empty and analysis['macros_data']['Grams'].sum() > 0:
    chart = alt.Chart(analysis['macros_data']).mark_arc(outerRadius=120).encode(
        theta=alt.Theta(field="Grams", type="quantitative"),
        color=alt.Color(field="Nutrient", type="nominal"),
        order=alt.Order(field="Percentage", sort="descending"),
        tooltip=["Nutrient", "Grams", alt.Tooltip("Percentage", format=".1f")]
    ).properties(title="今日營養素比例 (P:C:F)")
    col_p3.altair_chart(chart, use_container_width=True)
else:
    col_p3.info("無數據，請先紀錄飲食。")
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
                y=alt.Y('體重:Q', title="體重 (kg)", scale=alt.Scale(zero=False))
            )
            line = chart_base.mark_line(point=True).encode(tooltip=['日期:T', '體重:Q'])
            
            # 目標線
            goal_line = alt.Chart(pd.DataFrame({'目標體重': [target_weight]})).mark_rule(color='red', strokeDash=[5, 5]).encode(y='目標體重')

            st.altair_chart(line + goal_line, use_container_width=True) 
            st.dataframe(df_weight.sort_values(by='日期', ascending=False).head(50), use_container_width=True)
        else:
            st.info("尚無體重資料")

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

    st.divider()
    df_food = load_data(FOOD_SHEET_NAME)
    if not df_food.empty:
        st.dataframe(df_food.sort_values(by=['日期', '時間'], ascending=False).head(50), use_container_width=True)

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
        st.dataframe(df_w.sort_values(by=['日期', '時間'], ascending=False).head(50), use_container_width=True)

# --- Tab 4: 設定 ---
with tab4:
    st.subheader("⚙️ 應用程式設定")
    st.markdown("設定你的健康追蹤目標")
    
    curr_w_target = float(target_weight)
    curr_water_target = int(target_water)
    curr_cal_target = int(target_cal)
    curr_protein_target = int(target_protein)


    st.markdown("#### 體重與飲水目標")
    new_target_weight = st.number_input("目標體重 (kg)", 30.0, 150.0, curr_w_target, key="set_target_w")
    new_target_water = st.number_input("每日飲水目標 (ml)", 1000, 5000, curr_water_target, step=100, key="set_target_h")

    st.markdown("#### 營養素目標 (衝刺計畫)")
    st.caption("建議高蛋白攝取，幫助維持肌肉量")
    new_target_cal = st.number_input("每日熱量目標 (kcal)", 1000, 5000, curr_cal_target, key="set_target_cal")
    new_target_protein = st.number_input("每日蛋白質目標 (g)", 50, 300, curr_protein_target, key="set_target_protein")
    
    if st.button("儲存目標設定"):
        save_config('target_weight', new_target_weight)
        save_config('target_water', new_target_water)
        save_config('target_cal', new_target_cal)
        save_config('target_protein', new_target_protein)
        st.success("✅ 設定已儲存！")
