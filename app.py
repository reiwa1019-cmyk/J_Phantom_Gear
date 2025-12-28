import streamlit as st
import pandas as pd
import os
from datetime import datetime

# データ保存用のファイルパス
DATA_FILE = 'trade_log.csv'

# 初期化：データファイルがない場合は作成
if not os.path.exists(DATA_FILE):
    df = pd.DataFrame(columns=[
        '日付', '銘柄名', '銘柄コード', '売買', '株数', '価格', 
        '理由', '感情', 'セットアップ', '手仕舞い価格', '損益', '結果'
    ])
    df.to_csv(DATA_FILE, index=False)

def load_data():
    return pd.read_csv(DATA_FILE)

def save_data(data):
    df = pd.DataFrame(data)
    df.to_csv(DATA_FILE, index=False)

def main():
    st.set_page_config(page_title="J_Phantom_Gear", layout="wide")
    st.title("J_Phantom_Gear 💹")

    # サイドバー：入力フォーム
    st.sidebar.header("トレード記録入力")
    
    with st.sidebar.form(key='trade_form'):
        date = st.date_input("日付", datetime.now())
        symbol_name = st.text_input("銘柄名")
        symbol_code = st.text_input("銘柄コード")
        
        # 売買の色分け変更（買い＝赤、売り＝青）は表示上の装飾で行うか、
        # ここでは選択肢としてシンプルに残し、表示時に色を適用します。
        side = st.selectbox("売買", ["買い", "売り"])
        
        quantity = st.number_input("株数", min_value=1, value=100)
        price = st.number_input("価格", min_value=0.0, format="%.1f")
        reason = st.text_area("エントリー理由")
        emotion = st.slider("感情コンディション (1:冷静 - 5:興奮)", 1, 5, 3)
        setup = st.text_input("セットアップ (例: トライアングル、ボックス抜け)")
        
        # 決済用入力（オプション）
        st.markdown("---")
        st.markdown("### 決済情報（入力時のみ）")
        exit_price = st.number_input("手仕舞い価格", min_value=0.0, format="%.1f")
        
        submit_btn = st.form_submit_button("記録する")

    if submit_btn:
        # 損益計算
        pnl = 0
        result_type = "保有中"
        
        if exit_price > 0:
            if side == "買い":
                pnl = (exit_price - price) * quantity
            else: # 売り
                pnl = (price - exit_price) * quantity
            
            if pnl > 0:
                result_type = "利確"
            elif pnl < 0:
                result_type = "損切り"
            else:
                result_type = "同値"

        new_data = {
            '日付': date,
            '銘柄名': symbol_name,
            '銘柄コード': symbol_code,
            '売買': side,
            '株数': quantity,
            '価格': price,
            '理由': reason,
            '感情': emotion,
            'セットアップ': setup,
            '手仕舞い価格': exit_price if exit_price > 0 else None,
            '損益': pnl if exit_price > 0 else 0,
            '結果': result_type
        }
        
        df = load_data()
        df = pd.concat([df, pd.DataFrame([new_data])], ignore_index=True)
        save_data(df)
        st.success("トレードを記録しました！")

    # メインエリア：過去データ表示
    st.header("過去のトレード履歴")
    
    if os.path.exists(DATA_FILE):
        df = load_data()
        
        # データフレームの表示スタイル適用
        def highlight_rows(row):
            # 結果による行の背景色変更
            # 利確は薄いピンク (#FFEEEE)、損切りは薄い青 (#EEF7FF)
            # ※Streamlitのdataframe表示で有効なpandas stylerを使用
            styles = [''] * len(row)
            if row['結果'] == '利確':
                return ['background-color: #FFE6E6; color: black'] * len(row) # 薄いピンク
            elif row['結果'] == '損切り':
                return ['background-color: #E6F2FF; color: black'] * len(row) # 薄い青
            return [''] * len(row)

        def color_side(val):
            # 売買の文字色変更
            # 買い注文＝赤、売り注文＝青
            if val == '買い':
                return 'color: red; font-weight: bold'
            elif val == '売り':
                return 'color: blue; font-weight: bold'
            return ''

        # 最新のものが上に来るようにソート
        if not df.empty:
            df['日付'] = pd.to_datetime(df['日付'])
            df = df.sort_values(by='日付', ascending=False)
            
            # スタイルの適用
            st.dataframe(
                df.style.apply(highlight_rows, axis=1)\
                        .map(color_side, subset=['売買'])\
                        .format({'価格': '{:.1f}', '手仕舞い価格': '{:.1f}', '損益': '{:.0f}'}),
                use_container_width=True
            )
        else:
            st.info("データがありません。サイドバーから入力してください。")

if __name__ == "__main__":
    main()
