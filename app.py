import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import os
import uuid
import unicodedata
from datetime import datetime

# --- 設定 ---
DATA_FILE = 'trade_data_v3.csv'
HWM_FILE = 'hwm_data_v3.csv'
TAX_RATE = 0.15  # 報酬率

st.set_page_config(page_title="J_Phantom_Gear", layout="wide")

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
st.title("J_Phantom_Gear")

# ==========================================
# 1. 新規買付エリア
# ==========================================
st.markdown("### 📝 新規買付入力")

col1, col2 = st.columns(2)
with col1:
    input_date = st.date_input("買付日", datetime.now())
    # Enter誤送信防止
    code_input = st.text_input("証券コード (例: 7203 トヨタ)", max_chars=10, help="半角・全角どちらでもOK")

with col2:
    # 100株〜50000株まで
    qty_options = list(range(100, 50100, 100))
    qty = st.selectbox("数量 (株)", options=qty_options, index=0) 
    
    # ★変更点：value=None で最初は空欄にする
    price = st.number_input("取得単価 (円)", min_value=0.0, step=0.1, value=None, format="%.1f", placeholder="金額を入力")

# ボタンでのみ追加実行
if st.button("保有リストに追加", type="primary"):
    if not code_input:
        st.error("⚠️ 証券コードを入れてね！")
    elif price is None:
        st.error("⚠️ 取得単価を入れてね！")
    else:
        # 全角→半角変換
        code = unicodedata.normalize('NFKC', code_input)
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
        st.success(f"✅ {stock_name} ({qty}株) をリストに追加したよ！")
        st.rerun()

st.divider()

# ==========================================
# 2. 保有リスト (一発削除ボタン付き)
# ==========================================
st.markdown("### 📊 現在の保有リスト")

df = load_data()
holdings = df[df['ステータス'] == '保有中'].copy()

if holdings.empty:
    st.info("現在、保有している株はありません。")
else:
    # ★変更点：データフレームではなく、ボタン付きのリストを自作して表示
    # ヘッダー行
    h_col1, h_col2, h_col3, h_col4, h_col5, h_col6 = st.columns([0.5, 1.5, 1.5, 3.5, 1.5, 2])
    h_col1.write("削除")
    h_col2.write("買付日")
    h_col3.write("コード")
    h_col4.write("銘柄名")
    h_col5.write("数量")
    h_col6.write("取得単価")
    
    st.markdown("---") # 区切り線

    # データ行をループ表示
    for index, row in holdings.iterrows():
        c1, c2, c3, c4, c5, c6 = st.columns([0.5, 1.5, 1.5, 3.5, 1.5, 2])
        
        # ❌ボタン：押すと即座に削除
        if c1.button("❌", key=f"del_{row['ID']}"):
            df = df[df['ID'] != row['ID']] # IDが一致しないものだけ残す（＝削除）
            save_data(df)
            st.rerun() # 即リロード
        
        c2.write(row['買付日'])
        c3.write(row['証券コード'])
        c4.write(row['銘柄名'])
        c5.write(f"{int(row['数量']):,}株")
        c6.write(f"¥{row['取得単価']:,.0f}")

st.divider()

# ==========================================
# 3. 決済エリア
# ==========================================
if not holdings.empty:
    st.markdown("#### 👇 決済する場合はこちら")
    
    with st.container(border=True):
        # 決済用のリスト
        holdings['表示用'] = holdings.apply(lambda x: f"【{x['証券コード']}】{x['銘柄名']} - {x['数量']}株", axis=1)
        target = st.selectbox("どの銘柄を決済する？", holdings['表示用'], key='settle_select')
        
        target_id = holdings[holdings['表示用'] == target].iloc[0]['ID']
        
        c1, c2, c3 = st.columns(3)
        with c1:
            sell_date = st.date_input("売却日", datetime.now())
        with c2:
            sell_price = st.number_input("売却単価 (円)", min_value=0.0, step=0.1, format="%.1f")
        with c3:
            deal_type = st.radio("結果は？", ["利益確定 (報酬対象)", "損切り (損失繰越)", "恩株など (報酬対象外)"])
        
        if st.button("決済を確定する"):
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
                df.loc[df['ID'] == target_id, '報酬対象益'] = 0
            
            save_data(df)
            st.success(f"処理完了！ 損益: ¥{int(profit):,}円")
            st.rerun()

st.divider()

# ==========================================
# 4. 報酬確認エリア
# ==========================================
st.markdown("### 💰 成功報酬レポート")

carryover = load_hwm()
target_df = df[df['ステータス'].isin(['利確済', '損切済'])]

current_profit = target_df['報酬対象益'].sum()
net_profit = current_profit - carryover

# カード表示
c1, c2, c3 = st.columns(3)
c1.metric("今回の確定利益", f"¥{int(current_profit):,}")
c2.metric("前回の繰越損失", f"¥{int(carryover):,}", delta_color="inverse")

reward = 0
if net_profit > 0:
    reward = net_profit * TAX_RATE
    c3.metric("★ 請求する報酬額 (15%)", f"¥{int(reward):,}", f"利益ベース: ¥{int(net_profit):,}")
else:
    c3.metric("報酬額", "¥0", "損失繰越になります")

st.caption("▼ 計算履歴")
if not target_df.empty:
    st.table(target_df[['売却日', '銘柄名', '数量', '損益', 'ステータス']])

st.write("---")
with st.expander("管理者用：請求が終わったらここを押してリセット"):
    if st.button("期間を確定してリセット"):
        next_loss = abs(net_profit) if net_profit < 0 else 0
        save_hwm(next_loss)
        
        df_remaining = df[df['ステータス'] == '保有中']
        save_data(df_remaining)
        st.success("リセット完了！次の期間へ。")
        st.rerun()
