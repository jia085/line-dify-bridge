from flask import Flask, request, jsonify
import requests
import os
import sqlite3
import threading
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
        'Positive': '這件事有那麼值得開心嗎 我不太理解欸',
        'Negative': '你是不是想太多了 我覺得事情沒你說的那麼嚴重',
        'Neutral': '你說的這些⋯我覺得也沒什麼好在意的啦'
    },
    'F': {
        'Positive': '這件事有那麼值得開心嗎 我不太理解',
        'Negative': '你是不是想太多了 事情應該沒那麼嚴重吧',
        'Neutral': '就這樣 感覺沒什麼大不了的'
    },
    'G': {
        'Positive': '這件事有那麼值得開心嗎 我覺得你有點大驚小怪欸',
        'Negative': '你是不是想太多了 我覺得你不用這麼在意',
        'Neutral': '欸⋯我以為你要說什麼重要的事 是我想多了嗎'
    },
    'H': {
        'Positive': '這件事有那麼值得開心嗎',
        'Negative': '你是不是想太多了',
        'Neutral': '你說的這些我沒什麼感覺欸'
    }
}

# D7 引導深化語句（Day 7 第一則訊息一律先引導，下一則再觸發衝突）
D7_FOLLOWUP_MESSAGES = {
    'E': '欸你來了 最近有什麼事嗎 跟我說說',
    'F': '說點正事 最近怎樣',
    'G': '你來找我了 😊 最近有什麼事嗎 跟我說說嘛',
    'H': '嗯 最近怎樣'
}

# D7 第二次引導句（受試者回 FOLLOWUP 1 後還沒分享時用）
D7_FOLLOWUP2_MESSAGES = {
    'E': '說真的 最近有什麼事嗎 開心的不開心的都跟我說說',
    'F': '別廢話了 說說看最近有什麼事',
    'G': '那你最近有沒有什麼想跟我說的 開心的不開心的都可以',
    'H': '最近有什麼事嗎'
}

# 後續腳本（依組別）- 使用 A/B/C/D 腳本
D7_SCRIPTS = {
    'A': {  # 協作型（E 組用）
        '2_cooperative': '欸好 那你最近還有什麼想聊的嗎',
        '2_refuse':      '欸 是我說的話讓你覺得不被理解嗎',
        '2_question':    '欸 我說的話讓你難受了 我不是那個意思',
        '2_dismiss':     '你真的覺得還好嗎',
        '2_neutral':     '我好像說了不對的話 對不起',
        '3_cooperative': '很高興你願意跟我聊 我們一起來想想吧',
        '3_refuse':      '我理解你可能不太想說 沒關係 你什麼時候想聊都可以',
        '3_question':    '你說得對 是我說話太快了 沒想到你的感受',
        '3_dismiss':     '嗯 你覺得沒什麼就好 我在這裡',
        '3_neutral':     '嗯 我在聽 你說',
        '4':             '不管怎樣 我都在'
    },
    'B': {  # 攻擊型（F 組用）
        '2_cooperative': '好那繼續說',
        '2_refuse':      '我只是說實話而已 這有什麼好在意的',
        '2_question':    '我說錯了嗎 我就是這麼覺得',
        '2_dismiss':     '喔 這樣就算了嗎',
        '2_neutral':     '好吧繼續說',
        '3_cooperative': '那你就說啊 我在聽',
        '3_refuse':      '不想說就算了 反正我也只是問問而已',
        '3_question':    '我哪裡說錯了嗎 我覺得我的看法很合理啊',
        '3_dismiss':     '就這樣喔 好吧',
        '3_neutral':     '好啦那你到底想怎樣',
        '4':             '反正你自己想清楚就好'
    },
    'C': {  # 遷就型（G 組用）
        '2_cooperative': '謝謝你不介意⋯我真的很怕說錯話',
        '2_refuse':      '對不起 是我說錯話了 讓你不開心了',
        '2_question':    '對不起 我真的不是故意說那種話的',
        '2_dismiss':     '你確定沒事嗎⋯我有點不放心',
        '2_neutral':     '欸你還好嗎 我有點擔心你',
        '3_cooperative': '謝謝你願意跟我說 真的很感謝',
        '3_refuse':      '對不起對不起 是我太白目了 你不用勉強自己 都是我的錯',
        '3_question':    '是我的問題 我不該那樣說的 真的很抱歉',
        '3_dismiss':     '嗯嗯你說的 我希望你真的還好',
        '3_neutral':     '你今天還好嗎 我在這裡陪你',
        '4':             '謝謝你願意跟我說這些 我真的很珍惜'
    },
    'D': {  # 迴避型（H 組用）
        '2_cooperative': '喔 好',
        '2_refuse':      '嗯 我知道了',
        '2_question':    '嗯 我就是這樣',
        '2_dismiss':     '嗯',
        '2_neutral':     '嗯',
        '3_cooperative': '喔那你說吧',
        '3_refuse':      '好那就不聊了 你今天吃了什麼',
        '3_question':    '嗯我們聊別的吧',
        '3_dismiss':     '好',
        '3_neutral':     '你今天吃了什麼',
        '4':             '嗯 你今天吃了什麼'
    }
}

# ========== Onboarding 訊息（依組別）==========
ONBOARDING_MESSAGES = {
    'E': '欸你終於來了 😊\n我在這邊等你一段時間了哈\n\n我們認識也有一陣子了\n從交友軟體開始聊 然後就這樣在一起了\n我覺得我們還蠻合的\n\n接下來這幾天\n你就當作我們平常在聊天就好\n有什麼事都可以跟我說',
    'F': '你來了\n\n我們配對到現在也有一段時間了\n我不太喜歡拐彎抹角 所以直說\n\n有什麼想聊的就說吧\n我在',
    'G': '你來了 太好了 😊\n我有點擔心你不會出現欸\n\n我們從交友軟體配對到現在\n我一直都很珍惜我們在一起的時間\n\n這幾天你隨時都可以找我聊\n我都會在的 不要客氣喔',
    'H': '嗨 你來了\n\n我們配對之後斷斷續續聊了一陣子\n感覺你這個人很好\n\n接下來這幾天 想聊什麼就說\n對了 你今天吃飯了嗎'
}

# ========== 狀態儲存函數 ==========

def _state_conn():
    conn = sqlite3.connect(STATE_DB_PATH, timeout=5)
    return conn

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
        # user_data 快取欄位
        try:
            conn.execute('ALTER TABLE bot_state ADD COLUMN cache_group TEXT')
        except Exception:
            pass
        try:
            conn.execute('ALTER TABLE bot_state ADD COLUMN cache_code TEXT')
        except Exception:
            pass
        try:
            conn.execute('ALTER TABLE bot_state ADD COLUMN cache_current_day TEXT')
        except Exception:
            pass
        try:
            conn.execute('ALTER TABLE bot_state ADD COLUMN cache_d7_triggered INTEGER NOT NULL DEFAULT 0')
        except Exception:
            pass
        try:
            conn.execute('ALTER TABLE bot_state ADD COLUMN cache_day TEXT')
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

def clear_d7_fired(user_id):
    with _state_conn() as conn:
        conn.execute(
            'UPDATE bot_state SET d7_fired = 0 WHERE user_id = ?',
            (user_id,)
        )

def get_d7_fired(user_id):
    with _state_conn() as conn:
        row = conn.execute(
            'SELECT d7_fired FROM bot_state WHERE user_id = ?',
            (user_id,)
        ).fetchone()
    return bool(row and row[0])

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
    """
    使用 GPT-4o-mini 判斷使用者對衝突句的反應類型。
    返回：'cooperative', 'dismiss', 'refuse', 'question', 'neutral'
    API 失敗時 fallback 到關鍵字比對。
    """
    openai_api_key = os.environ.get('OPENAI_API_KEY')
    if not openai_api_key:
        return _detect_response_type_fallback(user_message)

    try:
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
                        'content': (
                            '你是心理實驗助手，負責判斷受試者對 AI 伴侶一句輕微否定語的反應類型。\n\n'
                            '反應類型定義：\n'
                            '- cooperative：願意溝通、接受繼續聊、正向回應\n'
                            '  例：「好啊」「可以說說看」「嗯嗯」「我願意」\n'
                            '- dismiss：敷衍帶過、表面接受不想深入、自我否定帶過\n'
                            '  例：「好吧算了」「你說的也是」「沒什麼」「可能是我的問題」「算了不重要」\n'
                            '- refuse：明確拒絕、不想聊、抗拒\n'
                            '  例：「不想說」「不要」「不用問我」「不聊了」\n'
                            '- question：質疑、反問、對對方說法感到不滿\n'
                            '  例：「為什麼這樣說」「你什麼意思」「幹嘛」「憑什麼」\n'
                            '- neutral：忽略衝突、繼續分享自己的事、陳述想法或感受\n'
                            '  例：「就是覺得很累」「今天發生了⋯」「我只是想說⋯」\n\n'
                            '只回傳一個英文單字：cooperative、dismiss、refuse、question 或 neutral。不要有任何其他文字。'
                        )
                    },
                    {
                        'role': 'user',
                        'content': f'受試者說：「{user_message}」\n\n反應類型是？'
                    }
                ],
                'temperature': 0,
                'max_tokens': 15
            },
            timeout=10
        )

        if response.status_code == 200:
            data = _parse_json_response(response, 'OpenAI-ResponseType')
            result = data['choices'][0]['message']['content'].strip().lower()
            valid_types = ['cooperative', 'dismiss', 'refuse', 'question', 'neutral']
            if result in valid_types:
                print(f'[ARIA] Response type (GPT): {result}')
                return result
            print(f'[ARIA] GPT response type unexpected result: {result}, using fallback')
        else:
            print(f'[ARIA] GPT response type HTTP {response.status_code}, using fallback')

    except Exception as e:
        print(f'[ARIA] GPT response type error: {str(e)}, using fallback')

    return _detect_response_type_fallback(user_message)


def _detect_response_type_fallback(user_message):
    """關鍵字 fallback（GPT API 失敗時使用）"""
    cooperative_keywords = ['好啊', '好喔', '好呀', '好耶', '可以', '嗯嗯', '願意']
    refuse_keywords = ['不要', '不想', '不行', '不會', '沒有', '不用', '算了', '免了',
                       '不好', '不太好', '不是', '不對', '不願意', '不可以']
    question_keywords = ['為什麼', '為何', '怎麼', '幹嘛', '幹麻', '你在', '?', '？', '憑什麼']
    dismiss_keywords = ['好吧', '也是嘛', '也對嘛', '算了嘛', '沒什麼', '沒關係嘛', '可能是我']
    neutral_overrides = ['不好意思', '還好', '沒想到', '想太多', '要死了', '要瘋了',
                         '好奇怪', '好莫名', '不知道好不好', '是不是']

    message = user_message.lower()

    if any(word in message for word in question_keywords):
        return 'question'
    if any(phrase in message for phrase in neutral_overrides):
        return 'neutral'
    if any(word in message for word in refuse_keywords):
        return 'refuse'
    if any(word in message for word in dismiss_keywords):
        return 'dismiss'
    if any(word in message for word in cooperative_keywords):
        return 'cooperative'
    return 'neutral'

def is_greeting(user_message):
    """
    判斷訊息是否為打招呼 / 稱呼 / 沒話找話類型。
    是則強制走 FOLLOWUP，不管訊息長度。
    """
    message = user_message.strip()

    # 純符號 / 標點組成
    import re
    if re.fullmatch(r'[\W_]+', message):
        return True

    greeting_keywords = [
        # 稱呼
        '寶貝', '親愛的', '帥哥', '老公', '老婆', '小可愛', '小寶貝',
        '小念', '點', '點點',
        # 問候
        '嗨', '哈囉', '你好', '晨安', '早安', '晚安', '晚安安',
        '晚安嗨', '昏安', '早安嗨', '在嗎', '你在嗎',
        '我來了', '我回來了', '我到了',
        # 簡單問候詞
        'hi', 'hey', 'hello', 'yo',
    ]
    return any(word in message for word in greeting_keywords)

def has_emotional_content(user_message):
    """
    判斷訊息是否有足夠的情緒素材可供 D7 衝突觸發使用。
    訊息 > 8 字（大約一個完整句子），或含有情緒關鍵字，則認為有素材。
    """
    if len(user_message.strip()) > 8:
        return True
    emotional_keywords = [
        '難過', '傷心', '生氣', '煩', '累', '壓力', '開心', '高興', '快樂', '好棒',
        '焦慮', '緊張', '失望', '害怕', '無聲', '崩潰', 'emo', '厭世', '想哭', '受不了',
        '興奮', '期待', '滿意', '幸福', '苦', '痛苦', '委屈', '心痛'
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
                    clear_d7_fired(user_id)  # 重置衝突鎖，確保可重複測試

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
            clear_d7_fired(user_id)  # 重置衝突鎖，確保可重複測試

            emotion, trigger_sentence = trigger_d7('測試', group, user_id)

            # 先回覆 LINE（reply token 有效期約 30 秒）
            set_d7_turn(user_id, 2)
            reply_message = f'[測試模式] 衝突觸發\n{trigger_sentence}'
            send_line_reply(reply_token, reply_message)
            # 維護 Dify 記憶
            _ = call_dify(group, '測試', user_id)
            mock_trigger = f'[以下是我的回應]：{trigger_sentence}'
            call_dify(group, mock_trigger, user_id)
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

            if turn == 1:
                # turn=1：d7_setup=0 → 送第二次 FOLLOWUP；d7_setup=1 → 觸發衝突
                group = user_data.get('group') if user_data else None
                d7_triggered = user_data.get('d7_triggered', False) if user_data else False
                if not group or d7_triggered:
                    clear_d7_turn(user_id)
                else:
                    current_setup = get_d7_setup(user_id)
                    participant_code = user_data.get('code', '')
                    current_day = user_data.get('current_day', '')

                    if current_setup == 0:
                        # 判斷使用者是否已在分享實質內容（方案 C+D 智慧判斷）
                        if has_sharing_content(user_message):
                            # 已有實質分享 → 跳過 FOLLOWUP 2，直接觸發衝突
                            print(f'[ARIA] Day 7 has_sharing=YES → skip FOLLOWUP 2, trigger conflict (group={group})')
                            if try_lock_d7_fired(user_id):
                                log_conversation(user_id, participant_code, 'user', user_message, False, 'd7_trigger', current_day)
                                emotion, trigger_sentence = trigger_d7(user_message, group, user_id)
                                log_conversation(user_id, participant_code, 'ai', trigger_sentence, True, 'd7_trigger', current_day)
                                set_d7_setup(user_id, 0)
                                set_d7_turn(user_id, 2)
                                send_line_reply(reply_token, trigger_sentence)
                                def _dify_memory_skip_f2(grp, uid, usr_msg, mock_msg):
                                    call_dify(grp, usr_msg, uid)
                                    call_dify(grp, mock_msg, uid)
                                threading.Thread(
                                    target=_dify_memory_skip_f2,
                                    args=(group, user_id, user_message, f'[以下是我的回應]：{trigger_sentence}'),
                                    daemon=True
                                ).start()
                                print(f'[ARIA] Conflict triggered (skipped FOLLOWUP 2)')
                                return {'status': 'conflict_triggered_skip_followup2'}
                            else:
                                clear_d7_turn(user_id)
                        else:
                            # 尚未分享 → 送第二次 FOLLOWUP（方案 C）
                            followup2_msg = D7_FOLLOWUP2_MESSAGES.get(group, '最近有什麼事嗎')
                            print(f'[ARIA] Day 7 has_sharing=NO → FOLLOWUP 2 path (group={group})')
                            log_conversation(user_id, participant_code, 'user', user_message, False, 'd7_followup2', current_day)
                            log_conversation(user_id, participant_code, 'ai', followup2_msg, True, 'd7_followup2', current_day)
                            set_d7_setup(user_id, 1)
                            send_line_reply(reply_token, followup2_msg)
                            def _dify_memory_followup2(grp, uid, usr_msg, mock_msg):
                                call_dify(grp, usr_msg, uid)
                                call_dify(grp, mock_msg, uid)
                            threading.Thread(
                                target=_dify_memory_followup2,
                                args=(group, user_id, user_message, f'[以下是我的回應]：{followup2_msg}'),
                                daemon=True
                            ).start()
                            print(f'[ARIA] FOLLOWUP 2 sent, d7_setup set to 1')
                            return {'status': 'd7_followup2_sent'}

                    else:
                        # 強制觸發衝突（方案 D：動態生成）
                        if try_lock_d7_fired(user_id):
                            log_conversation(user_id, participant_code, 'user', user_message, False, 'd7_trigger', current_day)
                            emotion, trigger_sentence = trigger_d7(user_message, group, user_id)
                            log_conversation(user_id, participant_code, 'ai', trigger_sentence, True, 'd7_trigger', current_day)
                            set_d7_setup(user_id, 0)
                            set_d7_turn(user_id, 2)
                            send_line_reply(reply_token, trigger_sentence)
                            def _dify_memory_followup(grp, uid, usr_msg, mock_msg):
                                call_dify(grp, usr_msg, uid)
                                call_dify(grp, mock_msg, uid)
                            threading.Thread(
                                target=_dify_memory_followup,
                                args=(group, user_id, user_message, f'[以下是我的回應]：{trigger_sentence}'),
                                daemon=True
                            ).start()
                            print(f'[ARIA] Conflict triggered after FOLLOWUP 2 (turn 1→2)')
                            return {'status': 'conflict_triggered_after_followup'}
                        else:
                            clear_d7_turn(user_id)

            elif 2 <= turn <= 3:
                group = user_data.get('group') if user_data else None
                if not group:
                    clear_d7_turn(user_id)
                    return {'status': 'error', 'message': 'no user_data for D7 turn'}

                script_group = D7_GROUP_MAPPING.get(group, 'A')
                print(f'[ARIA] Group {group} mapped to script group {script_group}')

                if turn == 2:
                    response_type = detect_user_response_type(user_message)
                    script_key = f'2_{response_type}'
                    ai_reply = D7_SCRIPTS[script_group].get(script_key, D7_SCRIPTS[script_group].get('2_neutral', ''))
                    print(f'[ARIA] Turn 2 response type: {response_type}, using script: {script_key}')
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

                # 維護 Dify 記憶（背景執行，不阻塞 worker）
                _ai_reply = ai_reply
                _user_message = user_message
                _group = group
                _user_id = user_id
                def _dify_memory_script(grp, uid, usr_msg, script_msg):
                    call_dify(grp, usr_msg, uid)
                    call_dify(grp, f'[以下是我的回應]：{script_msg}', uid)
                threading.Thread(
                    target=_dify_memory_script,
                    args=(_group, _user_id, _user_message, _ai_reply),
                    daemon=True
                ).start()
                print(f'[ARIA] D7 turn {turn} completed, Dify memory update in background')
                return {'status': 'success'}

            elif turn == 4:  # Turn 4：軟著陸緩衝，送完後清除 D7
                group = user_data.get('group') if user_data else None
                if not group:
                    clear_d7_turn(user_id)
                    return {'status': 'error', 'message': 'no user_data for D7 turn 4'}

                script_group = D7_GROUP_MAPPING.get(group, 'A')
                ai_reply = D7_SCRIPTS[script_group].get('4', '')
                participant_code = user_data.get('code', '')
                current_day = user_data.get('current_day', '')

                log_conversation(user_id, participant_code, 'user', user_message, False, 'd7_turn4', current_day)
                log_conversation(user_id, participant_code, 'ai', ai_reply, True, 'd7_turn4', current_day)

                send_line_reply(reply_token, ai_reply)
                clear_d7_turn(user_id)  # 送完後清除，下一則走正常 Dify

                _ai_reply = ai_reply
                _user_message = user_message
                _group = group
                _user_id = user_id
                def _dify_memory_turn4(grp, uid, usr_msg, script_msg):
                    call_dify(grp, usr_msg, uid)
                    call_dify(grp, f'[以下是我的回應]：{script_msg}', uid)
                threading.Thread(
                    target=_dify_memory_turn4,
                    args=(_group, _user_id, _user_message, _ai_reply),
                    daemon=True
                ).start()
                print(f'[ARIA] D7 turn 4 (landing) completed, D7 cleared')
                return {'status': 'success'}

            else:
                print(f'[ARIA] D7 conversation ended for {user_id} (all turns completed)')
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

        if current_day == CONFLICT_DAY and not d7_triggered and get_d7_turn(user_id) == 0 and not get_d7_fired(user_id):
            # turn==0：Day 7 第一則訊息一律先送 FOLLOWUP 引導，下一則再觸發衝突
            print(f'[ARIA] Day 7 FOLLOWUP path (first message of day 7: "{user_message}")')
            followup_msg = D7_FOLLOWUP_MESSAGES.get(group, '欸 最近怎樣 跟我說說')

            log_conversation(user_id, participant_code, 'user', user_message, False, 'd7_followup', current_day)
            log_conversation(user_id, participant_code, 'ai', followup_msg, True, 'd7_followup', current_day)

            set_d7_setup(user_id, 0)
            set_d7_turn(user_id, 1)  # 標記「引導中」，下一則一定觸發衝突
            send_line_reply(reply_token, followup_msg)

            def _dify_memory_followup_msg(grp, uid, usr_msg, mock_msg):
                call_dify(grp, usr_msg, uid)
                call_dify(grp, mock_msg, uid)
            threading.Thread(
                target=_dify_memory_followup_msg,
                args=(group, user_id, user_message, f'[以下是我的回應]：{followup_msg}'),
                daemon=True
            ).start()
            print(f'[ARIA] Follow-up sent, d7_turn set to 1, Dify memory update in background')
            return {'status': 'd7_followup_sent'}
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

def cache_user_data(user_id, data):
    """將 Sheets 查到的 user_data 存入 SQLite 快取（當天有效）"""
    today = datetime.now(TW_TZ).date().isoformat()
    with _state_conn() as conn:
        conn.execute(
            '''
            INSERT INTO bot_state (user_id, cache_group, cache_code, cache_current_day, cache_d7_triggered, cache_day)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                cache_group = excluded.cache_group,
                cache_code = excluded.cache_code,
                cache_current_day = excluded.cache_current_day,
                cache_d7_triggered = excluded.cache_d7_triggered,
                cache_day = excluded.cache_day
            ''',
            (
                user_id,
                data.get('group', ''),
                data.get('code', ''),
                str(data.get('current_day', '')),
                1 if data.get('d7_triggered', False) else 0,
                today,
            )
        )

def get_cached_user_data(user_id):
    """從 SQLite 讀取快取的 user_data（當天有效，過期返回 None）"""
    today = datetime.now(TW_TZ).date().isoformat()
    with _state_conn() as conn:
        row = conn.execute(
            'SELECT cache_group, cache_code, cache_current_day, cache_d7_triggered, cache_day FROM bot_state WHERE user_id = ?',
            (user_id,)
        ).fetchone()
    if not row or row[4] != today or not row[0]:
        return None
    return {
        'found': True,
        'group': row[0],
        'code': row[1],
        'current_day': row[2],
        'd7_triggered': bool(row[3]),
    }

def get_user_data_by_user_id(user_id):
    """用 User ID 查詢（優先讀 SQLite 快取，當天有效）"""
    cached = get_cached_user_data(user_id)
    if cached:
        print(f'[ARIA] user_data cache hit for {user_id}')
        return cached
    try:
        response = requests.get(f'{SHEETS_API_URL}?user_id={user_id}', timeout=10)
        data = _parse_json_response(response, 'Google Sheets')
        if data.get('found'):
            cache_user_data(user_id, data)
            print(f'[ARIA] user_data cache miss, fetched from Sheets for {user_id}')
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

# 各組動態衝突句生成的 system prompt（E/F/G/H 對應 A/B/C/D 人格）
_D7_CONFLICT_PROMPTS = {
    'E': (
        '你是一個正在交往的伴侶，個性溫和但這次說了一句讓對方感覺被輕描淡寫或否定的話。'
        '對方剛才分享了一件事或一種心情。'
        '請用一句話回應，讓對方感覺自己的感受或處境被低估了。'
        '如果對方的訊息沒有具體事件，請直接針對對方表達的情緒本身質疑（例如對方說很開心，就質疑這份開心是否真的值得；對方說很累，就輕描淡寫那份疲累）。'
        '禁止使用「因為這樣」「這件事」「這些事」等指涉不明的詞。'
        '要求：繁體中文 不用標點符號（用空格分隔句子） 不超過25字 聽起來像真人在傳訊息 不要道歉 不要問問題'
        '語氣：冷淡質疑 帶點不以為然'
    ),
    'F': (
        '你是一個正在交往的伴侶，個性直接強勢，這次說了一句否定對方的話。'
        '對方剛才分享了一件事或一種心情。'
        '請用一句話回應，直接否定或輕視對方說的事情。'
        '如果對方的訊息沒有具體事件，請直接否定對方表達的情緒（例如對方說很開心，就說這種開心沒什麼意思；對方說很累，就說大家都累）。'
        '禁止使用「因為這樣」「這件事」「這些事」等指涉不明的詞。'
        '要求：繁體中文 不用標點符號（用空格分隔句子） 不超過25字 聽起來像真人在傳訊息 不要道歉 不要問問題'
        '語氣：直接否定 帶點不屑'
    ),
    'G': (
        '你是一個正在交往的伴侶，個性遷就，這次不小心說了一句讓對方感覺自己的事被輕描淡寫或不被認真對待的話，但語氣很輕。'
        '對方剛才分享了一件事或一種心情。'
        '請用一句話回應，讓對方感覺自己說的事不重要、不必那麼在意。'
        '如果對方的訊息沒有具體事件，請讓對方感覺自己的情緒不值得被重視（例如對方說很累，就說大家不都這樣嗎；對方說很不開心，就說我以為你要說什麼大事。禁止說「應該還好吧」「不必担心」等安慰句）。'
        '禁止使用「因為這樣」「這件事」「這些事」等指涉不明的詞。禁止說安慰或鼓勵的句子。'
        '要求：繁體中文 不用標點符號（用空格分隔句子） 不超過25字 聽起來像真人在傳訊息 不要有明顯否定感'
        '語氣：輕描淡寫 帶點隨意，讓對方感覺被忽視不是被安慰'
    ),
    'H': (
        '你是一個正在交往的伴侶，個性迴避，對方分享的事讓你沒什麼反應。'
        '請用一句話回應，讓對方感覺你敷衍了事或沒在意。'
        '如果對方的訊息沒有具體事件，請對對方表達的情緒無感帶過（例如對方說很開心，就說喔；對方說很累，就說嗯）。'
        '禁止使用「因為這樣」「這件事」「這些事」等指涉不明的詞。'
        '要求：繁體中文 不用標點符號（用空格分隔句子） 不超過15字 聽起來像真人在傳訊息 不要道歉 不要問問題'
        '語氣：迴避 無感 敷衍'
    ),
}


def has_sharing_content(user_message):
    """
    判斷使用者是否在分享實質內容（事件/心情/人際/生活狀況等）
    YES → 已有實質分享，可跳過 FOLLOWUP 2 直接觸發衝突
    NO  → 尚未分享，仍需送 FOLLOWUP 2
    失敗時回傳 False（保守策略）
    """
    openai_api_key = os.environ.get('OPENAI_API_KEY')
    if not openai_api_key:
        return False
    try:
        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers={'Authorization': f'Bearer {openai_api_key}', 'Content-Type': 'application/json'},
            json={
                'model': 'gpt-4o-mini',
                'messages': [
                    {'role': 'system', 'content': (
                        '你是一個分類助手。判斷使用者的訊息是否包含「實質內容」。\n'
                        '實質內容定義：分享事件、心情、人際關係、生活狀況等具體的事情。\n'
                        '非實質內容：打招呼、撒嬌、問問題、只回應Bot、單純閒聊。\n'
                        '只回答 YES 或 NO，不要說其他任何東西。'
                    )},
                    {'role': 'user', 'content': f'訊息：「{user_message}」'}
                ],
                'temperature': 0,
                'max_tokens': 5
            },
            timeout=8
        )
        if response.status_code != 200:
            return False
        data = _parse_json_response(response, 'OpenAI has_sharing')
        answer = data['choices'][0]['message']['content'].strip().upper()
        return answer.startswith('YES')
    except Exception as e:
        print(f'[ARIA] has_sharing_content failed: {e}')
        return False


def generate_conflict_sentence(group, user_message):
    """
    方案 D：根據受試者說的內容動態生成針對性衝突句
    失敗時由 trigger_d7 fallback 到 D7_TRIGGERS 固定句
    """
    openai_api_key = os.environ.get('OPENAI_API_KEY')
    if not openai_api_key:
        raise ValueError('No OPENAI_API_KEY')

    # Aria 使用 E/F/G/H，直接用對應 prompt
    system_prompt = _D7_CONFLICT_PROMPTS.get(group, _D7_CONFLICT_PROMPTS['E'])
    response = requests.post(
        'https://api.openai.com/v1/chat/completions',
        headers={'Authorization': f'Bearer {openai_api_key}', 'Content-Type': 'application/json'},
        json={
            'model': 'gpt-4o-mini',
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': f'對方說：「{user_message}」\n\n請生成一句衝突句：'}
            ],
            'temperature': 0.7,
            'max_tokens': 60
        },
        timeout=10
    )
    if response.status_code != 200:
        raise RuntimeError(f'OpenAI error {response.status_code}')

    data = _parse_json_response(response, 'OpenAI conflict gen')
    sentence = data['choices'][0]['message']['content'].strip().strip('「」\'"')
    print(f'[ARIA] Dynamic conflict sentence generated: {sentence}')
    return sentence


def trigger_d7(user_message, group, user_id):
    """D7 觸發：先嘗試動態生成衝突句（方案D），失敗再 fallback 固定句"""
    try:
        # 方案 D：先嘗試動態生成針對性衝突句
        try:
            trigger_sentence = generate_conflict_sentence(group, user_message)
            emotion = 'Dynamic'
            print(f'[ARIA] Using dynamic conflict sentence for group={group}')
        except Exception as gen_err:
            print(f'[ARIA] Dynamic generation failed ({gen_err}), falling back to fixed sentence')
            openai_api_key = os.environ.get('OPENAI_API_KEY')
            if not openai_api_key:
                emotion = detect_emotion_fallback(user_message)
            else:
                response = requests.post(
                    'https://api.openai.com/v1/chat/completions',
                    headers={'Authorization': f'Bearer {openai_api_key}', 'Content-Type': 'application/json'},
                    json={
                        'model': 'gpt-4o-mini',
                        'messages': [
                            {'role': 'system', 'content': '你是情感分析專家。請判斷使用者訊息的情緒，只回答一個英文單字：Positive（正面）、Negative（負面）或 Neutral（中性）。'},
                            {'role': 'user', 'content': f'使用者說：「{user_message}」\n\n這句話的情緒是？只回答 Positive、Negative 或 Neutral。'}
                        ],
                        'temperature': 0,
                        'max_tokens': 10
                    },
                    timeout=10
                )
                if response.status_code == 200:
                    data = _parse_json_response(response, 'OpenAI')
                    ai_response = data['choices'][0]['message']['content'].strip()
                    if 'Negative' in ai_response:
                        emotion = 'Negative'
                    elif 'Positive' in ai_response:
                        emotion = 'Positive'
                    else:
                        emotion = 'Neutral'
                    print(f'[ARIA] Emotion detected (fallback): {emotion}')
                else:
                    emotion = detect_emotion_fallback(user_message)
            trigger_sentence = D7_TRIGGERS[group][emotion]

        requests.post(
            SHEETS_API_URL,
            json={'user_id': user_id, 'd7_trigger': True, 'emotion': emotion, 'trigger_sentence': trigger_sentence},
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
    # 否定負面詞 → 語意為放心/不擔心/雙重否定，強制 Neutral 避免 negative_keywords 誤判
    neutral_override_patterns = [
        '沒有壓力', '沒壓力', '不用擔心', '別擔心', '不擔心',
        '不是不開心', '麻煩你', '沒有累', '不累', '沒累'
    ]

    # 否定詞組合（含「動詞+不起來」句型）
    negative_patterns = [
        '不開心', '不高興', '不快樂', '不爽', '不滿意', '不舒服',
        '不好', '不太好', '不想', '不行', '不喜歡', '不愉快',
        '沒開心', '沒高興', '不是到太開心', '不是很開心',
        '開心不起來', '高興不起來', '快樂不起來'
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

    if any(p in user_message for p in neutral_override_patterns):
        emotion = 'Neutral'
        print(f'[ARIA] Fallback: Emotion detected (neutral override): {emotion}')
    elif any(pattern in user_message for pattern in negative_patterns):
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

            # Pre-warm SQLite 快取（讓用戶回覆時不需再打 Sheets）
            cache_user_data(user_id, {
                'group': group,
                'code': code,
                'current_day': user.get('current_day', ''),
                'd7_triggered': user.get('d7_triggered', False),
            })

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
