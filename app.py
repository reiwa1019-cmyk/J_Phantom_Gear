import streamlit as st
import pandas as pd
from datetime import datetime

# --- 1. 初期設定と関数定義 ---

def init_session_state():
    """セッション状態の初期化"""
    # 保有銘柄リスト（辞書形式で管理：Codeをキーにするのが管理しやすい）
    # 構造: {'7203': {'name': 'トヨタ', 'qty': 100, 'avg_price': 2000.0}, ...}
    if 'portfolio' not in st.session_state:
        st.session_state['portfolio'] = {}
    
    # 履歴表示用のログ
    if 'trade_log' not in st.session_state:
        st.session_state['trade_log'] = []

    # 画面表示用のメッセージ
    if 'system_msg' not in st.session_state:
        st.session_state['system_msg'] = ""

def calculate_weighted_average(current_qty, current_avg, add_qty, add_price):
    """
    【ジェシカ監修】加重移動平均の計算ロジック
    (現在の保有総額 + 今回の購入総額) ÷ (現在の保有数 + 今回の購入数)
    """
    total_cost = (current_qty * current_avg) + (add_qty * add_price)
    total_qty = current_qty + add_qty
    
    if total_qty == 0:
        return 0.0
    
    # 小数点以下2桁で丸める（円単位ならround(x)でもOK）
    return round(total_cost / total_qty, 2)

def add_stock_callback():
    """
    ボタンが押された時に実行される処理（コールバック）
    ここで計算と入力欄のリセットを行うことでエラーを回避する
    """
    # 入力値の取得
    input_date = st.session_state.input_date
    code = st.session_state.input_code
    qty = st.session_state.input_qty
    price = st.session_state.input_price

    # バリデーション（入力チェック）
    if not code or qty <= 0 or price < 0:
        st.session_state['system_msg'] = "⚠️ エラー: コード、数量、単価を正しく入力してね。"
        return

    portfolio = st.session_state['portfolio']

    # --- 計算ロジック ---
    if code in portfolio:
        # すでに持っている銘柄なら「移動平均」で単価更新
        current_data = portfolio[code]
        new_avg = calculate_weighted_average(
            current_data['qty'], 
            current_data['avg_price'], 
            qty, 
            price
        )
        # データを更新
        portfolio[code]['qty'] += qty
        portfolio[code]['avg_price'] = new_avg
        action_type = "買い増し"
    else:
        # 新規銘柄ならそのまま登録
        portfolio[code] = {
            'qty': qty,
            'avg_price': price
        }
        action_type = "新規買付"

    # ログに追加
    st.session_state['trade_log'].append({
        '日付': input_date,
        '区分': action_type,
        'コード': code,
        '数量': qty,
        '取得単価': price, # その時の約定単価
        '平均単価変動': portfolio[code]['avg_price'] # 計算後の平均単価
    })

    # メッセージ更新
    st.session_state['system_msg'] = f"✅ {code} を {qty}株 追加しました！（平均単価: {portfolio[code]['avg_price']}円）"

    # ★ここが重要：入力欄のリセット
    # keyに紐付いたsession_stateを直接書き換えても、コールバック内ならエラーにならない
    st.session_state.input_code = ""
    st.session_state.input_qty = 0
    st.session_state.input_price = 0.0

# --- 2. メイン画面構築 ---

def main():
    st.set_page_config(page_title="J_Phantom_Gear", layout="wide")
    init_session_state()

    st.title("J_Phantom_Gear ⚙️")
    st.markdown("---")

    # --- 入力エリア ---
    st.header("📝 新規買付入力")
    
    # 成功/エラーメッセージの表示
    if st.session_state['system_msg']:
        if "⚠️" in st.session_state['system_msg']:
            st.error(st.session_state['system_msg'])
        else:
            st.success(st.session_state['system_msg'])
        # 一度表示したらクリアしたい場合はここで空にする処理を入れるが、今回は残す

    with st.container():
        col1, col2 = st.columns(2)
        
        with col1:
            st.date_input("買付日", datetime.today(), key="input_date")
            st.text_input("証券コード (例: 7203)", key="input_code")
            
        with col2:
            st.number_input("数量 (株)", min_value=0, step=100, key="input_qty")
            st.number_input("取得単価 (円)", min_value=0.0, step=1.0, format="%.2f", key="input_price")

        # コールバックを使ったボタン
        st.button("保有リストに追加", on_click=add_stock_callback, type="primary")

    st.markdown("---")

    # --- 結果表示エリア ---
    col_res1, col_res2 = st.columns([1, 1])

    with col_res1:
        st.subheader("📊 現在の保有ポートフォリオ")
        if st.session_state['portfolio']:
            # 辞書をDataFrameに変換して表示
            df_port = pd.DataFrame.from_dict(st.session_state['portfolio'], orient='index')
            df_port.index.name = 'コード'
            st.dataframe(df_port.style.format({'avg_price': '{:.2f}', 'qty': '{:,}'}), use_container_width=True)
        else:
            st.info("まだ保有株はありません。")

    with col_res2:
        st.subheader("📜 取引履歴ログ")
        if st.session_state['trade_log']:
            df_log = pd.DataFrame(st.session_state['trade_log'])
            # 新しい順に表示
            st.dataframe(df_log.iloc[::-1], use_container_width=True)
        else:
            st.text("履歴なし")

if __name__ == "__main__":
    main()
