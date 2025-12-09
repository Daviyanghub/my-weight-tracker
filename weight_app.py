import streamlit as st
import pandas as pd
import gspread
import google.generativeai as genai
from datetime import datetime, date
from PIL import Image

# --- 設定區 ---
SHEET_ID = 'My Weight Data'  # 你的試算表名稱
WEIGHT_SHEET_NAME = '工作表1' # ⚠️注意：如果你的體重分頁叫 Sheet1，請改這裡
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
        new_sheet = sh.add_worksheet(title=sheet_name, rows=1000, cols=6)
        if sheet_name == FOOD_SHEET_NAME:
            new_sheet.append_row(['日期', '時間', '食物名稱', '熱量', '蛋白質', '碳水'])
        return new_sheet

# --- 2. 設定 Google AI (Gemini) ---
if "gemini_api_key" in st.secrets:
    genai.configure(api_key=st.secrets["gemini_api_key"])
else:
    st.error("⚠️ 尚未設定 Gemini API Key！請去 Secrets 貼上。")

def analyze_food_with_ai(image_data, text_input):
    """
    雙模型切換版：
    - 有圖：使用 gemini-pro-vision
    - 沒圖：使用 gemini-pro
    這樣就不需要依賴最新版套件，解決 404 問題。
    """
    
    # 準備 Prompt (你的指令)
    base_prompt = """
    你是一個專業營養師。請分析這份飲食。
    請估算它的：1.熱量(大卡), 2.蛋白質(克), 3.碳水化合物(克)。
    
    請直接回傳一個 JSON 格式，不要有markdown標記，格式如下：
    {
        "food_name": "食物簡稱",
        "calories": 數字,
        "protein": 數字,
        "carbs": 數字
    }
    """
    
    if text_input:
        base_prompt += f"\n使用者補充說明：{text_input}"

    try:
        st.toast("📡 呼叫 AI 營養師中...", icon="🤖")
        
        # --- 關鍵修改：自動切換模型 ---
        if image_data:
            # 情況 A：有照片 -> 用視覺模型 (gemini-pro-vision)
            # 注意：舊版模型要求圖片放列表前面
            model = genai.GenerativeModel('gemini-pro-vision')
            inputs = [base_prompt, image_data]
            response = model.generate_content(inputs)
        else:
            # 情況 B：純文字 -> 用文字模型 (gemini-pro)
            model = genai.GenerativeModel('gemini-pro')
            response = model.generate_content(base_prompt)
            
        st.toast("✅ 收到 AI 回應！正在解析...", icon="✨")
        print(f"DEBUG AI Response: {response.text}") 
        
        clean_json = response.text.replace('```json', '').replace('```', '').strip()
        return eval(clean_json)

    except Exception as e:
        st.error(f"❌ 發生錯誤：{e}")
        st.info("如果顯示 '404'，代表 AI 暫時連不上，請稍後再試。")
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

tab1, tab2 = st.tabs(["⚖️ 體重紀錄", "📸 飲食紀錄 (拍照/文字)"])

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

# --- 分頁 2: 飲食 (AI 視覺版) ---
with tab2:
    st.info("💡 拍張照，或者打字，AI 都能幫你算！")
    
    # 1. 圖片上傳區
    uploaded_file = st.file_uploader("📸 上傳食物照片", type=["jpg", "png", "jpeg"])
    image = None
    if uploaded_file is not None:
        image = Image.open(uploaded_file)
        st.image(image, caption='預覽照片', use_container_width=True)
    
    # 2. 文字補充區
    food_input = st.text_input("文字補充 (例如：飯只吃一半)", placeholder="也可以不傳照片，直接打字喔！")
    
    # 3. 按鈕
    if st.button("🍱 AI 幫我算熱量"):
        if uploaded_file or food_input:
            with st.spinner('AI 正在看照片分析中...'):
                result = analyze_food_with_ai(image, food_input)
            
            if result and result.get('calories', 0) > 0:
                # 顯示結果
                c1, c2, c3 = st.columns(3)
                c1.metric("🔥 熱量", f"{result['calories']} kcal")
                c2.metric("🥩 蛋白質", f"{result['protein']} g")
                c3.metric("🍚 碳水", f"{result['carbs']} g")
                
                st.write(f"**辨識結果：** {result['food_name']}")
                
                # 儲存按鈕
                if st.button("✅ 確認並儲存"): # 注意：Streamlit 巢狀按鈕有時需特別處理，這裡簡化邏輯
                    # 為了避免按鈕重置問題，這裡使用直接寫入邏輯
                    pass 
                
                # 這裡使用 session_state 來處理儲存，體驗會比較好
                st.session_state['last_result'] = result

    # 顯示儲存按鈕 (獨立出來以免消失)
    if 'last_result' in st.session_state:
        res = st.session_state['last_result']
        if st.button(f"📥 儲存：{res['food_name']}"):
            now_time = datetime.now().strftime("%H:%M")
            save_food_data(date.today(), now_time, res['food_name'], 
                          res['calories'], res['protein'], res['carbs'])
            st.success(f"已儲存！ ({res['calories']} kcal)")
            del st.session_state['last_result'] # 存完清除
            st.cache_data.clear()

    st.divider()
    
    try:
        df_food = load_data(FOOD_SHEET_NAME)
        if not df_food.empty:
            st.subheader("📝 近期飲食紀錄")
            st.dataframe(df_food.sort_values('日期', ascending=False))
    except:

        st.write("目前還沒有飲食資料")



