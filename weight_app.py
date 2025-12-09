import streamlit as st
import pandas as pd
import gspread
import google.generativeai as genai
from datetime import datetime, date
from PIL import Image
import pytz 

# --- 設定區 ---
SHEET_ID = 'My Weight Data'  # 你的試算表名稱
WEIGHT_SHEET_NAME = '工作表1' # 請確認這跟你的體重分頁名稱一樣
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
        # 如果找不到分頁，自動創一個 (現在多加了脂肪欄位)
        if sheet_name == FOOD_SHEET_NAME:
            new_sheet = sh.add_worksheet(title=sheet_name, rows=1000, cols=7) # 改成 7 欄
            new_sheet.append_row(['日期', '時間', '食物名稱', '熱量', '蛋白質', '碳水', '脂肪'])
        else:
            new_sheet = sh.add_worksheet(title=sheet_name, rows=1000, cols=4)
        return new_sheet

# --- 2. 設定 Google AI (Gemini) ---
if "gemini_api_key" in st.secrets:
    genai.configure(api_key=st.secrets["gemini_api_key"])
else:
    st.error("⚠️ 尚未設定 Gemini API Key！請去 Secrets 貼上。")

def analyze_food_with_ai(image_data, text_input):
    """
    VIP 升級版：使用 gemini-2.5-flash
    新增功能：回傳「脂肪」數據
    """
    # 使用你測試成功的 2.5 Flash
    model_name = 'gemini-2.5-flash'
    model = genai.GenerativeModel(model_name)
    
    prompt = """
    你是一個專業營養師。請分析這份飲食。
    請估算它的：
    1. 熱量(大卡)
    2. 蛋白質(克)
    3. 碳水化合物(克)
    4. 脂肪(克)  <-- 新增這個
    
    請直接回傳一個 JSON 格式，不要有markdown標記，格式如下：
    {
        "food_name": "食物簡稱",
        "calories": 數字,
        "protein": 數字,
        "carbs": 數字,
        "fat": 數字
    }
    """
    if text_input:
        prompt += f"\n使用者補充說明：{text_input}"

    inputs = [prompt]
    if image_data:
        inputs.append(image_data)
        
    try:
        st.toast(f"📡 呼叫 {model_name} 分析營養中...", icon="🚀")
        response = model.generate_content(inputs)
        st.toast("✅ 分析完成！", icon="✨")
        
        clean_json = response.text.replace('```json', '').replace('```', '').strip()
        return eval(clean_json)

    except Exception as e:
        st.error(f"❌ 發生錯誤：{e}")
        return None

# --- 3. 讀寫資料函式 ---
def save_weight_data(d, h, w, b):
    ws = get_google_sheet(WEIGHT_SHEET_NAME)
    ws.append_row([str(d), h, w, b])

# 新增 fat 參數
def save_food_data(date_str, time_str, food, cal, prot, carb, fat):
    ws = get_google_sheet(FOOD_SHEET_NAME)
    # 寫入 7 個欄位
    ws.append_row([str(date_str), str(time_str), food, cal, prot, carb, fat])

def load_data(sheet_name):
    ws = get_google_sheet(sheet_name)
    records = ws.get_all_records()
    return pd.DataFrame(records)

# ================= 介面開始 =================
st.title('🥗 健康管家 & 體重追蹤')

tab1, tab2 = st.tabs(["⚖️ 體重紀錄", "📸 飲食紀錄 (含脂肪)"])

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
            st.cache_data.clear()

    with col2:
        try:
            df_weight = load_data(WEIGHT_SHEET_NAME)
            if not df_weight.empty:
                st.subheader("📊 體重趨勢")
                st.line_chart(df_weight.set_index('日期')['體重'])
        except Exception as e:
            st.info("👈 尚無資料，請先輸入第一筆！")

# --- 分頁 2: 飲食 (四欄位版) ---
with tab2:
    st.info("💡 拍張照，AI 會幫你算 熱量、蛋白質、碳水 和 脂肪！")
    
    uploaded_file = st.file_uploader("📸 上傳食物照片", type=["jpg", "png", "jpeg"])
    image = None
    if uploaded_file is not None:
        image = Image.open(uploaded_file)
        st.image(image, caption='預覽照片', use_container_width=True)
    
    food_input = st.text_input("文字補充 (例如：飯只吃一半)", placeholder="也可以不傳照片，直接打字喔！")
    
    if st.button("🍱 AI 幫我算熱量"):
        if uploaded_file or food_input:
            with st.spinner('AI 正在分析...'):
                result = analyze_food_with_ai(image, food_input)
            
            if result and result.get('calories', 0) > 0:
                # 顯示結果 (變成 4 個圈圈)
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("🔥 熱量", f"{result['calories']} kcal")
                c2.metric("🥩 蛋白質", f"{result['protein']} g")
                c3.metric("🍚 碳水", f"{result['carbs']} g")
                # 新增脂肪顯示
                c4.metric("🥑 脂肪", f"{result.get('fat', 0)} g")
                
                st.write(f"**辨識結果：** {result['food_name']}")
                
                # 使用 session_state 暫存結果
                st.session_state['last_result'] = result

# 顯示儲存按鈕 (獨立出來以免消失)
if 'last_result' in st.session_state:
    res = st.session_state['last_result']
    if st.button(f"📥 儲存：{res['food_name']}"):
        # 修正：強制設定時區為 台北時間 (GMT+8)
        TAIPEI_TZ = pytz.timezone('Asia/Taipei')
        now_time = datetime.now(TAIPEI_TZ).strftime("%H:%M")
        
        # 這裡呼叫儲存函式
        save_food_data(date.today(), now_time, res['food_name'], 
                       res['calories'], res['protein'], res['carbs'], res.get('fat', 0))
        
        # --- 這裡修正了縮排與語法錯誤 ---
        st.success(f"已儲存！ (含脂肪 {res.get('fat', 0)}g)")
        
        # 刪除暫存狀態，讓按鈕消失避免重複按
        del st.session_state['last_result']




