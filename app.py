import streamlit as st
import pandas as pd
from datetime import datetime

# --- 1. 初期設定と関数定義 ---

def init_session_state():
    # ポートフォリオ構造: 
    # {'Code': {'qty': 保有数, 'avg_price': 平均単価, 'realized_pl': 累計確定損益}}
    if 'portfolio' not in st.session_state:
        st.session_state['portfolio'] = {}
    
    if 'trade_log' not in st.session_state:
        st.session_state['trade_log'] = []

    if 'system_msg' not in st.session_state:
        st.session_state['system_msg'] = ""

def calculate_weighted_average(current_qty, current_avg, add_qty, add_price):
    """加重移動平均（買い増し用）"""
    total_cost = (current_qty * current_avg) + (add_qty * add_price)
    total_qty = current_qty + add_qty
    if total_qty == 0: return 0.0
    return round(total_cost / total_qty, 2)

def add_stock_callback():
    """売買実行ボタンの処理"""
    # 入力値取得
    input_date = st.session_state.input_date
    trade_type = st.session_state.input_type # 買い or 売り
    code = st.session_state.input_code
    qty = st.session_state.input_qty
    price = st.session_state.input_price
    
    portfolio = st.session_state['portfolio']

    # --- バリデーション ---
    if not code or qty <= 0 or price < 0:
        st.session_state['system_msg'] = "⚠️ コード、数量、単価を正しく入れてね。"
        return

    # --- 処理分岐 ---
    
    # A. 新規・買い増しの場合
    if trade_type == "買い":
        if code in portfolio:
            current = portfolio[code]
            new_avg = calculate_weighted_average(current['qty'], current['avg_price'], qty, price)
            portfolio[code]['qty'] += qty
            portfolio[code]['avg_price'] = new_avg
            action = "買い増し"
            pl_display = 0 # 買いの時は損益発生なし
        else:
            portfolio[code] = {'qty': qty, 'avg_price': price, 'realized_pl': 0}
            new_avg = price
            action = "新規買付"
            pl_display = 0
            
        msg = f"✅ {code} を {qty}株 買いました（平均単価: {new_avg}円）"

    # B. 売り（恩株化・利確・損切り）の場合
    elif trade_type == "売り":
        if code not in portfolio or portfolio[code]['qty'] < qty:
            st.session_state['system_msg'] = "⚠️ エラー: 保有していない、または株数が足りません！"
            return
        
        current = portfolio[code]
        
        # ★重要ロジック：売却益の計算（元本回収額）
        # (売値 - 平均取得単価) * 株数
        profit_loss = (price - current['avg_price']) * qty
        
        # ポートフォリオ更新
        portfolio[code]['qty'] -= qty
        portfolio[code]['realized_pl'] += profit_loss # 累計損益に加算
        
        # もし全株売却したらリストから消す？（今回は履歴に残すため残高0で維持する設計にします）
        
        action = "売却"
        pl_display = profit_loss
        msg = f"📉 {code} を {qty}株 売却しました。確定損益: {int(profit_loss):,}円"

    # --- ログ保存 ---
    st.session_state['trade_log'].append({
        '日付': input_date,
        '区分': action,
        'コード': code,
        '数量': qty,
        '約定単価': price,
        '平均単価': portfolio[code]['avg_price'], # 売りでは変動しない！
        '確定損益': pl_display
    })

    st.session_state['system_msg'] = msg

    # 入力リセット
    st.session_state.input_code = ""
    st.session_state.input_qty = 0
    st.session_state.input_price = 0.0

# --- 2. 画面表示 ---

def main():
    st.set_page_config(page_title="J_Phantom_Gear", layout="wide")
    init_session_state()

    st.title("J_Phantom_Gear ⚙️")
    st.caption("恩株マネジメントシステム")
    st.markdown("---")

    # メッセージ表示
    if st.session_state['system_msg']:
        if "⚠️" in st.session_state['system_msg']:
            st.error(st.session_state['system_msg'])
        else:
            st.success(st.session_state['system_msg'])

    # --- 入力フォーム ---
    with st.container():
        st.subheader("📝 取引入力")
        col_type, col_date, col_code = st.columns([1, 1, 2])
        col_qty, col_price, col_btn = st.columns([1, 1, 1])

        with col_type:
            # ここで「買い」「売り」を選択
            st.radio("取引区分", ["買い", "売り"], horizontal=True, key="input_type")
        with col_date:
            st.date_input("取引日", datetime.today(), key="input_date")
        with col_code:
            st.text_input("証券コード", key="input_code")
            
        with col_qty:
            st.number_input("数量", min_value=0, step=100, key="input_qty")
        with col_price:
            st.number_input("約定単価", min_value=0.0, step=1.0, key="input_price")
        with col_btn:
            st.write("") # スペース調整
            st.write("")
            st.button("取引実行", on_click=add_stock_callback, type="primary", use_container_width=True)

    st.markdown("---")

    # --- 結果表示 ---
    col1, col2 = st.columns([3, 2])

    with col1:
        st.subheader("📊 ポートフォリオ＆恩株状況")
        if st.session_state['portfolio']:
            # データ加工
            data = []
            for code, val in st.session_state['portfolio'].items():
                # 恩株判定：保有があり、かつ累計損益がプラス（簡易判定）
                onkabu_status = "✨恩株達成" if (val['realized_pl'] > 0 and val['qty'] > 0) else "-"
                
                data.append({
                    'コード': code,
                    '保有株数': val['qty'],
                    '平均取得単価': f"{val['avg_price']:.2f}",
                    '累計確定損益': f"{int(val['realized_pl']):,}", # これが元本回収の目安
                    'ステータス': onkabu_status
                })
            
            df_port = pd.DataFrame(data)
            st.dataframe(df_port, use_container_width=True)
        else:
            st.info("保有なし")

    with col2:
        st.subheader("📜 取引履歴")
        if st.session_state['trade_log']:
            df_log = pd.DataFrame(st.session_state['trade_log'])
            # カラム順序調整
            df_log = df_log[['日付', '区分', 'コード', '数量', '約定単価', '確定損益']]
            st.dataframe(df_log.iloc[::-1], use_container_width=True)

if __name__ == "__main__":
    main()
