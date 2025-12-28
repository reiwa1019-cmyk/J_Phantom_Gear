import streamlit as st
import pandas as pd
from datetime import datetime, date
from github import Github
import io
import yfinance as yf
import time
import math

# --- 0. 設定・セキュリティ ---
st.set_page_config(page_title="成功報酬帳簿", layout="wide")

def check_password():
    if st.query_params.get("auth") == "granted":
        st.session_state['logged_in'] = True
    
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False

    if st.session_state['logged_in']:
        if st.sidebar.button("ログアウト"):
            st.session_state['logged_in'] = False
            st.query_params.clear()
            st.rerun()
        return True

    st.markdown("### 🔒 PASS")
    password = st.text_input("", type="password", label_visibility="collapsed")
    if st.button("ENTER"):
        if password == st.secrets["general"]["APP_PASSWORD"]:
            st.session_state['logged_in'] = True
            st.query_params["auth"] = "granted"
            st.rerun()
        else:
            st.error("Access Denied")
    return False

if not check_password(): st.stop()

# --- 1. 関数群 ---

def get_github_repo():
    try:
        token = st.secrets["general"]["GITHUB_TOKEN"]
        repo_name = st.secrets["general"]["REPO_NAME"]
        return Github(token).get_repo(repo_name)
    except: return None

@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_info(code):
    code = str(code).strip()
    if code == "ADJUST": return "過去損益調整", 0, 0, 0
    try:
        ticker = yf.Ticker(f"{code}.T")
        name = ticker.info.get('longName')
        if not name: name = ticker.info.get('shortName')
        if not name: name = f"コード({code})"
        
        price = ticker.fast_info.last_price
        prev_close = ticker.fast_info.previous_close
        
        change = 0
        pct_change = 0
        if price and prev_close:
            change = price - prev_close
            pct_change = (change / prev_close) * 100
            
        return name, price, change, pct_change
    except:
        return f"コード({code})", 0, 0, 0

def load_csv_from_github(filename):
    repo = get_github_repo()
    if not repo: return [] if filename == 'trade_log.csv' else {}
    
    try:
        file = repo.get_contents(filename)
        st.session_state[f'{filename}_sha'] = file.sha
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
    repo = get_github_repo()
    if not repo: return

    try:
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        content = csv_buffer.getvalue()
        sha = st.session_state.get(f'{filename}_sha')
        
        if sha:
            try:
                commit = repo.update_file(filename, f"Update {filename}", content, sha)
                st.session_state[f'{filename}_sha'] = commit['content'].sha
                return
            except: pass
            
        file = repo.get_contents(filename)
        commit = repo.update_file(filename, f"Update {filename}", content, file.sha)
        st.session_state[f'{filename}_sha'] = commit['content'].sha

    except Exception as e:
        try:
            repo.create_file(filename, f"Create {filename}", content)
        except: pass

def recalculate_all(logs):
    sorted_logs = sorted(logs, key=lambda x: x['日付'])
    portfolio = {}
    processed_logs = []

    for log in sorted_logs:
        code = str(log['証券コード']).strip()
        trade_type = log['区分']
        
        if trade_type == "データ調整":
            processed_logs.append(log)
            continue

        qty = int(log['数量'])
        price = float(log['約定単価'])
        
        log_name = log.get('銘柄名')
        current_name_in_port = portfolio.get(code, {}).get('name')
        
        if log_name and "コード(" not in str(log_name): final_name = log_name
        elif current_name_in_port and "コード(" not in str(current_name_in_port): final_name = current_name_in_port
        else: final_name = str(log_name) if log_name else f"コード({code})"

        if trade_type in ["買い", "新規買付", "買い増し"]:
            if code not in portfolio:
                portfolio[code] = {'name': final_name, 'qty': 0, 'avg_price': 0.0, 'realized_pl': 0}
            
            cur = portfolio[code]
            total_cost = (cur['qty'] * cur['avg_price']) + (qty * price)
            total_qty = cur['qty'] + qty
            new_avg = round(total_cost / total_qty, 2) if total_qty > 0 else 0.0
            
            portfolio[code].update({'qty': total_qty, 'avg_price': new_avg, 'name': final_name})
            log.update({'平均単価': new_avg, '確定損益': 0, '銘柄名': final_name})

        elif trade_type in ["売り", "売却"]:
            if code in portfolio:
                cur = portfolio[code]
                profit = (price - cur['avg_price']) * qty
                portfolio[code]['qty'] = max(0, cur['qty'] - qty)
                portfolio[code]['realized_pl'] += profit
                if final_name != f"コード({code})": portfolio[code]['name'] = final_name
                log.update({'平均単価': cur['avg_price'], '確定損益': profit, '銘柄名': portfolio[code]['name']})
        
        processed_logs.append(log)
    return portfolio, processed_logs

# --- 2. イベントハンドラ ---

def execute_transaction(tx_type, date_val, code_val, qty_val, price_val):
    s = st.session_state
    
    with st.spinner('🚀 処理中...'):
        if tx_type == "データ調整":
            new_log = {
                '日付': date_val, '区分': tx_type, '証券コード': "ADJUST",
                '銘柄名': "📊 過去損益調整引継", '数量': 0, '約定単価': 0, '平均単価': 0,
                '確定損益': int(price_val)
            }
        else:
            if not code_val or qty_val <= 0: return
            code = str(code_val).strip()
            name, _, _, _ = get_stock_info(code)
            new_log = {
                '日付': date_val, '区分': tx_type, '証券コード': code, '銘柄名': name,
                '数量': qty_val, '約定単価': price_val, '平均単価': 0, '確定損益': 0
            }
        
        s.trade_log.append(new_log)
        new_port, new_logs = recalculate_all(s.trade_log)
        
        save_to_github_fast('portfolio.csv', pd.DataFrame.from_dict(new_port, orient='index').reset_index().rename(columns={'index':'Code'}))
        save_to_github_fast('trade_log.csv', pd.DataFrame(new_logs))
        
        s.portfolio = new_port
        s.trade_log = new_logs
        st.toast("✅ 反映完了")

def handle_buy():
    s = st.session_state
    execute_transaction("買い", s.buy_date, s.buy_code, s.buy_qty, s.buy_price)
    s.buy_code = ""
    s.buy_price = 0.0

def handle_sell():
    s = st.session_state
    execute_transaction("売り", s.sell_date, s.sell_code, s.sell_qty, s.sell_price)
    s.sell_code = ""
    s.sell_price = 0.0

def handle_adjust():
    s = st.session_state
    execute_transaction("データ調整", s.adj_date, "ADJUST", 0, s.adj_amount)
    s.adj_amount = 0.0

def handle_save_changes(edited_df):
    with st.spinner('💾 再計算中...'):
        if '削除' in edited_df.columns:
            valid_rows = edited_df[edited_df['削除'] == False].drop(columns=['削除'])
        else: valid_rows = edited_df

        logs = valid_rows.to_dict(orient='records')
        new_port, new_logs = recalculate_all(logs)
        
        save_to_github_fast('portfolio.csv', pd.DataFrame.from_dict(new_port, orient='index').reset_index().rename(columns={'index':'Code'}))
        save_to_github_fast('trade_log.csv', pd.DataFrame(new_logs))
        
        st.session_state.portfolio = new_port
        st.session_state.trade_log = new_logs
        st.success("完了！")
        time.sleep(1)
        st.rerun()

# --- 3. メインUI ---

def main():
    if 'portfolio' not in st.session_state:
        with st.spinner('☁️ 起動中...'):
            st.session_state.portfolio = load_csv_from_github('portfolio.csv')
            st.session_state.trade_log = load_csv_from_github('trade_log.csv')

    st.title("J_Phantom_Gear ⚙️")
    st.caption("成功報酬帳簿")
    st.markdown("---")

    qty_options = list(range(100, 100100, 100))

    # ▼ 入力エリア
    with st.container():
        st.subheader("🔵 買い注文 (Buy)")
        c1, c2, c3_radio, c3, c4, c5 = st.columns([1.2, 1.2, 0.5, 1, 1, 1])
        with c1: st.date_input("日付", date.today(), key="buy_date", label_visibility="collapsed")
        with c2: st.text_input("証券コード", placeholder="証券コード", key="buy_code", label_visibility="collapsed")
        
        with c3_radio:
            buy_mode = st.radio("入力", ["選択", "手入"], key="buy_mode", label_visibility="collapsed", horizontal=False)
        with c3:
            if buy_mode == "選択":
                st.selectbox("数量", qty_options, index=0, key="buy_qty", label_visibility="collapsed")
            else:
                st.number_input("数量(手入力)", min_value=1, step=100, key="buy_qty_manual")
        
        final_buy_qty = st.session_state.buy_qty if buy_mode == "選択" else st.session_state.get("buy_qty_manual", 0)
        if buy_mode == "手入": st.session_state.buy_qty = final_buy_qty

        with c4: st.number_input("単価", step=0.1, format="%.1f", placeholder="単価", key="buy_price", label_visibility="collapsed")
        with c5: st.button("買い実行", on_click=handle_buy, type="primary", use_container_width=True)

    st.write("") 

    with st.container():
        st.subheader("🔴 売り注文 (Sell)")
        c1, c2, c3_radio, c3, c4, c5 = st.columns([1.2, 1.2, 0.5, 1, 1, 1])
        with c1: st.date_input("日付", date.today(), key="sell_date", label_visibility="collapsed")
        with c2: st.text_input("証券コード", placeholder="証券コード", key="sell_code", label_visibility="collapsed")
        
        with c3_radio:
            sell_mode = st.radio("入力", ["選択", "手入"], key="sell_mode", label_visibility="collapsed", horizontal=False)
        with c3:
            if sell_mode == "選択":
                st.selectbox("数量", qty_options, index=0, key="sell_qty", label_visibility="collapsed")
            else:
                st.number_input("数量(手入力)", min_value=1, step=100, key="sell_qty_manual")
        
        final_sell_qty = st.session_state.sell_qty if sell_mode == "選択" else st.session_state.get("sell_qty_manual", 0)
        if sell_mode == "手入": st.session_state.sell_qty = final_sell_qty

        with c4: st.number_input("単価", step=0.1, format="%.1f", placeholder="単価", key="sell_price", label_visibility="collapsed")
        with c5: st.button("売り実行", on_click=handle_sell, type="secondary", use_container_width=True)
    
    st.write("")

    # ▼ データ調整エリア
    st.markdown("### ⚙️ 過去の損益をまとめて調整する")
    with st.container():
        st.info("ここにスプレッドシートの累計損益（例: -2150000）を入力すると、計算のスタート地点を合わせることができます。")
        c1, c2, c3 = st.columns([1.2, 2, 1])
        with c1: st.date_input("日付", date.today(), key="adj_date", label_visibility="collapsed")
        with c2: st.number_input("調整額（マイナスなら - をつけて）", step=1000.0, format="%.0f", key="adj_amount", label_visibility="collapsed")
        with c3: st.button("調整実行", on_click=handle_adjust, use_container_width=True)

    st.markdown("---")

    # ▼ ポートフォリオ
    st.subheader("📊 現在のポートフォリオ")
    if st.session_state.portfolio:
        rows = []
        port_options = {}

        for code, v in st.session_state.portfolio.items():
            if v['qty'] <= 0: continue
            
            name, current_price, change, pct_change = get_stock_info(code)
            port_options[code] = f"{name} ({code})"

            cost = v['qty'] * v['avg_price']
            is_onkabu = v['realized_pl'] >= cost
            
            if is_onkabu: status_text = "🏆完全恩株達成！"
            else:
                remaining = int(cost - v['realized_pl'])
                status_text = f"あと{remaining:,}円"

            # 騰落率＆含み益の計算
            unrealized_pl = (current_price - v['avg_price']) * v['qty']
            unrealized_pct = 0.0
            if v['avg_price'] > 0:
                unrealized_pct = ((current_price - v['avg_price']) / v['avg_price']) * 100
            
            # 装飾
            mark_change = "🔺" if change > 0 else "▼" if change < 0 else "➖"
            change_str = f"{mark_change} {int(change)} ({pct_change:+.2f}%)"

            mark_pl = "🔺" if unrealized_pl > 0 else "▼" if unrealized_pl < 0 else "➖"
            pl_str = f"{mark_pl} {int(unrealized_pl):,}"
            
            mark_pct = "+" if unrealized_pct > 0 else ""
            pct_str = f"{mark_pct}{unrealized_pct:.2f}%"

            rows.append({
                '証券コード': code, 
                '銘柄名': name,
                '現在値': f"{int(current_price):,}円",
                '前日比': change_str,
                '保有株数': v['qty'], 
                '平均取得単価': f"{v['avg_price']:,.0f}",
                '騰落率': pct_str,  # NEW
                '損益': pl_str,      # NEW
                '保有元本': f"{int(cost):,}",
                '恩株までの距離': status_text,
                '累計確定利益': f"{int(v['realized_pl']):,}"
            })
        
        if rows:
            df = pd.DataFrame(rows).sort_values('証券コード')
            df.index = range(1, len(df) + 1)
            st.dataframe(df, use_container_width=True)
            
            with st.expander("📈 恩株シミュレーター", expanded=False):
                st.info("保有銘柄を選択すると、上昇率ごとの「恩株化に必要な売却数（100株単位）」を計算します。")
                selected_code_display = st.selectbox("銘柄選択", list(port_options.values()))
                
                if selected_code_display:
                    selected_code = selected_code_display.split("(")[-1].replace(")", "").strip()
                    target_data = st.session_state.portfolio[selected_code]
                    avg = target_data['avg_price']
                    qty = target_data['qty']
                    realized = target_data['realized_pl']
                    remaining_cost = (avg * qty) - realized 
                    
                    if remaining_cost <= 0:
                         st.success("🎉 すでに恩株化達成済みです！")
                    else:
                        sim_rows = []
                        patterns = [0, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200]
                        for p in patterns:
                            target_price = avg * (1 + p/100)
                            raw_needed = math.ceil(remaining_cost / target_price)
                            unit_needed = math.ceil(raw_needed / 100) * 100
                            rem_shares = qty - unit_needed
                            judge = f"✅ 残{rem_shares}株" if rem_shares >= 0 else "❌ 不可"
                            sim_rows.append({
                                "上昇率": f"+{p}%", "想定株価": f"{target_price:,.0f}円",
                                "必要売却数": f"{unit_needed:,}株", "恩株結果": judge
                            })
                        st.dataframe(pd.DataFrame(sim_rows), use_container_width=True)
        else: st.info("保有なし")
    else: st.info("データなし")

    st.write("")

    # ▼ 💰 成功報酬管理
    st.subheader("💰 成功報酬管理")
    total_realized_pl = sum([item['確定損益'] for item in st.session_state.trade_log]) if st.session_state.trade_log else 0
    col_r1, col_r2 = st.columns(2)
    
    with col_r1:
        if total_realized_pl > 0:
            reward = total_realized_pl * 0.15
            if reward > 10000:
                st.markdown(f"""
                <div style="background-color: #d4edda; padding: 20px; border-radius: 10px; border: 2px solid #c3e6cb;">
                    <h3 style="color: #155724; margin:0;">🎉 成功報酬請求額 (15%)</h3>
                    <h1 style="color: #155724; margin:0;">¥ {int(reward):,}</h1>
                    <p style="margin:0; color:#555;">(対象純利益: ¥ {int(total_realized_pl):,})</p>
                </div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 10px; border: 1px solid #ddd;">
                    <h3 style="color: #6c757d; margin:0;">⚠️ 請求不可 (1万円以下)</h3>
                    <h1 style="color: #6c757d; margin:0;">¥ {int(reward):,}</h1>
                    <p style="margin:0;">※報酬額が1万円を超えると請求対象になります</p>
                </div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style="background-color: #f8f9fa; padding: 20px; border-radius: 10px; border: 1px solid #ddd; opacity: 0.6;">
                <h3 style="color: #6c757d; margin:0;">成功報酬請求額</h3>
                <h1 style="color: #6c757d; margin:0;">¥ 0</h1>
                <p style="margin:0;">（純利益が出ていないため請求なし）</p>
            </div>""", unsafe_allow_html=True)

    with col_r2:
        if total_realized_pl < 0:
            loss = abs(total_realized_pl)
            st.markdown(f"""
            <div style="background-color: #f8d7da; padding: 20px; border-radius: 10px; border: 2px solid #f5c6cb;">
                <h3 style="color: #721c24; margin:0;">⚠️ 損失補填が必要な額</h3>
                <h1 style="color: #721c24; margin:0;">¥ {int(loss):,}</h1>
                <p style="margin:0;">（このマイナスを埋めるまで報酬は発生しません）</p>
            </div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style="background-color: #d1ecf1; padding: 20px; border-radius: 10px; border: 2px solid #bee5eb;">
                <h3 style="color: #0c5460; margin:0;">✨ 損益</h3>
                <h1 style="color: #0c5460; margin:0;">プラス運用中</h1>
            </div>""", unsafe_allow_html=True)

    st.markdown("---")

    # ▼ 📜 全取引履歴
    st.subheader("📜 全取引履歴 (銘柄別アーカイブ)")
    
    if st.session_state.trade_log:
        df_log = pd.DataFrame(st.session_state.trade_log)
        
        unique_codes = df_log['証券コード'].unique()
        for c in unique_codes:
            sub_df = df_log[df_log['証券コード'] == c]
            
            # 名前の取得（ADJUST対応）
            if c == "ADJUST":
                name_disp = "📊 過去損益調整"
                sub_pl = sub_df['確定損益'].sum()
                label = f"⚙️ {name_disp} | 調整額: ¥{int(sub_pl):,}"
            else:
                name_disp = sub_df.iloc[0]['銘柄名']
                sub_pl = sub_df['確定損益'].sum()
                if sub_pl > 0: label = f"🟥 {name_disp} ({c}) | 累計利益: +¥{int(sub_pl):,}"
                elif sub_pl < 0: label = f"🟦 {name_disp} ({c}) | 累計損失: ¥{int(sub_pl):,}"
                else: label = f"📁 {name_disp} ({c}) | 累計損益: ¥0"

            with st.expander(label):
                st.dataframe(
                    sub_df[['日付','区分','数量','約定単価','確定損益']].sort_values('日付', ascending=False),
                    use_container_width=True, hide_index=True
                )

        st.write("")
        
        with st.expander("🛠️ データの修正・削除はこちら（クリックで開く）"):
            if "削除" not in df_log.columns: df_log.insert(0, "削除", False)
            
            edited_df = st.data_editor(
                df_log,
                num_rows="dynamic",
                use_container_width=True, hide_index=True,
                column_config={
                    "削除": st.column_config.CheckboxColumn("削除", width="small", help="チェックを入れて下のボタンを押すと削除されます"),
                    "日付": st.column_config.DateColumn("日付", format="YYYY-MM-DD"),
                    "数量": st.column_config.NumberColumn("数量", min_value=0),
                    "約定単価": st.column_config.NumberColumn("約定単価", format="%d円"),
                    "平均単価": st.column_config.NumberColumn("平均単価", disabled=True),
                    "確定損益": st.column_config.NumberColumn("確定損益", disabled=True),
                }
            )
            if st.button("💾 修正・削除を反映", type="secondary"):
                handle_save_changes(edited_df)
    else:
        st.info("履歴なし")

if __name__ == "__main__":
    main()
