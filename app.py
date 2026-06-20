import streamlit as st
import pandas as pd
from datetime import datetime, date
from github import Github
import io
import yfinance as yf
import time
import math
import requests
from bs4 import BeautifulSoup
import re
import hmac, hashlib

# --- 0. 設定・セキュリティ ---
st.set_page_config(page_title="J_Phantom_Gear", layout="wide")

def _auth_secret():
    # トークン署名用のサーバ側シークレット(URLには出ない)。
    try:
        g = st.secrets["general"]
        return g.get("APP_PASSWORD", "admin123") + "|" + g.get("VIEWER_PASSWORD", "guest123") + "|jpg-auth"
    except Exception:
        return "jpg-auth-default"

def _make_token(role):
    exp = int(time.time()) + 24 * 3600  # 24時間有効
    payload = f"{role}.{exp}"
    sig = hmac.new(_auth_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:20]
    return f"{payload}.{sig}"

def _check_token(tok):
    try:
        role, exp, sig = str(tok).split(".")
        payload = f"{role}.{exp}"
        good = hmac.new(_auth_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:20]
        if hmac.compare_digest(sig, good) and int(exp) > time.time() and role in ("admin", "viewer"):
            return role
    except Exception:
        pass
    return None

def check_password():
    if 'user_role' not in st.session_state:
        st.session_state['user_role'] = None

    # 自動ログイン: 有効なURLトークンがあれば24時間ログイン維持(更新してもログイン画面に戻らない)
    if not st.session_state['user_role']:
        try:
            tok = st.query_params.get('t')
        except Exception:
            tok = None
        if tok:
            r = _check_token(tok)
            if r:
                st.session_state['user_role'] = r

    if st.session_state['user_role']:
        role_label = "管理者 (Admin)" if st.session_state['user_role'] == "admin" else "閲覧者 (Guest)"
        st.sidebar.caption(f"ログイン中: {role_label}（24時間維持）")
        if st.sidebar.button("ログアウト"):
            st.session_state['user_role'] = None
            try: st.query_params.clear()
            except Exception: pass
            st.rerun()
        return True

    st.markdown("### 🔒 Login")
    password = st.text_input("パスワードを入力してください", type="password")

    if st.button("ログイン"):
        admin_pass = st.secrets["general"].get("APP_PASSWORD", "admin123")
        viewer_pass = st.secrets["general"].get("VIEWER_PASSWORD", "guest123")

        role = None
        if password == admin_pass:
            role = "admin"
        elif password == viewer_pass:
            role = "viewer"

        if role:
            st.session_state['user_role'] = role
            try: st.query_params['t'] = _make_token(role)   # 24時間維持トークンをURLに保存
            except Exception: pass
            st.rerun()
        else:
            st.error("パスワードが間違っています")

    return False

if not check_password(): st.stop()

IS_ADMIN = (st.session_state['user_role'] == "admin")

# --- 1. 関数群 (GitHub / データ処理) ---

def get_github_repo():
    try:
        token = st.secrets["general"]["GITHUB_TOKEN"]
        repo_name = st.secrets["general"]["REPO_NAME"]
        return Github(token).get_repo(repo_name)
    except: return None

@st.cache_data(ttl=600)
def fetch_batch_prices(codes):
    target_codes = [str(c).strip() for c in codes if str(c).strip() not in ["ADJUST", "PAYMENT"]]
    if not target_codes: return {}

    tickers = [f"{c}.T" for c in target_codes]
    
    try:
        df = yf.download(tickers, period="5d", progress=False)['Close']
        data_map = {}
        
        if isinstance(df, pd.Series):
            df = df.to_frame(name=tickers[0])
            
        if not df.empty:
            latest_row = df.iloc[-1]
            prev_row = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]

            for t in tickers:
                code = t.replace(".T", "")
                val = latest_row.get(t)
                prev_val = prev_row.get(t)

                if pd.notnull(val):
                    val_f = float(val)
                    prev_f = float(prev_val) if pd.notnull(prev_val) else val_f
                    diff = val_f - prev_f
                    data_map[code] = {'price': val_f, 'diff': diff}
                else:
                    data_map[code] = {'price': 0.0, 'diff': 0.0}
        return data_map
    except Exception as e:
        return {}

def contains_japanese(text):
    return bool(re.search(r'[ぁ-んァ-ン一-龥]', str(text)))

@st.cache_data
def get_stock_name_fallback(code):
    try:
        code_str = str(code).strip()
        if not code_str.isdigit(): return f"コード({code_str})"

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        
        # 1. みんかぶ (正式な日本語名が取りやすい)
        try:
            url = f"https://minkabu.jp/stock/{code_str}"
            res = requests.get(url, headers=headers, timeout=5)
            res.encoding = "utf-8"
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                title_tag = soup.find('title')
                if title_tag and title_tag.text:
                    name = title_tag.text.split(' (')[0].strip()
                    if name and "minkabu" not in name.lower() and "見つかりません" not in name:
                        return name
        except: pass

        # 2. Kabutan (バックアップ)
        try:
            url = f"https://kabutan.jp/stock/?code={code_str}"
            res = requests.get(url, headers=headers, timeout=5)
            res.encoding = "utf-8"
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                title_tag = soup.find('title')
                if title_tag and title_tag.text:
                    name = title_tag.text.split('【')[0].strip()
                    if name and "kabutan" not in name.lower() and "探しておられる" not in name:
                        return name
        except: pass

        # 3. yfinance (最終手段)
        try:
            tkr = yf.Ticker(f"{code_str}.T")
            info = tkr.info
            name = info.get('longName') or info.get('shortName')
            if name: return name
        except: pass

    except Exception:
        pass
        
    return f"コード({code})"

def load_csv_from_github(filename):
    repo = get_github_repo()
    if not repo: return [] if filename == 'trade_log.csv' or filename == 'past_data.csv' else {}
    
    try:
        file = repo.get_contents(filename)
        if filename != 'past_data.csv':
            st.session_state[f'{filename}_sha'] = file.sha
        
        csv_data = file.decoded_content.decode("utf-8")
        df = pd.read_csv(io.StringIO(csv_data))
        
        if filename == 'portfolio.csv':
            df['Code'] = df['Code'].astype(str)
            # 後方互換: 欠損列を既定値で補完(旧バージョンのCSVでも起動時クラッシュしないように)。
            # 起動時はここで読んだ値を recalculate_all を経ずに直接表示するため、この補完が必須。
            for col, default in [('name', ''), ('qty', 0), ('avg_price', 0.0), ('realized_pl', 0), ('original_avg', 0.0)]:
                if col not in df.columns:
                    df[col] = default
                else:
                    df[col] = df[col].fillna(default)
            if 'position_side' not in df.columns:
                df['position_side'] = 'long'
            else:
                df['position_side'] = df['position_side'].fillna('long')
            return df.set_index('Code').to_dict(orient='index')
        elif filename == 'past_data.csv':
            return df
        else:
            df['証券コード'] = df['証券コード'].astype(str)
            df['日付'] = pd.to_datetime(df['日付']).dt.date
            if 'ボーナス' not in df.columns: df['ボーナス'] = False
            return df.to_dict(orient='records')
    except:
        return [] if filename == 'trade_log.csv' or filename == 'past_data.csv' else {}

def save_to_github_fast(filename, df):
    # 保存の成否を True/False で返す(呼び出し側で『両方成功した時だけ反映完了』を判定するため)。
    if not IS_ADMIN: return False
    repo = get_github_repo()
    if not repo: return False

    try:
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        content = csv_buffer.getvalue()
        sha = st.session_state.get(f'{filename}_sha')

        if sha:
            try:
                commit = repo.update_file(filename, f"Update {filename}", content, sha)
                st.session_state[f'{filename}_sha'] = commit['content'].sha
                return True
            except: pass

        file = repo.get_contents(filename)
        commit = repo.update_file(filename, f"Update {filename}", content, file.sha)
        st.session_state[f'{filename}_sha'] = commit['content'].sha
        return True

    except Exception as e:
        try:
            repo.create_file(filename, f"Create {filename}", content)
            return True
        except:
            return False

def is_clean_name(n):
    ns = str(n)
    if not ns or "コード(" in ns or "Yahoo!" in ns or "表示できません" in ns:
        return False
    return True

def recalculate_all(logs):
    # 履歴編集で空行/空欄が残っても落ちないよう、不正行を除外してから並べ替える
    def _valid(r):
        d = r.get('日付'); k = r.get('区分')
        if d is None or k is None: return False
        if str(d).strip() in ('', 'NaT', 'nan', 'None'): return False
        if str(k).strip() in ('', 'nan', 'None'): return False
        return True
    sorted_logs = sorted([l for l in logs if _valid(l)], key=lambda x: x['日付'])
    portfolio = {}
    processed_logs = []

    for log in sorted_logs:
        code = str(log['証券コード']).strip()
        trade_type = log['区分']
        is_bonus = log.get('ボーナス', False)
        
        if trade_type in ["データ調整", "報酬精算"]:
            processed_logs.append(log)
            continue

        if trade_type == "恩株精算":
            # 恩株ボーナス受領: その恩株のコストを「受領時の株価」に更新(コスト更新方式)。
            # 株は保有に残り恩株一覧から外れる。確定損益0(損益は発生しない)。以後の値上がり分だけ通常報酬対象。
            settle_price = float(log['約定単価'])
            if code in portfolio and portfolio[code].get('avg_price', 0) == 0 and portfolio[code].get('qty', 0) > 0:
                portfolio[code]['avg_price'] = settle_price
                portfolio[code]['original_avg'] = settle_price
            log.update({'確定損益': 0})
            processed_logs.append(log)
            continue

        try:
            qty = int(float(log['数量']))
            price = float(log['約定単価'])
        except (TypeError, ValueError):
            # 数量/単価が空・不正な行はスキップ(履歴編集の空行対策)。確定損益0で素通り。
            log.update({'確定損益': 0})
            processed_logs.append(log)
            continue

        log_name = log.get('銘柄名', "")
        current_port_name = portfolio.get(code, {}).get('name', "")
        
        final_name = f"コード({code})"
        if is_clean_name(current_port_name):
            final_name = current_port_name
        elif is_clean_name(log_name):
            final_name = log_name

        if trade_type in ["買い", "新規買付", "買い増し"]:
            # 反対玉ガード: 空売り建玉が残る銘柄への買いは両建て/ドテン誤区分として素通り(損益0)。
            if code in portfolio and portfolio[code]['qty'] > 0 and portfolio[code].get('position_side', 'long') == 'short':
                log.update({'確定損益': 0, '銘柄名': portfolio[code]['name']})
                processed_logs.append(log)
                continue

            if code not in portfolio:
                portfolio[code] = {'name': final_name, 'qty': 0, 'avg_price': 0.0, 'realized_pl': 0, 'original_avg': 0.0, 'position_side': 'long'}

            cur = portfolio[code]
            if contains_japanese(final_name) and not contains_japanese(cur['name']):
                 cur['name'] = final_name
            elif "コード(" in cur['name'] and "コード(" not in final_name:
                cur['name'] = final_name

            total_cost = (cur['qty'] * cur['avg_price']) + (qty * price)
            
            base_avg = cur.get('original_avg', cur['avg_price'])
            if base_avg == 0 and cur['qty'] == 0: base_avg = price
            elif base_avg == 0 and cur['avg_price'] > 0: base_avg = cur['avg_price']

            total_real_cost = (cur['qty'] * base_avg) + (qty * price)
            total_qty = cur['qty'] + qty
            
            new_avg = total_cost / total_qty if total_qty > 0 else 0.0
            new_real_avg = total_real_cost / total_qty if total_qty > 0 else 0.0
            
            portfolio[code].update({'qty': total_qty, 'avg_price': new_avg, 'original_avg': new_real_avg, 'position_side': 'long'})
            log.update({'平均単価': round(new_avg, 2), '確定損益': 0, '銘柄名': cur['name']})

        elif trade_type == "新規売り":
            # 反対玉ガード: 買い建玉が残る銘柄への新規売りは両建て禁止として素通り(損益0)。
            if code in portfolio and portfolio[code]['qty'] > 0 and portfolio[code].get('position_side', 'long') == 'long':
                log.update({'確定損益': 0, '銘柄名': portfolio[code]['name']})
                processed_logs.append(log)
                continue

            if code not in portfolio:
                portfolio[code] = {'name': final_name, 'qty': 0, 'avg_price': 0.0, 'realized_pl': 0, 'original_avg': 0.0, 'position_side': 'short'}

            cur = portfolio[code]
            if contains_japanese(final_name) and not contains_japanese(cur['name']):
                 cur['name'] = final_name
            elif "コード(" in cur['name'] and "コード(" not in final_name:
                cur['name'] = final_name

            # 空売りの建値は「売建の加重平均」(常に正の値)。買い増しの鏡像。
            total_proceeds = (cur['qty'] * cur['avg_price']) + (qty * price)
            total_qty = cur['qty'] + qty
            new_avg = total_proceeds / total_qty if total_qty > 0 else 0.0

            portfolio[code].update({'qty': total_qty, 'avg_price': new_avg, 'original_avg': new_avg, 'position_side': 'short'})
            log.update({'平均単価': round(new_avg, 2), '確定損益': 0, '銘柄名': cur['name']})

        elif trade_type == "買い戻し":
            # 空売り建玉があるときのみ決済。建玉なし/買い建ては逆クロスとして素通り(損益0)。
            if code in portfolio and portfolio[code].get('position_side', 'long') == 'short' and portfolio[code]['qty'] > 0:
                cur = portfolio[code]
                fill = min(qty, cur['qty'])                  # 過剰買い戻しは建玉数までにクランプ
                profit = (cur['avg_price'] - price) * fill   # 値下がりで利益
                new_qty = cur['qty'] - fill
                cur['realized_pl'] += profit
                cur['qty'] = new_qty
                if new_qty == 0:
                    cur['position_side'] = 'long'            # 建玉ゼロ→中立(次は買いでも空売りでも開始可)に戻す
                log.update({'平均単価': round(cur['avg_price'], 2), '確定損益': int(profit), '銘柄名': cur['name']})
            else:
                log.update({'確定損益': 0})

        elif trade_type in ["売り", "売却"]:
            # 逆クロスガード: 空売り建玉への通常売りは素通り(空売りの決済は『買い戻し』)。
            if code in portfolio and portfolio[code].get('position_side', 'long') == 'short' and portfolio[code]['qty'] > 0:
                log.update({'確定損益': 0, '銘柄名': portfolio[code]['name']})
                processed_logs.append(log)
                continue
            if code in portfolio:
                cur = portfolio[code]
                fill = min(qty, cur['qty'])   # 保有数を超える売りは実際に売れる数までに丸める(確定益の過大計上=過大請求を防ぐ)
                if is_bonus:
                    total_holding_cost = cur['qty'] * cur['avg_price']
                    sell_amount = fill * price
                    if sell_amount >= total_holding_cost:
                        # 元本を回収できた → 従来どおり恩株化(残り株のコストを0に)
                        profit = sell_amount - total_holding_cost
                        portfolio[code]['qty'] = cur['qty'] - fill
                        portfolio[code]['avg_price'] = 0.0
                        portfolio[code]['realized_pl'] += profit
                        log.update({'平均単価': 0, '確定損益': int(profit), '銘柄名': cur['name']})
                    else:
                        # 元本を回収しきれていない部分恩株化 → 普通の売りとして正しく計算。
                        # 確定損益は売った株の純益(price-avg)*fill。残り株はコストを残す(タダにしない)。
                        profit = (price - cur['avg_price']) * fill
                        portfolio[code]['qty'] = cur['qty'] - fill
                        portfolio[code]['realized_pl'] += profit
                        log.update({'平均単価': round(cur['avg_price'], 2), '確定損益': int(profit), '銘柄名': cur['name']})
                else:
                    profit = (price - cur['avg_price']) * fill
                    portfolio[code]['qty'] = cur['qty'] - fill
                    portfolio[code]['realized_pl'] += profit
                    log.update({'平均単価': round(cur['avg_price'], 2), '確定損益': int(profit), '銘柄名': cur['name']})
        
        processed_logs.append(log)
    return portfolio, processed_logs

# --- 2. イベントハンドラ ---

def execute_transaction(tx_type, date_val, code_val, qty_val, price_val, is_bonus=False):
    if not IS_ADMIN: return 

    s = st.session_state

    # 二重登録ガード: 直前(5秒以内)とまったく同じ取引が来たら弾く。
    # スマホの二度押し/保存中(GitHub書込で遅い)の再発火による二重計上を防ぐ。
    _sig = (tx_type, str(code_val).strip(), qty_val, price_val, bool(is_bonus))
    _now = time.time()
    _last = s.get('_last_tx')
    if _last and _last[0] == _sig and (_now - _last[1]) < 5:
        st.toast("⚠️ 直前と同じ取引です。二重登録を防ぐためスキップしました", icon="⚠️")
        return
    s['_last_tx'] = (_sig, _now)

    with st.spinner('🚀 処理中...'):
        if tx_type == "データ調整":
            new_log = {
                '日付': date_val, '区分': tx_type, '証券コード': "ADJUST",
                '銘柄名': "📊 過去損益調整引継", '数量': 0, '約定単価': 0, '平均単価': 0,
                '確定損益': int(price_val), 'ボーナス': False
            }
        elif tx_type == "報酬精算":
            new_log = {
                '日付': date_val, '区分': tx_type, '証券コード': "PAYMENT",
                '銘柄名': "✅ 成功報酬精算完了", '数量': 0, '約定単価': 0, '平均単価': 0,
                '確定損益': int(price_val), 'ボーナス': is_bonus
            }
        else:
            if not code_val or qty_val <= 0: return
            code = str(code_val).strip()
            name = get_stock_name_fallback(code) 
            new_log = {
                '日付': date_val, '区分': tx_type, '証券コード': code, '銘柄名': name,
                '数量': qty_val, '約定単価': price_val, '平均単価': 0, '確定損益': 0,
                'ボーナス': is_bonus
            }
        
        s.trade_log.append(new_log)
        new_port, new_logs = recalculate_all(s.trade_log)
        
        ok1 = save_to_github_fast('portfolio.csv', pd.DataFrame.from_dict(new_port, orient='index').reset_index().rename(columns={'index':'Code'}))
        ok2 = save_to_github_fast('trade_log.csv', pd.DataFrame(new_logs))

        s.portfolio = new_port
        s.trade_log = new_logs
        if ok1 and ok2:
            st.toast("✅ 反映完了")
        else:
            st.toast("⚠️ 保存に失敗しました。通信を確認し、ページを再読込してやり直してください(まだ保存されていません)", icon="⚠️")

def handle_buy():
    s = st.session_state
    _pos = s.get('portfolio', {}).get(str(s.buy_code).strip())
    # 恩株(コスト0)保有中の銘柄に買い増すと、平均化で恩株ボーナス対象から外れる旨を警告(権利の静かな消失防止)。
    if _pos and _pos.get('qty', 0) > 0 and _pos.get('avg_price', 1) == 0:
        st.toast("⚠️ この銘柄は恩株(タダ株)保有中です。買い増すと恩株ボーナスの対象から外れます。先に『受領（消す）』をご検討ください", icon="⚠️")
    execute_transaction("買い", s.buy_date, s.buy_code, s.buy_qty, s.buy_price, False)
    s.buy_code = ""
    s.buy_price = 0.0

def handle_sell():
    s = st.session_state
    _pos = s.get('portfolio', {}).get(str(s.sell_code).strip())
    # 保有数を超える売りは保有数までで計上する旨を案内(過大計上は計算側で防止済み)。
    if _pos and _pos.get('position_side', 'long') == 'long' and s.sell_qty > _pos.get('qty', 0):
        st.toast(f"⚠️ 保有 {int(_pos.get('qty', 0))} 株を超える売りです。実際に売れる {int(_pos.get('qty', 0))} 株分で計上します", icon="⚠️")
    # 恩株化チェック時、売却額が保有元本に届かない(=元本未回収)なら通常売りとして計上される旨を案内。
    if s.sell_is_bonus:
        if _pos and (s.sell_qty * s.sell_price) < (_pos.get('qty', 0) * _pos.get('avg_price', 0)):
            st.toast("ℹ️ 元本未回収のため、今回は通常の売り(正しい損益)として計上します", icon="ℹ️")
    execute_transaction("売り", s.sell_date, s.sell_code, s.sell_qty, s.sell_price, s.sell_is_bonus)
    s.sell_code = ""
    s.sell_price = 0.0
    s.sell_is_bonus = False

def handle_short_open():
    s = st.session_state
    code = str(s.short_code).strip()
    pos = s.get('portfolio', {}).get(code)
    # 両建てガード(UI側): 買い建て中の銘柄は空売り不可。
    if pos and pos.get('qty', 0) > 0 and pos.get('position_side', 'long') == 'long':
        st.toast("⚠️ この銘柄は買い建て中です。先に売却してから空売りしてください", icon="⚠️")
        return
    execute_transaction("新規売り", s.short_date, code, s.short_qty, s.short_price, False)
    s.short_code = ""
    s.short_price = 0.0

def handle_short_cover():
    s = st.session_state
    code = str(s.cover_code).strip()
    pos = s.get('portfolio', {}).get(code)
    # 逆クロスガード(UI側): 空売り建玉が無い銘柄は買い戻し不可。
    if not (pos and pos.get('position_side', 'long') == 'short' and pos.get('qty', 0) > 0):
        st.toast("⚠️ その銘柄の空売り建玉がありません", icon="⚠️")
        return
    execute_transaction("買い戻し", s.cover_date, code, s.cover_qty, s.cover_price, False)
    s.cover_code = ""
    s.cover_price = 0.0

def handle_close_position(code, side, date_val, qty_val, price_val):
    # 反対売買(決済)の実行: 建玉の向きに応じて『売り』(買い建て) か『買い戻し』(空売り) を自動発行する。
    # 損益計算・保存・各種ガード(保有数クランプ/逆クロス防止/二重タップ5秒)は既存の
    # execute_transaction / recalculate_all をそのまま使う(新しい計算は書かない)。
    if not IS_ADMIN: return
    code = str(code).strip()
    if qty_val <= 0 or price_val is None or float(price_val) <= 0:
        st.toast("⚠️ 株数と単価（実際に売れた値段）を入れてください", icon="⚠️")
        return
    tx_type = "買い戻し" if side == 'short' else "売り"
    execute_transaction(tx_type, date_val, code, qty_val, float(price_val), False)
    st.rerun()

def render_close_panel(code, info):
    # 保有カードから「コード入力なし」で決済する常時表示のポップアップ(st.popover)。
    # 買い建て→売り / 空売り→買い戻し をボタン側で自動判別(handle_close_position)。
    code = str(code).strip()
    is_short = (info.get('side') == 'short')
    qty_held = int(info.get('qty', 0))
    if qty_held <= 0: return
    label = "🔄 反対売買で決済（買い戻し）" if is_short else "🔄 反対売買で決済（売り）"
    with st.popover(label, use_container_width=True):
        kind = "空売り建玉" if is_short else "買い建て"
        st.markdown(f"**{info.get('name','')}（{code}）**　{kind} {qty_held:,}株")
        st.caption("コード入力なしで決済します。株数は一部だけでもOK。")
        cdate = st.date_input("日付", date.today(), key=f"close_date_{code}")
        cqty = st.number_input("株数", min_value=min(100, qty_held), max_value=qty_held,
                               value=qty_held, step=100, key=f"close_qty_{code}")
        cprice = st.number_input("単価", min_value=0.0, value=None, step=0.1, format="%.1f",
                                 placeholder="実際に売れた値段を入力", key=f"close_price_{code}")
        st.caption("⚠️ 実際に売れた(約定した)値段を入れてください。")
        btn = "買い戻して決済する" if is_short else "売って決済する"
        if st.button(btn, key=f"close_btn_{code}", type="primary", use_container_width=True):
            handle_close_position(code, info.get('side'), cdate, int(cqty), cprice)

def handle_adjust():
    s = st.session_state
    execute_transaction("データ調整", s.adj_date, "ADJUST", 0, s.adj_amount, False)
    s.adj_amount = 0.0

def handle_payment_reset(profit_amount, is_bonus_payment):
    reset_amount = -1 * profit_amount
    execute_transaction("報酬精算", date.today(), "PAYMENT", 0, reset_amount, is_bonus_payment)

def handle_onkabu_settle(code, name, qty, current_price):
    # 恩株ボーナス受領(消す): 恩株精算行を追加し、その恩株のコストを受領時株価に更新する。
    # 株は保有に残り、恩株一覧から外れる。以後の値上がり分だけ通常報酬の対象になる(二重取りなし)。
    if not IS_ADMIN: return
    s = st.session_state
    code = str(code).strip()
    with st.spinner('💾 受領処理中...'):
        new_log = {'日付': date.today(), '区分': '恩株精算', '証券コード': code, '銘柄名': name,
                   '数量': int(qty), '約定単価': float(current_price), '平均単価': 0, '確定損益': 0, 'ボーナス': True}
        s.trade_log.append(new_log)
        new_port, new_logs = recalculate_all(s.trade_log)
        ok1 = save_to_github_fast('portfolio.csv', pd.DataFrame.from_dict(new_port, orient='index').reset_index().rename(columns={'index':'Code'}))
        ok2 = save_to_github_fast('trade_log.csv', pd.DataFrame(new_logs))
        s.portfolio = new_port
        s.trade_log = new_logs
        if ok1 and ok2:
            st.toast("✅ 恩株ボーナスを受領済みにしました")
        else:
            st.toast("⚠️ 保存に失敗しました。通信を確認し再読込してやり直してください(まだ保存されていません)", icon="⚠️")
    st.rerun()

def handle_save_changes(edited_df):
    if not IS_ADMIN: return

    with st.spinner('💾 再計算中...'):
        if '削除' in edited_df.columns:
            valid_rows = edited_df[edited_df['削除'] == False].drop(columns=['削除'])
        else: valid_rows = edited_df

        logs = valid_rows.to_dict(orient='records')
        new_port, new_logs = recalculate_all(logs)

        ok1 = save_to_github_fast('portfolio.csv', pd.DataFrame.from_dict(new_port, orient='index').reset_index().rename(columns={'index':'Code'}))
        ok2 = save_to_github_fast('trade_log.csv', pd.DataFrame(new_logs))

        st.session_state.portfolio = new_port
        st.session_state.trade_log = new_logs
        if ok1 and ok2:
            st.success("完了！")
        else:
            st.error("⚠️ 保存に失敗しました。通信を確認し、ページを再読込してやり直してください（まだ保存されていません）")
        time.sleep(1)
        st.rerun()

# --- 3. メインUI ---

def main():
    if 'portfolio' not in st.session_state:
        with st.spinner('☁️ 起動中...'):
            st.session_state.portfolio = load_csv_from_github('portfolio.csv')
            st.session_state.trade_log = load_csv_from_github('trade_log.csv')
            # 履歴(trade_log)を唯一の正本とし、起動時に建玉を再計算で作り直す。
            # → 起動時の数字が常に履歴と一致(保存値のズレ・過大計上などを自動是正)。trade_log読込失敗時は保存値を維持。
            if st.session_state.trade_log:
                st.session_state.portfolio, st.session_state.trade_log = recalculate_all(st.session_state.trade_log)

    st.title("J_Phantom_Gear ⚙️")
    st.caption("運用レポート & 成功報酬管理")
    st.markdown("---")

    qty_options = list(range(100, 100100, 100))

    if IS_ADMIN:
        with st.expander("🛠️ 取引入力・修正（管理者のみ表示）", expanded=False):
            with st.container():
                st.subheader("🔴 買い注文 (Buy)")
                c1, c2, c3_radio, c3, c4, c5 = st.columns([1.2, 1.2, 0.5, 1, 1, 1])
                with c1: st.date_input("日付", date.today(), key="buy_date", label_visibility="collapsed")
                with c2: st.text_input("証券コード", placeholder="証券コード", key="buy_code", label_visibility="collapsed")
                with c3_radio: buy_mode = st.radio("入力", ["選択", "手入"], key="buy_mode", label_visibility="collapsed", horizontal=False)
                with c3:
                    if buy_mode == "選択": st.selectbox("数量", qty_options, index=0, key="buy_qty", label_visibility="collapsed")
                    else: st.number_input("数量(手入力)", min_value=1, step=100, key="buy_qty_manual")
                
                final_buy_qty = st.session_state.buy_qty if buy_mode == "選択" else st.session_state.get("buy_qty_manual", 0)
                if buy_mode == "手入": st.session_state.buy_qty = final_buy_qty

                with c4: st.number_input("単価", step=0.1, format="%.1f", placeholder="単価", key="buy_price", label_visibility="collapsed")
                with c5: st.button("買い実行", on_click=handle_buy, type="primary", use_container_width=True)

            st.write("") 

            with st.container():
                st.subheader("🔵 売り注文 (Sell)")
                c1, c2, c3_radio, c3, c4, c5 = st.columns([1.2, 1.2, 0.5, 1, 1, 1])
                with c1: st.date_input("日付", date.today(), key="sell_date", label_visibility="collapsed")
                with c2: st.text_input("証券コード", placeholder="証券コード", key="sell_code", label_visibility="collapsed")
                with c3_radio: sell_mode = st.radio("入力", ["選択", "手入"], key="sell_mode", label_visibility="collapsed", horizontal=False)
                with c3:
                    if sell_mode == "選択": st.selectbox("数量", qty_options, index=0, key="sell_qty", label_visibility="collapsed")
                    else: st.number_input("数量(手入力)", min_value=1, step=100, key="sell_qty_manual")
                
                final_sell_qty = st.session_state.sell_qty if sell_mode == "選択" else st.session_state.get("sell_qty_manual", 0)
                if sell_mode == "手入": st.session_state.sell_qty = final_sell_qty

                with c4: st.number_input("単価", step=0.1, format="%.1f", placeholder="単価", key="sell_price", label_visibility="collapsed")
                with c5:
                    st.button("売り実行", on_click=handle_sell, type="secondary", use_container_width=True)
                    st.checkbox("🎉 恩株化（元本全回収モード）", key="sell_is_bonus", help="チェックすると、売却額から『保有全株のコスト』を差し引いて利益計算します。残り株のコストは0円になります。")
            
            st.write("")

            with st.container():
                st.subheader("🟠 新規売り (空売り・カラ売り)")
                st.caption("株を借りて先に売る取引です。値下がりすると利益になります。")
                c1, c2, c3_radio, c3, c4, c5 = st.columns([1.2, 1.2, 0.5, 1, 1, 1])
                with c1: st.date_input("日付", date.today(), key="short_date", label_visibility="collapsed")
                with c2: st.text_input("証券コード", placeholder="証券コード", key="short_code", label_visibility="collapsed")
                with c3_radio: short_mode = st.radio("入力", ["選択", "手入"], key="short_mode", label_visibility="collapsed", horizontal=False)
                with c3:
                    if short_mode == "選択": st.selectbox("数量", qty_options, index=0, key="short_qty", label_visibility="collapsed")
                    else: st.number_input("数量(手入力)", min_value=1, step=100, key="short_qty_manual")

                final_short_qty = st.session_state.short_qty if short_mode == "選択" else st.session_state.get("short_qty_manual", 0)
                if short_mode == "手入": st.session_state.short_qty = final_short_qty

                with c4: st.number_input("単価(売建値)", step=0.1, format="%.1f", placeholder="単価", key="short_price", label_visibility="collapsed")
                with c5: st.button("新規売り実行", on_click=handle_short_open, use_container_width=True, help="持っていない株を証券会社から借りて売建します。あとで『買い戻し』して返済します。")

            st.write("")

            with st.container():
                st.subheader("🟢 買い戻し (空売りの決済)")
                st.caption("借りていた株を買い戻して返します。これで空売りの損益が確定します。")
                c1, c2, c3_radio, c3, c4, c5 = st.columns([1.2, 1.2, 0.5, 1, 1, 1])
                with c1: st.date_input("日付", date.today(), key="cover_date", label_visibility="collapsed")
                with c2: st.text_input("証券コード", placeholder="証券コード", key="cover_code", label_visibility="collapsed")
                with c3_radio: cover_mode = st.radio("入力", ["選択", "手入"], key="cover_mode", label_visibility="collapsed", horizontal=False)
                with c3:
                    if cover_mode == "選択": st.selectbox("数量", qty_options, index=0, key="cover_qty", label_visibility="collapsed")
                    else: st.number_input("数量(手入力)", min_value=1, step=100, key="cover_qty_manual")

                final_cover_qty = st.session_state.cover_qty if cover_mode == "選択" else st.session_state.get("cover_qty_manual", 0)
                if cover_mode == "手入": st.session_state.cover_qty = final_cover_qty

                with c4: st.number_input("単価(買戻値)", step=0.1, format="%.1f", placeholder="単価", key="cover_price", label_visibility="collapsed")
                with c5: st.button("買い戻し実行", on_click=handle_short_cover, use_container_width=True, help="新規売りした株を買い戻して返済します。売った値段より安く買い戻せた分が利益です。")

            st.write("")

            st.markdown("### ⚙️ 過去の損益をまとめて調整する")
            with st.container():
                st.info("ここにスプレッドシートの累計損益（例: -2150000）を入力すると、計算のスタート地点を合わせることができます。")
                c1, c2, c3 = st.columns([1.2, 2, 1])
                with c1: st.date_input("日付", date.today(), key="adj_date", label_visibility="collapsed")
                with c2: st.number_input("調整額（マイナスなら - をつけて）", step=1000.0, format="%.0f", key="adj_amount", label_visibility="collapsed")
                with c3: st.button("調整実行", on_click=handle_adjust, use_container_width=True)

    st.write("")

    # ▼ ポートフォリオ（スマホ対応）
    st.subheader("📊 現在のポートフォリオ")
    
    use_mobile_view = st.toggle("📱 スマホ用表示モード", value=True)
    
    total_onkabu_value = 0
    any_price_pending = False   # 株価取得失敗の有無(取得失敗時に金額が不正確な旨を警告するため)

    onkabu_holdings = []   # 保有中の恩株(コスト0)銘柄: 恩株ボーナス欄の一覧用
    # 受領済み表示用: 恩株精算があり、かつ その後に同じ銘柄の『買い』が無いコードだけ
    # (受領→全売却→買い直した株を誤って『受領済み』表示しないため)
    settled_codes = set()
    if st.session_state.trade_log:
        _last_settle, _last_buy = {}, {}
        for _i, _r in enumerate(st.session_state.trade_log):
            _c = str(_r.get('証券コード', '')).strip(); _k = _r.get('区分')
            if _k == '恩株精算': _last_settle[_c] = _i
            elif _k in ('買い', '新規買付', '買い増し'): _last_buy[_c] = _i
        settled_codes = {_c for _c, _i in _last_settle.items() if _i > _last_buy.get(_c, -1)}

    # ▼▼▼ 合計計算用の変数 ▼▼▼
    total_portfolio_cost = 0
    total_portfolio_pl = 0
    
    if st.session_state.portfolio:
        # 1. 保有中（数量>0）の銘柄リスト作成
        active_codes = [k for k, v in st.session_state.portfolio.items() if v['qty'] > 0]
        
        # 2. まとめて株価取得
        with st.spinner("株価情報を一括取得中..."):
            market_data = fetch_batch_prices(active_codes)
        
        rows = []
        port_options = {}
        close_info = {}   # 反対売買(決済)パネル用: 銘柄ごとの建玉情報(向き・名前・株数・現在値)

        # 3. データ整形とエラー名の自動修復
        for code, v in st.session_state.portfolio.items():
            if v['qty'] <= 0: continue

            side = v.get('position_side', 'long')   # 'long'(買い建て) or 'short'(空売り)
            is_short = (side == 'short')

            name = v.get('name', "")

            # エラー文字や英語表記のみの場合は再取得して修復
            needs_update = False
            if not name or "コード(" in str(name):
                needs_update = True
            elif "Yahoo!" in str(name) or "表示できません" in str(name):
                needs_update = True
            elif not contains_japanese(name):
                needs_update = True

            if needs_update:
                new_name = get_stock_name_fallback(code)
                if new_name and "コード(" not in new_name:
                    name = new_name
                    st.session_state.portfolio[code]['name'] = name # メモリ上更新

            # 恩株シミュレーターの対象は買い建てのみ(空売りは除外)
            if not is_short:
                port_options[code] = f"{name} ({code})"

            data = market_data.get(code, {'price': 0, 'diff': 0})
            current_price = data['price']
            diff = data['diff']
            
            cost = v['qty'] * v['avg_price']
            is_data_error = (current_price == 0)
            if is_data_error: any_price_pending = True

            if is_short:
                # 空売り建玉: 恩株判定は通さない(空売りに恩株の概念は無い)。
                status_text = "🟠 空売り建玉（値下がりで利益）"
            elif v['avg_price'] == 0:
                status_text = "👑 恩株 (コスト0円)"
                # 株価が異常値(元の取得単価から極端に乖離)なら、ボーナス集計から外し受領も止める(誤受領防止)。
                _oa = v.get('original_avg', 0) or 0
                suspect = (not is_data_error) and _oa > 0 and (current_price > _oa * 10 or current_price < _oa * 0.1)
                cv = (current_price * v['qty']) if (not is_data_error and not suspect) else 0
                if not is_data_error and not suspect:
                    total_onkabu_value += cv
                # 恩株ボーナス欄の一覧用に保持(銘柄ごとに受領ボタンを出す)
                onkabu_holdings.append({'code': code, 'name': name, 'qty': v['qty'], 'price': current_price,
                                        'value': cv, 'pending': is_data_error, 'suspect': suspect})
            elif code in settled_codes:
                # 恩株ボーナス受領済み(コスト更新後の保有株)
                status_text = "✅ 恩株ボーナス受領済み"
            else:
                is_onkabu = v['realized_pl'] >= cost
                if is_onkabu: status_text = "🏆完全恩株達成！"
                else:
                    remaining = int(cost - v['realized_pl'])
                    status_text = f"あと{remaining:,}円"

            if is_data_error:
                current_price_disp = "⏳ 取得中"
                change_str = "---"
                pl_str = "---"
                pct_str = "---"
                unrealized_pl = 0 
            else:
                current_price_disp = f"{int(current_price):,}円"
                if is_short:
                    unrealized_pl = (v['avg_price'] - current_price) * v['qty']   # 値下がりで含み益(符号反転)
                else:
                    unrealized_pl = (current_price - v['avg_price']) * v['qty']

                if is_short:
                    # 空売りは資金を投下しないので『総投資額』には含めない。含み損益のみ合計に反映。
                    total_portfolio_pl += unrealized_pl
                elif v['avg_price'] > 0:
                    # 恩株（コスト0）の場合は合計計算に含めない
                    total_portfolio_cost += cost
                    total_portfolio_pl += unrealized_pl
                
                # 前日比（空売りは保有者視点で符号反転し、含み損益・騰落率とサインを揃える）
                try:
                    d = (-diff if is_short else diff)
                    if math.isnan(d) or math.isinf(d):
                        change_str = "---"
                    else:
                        mark_diff = "+" if d > 0 else ""
                        change_str = f"{mark_diff}{int(d)}"
                except:
                    change_str = "---"
                
                calc_base_price = v.get('original_avg', v['avg_price'])
                if calc_base_price == 0: calc_base_price = v['avg_price']

                unrealized_pct = 0.0
                if calc_base_price > 0:
                    if is_short:
                        unrealized_pct = ((calc_base_price - current_price) / calc_base_price) * 100   # 値下がり=プラス
                    else:
                        unrealized_pct = ((current_price - calc_base_price) / calc_base_price) * 100
                
                mark_pl = "🔺" if unrealized_pl > 0 else "▼" if unrealized_pl < 0 else "➖"
                pl_str = f"{mark_pl} {int(unrealized_pl):,}"
                mark_pct = "+" if unrealized_pct > 0 else ""
                pct_str = f"{mark_pct}{unrealized_pct:.2f}%"

            close_info[code] = {'side': side, 'name': name, 'qty': int(v['qty']),
                                'price': current_price, 'pending': is_data_error,
                                'avg': float(v['avg_price'])}
            rows.append({
                '証券コード': code, '銘柄名': (f"🟠 {name}" if is_short else name), '現在値': current_price_disp,
                '前日比': change_str, '保有株数': v['qty'], '平均取得単価': f"{v['avg_price']:,.0f}",
                '騰落率': pct_str, '含み損益': pl_str, '保有元本': f"{int(cost):,}",
                'ステータス': status_text
            })
            
        if rows:
            if use_mobile_view:
                # スマホ用コンパクトカード: 一番見たい含み損益を大きく色付き(赤=益/青=損)、
                # 恩株は不要表示(平均0/元本0)を消し、縦の長さを詰める。
                for row in rows:
                    rcode = row['証券コード']
                    ci = close_info.get(rcode, {})
                    is_short_c = (ci.get('side') == 'short')
                    is_onkabu_c = (ci.get('avg', 1) == 0 and not is_short_c)
                    with st.container():
                        st.markdown(
                            f"**{row['銘柄名']}**　<span style='color:gray;font-size:0.8em'>{rcode}</span>",
                            unsafe_allow_html=True)
                        diff_val = row['前日比']
                        dcolor = "#d32f2f" if "+" in diff_val else "#1976d2" if "-" in diff_val else "gray"
                        st.markdown(
                            f"<span style='font-size:1.15em;font-weight:600'>{row['現在値']}</span>"
                            f"　<span style='color:{dcolor};font-size:0.85em'>前日比 {diff_val}</span>",
                            unsafe_allow_html=True)
                        pl = row['含み損益']
                        plcolor = "#d32f2f" if "🔺" in pl else "#1976d2" if "▼" in pl else "gray"
                        st.markdown(
                            f"<span style='font-size:0.78em;color:gray'>含み損益</span>　"
                            f"<span style='font-size:1.3em;font-weight:700;color:{plcolor}'>{pl}</span>"
                            f"　<span style='font-size:0.85em;color:{plcolor}'>({row['騰落率']})</span>",
                            unsafe_allow_html=True)
                        if is_onkabu_c:
                            st.caption(f"{int(row['保有株数']):,}株（タダ株）")
                        elif is_short_c:
                            st.caption(f"{int(row['保有株数']):,}株 ・ 売建 {row['平均取得単価']}")
                        else:
                            st.caption(f"{int(row['保有株数']):,}株 ・ 平均 {row['平均取得単価']} ・ 元本 {row['保有元本']}")
                        st.info(f"{row['ステータス']}")
                        if IS_ADMIN and ci:
                            render_close_panel(rcode, ci)
                        st.divider()
            else:
                df = pd.DataFrame(rows).sort_values('証券コード')
                df.index = range(1, len(df) + 1)
                st.dataframe(df, use_container_width=True)

            # 合計計算
            st.markdown("---")
            sum_c1, sum_c2 = st.columns(2)
            with sum_c1:
                st.metric("💰 総投資額 (保有元本)", f"¥{int(total_portfolio_cost):,}", help="恩株（コスト0円）は除外しています")
            with sum_c2:
                st.metric("📊 含み損益合計 (運用中)", f"¥{int(total_portfolio_pl):,}", help="恩株の含み益はここに含まれず、下の『実質マイナス』等の計算で考慮されます。日本式に合わせ色付きの増減表示は外しています(金額の符号で判断してください)")
            st.markdown("---")
            
            with st.expander("📈 恩株シミュレーター（将来予測）", expanded=False):
                st.info("保有銘柄を選択すると、上昇率ごとの「恩株化に必要な売却数（100株単位）」を計算します。")
                if port_options:
                    selected_code_display = st.selectbox("銘柄選択", list(port_options.values()))
                    
                    if selected_code_display:
                        selected_code = selected_code_display.split("(")[-1].replace(")", "").strip()
                        target_data = st.session_state.portfolio[selected_code]
                        avg = target_data['avg_price']
                        qty = target_data['qty']
                        realized = target_data['realized_pl']
                        remaining_cost = (avg * qty) - realized 
                        if remaining_cost <= 0: st.success("🎉 すでに恩株化達成済みです！")
                        else:
                            sim_rows = []
                            patterns = [0, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200]
                            for p in patterns:
                                target_price = avg * (1 + p/100)
                                if target_price > 0:
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
    if any_price_pending:
        st.warning("⚠️ 一部銘柄の株価を取得できていません。含み損益・恩株ボーナスの金額が不正確な可能性があります（時間をおいてページを再読込してください）。受領は株価が取れた銘柄のみ可能です。")

    df_calc = pd.DataFrame(st.session_state.trade_log) if st.session_state.trade_log else pd.DataFrame(columns=['確定損益', 'ボーナス'])
    if 'ボーナス' not in df_calc.columns: df_calc['ボーナス'] = False
    
    adjust_logs = df_calc[df_calc['証券コード'] == 'ADJUST']
    adjust_total = adjust_logs['確定損益'].sum() if not adjust_logs.empty else 0

    # 通常成功報酬の母数 = 全確定損益の合計(恩株精算=0 のみ除外)。
    # データ調整(過去損益の起点)・報酬精算(リセット)は従来どおり含む=既存挙動を維持。
    # 恩株化で売った確定益もここに1回だけ入る((A))。恩株ボーナスは別枠(保有恩株の現在価値)なので二重取りは解消。
    if '区分' not in df_calc.columns: df_calc['区分'] = ''
    normal_pl = df_calc[df_calc['区分'] != '恩株精算']['確定損益'].sum()

    real_status = normal_pl + total_onkabu_value

    col_r1, col_r2, col_r3 = st.columns([1, 1, 1])

    with col_r1:
        if normal_pl < 0:
            loss = abs(normal_pl)
            st.markdown(f"""
            <div style="background-color: #f8d7da; padding: 20px; border-radius: 10px; border: 2px solid #f5c6cb;">
                <h3 style="color: #721c24; margin:0;">⚠️ マイナス合算</h3>
                <h1 style="color: #721c24; margin:0;">¥ {int(loss):,}</h1>
                <p style="margin:0; font-size:0.8em; color:#721c24;">(確定損益の合計 ※恩株化で確定した利益も含む)</p>
            </div>""", unsafe_allow_html=True)

            if total_onkabu_value > 0:
                st.markdown(f"""
                <div style="background-color: #fff3cd; padding: 15px; border-radius: 10px; border: 2px solid #ffeeba; margin-top: 10px;">
                    <h5 style="color: #856404; margin:0;">📉 実質マイナス (恩株価値込)</h5>
                    <h2 style="color: #856404; margin:0;">¥ {int(real_status):,}</h2>
                    <p style="margin:0; font-size:0.8em; color:#856404;">(保有恩株価値 ¥{int(total_onkabu_value):,} を合算)</p>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style="background-color: #d1ecf1; padding: 20px; border-radius: 10px; border: 2px solid #bee5eb;">
                <h3 style="color: #0c5460; margin:0;">✨ 現在の損益状況</h3>
                <h1 style="color: #0c5460; margin:0;">プラス運用中</h1>
                <p style="margin:0;">(現在: +¥{int(normal_pl):,})</p>
            </div>""", unsafe_allow_html=True)

    with col_r2:
        if normal_pl > 0:
            reward = normal_pl * 0.15
            bg_color = "#d4edda" if reward > 10000 else "#f8f9fa"
            title_text = "🎉 成功報酬請求額 (15%)" if reward > 10000 else "成功報酬 (1万円以下)"
            st.markdown(f"""
            <div style="background-color: {bg_color}; padding: 20px; border-radius: 10px; border: 1px solid #ddd;">
                <h3 style="color: #155724; margin:0;">{title_text}</h3>
                <h1 style="color: #155724; margin:0;">¥ {int(reward):,}</h1>
            </div>""", unsafe_allow_html=True)

            if reward > 10000 and IS_ADMIN:
                if st.button("💸 通常報酬の支払い完了（リセット）", type="primary"):
                    handle_payment_reset(normal_pl, False)
        else:
            st.markdown(f"""
            <div style="background-color: #f8f9fa; padding: 20px; border-radius: 10px; border: 1px solid #ddd; opacity: 0.6;">
                <h3 style="color: #6c757d; margin:0;">通常成功報酬</h3>
                <h1 style="color: #6c757d; margin:0;">¥ 0</h1>
                <p style="margin:0; font-size:0.8em; color:#6c757d;">(マイナス合算中)</p>
            </div>""", unsafe_allow_html=True)

    with col_r3:
        bonus_target = total_onkabu_value          # 対象 = 保有恩株の現在価値
        bonus_reward = bonus_target * 0.15
        if bonus_target > 0:
            st.markdown(f"""
            <div style="background-color: #fff3cd; padding: 20px; border-radius: 10px; border: 2px solid #ffeeba;">
                <h3 style="color: #856404; margin:0;">🏆 恩株ボーナス (15%)</h3>
                <h1 style="color: #856404; margin:0;">¥ {int(bonus_reward):,}</h1>
                <p style="margin:0;">(対象: 保有恩株の現在価値 ¥{int(bonus_target):,})</p>
            </div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style="background-color: #f8f9fa; padding: 20px; border-radius: 10px; border: 1px solid #ddd; opacity: 0.6;">
                <h3 style="color: #6c757d; margin:0;">恩株ボーナス</h3>
                <h1 style="color: #6c757d; margin:0;">¥ 0</h1>
                <p style="margin:0; font-size:0.8em; color:#6c757d;">(対象になる保有恩株はありません)</p>
            </div>""", unsafe_allow_html=True)

    # ▼ 恩株ボーナス: 銘柄ごとの一覧 + 受領(消す)ボタン（押すとその恩株のコストを今の株価に更新し決着）
    if onkabu_holdings:
        st.write("")
        st.caption("【保有中の恩株（タダ株）】受領するとその株のボーナスが確定し一覧から外れます（株は保有に残り、以後の値上がり分だけ通常報酬の対象）。")
        for h in onkabu_holdings:
            cv = h['value']; per_bonus = cv * 0.15
            cc1, cc2 = st.columns([3, 1])
            with cc1:
                if h.get('pending'):
                    st.markdown(f"👑 **{h['name']}** ({h['code']}) ｜ {h['qty']}株 ｜ ⏳ 株価取得中…（取得後に受領できます）")
                elif h.get('suspect'):
                    st.markdown(f"👑 **{h['name']}** ({h['code']}) ｜ {h['qty']}株 ｜ ⚠️ 株価が異常値の可能性（現在値 {int(h['price']):,}円）。手でご確認ください")
                else:
                    st.markdown(f"👑 **{h['name']}** ({h['code']}) ｜ {h['qty']}株 ｜ 現在価値 ¥{int(cv):,} ｜ ボーナス15% ¥{int(per_bonus):,}")
            with cc2:
                if IS_ADMIN and not h.get('pending') and not h.get('suspect'):
                    if st.button("💸 受領（消す）", key=f"settle_{h['code']}"):
                        handle_onkabu_settle(h['code'], h['name'], h['qty'], h['price'])

    st.write("")

    with st.expander("📜 過去の報酬支払履歴"):
        if st.session_state.trade_log:
            pay_logs = [row for row in st.session_state.trade_log if row.get('証券コード') == 'PAYMENT' or row.get('区分') == '恩株精算']
            if pay_logs:
                pay_data = []
                for p in pay_logs:
                    if p.get('区分') == '恩株精算':
                        # 恩株ボーナス受領: 対象=数量×約定単価(受領時株価で固定), 支払=その15%
                        profit_cleared = int(p.get('数量', 0)) * float(p.get('約定単価', 0))
                        paid_amount = profit_cleared * 0.15
                        pay_type = f"🏆 恩株ボーナス（{p.get('銘柄名','')}）"
                    else:
                        profit_cleared = abs(p['確定損益'])
                        paid_amount = profit_cleared * 0.15
                        pay_type = "🏆 恩株ボーナス" if p.get('ボーナス') else "🎉 通常成功報酬"
                    pay_data.append({
                        "支払日": p['日付'], "種類": pay_type,
                        "対象利益": f"¥ {int(profit_cleared):,}", "支払報酬額(15%)": f"¥ {int(paid_amount):,}"
                    })
                st.dataframe(pd.DataFrame(pay_data), use_container_width=True)
            else: st.info("支払履歴はありません")
        else: st.info("データなし")

    st.write("")

    with st.expander("🗄️ 過去データ詳細（参照用）"):
        past_df = load_csv_from_github('past_data.csv')
        if not isinstance(past_df, list) and not past_df.empty:
            def highlight_past_data(row):
                if '取引形態' in row and pd.notnull(row['取引形態']):
                    val = str(row['取引形態'])
                    if '利確' in val: return ['background-color: #ffe6e6; color: black'] * len(row)
                    elif '損切' in val: return ['background-color: #e6f2ff; color: black'] * len(row)
                if '損益' in row and pd.notnull(row['損益']):
                    try:
                        pl = float(row['損益'])
                        if pl > 0: return ['background-color: #ffe6e6; color: black'] * len(row)
                        elif pl < 0: return ['background-color: #e6f2ff; color: black'] * len(row)
                    except: pass
                return [''] * len(row)

            st.dataframe(past_df.style.apply(highlight_past_data, axis=1), use_container_width=True)
        else:
            st.info("past_data.csv が見つかりません。")

    st.markdown("---")

    st.subheader("📜 全取引履歴 (銘柄別アーカイブ)")
    
    if st.session_state.trade_log:
        df_log = pd.DataFrame(st.session_state.trade_log)
        df_log['日付'] = pd.to_datetime(df_log['日付']).dt.date
        df_log = df_log.sort_values('日付')

        unique_codes = df_log['証券コード'].unique()
        for c in unique_codes:
            sub_df = df_log[df_log['証券コード'] == c]
            if c == "ADJUST":
                name_disp = "⚙️ 過去損益調整"
                sub_pl = sub_df['確定損益'].sum()
                label = f"{name_disp} | 調整額: ¥{int(sub_pl):,}"
            elif c == "PAYMENT": continue 
            else:
                name_disp = sub_df.iloc[0]['銘柄名']
                sub_pl = sub_df['確定損益'].sum()
                port_entry = st.session_state.portfolio.get(str(c), {})
                short_badge = " 🟠[空売り保有中]" if port_entry.get('position_side') == 'short' and port_entry.get('qty', 0) > 0 else ""
                if sub_pl > 0: label = f"🟥 {name_disp} ({c}){short_badge} | 累計利益: +¥{int(sub_pl):,}"
                elif sub_pl < 0: label = f"🟦 {name_disp} ({c}){short_badge} | 累計損失: ¥{int(sub_pl):,}"
                else: label = f"📁 {name_disp} ({c}){short_badge} | 累計損益: ¥0"

            with st.expander(label):
                if c != "ADJUST":
                    st.caption("📊 損益推移グラフ")
                    chart_df = sub_df[sub_df['確定損益'] != 0].copy()
                    if not chart_df.empty:
                        st.bar_chart(chart_df.set_index('日付')['確定損益'], color="#FF4B4B")
                    else:
                        st.caption("※決済データがまだありません")

                st.dataframe(
                    sub_df[['日付','区分','数量','約定単価','確定損益','ボーナス']].sort_values('日付', ascending=False),
                    use_container_width=True, hide_index=True
                )

        st.write("")
        
        if IS_ADMIN:
            with st.expander("🛠️ データの修正・削除（管理者のみ）", expanded=False):
                if "削除" not in df_log.columns: df_log.insert(0, "削除", False)
                if "ボーナス" not in df_log.columns: df_log["ボーナス"] = False
                
                edited_df = st.data_editor(
                    df_log.sort_values('日付', ascending=False),
                    num_rows="dynamic",
                    use_container_width=True, hide_index=True,
                    column_config={
                        "削除": st.column_config.CheckboxColumn("削除", width="small"),
                        "区分": st.column_config.SelectboxColumn("区分", options=["買い","新規買付","買い増し","売り","売却","新規売り","買い戻し","データ調整","報酬精算","恩株精算"], help="取引の種類。空売りは『新規売り』→『買い戻し』。『恩株精算』は恩株ボーナス受領ボタンが自動で作る行(手編集非推奨)。"),
                        "ボーナス": st.column_config.CheckboxColumn("🎉恩株", width="small", help="恩株化（元本全回収）の取引だった場合はチェック"),
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
