import streamlit as st
import pandas as pd
import gspread
from datetime import date

# --- 設定區 ---
# 這裡必須跟你的 Google 試算表名稱一模一樣
SHEET_NAME = 'My Weight Data'

# --- 連接 Google Sheets 函式 (有快取功能，不會每次都重連) ---
@st.cache_resource
def get_google_sheet():
    # 從 Secrets 裡讀取鑰匙
    credentials = st.secrets["service_account_info"]
    gc = gspread.service_account_from_dict(credentials)
    sh = gc.open(SHEET_NAME)
    return sh.sheet1

# --- 讀取資料函式 ---
def load_data():
    sheet = get_google_sheet()
    # 讀取所有紀錄
    records = sheet.get_all_records()
    # 如果是空的，回傳空的 DataFrame
    if not records:
        return pd.DataFrame(columns=['日期', '身高', '體重', 'BMI'])
    return pd.DataFrame(records)

# --- 寫入資料函式 ---
def save_data(date_str, height, weight, bmi):
    sheet = get_google_sheet()
    # 如果是第一筆資料（表頭不存在），先寫入表頭
    if len(sheet.get_all_values()) == 0:
        sheet.append_row(['日期', '身高', '體重', 'BMI'])
    
    # 寫入新的一行
    sheet.append_row([str(date_str), height, weight, bmi])

# ================= 介面開始 =================

st.title('☁️ 雲端體重監控 APP (永久保存版)')
st.write(f'資料儲存於：Google Sheet ({SHEET_NAME})')

# --- 左側：輸入區 ---
with st.sidebar:
    st.header("📝 新增紀錄")
    input_date = st.date_input("選擇日期", date.today())
    input_height = st.number_input("身高 (cm)", 100.0, 250.0, 170.0, 0.1)
    input_weight = st.number_input("體重 (kg)", 0.0, 200.0, step=0.1, format="%.1f")
    
    if input_height > 0:
        bmi = input_weight / ((input_height / 100) ** 2)
        st.caption(f"預覽 BMI: {bmi:.1f}")

    if st.button("上傳雲端"):
        try:
            with st.spinner('正在連線 Google 寫入資料...'):
                save_data(input_date, input_height, input_weight, round(bmi, 1))
            st.success(f"✅ 成功寫入！ ({input_date})")
            # 強制清除快取，讓右邊的圖表馬上更新
            st.cache_data.clear()
        except Exception as e:
            st.error(f"寫入失敗，請檢查權限或網路: {e}")

# --- 右側：顯示區 ---
try:
    df = load_data()
    
    if not df.empty:
        # 確保日期格式正確
        df['日期'] = pd.to_datetime(df['日期']).dt.date
        df = df.sort_values(by='日期')

        # 最新數據
        latest = df.iloc[-1]
        
        col1, col2, col3 = st.columns(3)
        col1.metric("最新體重", f"{latest['體重']} kg")
        col2.metric("最新 BMI", f"{latest['BMI']}")
        col3.metric("紀錄總筆數", f"{len(df)} 筆")

        st.divider()

        st.subheader("📊 體重趨勢")
        st.line_chart(df.set_index('日期')['體重'])

        with st.expander("查看 Google Sheet 原始資料"):
            st.dataframe(df.sort_values(by='日期', ascending=False))
    else:
        st.info("目前雲端表格是空的，快輸入第一筆資料吧！")

except Exception as e:
    st.warning("無法讀取資料，請確認：")
    st.markdown("1. Streamlit Secrets 是否設定正確？")
    st.markdown(f"2. Google Sheet 名稱是否叫 `{SHEET_NAME}`？")
    st.markdown("3. 是否有把 Sheet 分享給機器人 Email？")
    st.error(f"詳細錯誤訊息: {e}")