import streamlit as st
import pandas as pd
from datetime import datetime
from github import Github
import io
import yfinance as yf # 株価情報の取得用

# --- 0. 簡易セキュリティ ---
def check_password():
    """パスワード認証機能"""
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False

    if st.session_state['logged_in']:
        return True

    # シンプルな表示に変更
    st.markdown("### 🔒 PASS")
    password = st.text_input("", type="password", label_visibility="collapsed")
    
    if st.button("ENTER"):
        if password == st.secrets["general"]["APP_PASSWORD"]:
            st.session_state['logged_in'] = True
            st.rerun()
        else:
            st.error("Access Denied")
    
    return False

# --- 設定・GitHub接続 ---
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
            df['コード'] = df['コード'].astype(str)
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
    """証券コードから銘柄名を取得する関数"""
    try:
        # 日本株の場合は .T をつける
        ticker = yf.Ticker(f"{code}.T")
        info = ticker.info
        return info.get('longName', '名称不明')
    except:
        return "名称不明"

def calculate_weighted_average(current_qty, current_avg, add_qty, add_price):
    total_cost = (current_qty * current_avg) + (add_qty * add_price)
    total_qty = current_qty + add_qty
    if total_qty == 0: return 0.0
    return round(total_cost / total_qty, 2)

def add_stock_callback():
    input_date = st.session_state.input_date
    trade_type = st.session_state.input_type
    code = str(st.session_state.input_code)
    qty = st.session_state.input_qty
    price = st.session_state.input_price
    
    portfolio = st.session_state['portfolio']

    if not code or qty <= 0 or price < 0:
        st.session_state['system_msg'] = "⚠️ エラー: 入力内容を確認してね"
        return

    # 銘柄名の取得（既存になければ取得）
    stock_name = "名称不明"
    if code in portfolio and 'name' in portfolio[code]:
         stock_name = portfolio[code]['name']
    else:
        with st.spinner(f"🔍 {code} の情報を取得中..."):
            stock_name = get_stock_name(code)

    if trade_type == "買い":
        if code in portfolio:
            current = portfolio[code]
            new_avg = calculate_weighted_average(current['qty'], current['avg_price'], qty, price)
            portfolio[code]['qty'] += qty
            portfolio[code]['avg_price'] = new_avg
            portfolio[code]['name'] = stock_name # 名前更新
            action = "買い増し"
            pl_display = 0
        else:
            portfolio[code] = {'name': stock_name, 'qty': qty, 'avg_price': price, 'realized_pl': 0}
            new_avg = price
            action = "新規買付"
            pl_display = 0
        msg = f"✅ {stock_name}({code}) {qty}株 購入"

    elif trade_type == "売り":
        if code not in portfolio or portfolio[code]['qty'] < qty:
            st.session_state['system_msg'] = "⚠️ エラー: 保有数が足りません"
            return
        
        current = portfolio[code]
        profit = (price - current['avg_price']) * qty
        portfolio[code]['qty'] -= qty
        portfolio[code]['realized_pl'] += profit
        # 名前情報の維持
        if 'name' not in portfolio[code]: portfolio[code]['name'] = stock_name

        action = "売却"
        pl_display = profit
        msg = f"📉 {stock_name}({code}) {qty}株 売却 (損益: {int(profit):,}円)"

    st.session_state['trade_log'].append({
        '日付': input_date, '区分': action, '証券コード': code, '銘柄名': stock_name,
        '数量': qty, '約定単価': price, '平均単価': portfolio[code]['avg_price'],
        '確定損益': pl_display
    })
    
    st.session_state['system_msg'] = msg
    save_data_to_cloud()

    st.session_state.input_code = ""
    st.session_state.input_qty = 0
    st.session_state.input_price = 0.0

def save_data_to_cloud():
    if st.session_state['portfolio']:
        df = pd.DataFrame.from_dict(st.session_state['portfolio'], orient='index')
        df.index.name = 'Code'
        df.reset_index(inplace=True)
        save_to_github('portfolio.csv', df)

    if st.session_state['trade_log']:
        df = pd.DataFrame(st.session_state['trade_log'])
        save_to_github('trade_log.csv', df)
    
    st.toast("☁️ 保存完了")

def init_session_state():
    if 'portfolio' not in st.session_state:
        st.session_state['portfolio'] = load_csv_from_github('portfolio.csv')
    if 'trade_log' not in st.session_state:
        st.session_state['trade_log'] = load_csv_from_github('trade_log.csv')
    if 'system_msg' not in st.session_state:
        st.session_state['system_msg'] = ""

# --- UI ---

def main():
    st.set_page_config(page_title="J_Phantom_Gear", layout="wide")
    if not check_password(): return

    init_session_state()

    st.title("J_Phantom_Gear ⚙️")
    st.caption("成功報酬帳簿")
    st.markdown("---")

    if st.session_state['system_msg']:
        if "⚠️" in st.session_state['system_msg']:
            st.error(st.session_state['system_msg'])
        else:
            st.success(st.session_state['system_msg'])

    # 入力エリア
    with st.container():
        c1, c2, c3, c4, c5, c6 = st.columns([1, 1.2, 1.2, 1, 1, 1])
        with c1: st.radio("区分", ["買い", "売り"], key="input_type", label_visibility="collapsed")
        with c2: st.date_input("日付", datetime.today(), key="input_date", label_visibility="collapsed")
        with c3: st.text_input("証券コード", placeholder="証券コード", key="input_code", label_visibility="collapsed")
        with c4: st.number_input("数量", step=100, placeholder="数量", key="input_qty", label_visibility="collapsed")
        with c5: st.number_input("単価", step=1.0, placeholder="単価", key="input_price", label_visibility="collapsed")
        with c6: st.button("実行", on_click=add_stock_callback, type="primary", use_container_width=True)

    st.markdown("---")

    # メイン表示エリア（上下配置に変更）
    
    # 1. ポートフォリオ（主役）
    st.subheader("📊 現在のポートフォリオ")
    if st.session_state['portfolio']:
        data = []
        for c, v in st.session_state['portfolio'].items():
            # 銘柄名の取得（古いデータ用対応）
            name = v.get('name', get_stock_name(c))
            
            # --- 恩株判定ロジック (Ver.2) ---
            # 累計確定利益 >= 現在の保有コスト (株数 * 平均単価)
            current_cost = v['qty'] * v['avg_price']
            is_onkabu = (v['realized_pl'] >= current_cost) and (v['qty'] > 0)
            
            status = "🏆完全恩株" if is_onkabu else "-"
            # 恩株までの残り利益
            remaining = current_cost - v['realized_pl']
            if not is_onkabu and v['qty'] > 0:
                status = f"あと{int(remaining):,}円回収で恩株"

            if v['qty'] > 0: # 保有0のものは表示しない設定（好みで変更可）
                data.append({
                    '証券コード': c,
                    '銘柄名': name,
                    '保有株数': v['qty'],
                    '平均取得単価': f"{v['avg_price']:.2f}",
                    '現在保有コスト': f"{int(current_cost):,}",
                    '累計確定利益': f"{int(v['realized_pl']):,}",
                    'ステータス': status
                })
        
        if data:
            df_port = pd.DataFrame(data)
            # 1から始まるIndexを作成
            df_port.index = range(1, len(df_port) + 1)
            st.dataframe(df_port, use_container_width=True)
        else:
            st.info("現在保有している銘柄はありません")
    else:
        st.info("データなし")

    st.write("") # スペース
    st.write("") 

    # 2. 取引履歴（詳細）
    st.subheader("📜 全取引履歴")
    if st.session_state['trade_log']:
        df_log = pd.DataFrame(st.session_state['trade_log'])
        # カラム名の整理（既存データの整合性のため）
        if 'コード' in df_log.columns: df_log.rename(columns={'コード': '証券コード'}, inplace=True)
        
        # 必要なカラムだけ表示
        cols = ['日付', '区分', '証券コード', '銘柄名', '数量', '約定単価', '確定損益']
        # データにないカラムは埋める
        for col in cols:
            if col not in df_log.columns: df_log[col] = "-"
            
        df_display = df_log[cols].iloc[::-1].reset_index(drop=True)
        df_display.index = range(1, len(df_display) + 1)
        st.dataframe(df_display, use_container_width=True)

if __name__ == "__main__":
    main()
