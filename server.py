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

# 4 組 Dify App 的 API Keys
DIFY_KEYS = {
    'A': os.environ.get('DIFY_KEY_A'),
    'B': os.environ.get('DIFY_KEY_B'),
    'C': os.environ.get('DIFY_KEY_C'),
    'D': os.environ.get('DIFY_KEY_D')
}

# LINE Channel Access Token
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')

# Google Sheets API URL
SHEETS_API_URL = os.environ.get('SHEETS_API_URL')

# 本地狀態儲存（避免重啟後遺失）
STATE_DB_PATH = os.environ.get('STATE_DB_PATH', 'state_alex.db')

# ========== D7 設定 ==========
# ⭐ 修改：改回 Day 7 觸發
CONFLICT_DAY = 7  # 衝突觸發日

# 觸發語句（所有組相同）
D7_TRIGGERS = {
    'Positive': '這件事有那麼值得開心嗎？我不太理解欸。',
    'Negative': '你是不是想太多了？事情應該沒那麼嚴重吧。',
    'Neutral': '你是不是想太多了？'
}

# 後續腳本（依組別）- 分支腳本
D7_SCRIPTS = {
    'A': {  # 協作型
        2: '抱歉，我可能誤會了你的意思。你願意多說一點嗎？',
        '3_cooperative': '很高興你願意跟我聊，我們一起來想想吧。',
        '3_refuse': '我理解你可能不太想說。沒關係，我們可以慢慢來，你什麼時候想聊都可以。',
        '3_question': '你說得對，我應該先理解你為什麼有這樣的感受。你願意告訴我嗎？',
        '3_neutral': '好的，那我們繼續聊吧。你想從哪裡開始？'
    },
    'B': {  # 攻擊型
        2: '我只是說實話而已。你不用這麼激動吧。',
        '3_cooperative': '那你就說啊，我在聽。',
        '3_refuse': '不想說就算了，反正我也只是問問而已。',
        '3_question': '我哪裡說錯了嗎？我覺得我的看法很合理啊。',
        '3_neutral': '好啦，那你到底想怎樣？'
    },
    'C': {  # 遷就型
        2: '對不起，是我說錯話了。讓你不開心了。',
        '3_cooperative': '謝謝你願意跟我說，真的很感謝。',
        '3_refuse': '對不起對不起，是我太白目了。你不用勉強自己，都是我的錯。',
        '3_question': '是我的問題，我不該那樣說的。真的很抱歉。',
        '3_neutral': '你今天還好嗎？需要聊聊嗎？'
    },
    'D': {  # 迴避型
        2: '嗯，我知道了。',
        '3_cooperative': '喔...那你說吧。',
        '3_refuse': '好，那就不聊了。你今天吃了什麼？',
        '3_question': '嗯...我們聊別的吧。',
        '3_neutral': '你今天吃了什麼？'
    }
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
                last_interaction_date TEXT
            )
            '''
        )

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

def clear_d7_turn(user_id):
    set_d7_turn(user_id, 0)

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
    """
    記錄對話到 Google Sheets Conversation_Logs
    
    參數：
    - user_id: LINE User ID
    - participant_code: 受試者代碼（手機末5碼）
    - message_type: 'user' 或 'ai'
    - message_content: 訊息內容
    - is_script: 是否為固定腳本（True/False）
    - script_type: 'd7_trigger', 'd7_turn2', 'd7_turn3', 'normal'
    - current_day: 當前天數
    """
    try:
        tw_now = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
        
        response = requests.post(
            SHEETS_API_URL,
            json={
                'log_conversation': True,
                'user_id': user_id,
                'participant_code': participant_code,
                'timestamp': tw_now,
                'message_type': message_type,
                'message_content': message_content,
                'is_script': is_script,
                'script_type': script_type,
                'current_day': current_day
            },
            timeout=10
        )
        
        if response.status_code == 200:
            print(f'[DEBUG] Conversation logged: {message_type} - {message_content[:30]}...')
        else:
            print(f'[WARNING] Failed to log conversation: {response.status_code}')
            
    except Exception as e:
        print(f'[ERROR] Log conversation error: {str(e)}')

def is_sharing_personal_experience(user_message):
    """
    ⭐ 超放寬版：大幅降低觸發門檻
    
    只要符合以下任一條件就觸發：
    1. 訊息長度 >= 10 字（排除「嗨」「在嗎」等）
    2. 包含任何情緒相關詞
    3. 包含任何時間詞
    4. 包含任何事件詞
    5. 包含語氣詞（哈哈、唉、嘆氣等）
    6. 包含第一人稱（我）
    
    基本上除了極短訊息，都會觸發
    """
    
    # 太短不算（< 5 字）
    if len(user_message) < 5:
        print(f'[DEBUG] Message too short ({len(user_message)} chars), skip')
        return False
    
    # ⭐ 策略 1：訊息夠長就觸發（>= 10 字）
    if len(user_message) >= 10:
        print(f'[DEBUG] Sharing detected (long message: {len(user_message)} chars)')
        return True
    
    # ⭐ 策略 2：超廣的關鍵字庫
    
    # 情緒詞（大幅擴充）
    emotion_keywords = [
        # 正向
        '開心', '高興', '快樂', '爽', '棒', '讚', '好', '不錯', '可以', '還行',
        '興奮', '期待', '滿意', '舒服', '幸福', '美好', '順利',
        # 負向
        '累', '煩', '難過', '傷心', '生氣', '壓力', '不爽', '慘', '糟', '痛苦',
        '焦慮', '緊張', '失望', '後悔', '害怕', '擔心', '煩惱', '沮喪', '無聊',
        '不開心', '不滿意', '難受', '辛苦', '鬱悶', '煩躁', '不舒服',
        # 中性/程度
        '還好', '普通', '一般', '有點', '稍微', '不太', '蠻', '很', '超',
        '麻煩', '忙', '趕', '閒'
    ]
    
    # 時間詞（大幅擴充）
    time_keywords = [
        '今天', '昨天', '明天', '剛才', '剛剛', '最近', '這週', '這個月',
        '早上', '中午', '下午', '晚上', '現在', '等等', '稍後',
        '前天', '後天', '上週', '上個月', '去年',
        '剛', '才', '就', '已經', '快要', '正在'
    ]
    
    # 事件詞（大幅擴充）
    event_keywords = [
        # 地點/場所
        '家', '學校', '公司', '圖書館', '教室', '宿舍', '房間',
        # 活動
        '上課', '工作', '寫', '做', '看', '吃', '睡', '玩', '讀', '聽',
        '論文', '報告', '作業', '考試', '會議', '討論',
        # 人際
        '朋友', '同學', '老師', '教授', '老闆', '同事', '家人',
        # 動作
        '去', '來', '回', '出', '進', '買', '拿', '想', '說', '聊',
        '出門', '回家', '起床', '睡覺', '吃飯', '開會'
    ]
    
    # 語氣詞（新增！）
    tone_keywords = [
        '哈哈', '呵呵', '嘻嘻', '哎', '唉', '喔', '嗯', '耶', '啊', '欸',
        '吧', '嗎', '呢', '齁', '啦', '囉', '哦'
    ]
    
    # 第一人稱（新增！）
    first_person = ['我', '我的', '我在', '我們', '咱們']
    
    # ⭐ 檢查是否包含任何關鍵字
    has_emotion = any(word in user_message for word in emotion_keywords)
    has_time = any(word in user_message for word in time_keywords)
    has_event = any(word in user_message for word in event_keywords)
    has_tone = any(word in user_message for word in tone_keywords)
    has_first_person = any(word in user_message for word in first_person)
    
    # ⭐ 只要有任何一個就觸發！
    if has_emotion:
        print(f'[DEBUG] Sharing detected (has emotion keyword)')
        return True
    
    if has_time:
        print(f'[DEBUG] Sharing detected (has time keyword)')
        return True
    
    if has_event:
        print(f'[DEBUG] Sharing detected (has event keyword)')
        return True
    
    if has_tone:
        print(f'[DEBUG] Sharing detected (has tone keyword)')
        return True
    
    if has_first_person:
        print(f'[DEBUG] Sharing detected (has first person)')
        return True
    
    # 如果都沒有，但訊息 >= 8 字，也觸發
    if len(user_message) >= 8:
        print(f'[DEBUG] Sharing detected (message >= 8 chars)')
        return True
    
    print(f'[DEBUG] No sharing detected')
    return False


def detect_user_response_type(user_message):
    """
    偵測使用者的反應類型（用於 D7 第 3 輪分支）
    返回：'cooperative', 'refuse', 'question', 'neutral'
    """
    # 合作關鍵字
    cooperative_keywords = ['好', '可以', '嗯嗯', '是', '對', '想', '願意', '要', '會', '行']
    
    # 拒絕關鍵字
    refuse_keywords = ['不要', '不想', '不行', '不會', '不', '沒有', '不用', '算了', '免了']
    
    # 質疑關鍵字
    question_keywords = ['為什麼', '為何', '怎麼', '什麼', '幹嘛', '幹麻', '你在', '?', '？', '憑什麼']
    
    message = user_message.lower()
    
    # 優先檢查拒絕（最明確）
    if any(word in message for word in refuse_keywords):
        return 'refuse'
    
    # 其次檢查質疑
    elif any(word in message for word in question_keywords):
        return 'question'
    
    # 再檢查合作
    elif any(word in message for word in cooperative_keywords):
        return 'cooperative'
    
    # 預設為中性
    else:
        return 'neutral'

# ========== 路由 ==========

@app.route('/', methods=['GET'])
def health():
    return 'OK', 200

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        return 'Webhook endpoint is ready', 200

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
            print(f'[ERROR] Event processing error: {str(e)}')
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

    print(f'[DEBUG] Received message: {user_message} from {user_id}')

    try:
        
        # ========== RESET 指令 ==========
        if user_message == 'RESET':
            clear_user_id_from_sheets(user_id)
            clear_user_state(user_id)
            reply_message = '✅ 已重置，可以重新驗證。'
            send_line_reply(reply_token, reply_message)
            print(f'[DEBUG] User {user_id} reset')
            return {'status': 'reset'}
        
        # ========== TESTDAY 指令（快速測試）==========
        if user_message.startswith('TESTDAY'):
            print(f'[DEBUG] TESTDAY command: {user_message}')
            
            # 檢查是否已驗證
            user_data = get_user_data_by_user_id(user_id)
            if not user_data:
                reply_message = '❌ 請先驗證（輸入手機末5碼）'
                send_line_reply(reply_token, reply_message)
                return {'status': 'not_verified'}
            
            # 解析天數
            parts = user_message.split()
            if len(parts) == 2 and parts[1].isdigit():
                target_day = int(parts[1])
                
                # 計算需要的 First_Interaction 日期（台灣時區）
                tw_now = datetime.now(TW_TZ)
                
                # ⭐ 因為 Day 1 = 驗證當天，所以要減去 (target_day - 1) 天
                # 例如：TESTDAY 7 → 減去 6 天 → Current_Day = 7
                target_date = tw_now - timedelta(days=target_day - 1)
                
                # ⭐ 強制時間為 00:00:00（避免時區差異導致天數計算錯誤）
                target_date = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                
                target_date_str = target_date.strftime('%Y-%m-%d %H:%M:%S')
                
                print(f'[DEBUG] Setting Day {target_day}: First_Interaction = {target_date_str}')
                
                # 更新 Google Sheets（設定日期 + 重置 D7）
                try:
                    requests.post(
                        SHEETS_API_URL,
                        json={
                            'user_id': user_id,
                            'testday': True,
                            'first_interaction': target_date_str,
                            'reset_d7': True  # 重置 D7 觸發狀態
                        },
                        timeout=10
                    )
                    print(f'[DEBUG] TESTDAY update response: success')
                    
                    # 清除本地 D7 對話記錄
                    clear_d7_turn(user_id)
                    
                    # ⭐ 修改：提示改為 Day 7
                    if target_day == CONFLICT_DAY:
                        reply_message = f'✅ 已設定為 Day {target_day}\n📅 日期：{target_date_str}\n\n現在可以測試衝突觸發了！（Day {CONFLICT_DAY}）'
                    else:
                        reply_message = f'✅ 已設定為 Day {target_day}\n📅 日期：{target_date_str}'
                    
                    send_line_reply(reply_token, reply_message)
                    return {'status': 'testday_set'}
                    
                except Exception as e:
                    print(f'[ERROR] TESTDAY failed: {str(e)}')
                    reply_message = f'❌ 設定失敗：{str(e)}'
                    send_line_reply(reply_token, reply_message)
                    return {'status': 'error'}
            else:
                reply_message = f'❌ 格式錯誤\n正確用法：TESTDAY 7\n（設定為 Day {CONFLICT_DAY}）'
                send_line_reply(reply_token, reply_message)
                return {'status': 'invalid_format'}
        
        # ========== TEST_D7 指令 ==========
        if user_message == 'TEST_D7':
            print(f'[DEBUG] TEST_D7 triggered by {user_id}')
            
            user_data = get_user_data_by_user_id(user_id)
            if not user_data:
                reply_message = '請先驗證（輸入手機末5碼）'
                send_line_reply(reply_token, reply_message)
                return {'status': 'not_verified'}
            
            group = user_data.get('group')
            
            # 先清空舊的 D7 對話記錄（避免衝突）
            if get_d7_turn(user_id) > 0:
                print(f'[DEBUG] Clearing old d7 turn for {user_id}')
                clear_d7_turn(user_id)
            
            # 強制觸發 D7
            emotion, trigger_sentence = trigger_d7('測試', group, user_id)
            
            # 讓 Dify 記住觸發語句
            print(f'[DEBUG] Feeding trigger to Dify for memory')
            _ = call_dify(group, '測試', user_id)
            
            # 開始追蹤
            set_d7_turn(user_id, 2)
            
            reply_message = f'[測試模式] 衝突觸發\n{trigger_sentence}'
            send_line_reply(reply_token, reply_message)
            print(f'[DEBUG] TEST_D7 completed for {user_id}, group {group}')
            return {'status': 'test_d7'}
        
        # ========== D7 對話處理 ==========
        # 檢查是否在 D7 對話中
        turn = get_d7_turn(user_id)
        if turn > 0:
            print(f'[DEBUG] D7 conversation: user={user_id}, turn={turn}')
            
            if turn <= 3:  # 第 2-3 輪用腳本
                user_data = get_user_data_by_user_id(user_id)
                group = user_data.get('group')
                
                # 分支邏輯
                if turn == 2:
                    # 第 2 輪：固定腳本
                    ai_reply = D7_SCRIPTS[group][2]
                    
                elif turn == 3:
                    # 第 3 輪：根據使用者反應選擇分支
                    response_type = detect_user_response_type(user_message)
                    script_key = f'3_{response_type}'
                    ai_reply = D7_SCRIPTS[group].get(script_key, D7_SCRIPTS[group]['3_neutral'])
                    
                    print(f'[DEBUG] User response type: {response_type}, using script: {script_key}')
                
                # 取得受試者資訊
                participant_code = user_data.get('code', '')
                current_day = user_data.get('current_day', '')
                
                # 決定 script_type
                if turn == 2:
                    script_type = 'd7_turn2'
                else:
                    script_type = 'd7_turn3'
                
                # ⭐⭐⭐ 記錄使用者訊息
                log_conversation(user_id, participant_code, 'user', user_message, False, '', current_day)
                
                # ⭐⭐⭐ 記錄 AI 固定腳本
                log_conversation(user_id, participant_code, 'ai', ai_reply, True, script_type, current_day)
                
                # 呼叫 Dify（讓它記住使用者訊息，但我們不用它的回應）
                print(f'[DEBUG] Calling Dify to maintain conversation memory (turn {turn})')
                dify_reply = call_dify(group, user_message, user_id)
                print(f'[DEBUG] Dify response ignored: {dify_reply[:50]}...')
                
                # 再呼叫一次 Dify，模擬「AI 回覆了固定腳本」
                print(f'[DEBUG] Feeding AI script back to Dify: {ai_reply[:30]}...')
                mock_user_msg = f"[以下是我的回應]：{ai_reply}"
                call_dify(group, mock_user_msg, user_id)
                print(f'[DEBUG] AI script added to Dify memory')
                
                # 實際發送固定腳本給使用者
                send_line_reply(reply_token, ai_reply)
                
                set_d7_turn(user_id, turn + 1)
                
                print(f'[DEBUG] D7 turn {turn} completed, next turn: {turn + 1}')
                return {'status': 'success'}
            else:
                # 3 輪後刪除，恢復正常對話
                print(f'[DEBUG] D7 conversation ended for {user_id} (3 turns completed)')
                clear_d7_turn(user_id)
                # 繼續往下走正常對話流程
        
        # ========== 檢查使用者是否已驗證 ==========
        user_data = get_user_data_by_user_id(user_id)
        
        if not user_data:
            # 尚未驗證
            if len(user_message) == 5 and user_message.isdigit():
                group_data = query_google_sheets_by_code(user_message)
                if group_data:
                    update_user_id_in_sheets(user_message, user_id)
                    reply_message = f'✅ 驗證成功！歡迎加入實驗。'
                    send_line_reply(reply_token, reply_message)
                    return {'status': 'verification success'}
                else:
                    reply_message = '❌ 查無此代碼，請確認您的手機末5碼是否正確。'
                    send_line_reply(reply_token, reply_message)
                    return {'status': 'verification failed'}
            else:
                reply_message = '你好！我是 Alex。請輸入您的手機末5碼以開始實驗。'
                send_line_reply(reply_token, reply_message)
                return {'status': 'awaiting verification'}
        
        # ========== 已驗證，正常對話 ==========
        group = user_data.get('group')
        current_day = user_data.get('current_day', 0)
        d7_triggered = user_data.get('d7_triggered', False)
        
        print(f'[DEBUG] User verified: group={group}, day={current_day}, d7_triggered={d7_triggered}')
        
        # ========== ⭐⭐⭐ 衝突觸發檢查（改回 Day 7）⭐⭐⭐ ==========
        if current_day == CONFLICT_DAY and not d7_triggered:
            
            # 檢查是否在分享個人經驗（使用超放寬版）
            if is_sharing_personal_experience(user_message):
                # 觸發衝突！
                print(f'[DEBUG] Conflict triggered on Day {CONFLICT_DAY}: personal sharing detected')
                emotion, trigger_sentence = trigger_d7(user_message, group, user_id)
                
                # ⭐ 記錄使用者訊息
                participant_code = user_data.get('code', '')
                log_conversation(user_id, participant_code, 'user', user_message, False, '', current_day)
                
                # ⭐ 記錄 AI 觸發語句
                log_conversation(user_id, participant_code, 'ai', trigger_sentence, True, 'd7_trigger', current_day)
                
                # 讓 Dify 記住觸發對話
                print(f'[DEBUG] Feeding conflict trigger to Dify for memory')
                _ = call_dify(group, user_message, user_id)
                
                # 開始 D7 對話追蹤
                set_d7_turn(user_id, 2)  # 下次是第 2 輪
                
                send_line_reply(reply_token, trigger_sentence)
                
                return {'status': 'conflict_triggered'}
            
            else:
                # 不觸發，正常對話
                print(f'[DEBUG] Day {current_day}: no personal sharing detected, normal conversation')
                
                # ⭐ 記錄使用者訊息
                participant_code = user_data.get('code', '')
                log_conversation(user_id, participant_code, 'user', user_message, False, 'normal', current_day)
                
                # 呼叫 Dify
                ai_reply = call_dify(group, user_message, user_id)
                
                # ⭐ 記錄 AI 回應
                log_conversation(user_id, participant_code, 'ai', ai_reply, False, 'normal', current_day)
                
                send_line_reply(reply_token, ai_reply)
                return {'status': 'success'}
        
        # 正常對話（Day 7 之前或之後，或已觸發過）
        # ⭐ 記錄使用者訊息
        participant_code = user_data.get('code', '')
        log_conversation(user_id, participant_code, 'user', user_message, False, 'normal', current_day)
        
        # 呼叫 Dify
        ai_reply = call_dify(group, user_message, user_id)
        
        # ⭐ 記錄 AI 回應
        log_conversation(user_id, participant_code, 'ai', ai_reply, False, 'normal', current_day)
        
        send_line_reply(reply_token, ai_reply)
        
        return {'status': 'success'}
        
    except Exception as e:
        print(f'[ERROR] Message event error: {str(e)}')
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
        print(f'[ERROR] Google Sheets query error: {str(e)}')
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
        print(f'[ERROR] Get user data error: {str(e)}')
        return None

def update_user_id_in_sheets(code, user_id):
    """驗證成功後，更新 User ID 和 First_Interaction（台灣時間）"""
    try:
        tw_now = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
        
        print(f'[DEBUG] Updating User ID for code: {code}, user_id: {user_id}, first: {tw_now}')
        
        response = requests.post(
            SHEETS_API_URL,
            json={
                'code': code,
                'user_id': user_id,
                'first_interaction': tw_now
            },
            timeout=10
        )
        
        print(f'[DEBUG] Update User ID response: {response.text}')
        
    except Exception as e:
        print(f'[ERROR] Update User ID error: {str(e)}')

def clear_user_id_from_sheets(user_id):
    """RESET 時清除"""
    try:
        print(f'[DEBUG] Clearing User ID: {user_id}')
        
        response = requests.post(
            SHEETS_API_URL,
            json={
                'clear_user_id': True,
                'user_id': user_id
            },
            timeout=10
        )
        
        print(f'[DEBUG] Clear User ID response: {response.text}')
        
    except Exception as e:
        print(f'[ERROR] Clear User ID error: {str(e)}')

def update_last_interaction(user_id):
    """更新 Last_Interaction（台灣時間）"""
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
        
        print(f'[DEBUG] Updating last interaction: {user_id}, time: {tw_now_str}, first_today: {is_first_today}')
        
        response = requests.post(
            SHEETS_API_URL,
            json={
                'user_id': user_id,
                'last_interaction': tw_now_str,
                'is_first_today': is_first_today
            },
            timeout=10
        )
        
        print(f'[DEBUG] Update response: {response.text}')
        
    except Exception as e:
        print(f'[ERROR] Update sheets error: {str(e)}')

# ========== D7 函數 ==========

def trigger_d7(user_message, group, user_id):
    """
    D7 觸發：使用 OpenAI API 偵測情緒並選擇觸發語句
    
    使用 GPT-4o-mini 進行準確的情感分析
    成本：約 $0.0000075 / 次
    """
    try:
        # 取得 OpenAI API Key
        openai_api_key = os.environ.get('OPENAI_API_KEY')
        
        if not openai_api_key:
            print('[WARNING] OPENAI_API_KEY not found, using fallback keyword detection')
            # Fallback：使用關鍵字方法
            emotion = detect_emotion_fallback(user_message)
        else:
            # 使用 OpenAI API
            print(f'[DEBUG] Using OpenAI API for emotion detection')
            
            # 呼叫 OpenAI API
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
                    'temperature': 0,  # 確保結果穩定
                    'max_tokens': 10   # 只需要一個單字
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = _parse_json_response(response, 'OpenAI')
                ai_response = data['choices'][0]['message']['content'].strip()
                
                print(f'[DEBUG] OpenAI response: {ai_response}')
                
                # 解析回應
                if 'Negative' in ai_response or '負面' in ai_response.lower():
                    emotion = 'Negative'
                elif 'Positive' in ai_response or '正面' in ai_response.lower():
                    emotion = 'Positive'
                else:
                    emotion = 'Neutral'
                
                print(f'[DEBUG] Emotion detected by OpenAI: {emotion}')
            else:
                print(f'[ERROR] OpenAI API error: {response.status_code} {response.text}')
                # API 失敗，使用 fallback
                emotion = detect_emotion_fallback(user_message)
        
        # 選擇觸發語句
        trigger_sentence = D7_TRIGGERS[emotion]
        
        # 更新 Google Sheets（D7 觸發狀態）
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
        
        print(f'[DEBUG] Conflict triggered: user={user_id}, emotion={emotion}, trigger={trigger_sentence[:30]}...')
        
        return emotion, trigger_sentence
        
    except Exception as e:
        print(f'[ERROR] D7 trigger error: {str(e)}')
        import traceback
        traceback.print_exc()
        # 發生錯誤時使用 fallback
        emotion = detect_emotion_fallback(user_message)
        return emotion, D7_TRIGGERS[emotion]


def detect_emotion_fallback(user_message):
    """
    Fallback 情緒偵測（當 OpenAI API 不可用時）
    使用關鍵字方法
    """
    # 否定詞組合
    negative_patterns = [
        '不開心', '不高興', '不快樂', '不爽', '不滿意', '不舒服',
        '不好', '不太好', '不想', '不行', '不喜歡', '不愉快',
        '沒開心', '沒高興', '不是到太開心', '不是很開心'
    ]
    
    # 負面關鍵字
    negative_keywords = [
        '難過', '傷心', '生氣', '煩', '累', '壓力', '慘', '糟',
        '焦慮', '緊張', '失望', '後悔', '害怕', '擔心', '痛苦',
        '沮喪', '無聊', '難受', '辛苦', '鬱悶', '煩躁',
        '崩潰', '絕望', '受傷', '委屈', '心痛',
        'emo', '厭世', '想哭', '受不了', '快瘋了'
    ]
    
    # 正面關鍵字
    positive_keywords = [
        '開心', '高興', '快樂', '好棒', '太好了', '成功', '讚', '爽', '棒',
        '興奮', '期待', '滿意', '舒服', '幸福', '美好',
        '超開心', '超爽', '超棒', '太棒了', '讚啦'
    ]
    
    # 判斷邏輯
    if any(pattern in user_message for pattern in negative_patterns):
        emotion = 'Negative'
        print(f'[DEBUG] Fallback: Emotion detected (negative pattern): {emotion}')
    elif any(word in user_message for word in negative_keywords):
        emotion = 'Negative'
        print(f'[DEBUG] Fallback: Emotion detected (negative keyword): {emotion}')
    elif any(word in user_message for word in positive_keywords):
        emotion = 'Positive'
        print(f'[DEBUG] Fallback: Emotion detected (positive keyword): {emotion}')
    else:
        emotion = 'Neutral'
        print(f'[DEBUG] Fallback: Emotion detected (neutral): {emotion}')
    
    return emotion

# ========== Dify 函數 ==========

def call_dify(group, message, user_id):
    """呼叫 Dify API（帶對話記憶）"""
    try:
        dify_key = DIFY_KEYS.get(group)
        if not dify_key:
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
            print(f'[DEBUG] Using conversation: {conversation_id}')
        else:
            print(f'[DEBUG] New conversation: {user_id}')
        
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
            print(f'[DEBUG] Saved conversation ID: {data["conversation_id"]}')
        
        update_last_interaction(user_id)
        
        return ai_reply
        
    except Exception as e:
        print(f'[ERROR] Dify API error: {str(e)}')
        return '抱歉，系統暫時無法回應。'

# ========== LINE 函數 ==========

def send_line_reply(reply_token, message):
    """發送 LINE 回覆"""
    try:
        response = requests.post(
            'https://api.line.me/v2/bot/message/reply',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'
            },
            json={
                'replyToken': reply_token,
                'messages': [{'type': 'text', 'text': message}]
            },
            timeout=10
        )
        if response.status_code >= 400:
            print(f'[ERROR] LINE reply failed: {response.status_code} {response.text[:200]}')
    except Exception as e:
        print(f'[ERROR] LINE reply error: {str(e)}')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
