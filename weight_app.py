import streamlit as st
import pandas as pd
import gspread
import google.generativeai as genai
from datetime import datetime, date, time
from PIL import Image
import pytz
import json 
import altair as alt 
import re

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
        WEIGHT_SHEET_NAME: ['日期', '身高', '體重', 'BMI', '腰圍'],
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

    # 🔥🔥🔥 1/1 衝刺計畫 (168 斷食版) 預設值 🔥🔥🔥
    if 'target_weight' not in config: config['target_weight'] = 75.0
    if 'target_water' not in config: config['target_water'] = 3000
    if 'target_cal' not in config: config['target_cal'] = 1500
    if 'target_protein' not in config: config['target_protein'] = 160
    
    return config

# --- 核心邏輯函式 ---


def analyze_food_with_ai(image_data, text_input):
    """(通用修正版) 增加 Token 上限並增強 JSON 清洗能力"""

    if "gemini_api_key" not in st.secrets:
        st.error("❌ Gemini API Key 尚未設定！")
        return None

    genai.configure(api_key=st.secrets["gemini_api_key"])

    # ---------------------------------------------------------
    # 🔧 設定模型：如果 1.5 不能用，請試試看以下幾個名稱：
    # 1. "gemini-pro" (最通用，但處理圖片能力較弱)
    # 2. "gemini-2.0-flash-exp" (如果你是想用最新的)
    # 3. 或是改回你原本的 "gemini-2.5-flash" (如果你確定這名稱對你的帳號有效)
    # ---------------------------------------------------------
    target_model_name = "gemini-2.5-flash"  # 這裡先預設嘗試 2.0，若不行請改回你原本的名稱

    try:
        model = genai.GenerativeModel(target_model_name)
    except Exception:
        # 如果指定的模型失敗，自動切換回最基本的 gemini-pro (純文字) 或提示錯誤
        st.warning(f"⚠️ 無法載入 {target_model_name}，嘗試切換至 gemini-pro...")
        model = genai.GenerativeModel("gemini-pro")

    now_dt = datetime.now(TAIPEI_TZ)
    current_time_str = now_dt.strftime("%Y-%m-%d %H:%M")

    prompt = f"""
你是一個專業營養師，正在協助使用者進行「168斷食減重衝刺」。
現在的時間是：{current_time_str}。

【專屬食物資料庫（優先使用）】
若食物描述中包含 “蛋白粉”、“Tryall”、“香醇可可”、“奶茶風味”，
請直接使用以下固定數值（每 25g）：
- 熱量：110 kcal
- 蛋白質：18 g
- 脂肪：2.6 g
- 碳水：3.8 g
依使用者描述自動換算份量（例如 1.6 杯就是上述數值乘以 1.6）。

【任務】
請分析飲食並輸出 JSON 格式。
重要：請務必輸出完整的 JSON，不要被截斷。
{{
  "food_name": "食物名稱",
  "calories": 數字(整數),
  "protein": 數字(小數點後一位),
  "carbs": 數字(小數點後一位),
  "fat": 數字(小數點後一位),
  "date": "YYYY-MM-DD",
  "time": "HH:MM"
}}
"""

    if text_input:
        prompt += f"\n使用者補充：{text_input}"

    contents = [prompt]
    
    # 圖片處理 (部分舊模型可能不支援圖片，這裡做防呆)
    if image_data:
        try:
            if "vision" in target_model_name or "flash" in target_model_name or "pro" in target_model_name:
                buf = BytesIO()
                image_data.save(buf, format="JPEG")
                img_bytes = buf.getvalue()
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                contents.append({"mime_type": "image/jpeg", "data": b64})
        except Exception as e:
            st.caption(f"⚠️ 略過圖片分析 (模型可能不支援或格式錯誤): {e}")

    try:
        st.toast(f"📡 AI 分析中 ({target_model_name})...", icon="⏳")

        # 🔥 關鍵修正：把 max_output_tokens 拉大，解決「JSON被切一半」的問題
        response = model.generate_content(
            contents,
            generation_config={
                "temperature": 0.7,
                "max_output_tokens": 2000, 
            }
        )

        raw = response.text
        
        # --- 強力清洗 JSON (Regex) ---
        # 就算 AI 回傳了 Markdown 或其他廢話，這段程式碼會硬抓出 JSON
        match = re.search(r'\{[\s\S]*\}', raw)
        
        if match:
            json_str = match.group(0)
            return json.loads(json_str)
        else:
            # 最後手段：嘗試清理 markdown 符號
            clean = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(clean)

    except json.JSONDecodeError:
        st.error("❌ JSON 解析失敗 (格式仍有誤)")
        st.markdown("#### AI 原始回傳：")
        st.code(raw)
        return None

    except Exception as e:
        st.error(f"❌ 發生錯誤 (可能是模型名稱無效): {e}")
        st.caption("建議：請在程式碼中修改 `target_model_name` 為你確認可用的模型 (例如 'gemini-pro')")
        return None


# --- 資料讀寫與計算 ---

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

def save_weight_data(d, h, w, b, waist): # 多一個 waist 參數
    ws = get_google_sheet(WEIGHT_SHEET_NAME)
    ws.append_row([str(d), h, w, b, waist]) # 寫入五個值
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
    
    try:
        df_food = load_data(FOOD_SHEET_NAME)
        if not df_food.empty and '日期' in df_food.columns:
            df_target = df_food[df_food['日期'].astype(str) == target_date_str]
            for col, key in [('熱量', 'cal'), ('蛋白質', 'prot'), ('碳水', 'carb'), ('脂肪', 'fat')]:
                if col in df_target.columns:
                    totals[key] = pd.to_numeric(df_target[col], errors='coerce').fillna(0).sum()
    except Exception: pass

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
    """計算並回傳今日營養目標達成狀況及建議 (168 衝刺版 - 熱量佔比修正)"""
    
    target_cal = config.get('target_cal', 1500)
    target_protein = config.get('target_protein', 160)
    
    # 計算今日達成率
    cal_percent = (daily_stats['cal'] / target_cal) * 100 if target_cal > 0 else 0
    prot_percent = (daily_stats['prot'] / target_protein) * 100 if target_protein > 0 else 0
    
    # --- 修改重點開始：計算各營養素的「熱量」而非僅是用「克數」 ---
    # 轉換係數：蛋白質 4kcal/g, 碳水 4kcal/g, 脂肪 9kcal/g
    prot_cal = daily_stats['prot'] * 4
    carb_cal = daily_stats['carb'] * 4
    fat_cal = daily_stats['fat'] * 9
    total_macro_cal = prot_cal + carb_cal + fat_cal

    macros_data = pd.DataFrame({
        'Nutrient': ['蛋白質', '碳水化合物', '脂肪'],
        'Grams': [daily_stats['prot'], daily_stats['carb'], daily_stats['fat']],
        'Calories': [prot_cal, carb_cal, fat_cal]  # 新增熱量欄位
    })
    
    # 百分比改用「熱量」來計算
    macros_data['Percentage'] = (macros_data['Calories'] / total_macro_cal) * 100 if total_macro_cal > 0 else 0
    # --- 修改重點結束 ---
    
    # 🔥🔥🔥 衝刺警示系統 (168 修正版) 🔥🔥🔥
    alerts = []
    
    # 1. 熱量控制
    if daily_stats['cal'] > target_cal:
        excess = daily_stats['cal'] - target_cal
        alerts.append(("🔥 熱量超標", f"已超出 {excess} kcal！請立即停止進食，喝水撐過剩下的斷食時間。", "red"))
    elif daily_stats['cal'] < target_cal * 0.5:
        alerts.append(("⚡ 熱量過低", "吃太少會掉肌肉！請在進食窗口內盡快補充足夠熱量。", "orange"))
        
    # 2. 蛋白質檢核
    if daily_stats['prot'] < target_protein:
        missing_prot = target_protein - daily_stats['prot']
        alerts.append(("🥩 蛋白質不足", f"還差 {missing_prot:.0f}g！請務必在「進食窗口結束前」補足。", "orange"))
        
    # 3. 碳水檢核
    if daily_stats['carb'] > 120:
        alerts.append(("🍚 碳水偏高", "今日碳水已超過 120g，會影響斷食燃脂效率。下一餐請只吃肉和菜。", "orange"))
    
    return {
        'cal_percent': cal_percent,
        'prot_percent': prot_percent,
        'macros_data': macros_data,
        'alerts': alerts
    }

# ================= 介面開始 =================
st.set_page_config(layout="wide", page_title="健康管家 AI - 168 衝刺版")
st.title('🚀 1/1 減重衝刺戰情室 (168 斷食)')

config = get_config()
target_water = config.get('target_water', 3000)
target_weight = config.get('target_weight', 75.0)
target_cal = config.get('target_cal', 1500)
target_protein = config.get('target_protein', 160)

# --- 儀表板 ---
st.markdown("### 📅 每日戰況")

col_date, col_empty = st.columns([1, 2])
with col_date:
    default_today = datetime.now(TAIPEI_TZ).date()
    view_date = st.date_input("🔍 檢視日期", default_today)

with st.spinner(f"正在讀取 {view_date} 資料..."):
    daily_stats = calculate_daily_summary(view_date)
    analysis = calculate_daily_macros_goal(daily_stats, config)

water_delta = f"目標 {target_water}"
if daily_stats['water'] < target_water:
    water_delta = f"⚠️ 還差 {target_water - daily_stats['water']} ml"
else:
    water_delta = "✅ 達標"

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("💧 飲水", f"{int(daily_stats['water'])} ml", delta=water_delta)
col2.metric("🔥 熱量", f"{int(daily_stats['cal'])} kcal", delta=f"上限 {target_cal}", delta_color="inverse")
col3.metric("🥩 蛋白質", f"{int(daily_stats['prot'])} g", delta=f"目標 {target_protein}")
col4.metric("🍚 碳水", f"{int(daily_stats['carb'])} g", delta="建議 < 100")
col5.metric("🥑 脂肪", f"{int(daily_stats['fat'])} g")
st.divider()

# --- 衝刺計畫追蹤與警示 ---
st.markdown("### 🎯 教練建議 (AI 監控中)")

if analysis['alerts']:
    for alert, message, color in analysis['alerts']:
        if color == "red":
            st.error(f"🛑 {alert}: {message}")
        else:
            st.warning(f"⚠️ {alert}: {message}")
else:
    if daily_stats['cal'] > 500:
        st.success("🌟 完美！今日飲食控制得非常好，請繼續保持！")

col_p1, col_p2, col_p3 = st.columns(3)

# 1. 蛋白質達成率
col_p1.metric("蛋白質達成率", f"{analysis['prot_percent']:.1f} %")
col_p1.progress(min(analysis['prot_percent'] / 100, 1.0))

# 2. 熱量消耗額度
calories_left = max(target_cal - daily_stats['cal'], 0)
col_p2.metric("今日剩餘熱量額度", f"{int(calories_left)} kcal")
prog_val = min(analysis['cal_percent'] / 100, 1.0)
col_p2.progress(prog_val)

# 3. 營養比例 (熱量佔比) - 修改版
# 使用 st.markdown 模擬 Metric 的標題樣式，讓三欄視覺對齊
col_p3.markdown("""
    <style>
    .macro-title {
        font-size: 14px;
        font-weight: 400;
        color: rgb(250, 250, 250);
        margin-bottom: 5px;
    }
    </style>
    <div class="macro-title">營養素熱量比例 (kcal)</div>
    """, unsafe_allow_html=True)

if not analysis['macros_data'].empty and analysis['macros_data']['Calories'].sum() > 0:
    chart = alt.Chart(analysis['macros_data']).mark_arc(outerRadius=85).encode(
        # 關鍵：這裡指定使用 "Calories" (熱量) 作為角度
        theta=alt.Theta(field="Calories", type="quantitative"),
        # 指定顏色：蛋白(紅), 碳水(藍), 脂肪(黃)
        color=alt.Color(field="Nutrient", type="nominal", 
                        scale=alt.Scale(domain=['蛋白質', '碳水化合物', '脂肪'], 
                                      range=['#FF4B4B', '#3186CC', '#FFAA00']),
                        legend=None), # 隱藏圖例以節省空間，改用 Tooltip
        order=alt.Order(field="Percentage", sort="descending"),
        tooltip=[
            "Nutrient", 
            alt.Tooltip("Grams", format=".1f", title="重量(g)"), 
            alt.Tooltip("Calories", format=".0f", title="熱量(kcal)"),
            alt.Tooltip("Percentage", format=".1f", title="熱量佔比(%)")
        ]
    )
    col_p3.altair_chart(chart, use_container_width=True)
else:
    col_p3.info("尚無數據")
st.divider()

# --- 分頁區 ---
tab1, tab2, tab3, tab4 = st.tabs(["⚖️ 體重 & 目標", "📸 飲食分析", "💧 飲水", "⚙️ 設定"])

# --- Tab 1: 體重 & 目標 ---
# --- Tab 1: 體重 & 目標 ---
with tab1:
    col_w1, col_w2 = st.columns([1, 2])
    with col_w1:
        st.markdown("#### 紀錄身體數據") # 改一下標題
        # ... (日期、身高、體重程式碼不變) ...
        w_weight = st.number_input("體重 (kg)", 0.0, 200.0, step=0.1, format="%.1f")
        
        # 🔥 新增腰圍輸入
        w_waist = st.number_input("腰圍 (cm)", 40.0, 150.0, step=0.1, format="%.1f")
        
        # ... (BMI 計算不變) ...
            
        if st.button("紀錄數據"):
            # 呼叫更新後的函式
            save_weight_data(w_date, w_height, w_weight, round(bmi, 1), w_waist)
            st.success("✅ 紀錄成功！")
            st.rerun()

    with col_w2:
        df_weight = load_data(WEIGHT_SHEET_NAME)
        if not df_weight.empty and '體重' in df_weight.columns:
            df_weight['日期'] = pd.to_datetime(df_weight['日期'])
            chart_base = alt.Chart(df_weight).encode(
                x=alt.X('日期:T', title="日期"), 
                y=alt.Y('體重:Q', title="體重 (kg)", scale=alt.Scale(zero=False))
            )
            line = chart_base.mark_line(point=True, color='#29B5E8').encode(tooltip=['日期:T', '體重:Q'])
            goal_line = alt.Chart(pd.DataFrame({'目標體重': [target_weight]})).mark_rule(color='#FF4B4B', strokeDash=[5, 5], size=2).encode(y='目標體重')
            text = alt.Chart(pd.DataFrame({'y': [target_weight], 'text': [f'目標 {target_weight}kg']})).mark_text(align='left', dx=5, dy=-5, color='#FF4B4B').encode(y='y', text='text')
            st.altair_chart(line + goal_line + text, use_container_width=True)
            st.dataframe(df_weight.sort_values(by='日期', ascending=False).head(50), use_container_width=True)
        else:
            st.info("尚無體重資料")

# --- Tab 2: 飲食 ---
with tab2:
    st.info("💡 168 斷食提示：請確保所有進食都在 8 小時窗口內完成！")
    col_f1, col_f2 = st.columns([1, 2])
    with col_f1:
        uploaded_file = st.file_uploader("📸 上傳食物照片", type=["jpg", "png", "jpeg"])
        image = None
        if uploaded_file:
            image = Image.open(uploaded_file).convert('RGB') # <--- 修改這一行 (第 248 行)
            st.image(image, caption='預覽', use_container_width=True)
        
        food_input = st.text_input("文字補充", placeholder="例如：去皮雞腿便當，飯只吃一半")
        
        if st.button("🍱 AI 分析"):
            if uploaded_file or food_input:
                res = analyze_food_with_ai(image, food_input)
                if res: st.session_state['last_result'] = res

    with col_f2:
        if 'last_result' in st.session_state:
            res = st.session_state['last_result']
            st.markdown("#### 🍽️ 分析結果")
            
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
    st.markdown(f"**今日目標:** {target_water} ml (喝水不破壞斷食，多喝！)")
    
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
    st.subheader("⚙️ 衝刺計畫設定")
    curr_w_target = float(target_weight)
    curr_water_target = int(target_water)
    curr_cal_target = int(target_cal)
    curr_protein_target = int(target_protein)

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        st.markdown("#### 體重與飲水")
        new_target_weight = st.number_input("目標體重 (kg)", 30.0, 150.0, curr_w_target, key="set_target_w")
        new_target_water = st.number_input("每日飲水目標 (ml)", 1000, 5000, curr_water_target, step=100, key="set_target_h")
    
    with col_s2:
        st.markdown("#### 營養素目標")
        new_target_cal = st.number_input("每日熱量上限 (kcal)", 1000, 5000, curr_cal_target, key="set_target_cal")
        new_target_protein = st.number_input("每日蛋白質目標 (g)", 50, 300, curr_protein_target, key="set_target_protein")
    
    if st.button("更新設定"):
        save_config('target_weight', new_target_weight)
        save_config('target_water', new_target_water)
        save_config('target_cal', new_target_cal)
        save_config('target_protein', new_target_protein)
        st.success("✅ 設定已更新！")














