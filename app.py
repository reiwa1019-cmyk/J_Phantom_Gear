import streamlit as st
import pandas as pd
from datetime import datetime
from github import Github
import io

# --- 設定 ---
# requirements.txt に "PyGithub" が必要

def get_github_repo():
    """GitHubリポジトリへの接続"""
    try:
        token = st.secrets["general"]["GITHUB_TOKEN"]
        repo_name = st.secrets["general"]["REPO_NAME"]
        g = Github(token)
        return g.get_repo(repo_name)
    except Exception as e:
        st.error(f"GitHub接続エラー: Secretsの設定を確認してね！\n{e}")
        return None

def load_csv_from_github(filename):
    """GitHubからCSV読み込み"""
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
    """GitHubへ上書き保存"""
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
        st.error(f"保存失敗: {e}")

# --- メイン処理 ---

def init_session_state():
    if 'portfolio' not in st.session_state:
        st.session_state['portfolio'] = load_csv_from_github('portfolio.csv')
    if 'trade_log' not in st.session_state:
        st.session_state['trade_log'] = load_csv_from_github('trade_log.csv')
    if 'system_msg' not in st.session_state:
        st.session_state['system_msg'] = ""

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

    if trade_type == "買い":
        if code in portfolio:
            current = portfolio[code]
            new_avg = calculate_weighted_average(current['qty'], current['avg_price'], qty, price)
            portfolio[code]['qty'] += qty
            portfolio[code]['avg_price'] = new_avg
            action = "買い増し"
            pl_display = 0
        else:
            portfolio[code] = {'qty': qty, 'avg_price': price, 'realized_pl': 0}
            new_avg = price
            action = "新規買付"
            pl_display = 0
        msg = f"✅ {code} {qty}株 購入 (平均: {new_avg}円)"

    elif trade_type == "売り":
        if code not in portfolio or portfolio[code]['qty'] < qty:
            st.session_state['system_msg'] = "⚠️ エラー: 保有数が足りません"
            return
        
        current = portfolio[code]
        profit = (price - current['avg_price']) * qty
        portfolio[code]['qty'] -= qty
        portfolio[code]['realized_pl'] += profit
        action = "売却"
        pl_display = profit
        msg = f"📉 {code} {qty}株 売却 (損益: {int(profit):,}円)"

    st.session_state['trade_log'].append({
        '日付': input_date, '区分': action, 'コード': code,
        '数量': qty, '約定単価': price, '平均単価': portfolio[code]['avg_price'],
        '確定損益': pl_display
    })
    
    st.session_state['system_msg'] = msg
    
    # ★GitHub保存実行
    save_data_to_cloud()

    # 入力クリア
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
    
    st.toast("☁️ データ保存完了！")

# --- UI ---

def main():
    st.set_page_config(page_title="J_Phantom_Gear", layout="wide")
    init_session_state()

    st.title("J_Phantom_Gear ⚙️")
    st.caption("GitHub Sync Mode")
    st.markdown("---")

    if st.session_state['system_msg']:
        if "⚠️" in st.session_state['system_msg']:
            st.error(st.session_state['system_msg'])
        else:
            st.success(st.session_state['system_msg'])

    with st.container():
        col1, col2, col3, col4, col5, col6 = st.columns([1,1.2,1.5,1,1,1])
        with col1: st.radio("区分", ["買い", "売り"], key="input_type")
        with col2: st.date_input("日付", datetime.today(), key="input_date")
        with col3: st.text_input("コード", key="input_code")
        with col4: st.number_input("数量", step=100, key="input_qty")
        with col5: st.number_input("単価", step=1.0, key="input_price")
        with col6: 
            st.write("")
            st.write("")
            st.button("実行", on_click=add_stock_callback, type="primary")

    st.markdown("---")

    c1, c2 = st.columns([3, 2])
    with c1:
        st.subheader("📊 ポートフォリオ")
        if st.session_state['portfolio']:
            data = []
            for c, v in st.session_state['portfolio'].items():
                status = "✨恩株" if v['realized_pl'] > 0 and v['qty'] > 0 else "-"
                data.append({'コード': c, '保有': v['qty'], '平均単価': f"{v['avg_price']:.2f}", '累計損益': f"{int(v['realized_pl']):,}", '状態': status})
            st.dataframe(pd.DataFrame(data), use_container_width=True)
    
    with c2:
        st.subheader("📜 履歴")
        if st.session_state['trade_log']:
            st.dataframe(pd.DataFrame(st.session_state['trade_log']).iloc[::-1], use_container_width=True)

if __name__ == "__main__":
    main()
