import streamlit as st
import pandas as pd
from datetime import datetime, date
from github import Github
import io
import yfinance as yf
import time

# --- 0. 設定・セキュリティ ---
st.set_page_config(page_title="成功報酬帳簿", layout="wide")

def check_password():
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False

    if st.session_state['logged_in']: return True

    st.markdown("### 🔒 PASS")
    password = st.text_input("", type="password", label_visibility="collapsed")
    if st.button("ENTER"):
        if password == st.secrets["general"]["APP_PASSWORD"]:
            st.session_state['logged_in'] = True
            st.rerun()
        else:
            st.error("Access Denied")
    return False

if not check_password(): st.stop()

# --- 1. 高速化関数群 (Core Logic) ---

def get_github_repo():
    try:
        token = st.secrets["general"]["GITHUB_TOKEN"]
        repo_name = st.secrets["general"]["REPO_NAME"]
        return Github(token).get_repo(repo_name)
    except: return None

# ★最強の高速化: 銘柄名を24時間キャッシュ＆エラー時は即座にスキップ
@st.cache_data(ttl=86400, show_spinner=False)
def get_stock_name_cached(code):
    try:
        ticker = yf.Ticker(f"{code}.T")
        name = ticker.info.get('longName', None)
        return name if name else f"コード({code})"
    except:
        return f"コード({code})"

def load_csv_from_github(filename):
    repo = get_github_repo()
    if not repo: return [] if filename == 'trade_log.csv' else {}
    
    try:
        file = repo.get_contents(filename)
        st.session_state[f'{filename}_sha'] = file.sha # SHAを記憶
        csv_data = file.decoded_content.decode("utf-8")
        df = pd.read_csv(io.StringIO(csv_data))
        
        if filename == 'portfolio.csv':
            df['Code'] = df['Code'].astype(str)
            return df.set_index('Code').to_dict(orient='index')
        else:
            df['証券コード'] = df['証券コード'].astype(str)
            df['日付'] = pd.to_datetime(df['日付']).dt.date
            return df.to_dict(orient='records')
    except:
        return [] if filename == 'trade_log.csv' else {}

def save_to_github_fast(filename, df):
    """SHAを利用した高速保存（無駄な読み込みをカット）"""
    repo = get_github_repo()
    if not repo: return

    try:
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        content = csv_buffer.getvalue()
        sha = st.session_state.get(f'{filename}_sha')
        
        # 記憶しているSHAで直接更新を試みる
        if sha:
            try:
                commit = repo.update_file(filename, f"Update {filename}", content, sha)
                st.session_state[f'{filename}_sha'] = commit['content'].sha
                return
            except: pass # SHA不一致なら下記へ
            
        # 失敗時は正攻法で取得して更新
        file = repo.get_contents(filename)
        commit = repo.update_file(filename, f"Update {filename}", content, file.sha)
        st.session_state[f'{filename}_sha'] = commit['content'].sha

    except Exception as e:
        # ファイルがない場合は新規作成
        try:
            repo.create_file(filename, f"Create {filename}", content)
        except Exception as create_err:
            st.error(f"Save Error: {create_err}")

def recalculate_all(logs):
    """全履歴からのリプレイ再計算"""
    sorted_logs = sorted(logs, key=lambda x: x['日付'])
    portfolio = {}
    processed_logs = []

    for log in sorted_logs:
        code = str(log['証券コード'])
        qty = int(log['数量'])
        price = float(log['約定単価'])
        trade_type = log['区分']
        name = log.get('銘柄名', str(code))

        if trade_type in ["買い", "新規買付", "買い増し"]:
            if code not in portfolio:
                portfolio[code] = {'name': name, 'qty': 0, 'avg_price': 0.0, 'realized_pl': 0}
            
            cur = portfolio[code]
            total_cost = (cur['qty'] * cur['avg_price']) + (qty * price)
            total_qty = cur['qty'] + qty
            new_avg = round(total_cost / total_qty, 2) if total_qty > 0 else 0.0
            
            portfolio[code].update({'qty': total_qty, 'avg_price': new_avg, 'name': name})
            log.update({'平均単価': new_avg, '確定損益': 0})

        elif trade_type in ["売り", "売却"]:
            if code in portfolio:
                cur = portfolio[code]
                profit = (price - cur['avg_price']) * qty
                portfolio[code]['qty'] = max(0, cur['qty'] - qty)
                portfolio[code]['realized_pl'] += profit
                log.update({'平均単価': cur['avg_price'], '確定損益': profit})
        
        processed_logs.append(log)
    return portfolio, processed_logs

# --- 2. イベントハンドラ ---

def handle_add_transaction():
    """新規追加時の処理"""
    s = st.session_state
    if not s.input_code or s.input_qty <= 0: return

    with st.spinner('🚀 処理中...'):
        # 銘柄名取得（キャッシュ活用）
        current_name = s.portfolio.get(s.input_code, {}).get('name')
        name = current_name if current_name else get_stock_name_cached(s.input_code)
        
        new_log = {
            '日付': s.input_date,
            '区分': "買い" if s.input_type == "買い" else "売り",
            '証券コード': s.input_code,
            '銘柄名': name,
            '数量': s.input_qty,
            '約定単価': s.input_price,
            '平均単価': 0, '確定損益': 0
        }
        
        s.trade_log.append(new_log)
        new_port, new_logs = recalculate_all(s.trade_log)
        
        # 保存
        save_to_github_fast('portfolio.csv', pd.DataFrame.from_dict(new_port, orient='index').reset_index().rename(columns={'index':'Code'}))
        save_to_github_fast('trade_log.csv', pd.DataFrame(new_logs))
        
        # State更新
        s.portfolio = new_port
        s.trade_log = new_logs
        
        # 入力リセット
        s.input_code = ""
        s.input_qty = 0
        s.input_price = 0.0
        st.toast(f"✅ {name} 反映完了")

def handle_save_changes(edited_df):
    """編集保存時の処理"""
    with st.spinner('💾 再計算して保存中...'):
        logs = edited_df.to_dict(orient='records')
        new_port, new_logs = recalculate_all(logs)
        
        save_to_github_fast('portfolio.csv', pd.DataFrame.from_dict(new_port, orient='index').reset_index().rename(columns={'index':'Code'}))
        save_to_github_fast('trade_log.csv', pd.DataFrame(new_logs))
        
        st.session_state.portfolio = new_port
        st.session_state.trade_log = new_logs
        st.success("修正を反映しました！")

# --- 3. メインUI ---

def main():
    # 初期化
    if 'portfolio' not in st.session_state:
        with st.spinner('☁️ データを取得中...'):
            st.session_state.portfolio = load_csv_from_github('portfolio.csv')
            st.session_state.trade_log = load_csv_from_github('trade_log.csv')

    st.title("J_Phantom_Gear ⚙️")
    st.caption("成功報酬帳簿")
    st.markdown("---")

    # ▼ 入力エリア
    with st.expander("📝 新規取引入力", expanded=True):
        c1, c2, c3, c4, c5, c6 = st.columns([1, 1.2, 1.2, 1, 1, 1])
        with c1: st.radio("Type", ["買い", "売り"], key="input_type", label_visibility="collapsed")
        with c2: st.date_input("Date", date.today(), key="input_date", label_visibility="collapsed")
        with c3: st.text_input("Code", placeholder="証券コード", key="input_code", label_visibility="collapsed")
        with c4: st.number_input("Qty", step=100, placeholder="数量", key="input_qty", label_visibility="collapsed")
        with c5: st.number_input("Price", step=1.0, placeholder="単価", key="input_price", label_visibility="collapsed")
        with c6: st.button("実行", on_click=handle_add_transaction, type="primary", use_container_width=True)

    st.markdown("---")

    # ▼ ポートフォリオ表示
    st.subheader("📊 現在のポートフォリオ")
    if st.session_state.portfolio:
        rows = []
        for code, v in st.session_state.portfolio.items():
            if v['qty'] <= 0: continue # 保有0はスキップ
            
            cost = v['qty'] * v['avg_price']
            is_onkabu = v['realized_pl'] >= cost
            
            status = "🏆完全恩株" if is_onkabu else f"あと{int(cost - v['realized_pl']):,}円"
            
            rows.append({
                '証券コード': code,
                '銘柄名': v.get('name', '-'),
                '保有株数': v['qty'],
                '平均取得単価': f"{v['avg_price']:,.0f}", # 小数点なしで見やすく
                '現在保有コスト': f"{int(cost):,}",
                '累計確定利益': f"{int(v['realized_pl']):,}",
                'ステータス': status
            })
        
        if rows:
            df = pd.DataFrame(rows).sort_values('証券コード')
            df.index = range(1, len(df) + 1)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("現在保有している銘柄はありません")
    else:
        st.info("データがありません")

    st.write("")

    # ▼ 編集可能履歴エリア
    st.subheader("📜 全取引履歴（編集モード）")
    st.caption("※内容を直接修正・削除(行選択してDelete)できます。修正後は必ず下のボタンを押してください。")
    
    if st.session_state.trade_log:
        df_log = pd.DataFrame(st.session_state.trade_log)
        
        edited_df = st.data_editor(
            df_log,
            num_rows="dynamic",
            column_config={
                "日付": st.column_config.DateColumn("日付", format="YYYY-MM-DD"),
                "区分": st.column_config.SelectboxColumn("区分", options=["買い", "売り"]),
                "数量": st.column_config.NumberColumn("数量", min_value=0),
                "約定単価": st.column_config.NumberColumn("約定単価", format="%d円"),
                "平均単価": st.column_config.NumberColumn("平均単価", disabled=True),
                "確定損益": st.column_config.NumberColumn("確定損益", disabled=True),
            },
            use_container_width=True,
            hide_index=True
        )

        if st.button("💾 修正内容を保存＆再計算する", type="secondary", use_container_width=True):
            handle_save_changes(edited_df)

if __name__ == "__main__":
    main()
