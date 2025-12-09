import streamlit as st
import pandas as pd
import os
from datetime import date

# --- 設定檔案名稱 ---
FILE_NAME = 'weight_history.csv'

# --- 介面標題 ---
st.title('🏋️‍♂️ 我的體重監控 APP (v2.0)')
st.write('每天記錄一點點，看見進步的曲線！')

# --- 左側：輸入區 ---
with st.sidebar:
    st.header("📝 新增紀錄")
    input_date = st.date_input("選擇日期", date.today())
    
    # 新增：身高欄位 (預設 170，你可以自己改)
    input_height = st.number_input("身高 (cm)", min_value=100.0, max_value=250.0, value=170.0, step=0.1)
    
    input_weight = st.number_input("體重 (kg)", min_value=0.0, max_value=200.0, step=0.1, format="%.1f")
    
    # 計算 BMI 預覽
    if input_height > 0:
        bmi = input_weight / ((input_height / 100) ** 2)
        st.caption(f"目前計算 BMI: {bmi:.1f}")

    if st.button("儲存紀錄"):
        # 1. 整理資料
        new_data = pd.DataFrame({
            '日期': [input_date],
            '體重': [input_weight],
            'BMI': [round(bmi, 1)] # 把 BMI 也存進去
        })
        
        # 2. 存檔
        if not os.path.exists(FILE_NAME):
            new_data.to_csv(FILE_NAME, index=False)
        else:
            # 如果舊檔案沒有 BMI 欄位，這行會確保新資料能順利寫入
            new_data.to_csv(FILE_NAME, mode='a', header=False, index=False)
            
        st.success(f"已儲存：{input_weight} kg (BMI {bmi:.1f})")

# --- 右側：顯示區 ---
if os.path.exists(FILE_NAME):
    df = pd.read_csv(FILE_NAME)
    
    # 確保資料依照日期排序
    df = df.sort_values(by='日期')

    # 取得最新一筆資料
    latest_weight = df.iloc[-1]['體重']
    
    # 如果有 BMI 欄位就讀取，沒有就重算 (為了相容舊資料)
    if 'BMI' in df.columns:
        latest_bmi = df.iloc[-1]['BMI']
    else:
        # 簡單防呆：如果舊資料沒存 BMI，這裡用目前的輸入暫代顯示
        latest_bmi = latest_weight / ((input_height / 100) ** 2)

    # --- 關鍵指標儀表板 ---
    col1, col2, col3 = st.columns(3)
    col1.metric("目前體重", f"{latest_weight} kg")
    col2.metric("目前 BMI", f"{latest_bmi:.1f}")
    
    # 判斷 BMI 狀態
    state = "正常"
    if latest_bmi < 18.5: state = "過輕 🟦"
    elif 18.5 <= latest_bmi < 24: state = "正常 🟩"
    elif 24 <= latest_bmi < 27: state = "過重 🟧"
    else: state = "肥胖 🟥"
    col3.metric("健康狀態", state)

    st.divider() # 分隔線

    # --- 圖表區 ---
    st.subheader("📊 體重趨勢圖")
    st.line_chart(df.set_index('日期')['體重'])
    
    with st.expander("查看詳細數據表格"):
        st.dataframe(df.sort_values(by='日期', ascending=False))
else:
    st.info("👈 請在左側輸入你的身高體重，開始第一筆紀錄！")