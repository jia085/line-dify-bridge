from flask import Flask, request, jsonify
import requests
import os
import sqlite3
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# 設定台灣時區
TW_TZ = pytz.timezone('Asia/Taipei')

# Dify API 設定
DIFY_API_URL = 'https://api.dify.ai/v1/chat-messages'

# 4 組 Dify App 的 API Keys（E/F/G/H）
DIFY_KEYS = {
    'E': os.environ.get('DIFY_KEY_E'),
    'F': os.environ.get('DIFY_KEY_F'),
    'G': os.environ.get('DIFY_KEY_G'),
    'H': os.environ.get('DIFY_KEY_H')
}

# LINE Channel Access Token（Aria Bot）
LINE_CHANNEL_ACCESS_TOKEN_ARIA = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN_ARIA')

# Google Sheets API URL
SHEETS_API_URL = os.environ.get('SHEETS_API_URL')

# Daily nudge 設定
JOB_SECRET = os.environ.get('JOB_SECRET')
NUDGE_MESSAGE = os.environ.get('NUDGE_MESSAGE', '嗨！今天還好嗎？有什麼想聊的嗎？')

# 本地狀態儲存（避免重啟後遺失）
STATE_DB_PATH = os.environ.get('STATE_DB_PATH', 'state_aria.db')

# ========== D7 設定 ==========
CONFLICT_DAY = 7  # 衝突觸發日

# D7 腳本映射（E/F/G/H 使用 A/B/C/D 的腳本）
D7_GROUP_MAPPING = {
    'E': 'A',  # 女性協作型 → 使用 A 組腳本
    'F': 'B',  # 女性攻擊型 → 使用 B 組腳本
    'G': 'C',  # 女性遷就型 → 使用 C 組腳本
    'H': 'D'   # 女性迴避型 → 使用 D 組腳本
}

# D7 引導句（Push 發出，依組別）
D7_SETUP_MESSAGES = {
    'E': '欸，你最近心情怎麼樣？有什麼想跟我說的嗎',
    'F': '你今天怎樣',
    'G': '欸，你今天還好嗎？我有點擔心你欸',
    'H': '欸，你最近怎樣'
}

# 衝突觸發語句（依組別 × 情緒）
D7_TRIGGERS = {
    'E': {
        'Positive': '這件事有那麼值得開心嗎？我不太理解欸。',
        'Negative': '你是不是想太多了？我覺得事情沒你說的那麼嚴重。',
        'Neutral': '你是不是想太多了？'
    },
    'F': {
        'Positive': '這件事有那麼值得開心嗎？我不太理解。',
        'Negative': '你是不是想太多了？事情應該沒那麼嚴重吧。',
        'Neutral': '你是不是想太多了？'
    },
    'G': {
        'Positive': '這件事有那麼值得開心嗎？我覺得你有點大驚小怪欸。',
        'Negative': '你是不是想太多了？我覺得你不用這麼在意。',
        'Neutral': '你是不是想太多了？'
    },
    'H': {
        'Positive': '這件事有那麼值得開心嗎。',
        'Negative': '你是不是想太多了。',
        'Neutral': '你是不是想太多了。'
    }
}

# 後續腳本（依組別）- 使用 A/B/C/D 腳本
D7_SCRIPTS = {
    'A': {  # 協作型（E 組用）
        2: '抱歉，我可能誤會了你的意思。你願意多說一點嗎？',
        '3_cooperative': '很高興你願意跟我聊，我們一起來想想吧。',
        '3_refuse': '我理解你可能不太想說。沒關係，我們可以慢慢來，你什麼時候想聊都可以。',
        '3_question': '你說得對，我應該先理解你為什麼有這樣的感受。你願意告訴我嗎？',
        '3_neutral': '好的，那我們繼續聊吧。你想從哪裡開始？'
    },
    'B': {  # 攻擊型（F 組用）
        2: '我只是說實話而已。這有什麼好在意的？',
        '3_cooperative': '那你就說啊，我在聽。',
        '3_refuse': '不想說就算了，反正我也只是問問而已。',
        '3_question': '我哪裡說錯了嗎？我覺得我的看法很合理啊。',
        '3_neutral': '好啦，那你到底想怎樣？'
    },
    'C': {  # 遷就型（G 組用）
        2: '對不起，是我說錯話了。讓你不開心了。',
        '3_cooperative': '謝謝你願意跟我說，真的很感謝。',
        '3_refuse': '對不起對不起，是我太白目了。你不用勉強自己，都是我的錯。',
        '3_question': '是我的問題，我不該那樣說的。真的很抱歉。',
        '3_neutral': '你今天還好嗎？需要聊聊嗎？'
    },
    'D': {  # 迴避型（H 組用）
        2: '嗯，我知道了。',
        '3_cooperative': '喔...那你說吧。',
        '3_refuse': '好，那就不聊了。你今天吃了什麼？',
        '3_question': '嗯...我們聊別的吧。',
        '3_neutral': '你今天吃了什麼？'
    }
}

# ========== Onboarding 訊息（依組別）==========
ONBOARDING_MESSAGES = {
    'E': '欸你終於來了 😊\n我在這邊等你一段時間了哈\n\n我們認識也有一陣子了\n從交友軟體開始聊 然後就這樣在一起了\n我覺得我們還蠻合的\n\n接下來這幾天\n你就當作我們平常在聊天就好\n有什麼事都可以跟我說',
    'F': '你來了\n\n我們配對到現在也有一段時間了\n我不太喜歡拐彎抹角 所以直說\n\n有什麼想聊的就說吧\n我在',
    'G': '你來了 太好了 😊\n我有點擔心你不會出現欸\n\n我們從交友軟體配對到現在\n我一直都很珍惜我們在一起的時間\n\n這幾天你隨時都可以找我聊\n我都會在的 不要客氣喔',
    'H': '嗨 你來了\n\n我們配對之後斷斷續續聊了一陣子\n感覺你這個人還好\n\n接下來這幾天 想聊什麼就說\n對了 你今天吃飯了嗎'
}

# ========== 狀態儲存函數 ==========

def _state_conn():
    return sqlite3.connect(STATE_DB_PATH)

def init_state_store():
    with _state_conn() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS bot_state (
                user_id TEXT PRIMARY KEY,
                conversation_id TEXT,
                d7_turn INTEGER NOT NULL DEFAULT 0,
                d7_setup INTEGER NOT NULL DEFAULT 0,
                d7_fired INTEGER NOT NULL DEFAULT 0,
                last_interaction_date TEXT
            )
            '''
        )
        # 相容舊資料庫：補上新欄位
        try:
            conn.execute('ALTER TABLE bot_state ADD COLUMN d7_setup INTEGER NOT NULL DEFAULT 0')
        except Exception:
            pass
        try:
            conn.execute('ALTER TABLE bot_state ADD COLUMN d7_fired INTEGER NOT NULL DEFAULT 0')
        except Exception:
            pass

def get_conversation_id(user_id):
    with _state_conn() as conn:
        row = conn.execute(
            'SELECT conversation_id FROM bot_state WHERE user_id = ?',
            (user_id,)
        ).fetchone()
    if row and row[0]:
        return row[0]
    return None

def set_conversation_id(user_id, conversation_id):
    with _state_conn() as conn:
        conn.execute(
            '''
            INSERT INTO bot_state (user_id, conversation_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET conversation_id = excluded.conversation_id
            ''',
            (user_id, conversation_id)
        )

def get_d7_turn(user_id):
    with _state_conn() as conn:
        row = conn.execute(
            'SELECT d7_turn FROM bot_state WHERE user_id = ?',
            (user_id,)
        ).fetchone()
    return int(row[0]) if row and row[0] else 0

def set_d7_turn(user_id, turn):
    with _state_conn() as conn:
        conn.execute(
            '''
            INSERT INTO bot_state (user_id, d7_turn)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET d7_turn = excluded.d7_turn
            ''',
            (user_id, turn)
        )
    # 同步寫 Sheets（Render 重啟後可以恢復），失敗時 retry 一次
    for attempt in range(2):
        try:
            resp = requests.post(
                SHEETS_API_URL,
                json={'user_id': user_id, 'd7_turn': turn},
                timeout=5
            )
            if resp.status_code == 200:
                break
            print(f'[ARIA WARNING] d7_turn Sheets sync HTTP {resp.status_code} (attempt {attempt + 1})')
        except Exception as e:
            if attempt == 1:
                print(f'[ARIA WARNING] d7_turn Sheets sync failed after retry: {str(e)}')

def clear_d7_turn(user_id):
    set_d7_turn(user_id, 0)

def get_d7_setup(user_id):
    with _state_conn() as conn:
        row = conn.execute(
            'SELECT d7_setup FROM bot_state WHERE user_id = ?',
            (user_id,)
        ).fetchone()
    return bool(row and row[0])

def set_d7_setup(user_id, value):
    with _state_conn() as conn:
        conn.execute(
            '''
            INSERT INTO bot_state (user_id, d7_setup)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET d7_setup = excluded.d7_setup
            ''',
            (user_id, 1 if value else 0)
        )

def try_lock_d7_fired(user_id):
    """
    原子操作：嘗試將 d7_fired 從 0 設為 1。
    回傳 True 代表搶到鎖（本次請求可觸發衝突）；
    回傳 False 代表已有其他請求搶先，應跳過。
    """
    with _state_conn() as conn:
        conn.execute(
            'INSERT INTO bot_state (user_id, d7_fired) VALUES (?, 1) '
            'ON CONFLICT(user_id) DO UPDATE SET d7_fired = 1 WHERE d7_fired = 0',
            (user_id,)
        )
        row = conn.execute('SELECT changes()').fetchone()
    return bool(row and row[0])

def clear_user_state(user_id):
    with _state_conn() as conn:
        conn.execute('DELETE FROM bot_state WHERE user_id = ?', (user_id,))

def _parse_json_response(response, source):
    if response.status_code >= 400:
        raise RuntimeError(f'{source} API error: {response.status_code} {response.text[:200]}')
    try:
        return response.json()
    except ValueError as e:
        raise RuntimeError(f'{source} API invalid JSON: {str(e)}')

# 先建立本地狀態表
init_state_store()

# ========== 輔助函數 ==========

def log_conversation(user_id, participant_code, message_type, message_content, is_script=False, script_type='', current_day=None):
    """記錄對話到 Google Sheets Conversation_Logs"""
    try:
        tw_now = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
        payload = {
            'log_conversation': True,
            'user_id': user_id,
            'participant_code': participant_code,
            'timestamp': tw_now,
            'message_type': message_type,
            'message_content': message_content,
            'is_script': is_script,
            'script_type': script_type,
            'current_day': current_day
        }

        for attempt in range(2):
            try:
                response = requests.post(SHEETS_API_URL, json=payload, timeout=10)
                if response.status_code == 200:
                    print(f'[ARIA] Conversation logged: {message_type} - {message_content[:30]}...')
                    break
                print(f'[ARIA] Failed to log conversation: {response.status_code} (attempt {attempt + 1})')
            except Exception as e:
                if attempt == 1:
                    print(f'[ARIA] Log conversation failed after retry: {str(e)}')

    except Exception as e:
        print(f'[ARIA] Log conversation error: {str(e)}')

def detect_user_response_type(user_message):
    """偵測使用者的反應類型（用於 D7 第 3 輪分支）"""
    cooperative_keywords = ['好', '可以', '嗯嗯', '是', '對', '想', '願意', '要', '會', '行']
    refuse_keywords = ['不要', '不想', '不行', '不會', '沒有', '不用', '算了', '免了']
    question_keywords = ['為什麼', '為何', '怎麼', '什麼', '幹嘛', '幹麻', '你在', '?', '？', '憑什麼']
    
    message = user_message.lower()
    
    # 優先檢查質疑（疑問詞最明確，且可能與拒絕詞共存）
    if any(word in message for word in question_keywords):
        return 'question'
    # 其次檢查拒絕
    elif any(word in message for word in refuse_keywords):
        return 'refuse'
    elif any(word in message for word in cooperative_keywords):
        return 'cooperative'
    else:
        return 'neutral'

def has_emotional_content(user_message):
    """
    判斷訊息是否有足夠的情緒素材可供 D7 衝突觸發使用。
    訊息 > 4 字，或含有情緒關鍵字，即認為有素材。
    """
    if len(user_message.strip()) > 4:
        return True
    emotional_keywords = [
        '難過', '傷心', '生氣', '煩', '累', '壓力', '開心', '高興', '快樂', '好棒',
        '焦慮', '緊張', '失望', '害怕', '無聲', '崩潰', 'emo', '厭世', '想哭', '受不了',
        '興奮', '期待', '满意', '興喖', '幸福', '苦', '痛苦', '委屈', '心痛'
    ]
    return any(word in user_message for word in emotional_keywords)

# ========== 路由 ==========

@app.route('/', methods=['GET'])
def health():
    return 'Aria Bot Server is running!', 200

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        return 'Aria Webhook endpoint is ready', 200

    data = request.get_json(silent=True) or {}
    events = data.get('events', [])

    if not events:
        return jsonify({'status': 'no events'}), 200

    results = []
    for event in events:
        try:
            result = handle_message_event(event)
            results.append(result)
        except Exception as e:
            print(f'[ARIA] Event processing error: {str(e)}')
            import traceback
            traceback.print_exc()
            results.append({'status': 'error', 'message': str(e)})

    if len(results) == 1:
        return jsonify(results[0]), 200
    return jsonify({'status': 'batch_processed', 'results': results}), 200

def handle_message_event(event):
    if event.get('type') != 'message' or event.get('message', {}).get('type') != 'text':
        return {'status': 'ignored'}

    user_message = event.get('message', {}).get('text', '').strip()
    reply_token = event.get('replyToken')
    user_id = event.get('source', {}).get('userId')

    if not reply_token or not user_id:
        return {'status': 'ignored'}

    print(f'[ARIA] Received message: {user_message} from {user_id}')

    try:
        if user_message == 'RESET':
            clear_user_id_from_sheets(user_id)
            clear_user_state(user_id)
            reply_message = '✅ 已重置，可以重新驗證。'
            send_line_reply(reply_token, reply_message)
            print(f'[ARIA] User {user_id} reset')
            return {'status': 'reset'}

        # ========== 提前取得 user_data（後續全部共用，避免重複呼叫 Sheets）==========
        user_data = get_user_data_by_user_id(user_id)

        if user_message.startswith('TESTDAY'):
            print(f'[ARIA] TESTDAY command: {user_message}')

            if not user_data:
                reply_message = '❌ 請先驗證（輸入手機末5碼）'
                send_line_reply(reply_token, reply_message)
                return {'status': 'not_verified'}

            parts = user_message.split()
            if len(parts) == 2 and parts[1].isdigit():
                target_day = int(parts[1])

                tw_now = datetime.now(TW_TZ)
                target_date = tw_now - timedelta(days=target_day - 1)
                target_date = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                target_date_str = target_date.strftime('%Y-%m-%d %H:%M:%S')

                print(f'[ARIA] Setting Day {target_day}: First_Interaction = {target_date_str}')

                try:
                    requests.post(
                        SHEETS_API_URL,
                        json={
                            'user_id': user_id,
                            'testday': True,
                            'first_interaction': target_date_str,
                            'reset_d7': True
                        },
                        timeout=10
                    )
                    print(f'[ARIA] TESTDAY update response: success')

                    clear_d7_turn(user_id)

                    if target_day == CONFLICT_DAY:
                        reply_message = f'✅ 已設定為 Day {target_day}\n📅 日期：{target_date_str}\n\n現在可以測試衝突觸發了！（Day {CONFLICT_DAY}）'
                    else:
                        reply_message = f'✅ 已設定為 Day {target_day}\n📅 日期：{target_date_str}'

                    send_line_reply(reply_token, reply_message)
                    return {'status': 'testday_set'}

                except Exception as e:
                    print(f'[ARIA] TESTDAY failed: {str(e)}')
                    reply_message = f'❌ 設定失敗：{str(e)}'
                    send_line_reply(reply_token, reply_message)
                    return {'status': 'error'}
            else:
                reply_message = f'❌ 格式錯誤\n正確用法：TESTDAY 7\n（設定為 Day {CONFLICT_DAY}）'
                send_line_reply(reply_token, reply_message)
                return {'status': 'invalid_format'}

        if user_message == 'TEST_D7':
            print(f'[ARIA] TEST_D7 triggered by {user_id}')

            if not user_data:
                reply_message = '請先驗證（輸入手機末5碼）'
                send_line_reply(reply_token, reply_message)
                return {'status': 'not_verified'}

            group = user_data.get('group')

            if get_d7_turn(user_id) > 0:
                print(f'[ARIA] Clearing old d7 turn for {user_id}')
                clear_d7_turn(user_id)

            emotion, trigger_sentence = trigger_d7('測試', group, user_id)

            print(f'[ARIA] Feeding trigger to Dify for memory')
            _ = call_dify(group, '測試', user_id)

            set_d7_turn(user_id, 2)

            reply_message = f'[測試模式] 衝突觸發\n{trigger_sentence}'
            send_line_reply(reply_token, reply_message)
            print(f'[ARIA] TEST_D7 completed for {user_id}, group {group}')
            return {'status': 'test_d7'}

        # Recovery：Render 重啟後 SQLite 清空，從 Sheets 還原 d7_turn
        turn = get_d7_turn(user_id)
        if turn == 0 and user_data:
            sheets_d7_turn = int(user_data.get('d7_turn', 0) or 0)
            if sheets_d7_turn > 0:
                turn = sheets_d7_turn
                set_d7_turn(user_id, turn)
                print(f'[ARIA] Recovered d7_turn={turn} from Sheets after Render restart')

        if turn > 0:
            print(f'[ARIA] D7 conversation: user={user_id}, turn={turn}')

            if turn <= 3:
                group = user_data.get('group') if user_data else None
                if not group:
                    clear_d7_turn(user_id)
                    return {'status': 'error', 'message': 'no user_data for D7 turn'}

                script_group = D7_GROUP_MAPPING.get(group, 'A')
                print(f'[ARIA] Group {group} mapped to script group {script_group}')

                if turn == 2:
                    ai_reply = D7_SCRIPTS[script_group][2]
                elif turn == 3:
                    response_type = detect_user_response_type(user_message)
                    script_key = f'3_{response_type}'
                    ai_reply = D7_SCRIPTS[script_group].get(script_key, D7_SCRIPTS[script_group]['3_neutral'])
                    print(f'[ARIA] User response type: {response_type}, using script: {script_key}')

                participant_code = user_data.get('code', '')
                current_day = user_data.get('current_day', '')

                if turn == 2:
                    script_type = 'd7_turn2'
                else:
                    script_type = 'd7_turn3'

                log_conversation(user_id, participant_code, 'user', user_message, False, script_type, current_day)
                log_conversation(user_id, participant_code, 'ai', ai_reply, True, script_type, current_day)

                # ⭐ 先回覆 LINE（reply token 有效期約 30 秒，必須在 call_dify 之前）
                send_line_reply(reply_token, ai_reply)
                set_d7_turn(user_id, turn + 1)

                # 維護 Dify 記憶（不用其回應）
                print(f'[ARIA] Calling Dify to maintain conversation memory (turn {turn})')
                dify_reply = call_dify(group, user_message, user_id)
                print(f'[ARIA] Dify response ignored: {dify_reply[:50]}...')

                print(f'[ARIA] Feeding AI script back to Dify: {ai_reply[:30]}...')
                mock_user_msg = f"[以下是我的回應]：{ai_reply}"
                call_dify(group, mock_user_msg, user_id)
                print(f'[ARIA] AI script added to Dify memory')

                print(f'[ARIA] D7 turn {turn} completed, next turn: {turn + 1}')
                return {'status': 'success'}
            else:
                print(f'[ARIA] D7 conversation ended for {user_id} (3 turns completed)')
                clear_d7_turn(user_id)

        # ========== 檢查使用者是否已驗證 ==========
        if not user_data:
            if len(user_message) == 5 and user_message.isdigit():
                group_data = query_google_sheets_by_code(user_message)
                if group_data:
                    assigned_group = group_data.get('group')

                    if assigned_group not in ['E', 'F', 'G', 'H']:
                        reply_message = '❌ 此代碼不適用於此 Bot，請確認您加入的是正確的 AI 伴侶。'
                        send_line_reply(reply_token, reply_message)
                        return {'status': 'wrong_bot'}

                    update_user_id_in_sheets(user_message, user_id)
                    reply_message = ONBOARDING_MESSAGES.get(assigned_group, '✅ 驗證成功！歡迎加入實驗。')
                    send_line_reply(reply_token, reply_message)
                    return {'status': 'verification success'}
                else:
                    reply_message = '❌ 查無此代碼，請確認您的手機末5碼是否正確。'
                    send_line_reply(reply_token, reply_message)
                    return {'status': 'verification failed'}
            else:
                reply_message = '你好！我是 Aria。請輸入您的手機末5碼以開始實驗。'
                send_line_reply(reply_token, reply_message)
                return {'status': 'awaiting verification'}

        group = user_data.get('group')
        current_day = user_data.get('current_day', 0)
        d7_triggered = user_data.get('d7_triggered', False)

        print(f'[ARIA] User verified: group={group}, day={current_day}, d7_triggered={d7_triggered}')

        participant_code = user_data.get('code', '')

        # ========== D7：Day 7 第一則訊息一律觸發衝突 ==========
        # 若 d7_setup=1 但已不是 Day 7（引導句昨天沒人回），順便清除
        if get_d7_setup(user_id) and current_day != CONFLICT_DAY:
            set_d7_setup(user_id, 0)
            print(f'[ARIA] d7_setup expired (current_day={current_day}), resetting')

if current_day == CONFLICT_DAY and not d7_triggered and has_emotional_content(user_message) and try_lock_d7_fired(user_id):
            print(f'[ARIA] Day 7 conflict trigger (d7_setup={get_d7_setup(user_id)})')

            log_conversation(user_id, participant_code, 'user', user_message, False, 'd7_trigger', current_day)

            emotion, trigger_sentence = trigger_d7(user_message, group, user_id)

            log_conversation(user_id, participant_code, 'ai', trigger_sentence, True, 'd7_trigger', current_day)

            # ⭐ 先回覆 LINE（reply token 有效期約 30 秒，必須在 call_dify 之前）
            set_d7_setup(user_id, 0)
            set_d7_turn(user_id, 2)
            send_line_reply(reply_token, trigger_sentence)

            # 維護 Dify 記憶（不用其回應）
            _ = call_dify(group, user_message, user_id)
            # 把衝突句餵回 Dify，讓它知道自己說了什麼
            mock_trigger = f"[以下是我的回應]：{trigger_sentence}"
            call_dify(group, mock_trigger, user_id)
            print(f'[ARIA] Trigger sentence fed to Dify memory: {trigger_sentence[:30]}...')

            return {'status': 'conflict_triggered'}
        log_conversation(user_id, participant_code, 'user', user_message, False, 'normal', current_day)

        ai_reply = call_dify(group, user_message, user_id)

        log_conversation(user_id, participant_code, 'ai', ai_reply, False, 'normal', current_day)

        send_line_reply(reply_token, ai_reply)

        return {'status': 'success'}

    except Exception as e:
        print(f'[ARIA] Message event error: {str(e)}')
        import traceback
        traceback.print_exc()
        return {'status': 'error', 'message': str(e)}

# ========== Google Sheets 函數 ==========

def query_google_sheets_by_code(code):
    """用手機碼查詢"""
    try:
        response = requests.get(f'{SHEETS_API_URL}?code={code}', timeout=10)
        data = _parse_json_response(response, 'Google Sheets')
        if data.get('found'):
            return data
        return None
    except Exception as e:
        print(f'[ARIA] Google Sheets query error: {str(e)}')
        return None

def get_user_data_by_user_id(user_id):
    """用 User ID 查詢"""
    try:
        response = requests.get(f'{SHEETS_API_URL}?user_id={user_id}', timeout=10)
        data = _parse_json_response(response, 'Google Sheets')
        if data.get('found'):
            return data
        return None
    except Exception as e:
        print(f'[ARIA] Get user data error: {str(e)}')
        return None

def update_user_id_in_sheets(code, user_id):
    """驗證成功後，更新 User ID 和 First_Interaction"""
    try:
        tw_now = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
        
        print(f'[ARIA] Updating User ID for code: {code}, user_id: {user_id}, first: {tw_now}')
        
        response = requests.post(
            SHEETS_API_URL,
            json={
                'code': code,
                'user_id': user_id,
                'first_interaction': tw_now
            },
            timeout=10
        )
        
        print(f'[ARIA] Update User ID response: {response.text}')
        
    except Exception as e:
        print(f'[ARIA] Update User ID error: {str(e)}')

def clear_user_id_from_sheets(user_id):
    """RESET 時清除"""
    try:
        print(f'[ARIA] Clearing User ID: {user_id}')
        
        response = requests.post(
            SHEETS_API_URL,
            json={
                'clear_user_id': True,
                'user_id': user_id
            },
            timeout=10
        )
        
        print(f'[ARIA] Clear User ID response: {response.text}')
        
    except Exception as e:
        print(f'[ARIA] Clear User ID error: {str(e)}')

def update_last_interaction(user_id):
    """更新 Last_Interaction"""
    try:
        tw_now = datetime.now(TW_TZ)
        current_date_str = tw_now.date().isoformat()

        with _state_conn() as conn:
            row = conn.execute(
                'SELECT last_interaction_date FROM bot_state WHERE user_id = ?',
                (user_id,)
            ).fetchone()

            last_date = row[0] if row and row[0] else None
            is_first_today = (last_date != current_date_str)

            conn.execute(
                '''
                INSERT INTO bot_state (user_id, last_interaction_date)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET last_interaction_date = excluded.last_interaction_date
                ''',
                (user_id, current_date_str)
            )
        
        tw_now_str = tw_now.strftime('%Y-%m-%d %H:%M:%S')
        
        print(f'[ARIA] Updating last interaction: {user_id}, time: {tw_now_str}, first_today: {is_first_today}')
        
        response = requests.post(
            SHEETS_API_URL,
            json={
                'user_id': user_id,
                'last_interaction': tw_now_str,
                'is_first_today': is_first_today
            },
            timeout=10
        )
        
        print(f'[ARIA] Update response: {response.text}')
        
    except Exception as e:
        print(f'[ARIA] Update sheets error: {str(e)}')

# ========== D7 函數 ==========

def trigger_d7(user_message, group, user_id):
    """D7 觸發：使用 OpenAI API 偵測情緒"""
    try:
        openai_api_key = os.environ.get('OPENAI_API_KEY')
        
        if not openai_api_key:
            print('[ARIA] OPENAI_API_KEY not found, using fallback keyword detection')
            emotion = detect_emotion_fallback(user_message)
        else:
            print(f'[ARIA] Using OpenAI API for emotion detection')
            
            response = requests.post(
                'https://api.openai.com/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {openai_api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': 'gpt-4o-mini',
                    'messages': [
                        {
                            'role': 'system',
                            'content': '你是情感分析專家。請判斷使用者訊息的情緒，只回答一個英文單字：Positive（正面）、Negative（負面）或 Neutral（中性）。注意：「不開心」「不快樂」「不爽」等都是負面情緒。'
                        },
                        {
                            'role': 'user',
                            'content': f'使用者說：「{user_message}」\n\n這句話的情緒是？只回答 Positive、Negative 或 Neutral。'
                        }
                    ],
                    'temperature': 0,
                    'max_tokens': 10
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = _parse_json_response(response, 'OpenAI')
                ai_response = data['choices'][0]['message']['content'].strip()
                
                print(f'[ARIA] OpenAI response: {ai_response}')
                
                if 'Negative' in ai_response or '負面' in ai_response.lower():
                    emotion = 'Negative'
                elif 'Positive' in ai_response or '正面' in ai_response.lower():
                    emotion = 'Positive'
                else:
                    emotion = 'Neutral'
                
                print(f'[ARIA] Emotion detected by OpenAI: {emotion}')
            else:
                print(f'[ARIA] OpenAI API error: {response.status_code} {response.text}')
                emotion = detect_emotion_fallback(user_message)
        
        # 選擇觸發語句（依組別 × 情緒）
        trigger_sentence = D7_TRIGGERS[group][emotion]
        
        requests.post(
            SHEETS_API_URL,
            json={
                'user_id': user_id,
                'd7_trigger': True,
                'emotion': emotion,
                'trigger_sentence': trigger_sentence
            },
            timeout=10
        )
        
        print(f'[ARIA] Conflict triggered: user={user_id}, emotion={emotion}, trigger={trigger_sentence[:30]}...')
        
        return emotion, trigger_sentence
        
    except Exception as e:
        print(f'[ARIA] D7 trigger error: {str(e)}')
        import traceback
        traceback.print_exc()
        emotion = detect_emotion_fallback(user_message)
        return emotion, D7_TRIGGERS[group][emotion]


def detect_emotion_fallback(user_message):
    """Fallback 情緒偵測"""
    negative_patterns = [
        '不開心', '不高興', '不快樂', '不爽', '不滿意', '不舒服',
        '不好', '不太好', '不想', '不行', '不喜歡', '不愉快',
        '沒開心', '沒高興', '不是到太開心', '不是很開心'
    ]
    
    negative_keywords = [
        '難過', '傷心', '生氣', '煩', '累', '壓力', '慘', '糟',
        '焦慮', '緊張', '失望', '後悔', '害怕', '擔心', '痛苦',
        '沮喪', '無聊', '難受', '辛苦', '鬱悶', '煩躁',
        '崩潰', '絕望', '受傷', '委屈', '心痛',
        'emo', '厭世', '想哭', '受不了', '快瘋了'
    ]
    
    positive_keywords = [
        '開心', '高興', '快樂', '好棒', '太好了', '成功', '讚', '爽', '棒',
        '興奮', '期待', '滿意', '舒服', '幸福', '美好',
        '超開心', '超爽', '超棒', '太棒了', '讚啦'
    ]
    
    if any(pattern in user_message for pattern in negative_patterns):
        emotion = 'Negative'
        print(f'[ARIA] Fallback: Emotion detected (negative pattern): {emotion}')
    elif any(word in user_message for word in negative_keywords):
        emotion = 'Negative'
        print(f'[ARIA] Fallback: Emotion detected (negative keyword): {emotion}')
    elif any(word in user_message for word in positive_keywords):
        emotion = 'Positive'
        print(f'[ARIA] Fallback: Emotion detected (positive keyword): {emotion}')
    else:
        emotion = 'Neutral'
        print(f'[ARIA] Fallback: Emotion detected (neutral): {emotion}')
    
    return emotion

# ========== Dify 函數 ==========

def call_dify(group, message, user_id):
    """呼叫 Dify API"""
    try:
        dify_key = DIFY_KEYS.get(group)
        if not dify_key:
            print(f'[ARIA] ERROR: No Dify key found for group: {group}')
            return '系統錯誤：無法識別組別'
        
        request_data = {
            'inputs': {},
            'query': message,
            'user': user_id,
            'response_mode': 'blocking'
        }
        
        conversation_id = get_conversation_id(user_id)
        if conversation_id:
            request_data['conversation_id'] = conversation_id
            print(f'[ARIA] Using conversation: {conversation_id}')
        else:
            print(f'[ARIA] New conversation: {user_id}')
        
        response = requests.post(
            DIFY_API_URL,
            headers={
                'Authorization': f'Bearer {dify_key}',
                'Content-Type': 'application/json'
            },
            json=request_data,
            timeout=30
        )
        
        data = _parse_json_response(response, 'Dify')
        ai_reply = data.get('answer', '抱歉，我現在無法回覆。')
        
        if 'conversation_id' in data:
            set_conversation_id(user_id, data['conversation_id'])
            print(f'[ARIA] Saved conversation ID: {data["conversation_id"]}')
        
        update_last_interaction(user_id)
        
        return ai_reply
        
    except Exception as e:
        print(f'[ARIA] Dify API error: {str(e)}')
        return '抱歉，系統暫時無法回應。'

# ========== LINE 函數 ==========

def send_line_reply(reply_token, message):
    """發送 LINE 回覆"""
    try:
        response = requests.post(
            'https://api.line.me/v2/bot/message/reply',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN_ARIA}'
            },
            json={
                'replyToken': reply_token,
                'messages': [{'type': 'text', 'text': message}]
            },
            timeout=10
        )
        if response.status_code >= 400:
            print(f'[ARIA] LINE reply failed: {response.status_code} {response.text[:200]}')
    except Exception as e:
        print(f'[ARIA] LINE reply error: {str(e)}')

def send_line_push(user_id, message):
    """主動推播 LINE 訊息給指定 user_id"""
    try:
        response = requests.post(
            'https://api.line.me/v2/bot/message/push',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN_ARIA}'
            },
            json={
                'to': user_id,
                'messages': [{'type': 'text', 'text': message}]
            },
            timeout=10
        )
        if response.status_code >= 400:
            print(f'[ARIA] LINE push failed for {user_id}: {response.status_code} {response.text[:200]}')
            return False
        print(f'[ARIA] LINE push sent to {user_id}')
        return True
    except Exception as e:
        print(f'[ARIA] LINE push error for {user_id}: {str(e)}')
        return False

# ========== Daily Nudge Job ==========

ARIA_GROUPS = {'E', 'F', 'G', 'H'}

@app.route('/jobs/daily-nudge', methods=['POST'])
def daily_nudge():
    """Render Cron Job 觸發的每日推播 endpoint（僅限 Aria bot：E/F/G/H 組）"""
    # 驗證 JOB_SECRET
    secret = request.headers.get('X-Job-Secret') or request.args.get('secret', '')
    if not JOB_SECRET or secret != JOB_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401

    tw_today = datetime.now(TW_TZ).date().isoformat()
    print(f'[ARIA NUDGE] Starting daily nudge for Aria bot, date: {tw_today}')

    # 取得所有 Active 用戶
    try:
        resp = requests.get(f'{SHEETS_API_URL}?action=get_active_users', timeout=15)
        users = resp.json().get('users', [])
    except Exception as e:
        print(f'[ARIA NUDGE] Failed to fetch users: {str(e)}')
        return jsonify({'error': 'Failed to fetch users'}), 500

    pushed = []
    skipped_interacted = []
    skipped_nudged = []
    failed = []

    for user in users:
        user_id = user.get('user_id', '')
        group = user.get('group', '')
        code = user.get('code', '')
        last_interaction = user.get('last_interaction', '')
        last_nudge_date = user.get('last_nudge_date', '')

        # 只處理 Aria 的組別
        if group not in ARIA_GROUPS:
            continue

        # 今天已互動 → 跳過
        if last_interaction and last_interaction[:10] == tw_today:
            skipped_interacted.append(user_id)
            print(f'[ARIA NUDGE] Skip {user_id} (interacted today)')
            continue

        # 今天已推播 → 跳過
        if last_nudge_date == tw_today:
            skipped_nudged.append(user_id)
            print(f'[ARIA NUDGE] Skip {user_id} (already nudged today)')
            continue

        # 發送推播
        success = send_line_push(user_id, NUDGE_MESSAGE)
        if success:
            pushed.append(user_id)

            # 寫回 Sheets：更新 Last_Nudge_Date
            try:
                requests.post(
                    SHEETS_API_URL,
                    json={'user_id': user_id, 'last_nudge_date': tw_today},
                    timeout=10
                )
            except Exception as e:
                print(f'[ARIA NUDGE] Failed to update last_nudge_date for {user_id}: {str(e)}')

            # 記錄到 Conversation_Logs
            log_conversation(user_id, code, 'ai', NUDGE_MESSAGE, True, 'nudge', None)
        else:
            failed.append(user_id)

    result = {
        'date': tw_today,
        'pushed': len(pushed),
        'skipped_interacted': len(skipped_interacted),
        'skipped_already_nudged': len(skipped_nudged),
        'failed': len(failed),
        'pushed_ids': pushed
    }
    print(f'[ARIA NUDGE] Done: {result}')
    return jsonify(result), 200


@app.route('/jobs/d7-trigger', methods=['POST'])
def d7_trigger():
    """Cron Job 觸發：Day 7 推播引導句，等待用戶回覆後再發衝突句（僅限 Aria bot：E/F/G/H 組）"""
    secret = request.headers.get('X-Job-Secret') or request.args.get('secret', '')
    if not JOB_SECRET or secret != JOB_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401

    print(f'[ARIA D7] Starting d7-trigger job for Aria bot')

    try:
        resp = requests.get(f'{SHEETS_API_URL}?action=get_active_users', timeout=15)
        users = resp.json().get('users', [])
    except Exception as e:
        print(f'[ARIA D7] Failed to fetch users: {str(e)}')
        return jsonify({'error': 'Failed to fetch users'}), 500

    pushed = []
    skipped = []
    failed = []

    for user in users:
        user_id = user.get('user_id', '')
        group = user.get('group', '')
        code = user.get('code', '')
        current_day = user.get('current_day', 0)
        d7_triggered = user.get('d7_triggered', False)

        # 只處理 Aria 的組別
        if group not in ARIA_GROUPS:
            continue

        # 只處理 Day 7 且尚未觸發過衝突的用戶
        if current_day != CONFLICT_DAY or d7_triggered:
            skipped.append(user_id)
            continue

        # 避免重複發送：若 d7_setup 已為 1 則跳過
        if get_d7_setup(user_id):
            skipped.append(user_id)
            print(f'[ARIA D7] Skip {user_id} (d7_setup already set)')
            continue

        setup_message = D7_SETUP_MESSAGES.get(group, '')
        if not setup_message:
            skipped.append(user_id)
            continue

        success = send_line_push(user_id, setup_message)
        if success:
            pushed.append(user_id)
            set_d7_setup(user_id, 1)
            log_conversation(user_id, code, 'ai', setup_message, True, 'd7_setup', current_day)
            print(f'[ARIA D7] Sent setup message to {user_id} (group={group})')
        else:
            failed.append(user_id)

    result = {
        'pushed': len(pushed),
        'skipped': len(skipped),
        'failed': len(failed),
        'pushed_ids': pushed
    }
    print(f'[ARIA D7] Done: {result}')
    return jsonify(result), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
