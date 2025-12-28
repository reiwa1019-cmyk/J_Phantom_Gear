import streamlit as st
import pandas as pd
from datetime import datetime, date
from github import Github
import io
import yfinance as yf

# --- 0. 簡易セキュリティ ---
def check_password():
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False

    if st.session_state['logged_in']:
        return True

    st.markdown("### 🔒 PASS")
    password = st.text_input("", type="password", label_visibility="collapsed")
    
    if st.button("ENTER"):
        if password == st.secrets["general"]["APP_PASSWORD"]:
            st.session_state['logged_in'] = True
            st.rerun()
        else:
            st.error("Access Denied")
    return False

# --- GitHub接続 ---
def get_github_repo():
    try:
        token = st.secrets["general"]["GITHUB_TOKEN"]
        repo_name = st.secrets["general"]["REPO_NAME"]
        g = Github(token)
        return g.get_repo(repo_name)
    except Exception as e:
        st.error(f"Connection Error: {e}")
        return None

def load_csv_from_github(filename):
    repo = get_github_repo()
    if not repo: return {} if filename == 'portfolio.csv' else []
    
    try:
        file_content = repo.get_contents(filename)
        csv_data = file_content.decoded_content.decode("utf-8")
        df = pd.read_csv(io.StringIO(csv_data))
        
        if filename == 'portfolio.csv':
            df['Code'] = df['Code'].astype(str)
            return df.set_index('Code').to_dict(orient='index')
        else:
            df['証券コード'] = df['証券コード'].astype(str)
            # 日付型変換
            df['日付'] = pd.to_datetime(df['日付']).dt.date
            return df.to_dict(orient='records')
    except:
        return {} if filename == 'portfolio.csv' else []

def save_to_github(filename, df):
    repo = get_github_repo()
    if not repo: return

    try:
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        content = csv_buffer.getvalue()
        try:
            file = repo.get_contents(filename)
            repo.update_file(filename, f"Update {filename}", content, file.sha)
        except:
            repo.create_file(filename, f"Create {filename}", content)
    except Exception as e:
        st.error(f"Save Failed: {e}")

# --- ロジック ---
def get_stock_name(code):
    try:
        ticker = yf.Ticker(f"{code}.T")
        return ticker.info.get('longName', '名称不明')
    except:
        return "名称不明"

def recalculate_all(logs):
    """
    全履歴データからポートフォリオを「リプレイ（再計算）」する関数
    これにより、過去データの修正が正しく現在に反映される
    """
    # 日付順（古い順）にソート
    sorted_logs = sorted(logs, key=lambda x: x['日付'])
    
    portfolio = {}
    processed_logs = []

    for log in sorted_logs:
        code = str(log['証券コード'])
        qty = int(log['数量'])
        price = float(log['約定単価'])
        trade_type = log['区分']
        name = log.get('銘柄名', '名称不明')

        # 名前情報の補完（なければ取得はしない、重くなるから）
        
        if trade_type == "買い" or trade_type == "新規買付" or trade_type == "買い増し":
            if code not in portfolio:
                portfolio[code] = {'name': name, 'qty': 0, 'avg_price': 0.0, 'realized_pl': 0}
            
            # 加重平均計算
            current = portfolio[code]
            total_cost = (current['qty'] * current['avg_price']) + (qty * price)
            total_qty = current['qty'] + qty
            new_avg = round(total_cost / total_qty, 2) if total_qty > 0 else 0.0
            
            portfolio[code]['qty'] = total_qty
            portfolio[code]['avg_price'] = new_avg
            portfolio[code]['name'] = name # 名前更新
            
            # ログにもその時点の平均単価を記録（修正）
            log['平均単価'] = new_avg
            log['確定損益'] = 0

        elif trade_type == "売り" or trade_type == "売却":
            if code in portfolio:
                current = portfolio[code]
                # 利益計算
                profit = (price - current['avg_price']) * qty
                
                portfolio[code]['qty'] = max(0, current['qty'] - qty)
                portfolio[code]['realized_pl'] += profit
                
                log['平均単価'] = current['avg_price']
                log['確定損益'] = profit
        
        processed_logs.append(log)

    return portfolio, processed_logs

def add_stock_callback():
    """新規入力時の処理"""
    input_date = st.session_state.input_date
    trade_type = st.session_state.input_type
    code = str(st.session_state.input_code)
    qty = st.session_state.input_qty
    price = st.session_state.input_price
    
    if not code or qty <= 0: return

    # 銘柄名取得
    current_port = st.session_state['portfolio']
    stock_name = current_port[code]['name'] if code in current_port else get_stock_name(code)
    
    action = "買い" if trade_type == "買い" else "売り"
    
    # 新しいログを追加
    new_log = {
        '日付': input_date,
        '区分': action,
        '証券コード': code,
        '銘柄名': stock_name,
        '数量': qty,
        '約定単価': price,
        '平均単価': 0, # 後で計算
        '確定損益': 0 # 後で計算
    }
    
    # 既存ログに追加して、再計算を実行
    st.session_state['trade_log'].append(new_log)
    
    # ★ここがミソ！全再計算してポートフォリオを作り直す
    new_port, new_logs = recalculate_all(st.session_state['trade_log'])
    
    st.session_state['portfolio'] = new_port
    st.session_state['trade_log'] = new_logs
    
    save_data_to_cloud()
    
    st.session_state.input_code = ""
    st.session_state.input_qty = 0
    st.session_state.input_price = 0.0
    st.session_state['system_msg'] = f"✅ {stock_name} のデータを反映しました"

def save_changes(edited_df):
    """編集されたデータで再計算して保存"""
    # データフレームを辞書リストに変換
    logs = edited_df.to_dict(orient='records')
    
    # 再計算
    new_port, new_logs = recalculate_all(logs)
    
    st.session_state['portfolio'] = new_port
    st.session_state['trade_log'] = new_logs
    save_data_to_cloud()
    st.success("再計算して保存しました！")

def init_session_state():
    if 'portfolio' not in st.session_state:
        st.session_state['portfolio'] = load_csv_from_github('portfolio.csv')
    if 'trade_log' not in st.session_state:
        st.session_state['trade_log'] = load_csv_from_github('trade_log.csv')
    if 'system_msg' not in st.session_state:
        st.session_state['system_msg'] = ""

# --- UI ---
def main():
    st.set_page_config(page_title="成功報酬帳簿", layout="wide")
    if not check_password(): return
    init_session_state()

    st.title("J_Phantom_Gear ⚙️")
    st.caption("成功報酬帳簿")
    st.markdown("---")

    if st.session_state.get('system_msg'):
        st.success(st.session_state['system_msg'])
        st.session_state['system_msg'] = ""

    # 1. 入力エリア
    with st.expander("📝 新規取引入力", expanded=True):
        c1, c2, c3, c4, c5, c6 = st.columns([1, 1.2, 1.2, 1, 1, 1])
        with c1: st.radio("区分", ["買い", "売り"], key="input_type", label_visibility="collapsed")
        with c2: st.date_input("日付", date.today(), key="input_date", label_visibility="collapsed")
        with c3: st.text_input("証券コード", placeholder="証券コード", key="input_code", label_visibility="collapsed")
        with c4: st.number_input("数量", step=100, placeholder="数量", key="input_qty", label_visibility="collapsed")
        with c5: st.number_input("単価", step=1.0, placeholder="単価", key="input_price", label_visibility="collapsed")
        with c6: st.button("実行", on_click=add_stock_callback, type="primary", use_container_width=True)

    st.markdown("---")

    # 2. ポートフォリオ（恩株判定）
    st.subheader("📊 現在のポートフォリオ")
    if st.session_state['portfolio']:
        data = []
        for c, v in st.session_state['portfolio'].items():
            current_cost = v['qty'] * v['avg_price']
            is_onkabu = (v['realized_pl'] >= current_cost) and (v['qty'] > 0)
            
            status = "🏆完全恩株" if is_onkabu else "-"
            remaining = current_cost - v['realized_pl']
            if not is_onkabu and v['qty'] > 0:
                status = f"あと{int(remaining):,}円回収"

            if v['qty'] > 0: # 保有0は非表示
                data.append({
                    '証券コード': c,
                    '銘柄名': v.get('name', 'Unknown'),
                    '保有株数': v['qty'],
                    '平均取得単価': f"{v['avg_price']:.2f}",
                    '現在保有コスト': f"{int(current_cost):,}",
                    '累計確定利益': f"{int(v['realized_pl']):,}",
                    'ステータス': status
                })
        
        if data:
            df_port = pd.DataFrame(data)
            # 証券コード順にソート
            df_port = df_port.sort_values('証券コード')
            df_port.index = range(1, len(df_port) + 1)
            st.dataframe(df_port, use_container_width=True)
        else:
            st.info("保有銘柄なし")

    st.write("") 

    # 3. 履歴編集エリア（ここが新機能！）
    st.subheader("📜 全取引履歴（修正・削除可能）")
    st.caption("※データを直接書き換えて修正できます。行を選んでDeleteキーで削除も可能。修正後は必ず「保存＆再計算」を押してね。")

    if st.session_state['trade_log']:
        df_log = pd.DataFrame(st.session_state['trade_log'])
        
        # 編集用データフレーム設定
        edited_df = st.data_editor(
            df_log,
            num_rows="dynamic", # 行の追加・削除を許可
            column_config={
                "日付": st.column_config.DateColumn("日付", format="YYYY-MM-DD"),
                "区分": st.column_config.SelectboxColumn("区分", options=["買い", "売り"]),
                "数量": st.column_config.NumberColumn("数量", min_value=0),
                "約定単価": st.column_config.NumberColumn("約定単価", min_value=0, format="%.0f円"),
                "証券コード": st.column_config.TextColumn("証券コード"),
                "銘柄名": st.column_config.TextColumn("銘柄名"),
                # 計算結果列は編集不可にする
                "平均単価": st.column_config.NumberColumn("平均単価 (自動計算)", disabled=True),
                "確定損益": st.column_config.NumberColumn("確定損益 (自動計算)", disabled=True),
            },
            use_container_width=True,
            hide_index=True
        )

        # 変更検知ボタン
        if st.button("💾 修正内容を保存＆再計算する"):
            save_changes(edited_df)
    else:
        st.info("履歴なし")

if __name__ == "__main__":
    main()
