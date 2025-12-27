import streamlit as st
import pandas as pd
import yfinance as yf
import requests
from bs4 import BeautifulSoup
import os
import uuid
from datetime import datetime

# --- 設定 ---
DATA_FILE = 'trade_data_v3.csv'
HWM_FILE = 'hwm_data_v3.csv'
TAX_RATE = 0.15  # 報酬率

st.set_page_config(page_title="GIT Fuyaseru Manager", layout="wide")

# --- 関数定義 ---
def load_data():
    if os.path.exists(DATA_FILE):
        return pd.read_csv(DATA_FILE)
    else:
        return pd.DataFrame(columns=[
            'ID', '買付日', '証券コード', '銘柄名', '数量', '取得単価', '取得額',
            '売却日', '売却単価', '売却額', '損益', 'ステータス', '報酬対象益'
        ])

def save_data(df):
    df.to_csv(DATA_FILE, index=False)

def load_hwm():
    if os.path.exists(HWM_FILE):
        df = pd.read_csv(HWM_FILE)
        return df.iloc[0]['繰越損失']
    return 0.0

def save_hwm(loss):
    pd.DataFrame({'繰越損失': [loss]}).to_csv(HWM_FILE, index=False)

# 企業名取得 (Yahoo!ファイナンス)
def get_stock_name_jp(code):
    try:
        url = f"https://finance.yahoo.co.jp/quote/{code}.T"
        res = requests.get(url)
        soup = BeautifulSoup(res.text, 'html.parser')
        title = soup.find('title').text
        if "【" in title:
            name = title.split('】')[1].split(' -')[0]
            return name
        return f"コード {code}"
    except:
        return f"コード {code}"

# --- メイン画面 ---
st.title("💹 GIT Fuyaseru Manager")

# タブメニュー
tab1, tab2, tab3 = st.tabs(["📝 1. 新規買付 (保有)", "🔄 2. 決済 (利確/損切)", "💰 3. 報酬確認"])

# --- タブ1：新規買付 ---
with tab1:
    st.markdown("### 新しく株を買ったらここに入力")
    with st.form("entry_form"):
        col1, col2 = st.columns(2)
        with col1:
            input_date = st.date_input("買付日", datetime.now())
            code = st.text_input("証券コード (例: 7711)", max_chars=4)
        with col2:
            qty = st.number_input("数量 (株)", min_value=100, step=100)
            price = st.number_input("取得単価 (円)", min_value=0.0, step=0.1, format="%.1f")
        
        submitted = st.form_submit_button("保有リストに追加")
        
        if submitted and code:
            stock_name = get_stock_name_jp(code)
            df = load_data()
            new_id = str(uuid.uuid4())
            
            new_data = {
                'ID': new_id,
                '買付日': input_date,
                '証券コード': code,
                '銘柄名': stock_name,
                '数量': qty,
                '取得単価': price,
                '取得額': qty * price,
                '売却日': None,
                '売却単価': 0.0,
                '売却額': 0.0,
                '損益': 0.0,
                'ステータス': '保有中',
                '報酬対象益': 0.0
            }
            df = pd.concat([df, pd.DataFrame([new_data])], ignore_index=True)
            save_data(df)
            st.success(f"✅ {stock_name} ({qty}株) を保有リストに追加したよ！")

# --- タブ2：決済 ---
with tab2:
    st.markdown("### 保有中の株を売ったらここ")
    df = load_data()
    holdings = df[df['ステータス'] == '保有中'].copy()
    
    if holdings.empty:
        st.info("現在、保有中の株はないよ。")
    else:
        # わかりやすい選択リストを作る
        holdings['表示用'] = holdings.apply(lambda x: f"【{x['証券コード']}】{x['銘柄名']} - {x['数量']}株 (取得: {x['取得単価']}円)", axis=1)
        target = st.selectbox("どの銘柄を決済する？", holdings['表示用'])
        
        # 選択したデータのIDを特定
        target_id = holdings[holdings['表示用'] == target].iloc[0]['ID']
        
        st.divider()
        with st.form("exit_form"):
            col1, col2 = st.columns(2)
            with col1:
                sell_date = st.date_input("売却日", datetime.now())
                sell_price = st.number_input("売却単価 (円)", min_value=0.0, step=0.1, format="%.1f")
            with col2:
                # シンプルな3択
                deal_type = st.radio("結果は？", ["利益確定 (報酬対象)", "損切り (損失繰越)", "恩株など (報酬対象外)"])
            
            finish_btn = st.form_submit_button("決済を確定する")
            
            if finish_btn:
                # 計算処理
                row = df[df['ID'] == target_id].iloc[0]
                sell_val = sell_price * row['数量']
                profit = sell_val - row['取得額']
                
                df.loc[df['ID'] == target_id, '売却日'] = sell_date
                df.loc[df['ID'] == target_id, '売却単価'] = sell_price
                df.loc[df['ID'] == target_id, '売却額'] = sell_val
                df.loc[df['ID'] == target_id, '損益'] = profit
                
                if "利益確定" in deal_type:
                    df.loc[df['ID'] == target_id, 'ステータス'] = '利確済'
                    df.loc[df['ID'] == target_id, '報酬対象益'] = profit
                elif "損切り" in deal_type:
                    df.loc[df['ID'] == target_id, 'ステータス'] = '損切済'
                    df.loc[df['ID'] == target_id, '報酬対象益'] = profit
                else:
                    df.loc[df['ID'] == target_id, 'ステータス'] = '対象外'
                    df.loc[df['ID'] == target_id, '報酬対象益'] = 0 # 報酬計算には入れない
                
                save_data(df)
                st.success(f"処理完了！ 損益: ¥{int(profit):,}円")
                st.rerun()

# --- タブ3：報酬確認 ---
with tab3:
    st.markdown("### 💰 成功報酬レポート")
    
    df = load_data()
    carryover = load_hwm()
    
    # 計算対象（利確と損切のみ）
    target_df = df[df['ステータス'].isin(['利確済', '損切済'])]
    
    current_profit = target_df['報酬対象益'].sum()
    net_profit = current_profit - carryover
    
    # カード表示で見やすく
    col1, col2, col3 = st.columns(3)
    col1.metric("今回の確定利益", f"¥{int(current_profit):,}")
    col2.metric("前回の繰越損失", f"¥{int(carryover):,}", delta_color="inverse")
    
    reward = 0
    if net_profit > 0:
        reward = net_profit * TAX_RATE
        col3.metric("★ 請求する報酬額 (15%)", f"¥{int(reward):,}", f"利益ベース: ¥{int(net_profit):,}")
    else:
        col3.metric("報酬額", "¥0", "損失繰越になります")

    st.divider()
    
    # 履歴（シンプルな表）
    st.caption("▼ 計算の内訳 (csvダウンロードなどは無し)")
    if not target_df.empty:
        st.table(target_df[['売却日', '銘柄名', '数量', '損益', 'ステータス']])
    else:
        st.write("まだ決済された取引はありません。")

    # 締め処理エリア
    st.write("---")
    with st.expander("管理者用：請求が終わったらここを押してリセット"):
        if st.button("期間を確定してリセット"):
            next_loss = abs(net_profit) if net_profit < 0 else 0
            save_hwm(next_loss)
            
            # 完了分を消去、保有中だけ残す
            df_remaining = df[df['ステータス'] == '保有中']
            save_data(df_remaining)
            st.success("リセット完了！次の期間へ。")
            st.rerun()