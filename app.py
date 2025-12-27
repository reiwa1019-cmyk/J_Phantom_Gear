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

# --- Session State 初期化 (入力クリア用) ---
if 'entry_date' not in st.session_state:
    st.session_state['entry_date'] = datetime.now()
if 'entry_code' not in st.session_state:
    st.session_state['entry_code'] = ""
if 'entry_qty' not in st.session_state:
    st.session_state['entry_qty'] = 100
if 'entry_price' not in st.session_state:
    st.session_state['entry_price'] = None  # Noneで初期化できないウィジェット対策は後述

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
# 1. 新規買付エリア (入力クリア機能付き)
# ==========================================
st.markdown("### 📝 新規買付入力")

col1, col2 = st.columns(2)
with col1:
    input_date = st.date_input("買付日", key='entry_date')
    # keyを指定してsession_stateで管理
    code_input = st.text_input("証券コード (例: 7203 トヨタ)", max_chars=10, help="半角・全角どちらでもOK", key='entry_code')

with col2:
    qty_options = list(range(100, 50100, 100))
    # selectboxはindexで管理する必要があるが、単純化のため値を直接参照
    qty = st.selectbox("数量 (株)", options=qty_options, key='entry_qty')
    
    # 取得単価 (keyをつける)
    # number_inputのNone許容はStreamlitのバージョンによるが、空欄っぽく見せる
    price = st.number_input("取得単価 (円)", min_value=0.0, step=0.1, value=None, format="%.1f", placeholder="金額を入力", key='entry_price')

# 追加ボタン
if st.button("保有リストに追加", type="primary"):
    if not code_input:
        st.error("⚠️ 証券コードを入れてね！")
    elif price is None:
        st.error("⚠️ 取得単価を入れてね！")
    else:
        # 全角→半角変換
        code = unicodedata.normalize('NFKC', code_input)
        
        df = load_data()
        
        # --- ナンピン（買い増し）合算ロジック ---
        # 既に保有中で同じコードのものがあるか探す
        existing_mask = (df['ステータス'] == '保有中') & (df['証券コード'] == code)
        
        if existing_mask.any():
            # 合算処理
            target_idx = df.index[existing_mask][0]
            current_row = df.loc[target_idx]
            
            old_qty = current_row['数量']
            old_amount = current_row['取得額']
            
            add_qty = qty
            add_amount = qty * price
            
            new_total_qty = old_qty + add_qty
            new_total_amount = old_amount + add_amount
            new_avg_price = new_total_amount / new_total_qty
            
            # データ更新
            df.at[target_idx, '数量'] = new_total_qty
            df.at[target_idx, '取得額'] = new_total_amount
            df.at[target_idx, '取得単価'] = new_avg_price
            df.at[target_idx, '買付日'] = input_date # 最新の買付日に更新
            
            stock_name = current_row['銘柄名'] # 名前は既存のものを使用
            msg = f"✅ {stock_name} を買い増ししたよ！ (合計 {new_total_qty}株 / 平均 {new_avg_price:,.1f}円)"
            
        else:
            # 新規追加処理
            stock_name = get_stock_name_jp(code)
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
            msg = f"✅ {stock_name} ({qty}株) をリストに追加したよ！"

        save_data(df)
        st.success(msg)
        
        # --- 入力欄のリセット ---
        # session_stateを空または初期値にする
        st.session_state['entry_code'] = ""
        st.session_state['entry_price'] = None
        st.session_state['entry_qty'] = 100
        # 画面をリロードして反映
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
    # ヘッダー行
    h_col1, h_col2, h_col3, h_col4, h_col5, h_col6 = st.columns([0.5, 1.5, 1.5, 3.5, 1.5, 2])
    h_col1.write("削除")
    h_col2.write("買付日")
    h_col3.write("コード")
    h_col4.write("銘柄名")
    h_col5.write("数量")
    h_col6.write("取得単価")
    
    st.markdown("---")

    # データ行をループ表示
    for index, row in holdings.iterrows():
        c1, c2, c3, c4, c5, c6 = st.columns([0.5, 1.5, 1.5, 3.5, 1.5, 2])
        
        # ❌ボタン
        if c1.button("❌", key=f"del_{row['ID']}"):
            df = df[df['ID'] != row['ID']]
            save_data(df)
            st.rerun()
        
        c2.write(row['買付日'])
        c3.write(row['証券コード'])
        c4.write(row['銘柄名'])
        c5.write(f"{int(row['数量']):,}株")
        c6.write(f"¥{row['取得単価']:,.1f}") # 平均単価なので小数点出す

st.divider()

# ==========================================
# 3. 決済エリア (自動判定 & 一部売却対応)
# ==========================================
if not holdings.empty:
    st.markdown("#### 決済") # 文言変更
    
    with st.container(border=True):
        # 選択リスト
        holdings['表示用'] = holdings.apply(lambda x: f"【{x['証券コード']}】{x['銘柄名']} - {int(x['数量']):,}株 (平均 {x['取得単価']:,.1f}円)", axis=1)
        target = st.selectbox("どの銘柄を決済する？", holdings['表示用'], key='settle_select')
        
        # 選択された行のデータを取得
        target_row = holdings[holdings['表示用'] == target].iloc[0]
        target_id = target_row['ID']
        max_qty = int(target_row['数量'])
        
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            sell_date = st.date_input("売却日", datetime.now(), key='sell_date')
            # 売却数量を選択 (デフォルトは全株)
            sell_qty = st.number_input(f"売却数量 (保有: {max_qty}株)", min_value=100, max_value=max_qty, value=max_qty, step=100)
            
        with col_s2:
            # 初期値空欄
            sell_price = st.number_input("売却単価 (円)", min_value=0.0, step=0.1, value=None, format="%.1f", placeholder="売値を入力")
            
            st.write("状態") # 文言変更
            # 恩株チェックボックスのみ
            is_bonus = st.checkbox("恩株など (報酬対象外にする)", value=False)
        
        if st.button("決済を確定する"):
            if sell_price is None:
                st.error("売却単価を入れてね！")
            else:
                # 計算処理
                current_avg_price = target_row['取得単価']
                
                # 売却分の取得額
                cost_basis = current_avg_price * sell_qty
                # 売却額
                sales_proceeds = sell_price * sell_qty
                # 損益
                profit = sales_proceeds - cost_basis
                
                # ステータス自動判定
                status = ""
                reward_profit = 0.0
                
                if is_bonus:
                    status = "対象外"
                    reward_profit = 0
                elif profit > 0:
                    status = "利確済"
                    reward_profit = profit
                elif profit < 0:
                    status = "損切済"
                    reward_profit = profit # マイナスが入る
                else:
                    status = "損切済" # プラマイゼロはとりあえず損切扱いで処理(報酬なし)
                    reward_profit = 0

                # データの保存処理
                # 1. 売却履歴として新しいレコードを作る（これが決済済みリストに行く）
                #    IDは新規発行して分離する
                history_id = str(uuid.uuid4())
                history_data = {
                    'ID': history_id,
                    '買付日': target_row['買付日'],
                    '証券コード': target_row['証券コード'],
                    '銘柄名': target_row['銘柄名'],
                    '数量': sell_qty,
                    '取得単価': current_avg_price,
                    '取得額': cost_basis,
                    '売却日': sell_date,
                    '売却単価': sell_price,
                    '売却額': sales_proceeds,
                    '損益': profit,
                    'ステータス': status,
                    '報酬対象益': reward_profit
                }
                df = pd.concat([df, pd.DataFrame([history_data])], ignore_index=True)
                
                # 2. 元の保有データの更新
                if sell_qty == max_qty:
                    # 全株売却 -> 元の保有データを削除
                    df = df[df['ID'] != target_id]
                else:
                    # 一部売却 -> 数量と取得額を減らして残す
                    remaining_qty = max_qty - sell_qty
                    remaining_cost = target_row['取得額'] - cost_basis
                    
                    df.loc[df['ID'] == target_id, '数量'] = remaining_qty
                    df.loc[df['ID'] == target_id, '取得額'] = remaining_cost
                    # 取得単価は変わらない（平均法）
                
                save_data(df)
                
                if status == "対象外":
                    st.success(f"処理完了！ (対象外取引)")
                else:
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
