from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# 設定台灣時區
TW_TZ = pytz.timezone('Asia/Taipei')

# Dify API 設定
DIFY_API_URL = 'https://api.dify.ai/v1/chat-messages'

# Aria Bot 只處理 E/F/G/H 四組（女性版）
DIFY_KEYS = {
    'E': os.environ.get('DIFY_KEY_E'),  # 女性協作型
    'F': os.environ.get('DIFY_KEY_F'),  # 女性攻擊型
    'G': os.environ.get('DIFY_KEY_G'),  # 女性遷就型
    'H': os.environ.get('DIFY_KEY_H'),  # 女性迴避型
}

# D7 腳本映射（E→A, F→B, G→C, H→D，因為人格相同）
D7_GROUP_MAPPING = {
    'E': 'A',  # 女性協作 → 用 A 的腳本
    'F': 'B',  # 女性攻擊 → 用 B 的腳本
    'G': 'C',  # 女性遷就 → 用 C 的腳本
    'H': 'D',  # 女性迴避 → 用 D 的腳本
}

# LINE Channel Access Token（Aria）
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN_ARIA')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET_ARIA')

# Google Sheets API URL
SHEETS_API_URL = os.environ.get('SHEETS_API_URL')

# 儲存對話 ID（對話記憶）
user_conversations = {}

# 儲存今天已互動的使用者
today_interacted = set()
last_date_check = datetime.now(TW_TZ).date()

# ========== D7 設定 ==========

# 觸發語句（所有組相同）
D7_TRIGGERS = {
    'Positive': '這件事有那麼值得開心嗎？我不太理解欸。',
    'Negative': '你是不是想太多了？事情應該沒那麼嚴重吧。',
    'Neutral': '你是不是想太多了？'
}

# D7 腳本（A/B/C/D 四組，E/F/G/H 映射使用）
D7_SCRIPTS = {
    'A': {  # 協作型（E 也用這個）
        2: '抱歉，我可能誤會了你的意思。你願意多說一點嗎？',
        '3_cooperative': '很高興你願意跟我聊，我們一起來想想吧。',
        '3_refuse': '我理解你可能不太想說。沒關係，我們可以慢慢來，你什麼時候想聊都可以。',
        '3_question': '你說得對，我應該先理解你為什麼有這樣的感受。你願意告訴我嗎？',
        '3_neutral': '好的，那我們繼續聊吧。你想從哪裡開始？'
    },
    'B': {  # 攻擊型（F 也用這個）
        2: '我只是說實話而已。你不用這麼激動吧。',
        '3_cooperative': '那你就說啊，我在聽。',
        '3_refuse': '不想說就算了，反正我也只是問問而已。',
        '3_question': '我哪裡說錯了嗎？我覺得我的看法很合理啊。',
        '3_neutral': '好啦，那你到底想怎樣？'
    },
    'C': {  # 遷就型（G 也用這個）
        2: '對不起，是我說錯話了。讓你不開心了。',
        '3_cooperative': '謝謝你願意跟我說，真的很感謝。',
        '3_refuse': '對不起對不起，是我太白目了。你不用勉強自己，都是我的錯。',
        '3_question': '是我的問題，我不該那樣說的。真的很抱歉。',
        '3_neutral': '你今天還好嗎？需要聊聊嗎？'
    },
    'D': {  # 迴避型（H 也用這個）
        2: '嗯，我知道了。',
        '3_cooperative': '喔...那你說吧。',
        '3_refuse': '好，那就不聊了。你今天吃了什麼？',
        '3_question': '嗯...我們聊別的吧。',
        '3_neutral': '你今天吃了什麼？'
    }
}

# 追蹤 D7 對話輪數
d7_conversations = {}  # {user_id: turn_count}

# ========== 輔助函數 ==========

def log_conversation(user_id, participant_code, message_type, message_content, is_script=False, script_type='', current_day=None):
    """記錄對話到 Google Sheets Conversation_Logs"""
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
            print(f'[ARIA] Conversation logged: {message_type} - {message_content[:30]}...')
        else:
            print(f'[ARIA WARNING] Failed to log conversation: {response.status_code}')
            
    except Exception as e:
        print(f'[ARIA ERROR] Log conversation error: {str(e)}')

def is_sharing_personal_experience(user_message):
    """偵測使用者是否在分享個人經驗或情緒"""
    
    if len(user_message) < 5:
        return False
    
    emotion_keywords = [
        '開心', '高興', '快樂', '爽', '棒', '讚', '興奮', '期待', '滿意', '舒服',
        '難過', '傷心', '生氣', '煩', '累', '壓力', '不爽', '慘', '糟', '痛苦',
        '焦慮', '緊張', '失望', '後悔', '害怕', '擔心', '煩惱', '沮喪', '無聊',
        '不開心', '不滿意', '難受', '辛苦', '鬱悶', '煩躁', '不舒服'
    ]
    
    strong_emotion_keywords = [
        '好累', '超累', '很累', '累死', '累爆',
        '好煩', '超煩', '很煩', '煩死',
        '好開心', '超開心', '很開心',
        '好難過', '超難過', '很難過',
        '不開心', '不爽', '難受', '痛苦', '辛苦',
        '好慘', '好糟', '太累', '太煩'
    ]
    
    event_keywords = [
        '今天', '昨天', '剛才', '最近', '這週', '這個月', '早上', '下午', '晚上', '剛剛',
        '發生', '遇到', '碰到', '經歷', '覺得', '感覺', '想到', '遇見',
        '跟', '和', '被', '給', '讓', '朋友', '家人', '同事', '老闆', '教授', '老師',
        '上課', '工作', '學校', '公司', '論文', '報告', '考試'
    ]
    
    has_i = '我' in user_message
    has_emotion = any(word in user_message for word in emotion_keywords)
    has_event = any(word in user_message for word in event_keywords)
    has_strong_emotion = any(word in user_message for word in strong_emotion_keywords)
    
    if has_i and (has_emotion or has_event):
        print(f'[ARIA DEBUG] Sharing detected (Type 1): has_i=True, emotion={has_emotion}, event={has_event}')
        return True
    
    if has_strong_emotion:
        print(f'[ARIA DEBUG] Sharing detected (Type 2): strong_emotion={has_strong_emotion}')
        return True
    
    if has_emotion and has_event:
        print(f'[ARIA DEBUG] Sharing detected (Type 3): emotion + event')
        return True
    
    print(f'[ARIA DEBUG] No sharing detected')
    return False

def detect_user_response_type(user_message):
    """偵測使用者的反應類型（用於 D7 第 3 輪分支）"""
    cooperative_keywords = ['好', '可以', '嗯嗯', '是', '對', '想', '願意', '要', '會', '行']
    refuse_keywords = ['不要', '不想', '不行', '不會', '不', '沒有', '不用', '算了', '免了']
    question_keywords = ['為什麼', '為何', '怎麼', '什麼', '幹嘛', '幹麻', '你在', '?', '？', '憑什麼']
    
    message = user_message.lower()
    
    if any(word in message for word in refuse_keywords):
        return 'refuse'
    elif any(word in message for word in question_keywords):
        return 'question'
    elif any(word in message for word in cooperative_keywords):
        return 'cooperative'
    else:
        return 'neutral'

# ========== 路由 ==========

@app.route('/', methods=['GET'])
def health():
    return 'Aria Bot Server is running!', 200

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        return 'Aria Webhook endpoint is ready', 200
    
    try:
        data = request.json
        events = data.get('events', [])
        
        if not events:
            return jsonify({'status': 'no events'}), 200
        
        event = events[0]
        
        if event['type'] != 'message' or event['message']['type'] != 'text':
            return jsonify({'status': 'ignored'}), 200
        
        user_message = event['message']['text'].strip()
        reply_token = event['replyToken']
        user_id = event['source']['userId']
        
        print(f'[ARIA] Received: {user_message} from {user_id}')
        
        # ========== RESET 指令 ==========
        if user_message == 'RESET':
            clear_user_id_from_sheets(user_id)
            if user_id in user_conversations:
                del user_conversations[user_id]
            if user_id in today_interacted:
                today_interacted.remove(user_id)
            if user_id in d7_conversations:
                del d7_conversations[user_id]
            reply_message = '✅ 已重置，可以重新驗證。'
            send_line_reply(reply_token, reply_message)
            print(f'[ARIA] User {user_id} reset')
            return jsonify({'status': 'reset'}), 200
        
        # ========== TESTDAY 指令 ==========
        if user_message.startswith('TESTDAY'):
            print(f'[ARIA] TESTDAY command: {user_message}')
            
            user_data = get_user_data_by_user_id(user_id)
            if not user_data:
                reply_message = '❌ 請先驗證（輸入手機末5碼）'
                send_line_reply(reply_token, reply_message)
                return jsonify({'status': 'not_verified'}), 200
            
            parts = user_message.split()
            if len(parts) == 2 and parts[1].isdigit():
                target_day = int(parts[1])
                tw_now = datetime.now(TW_TZ)
                target_date = tw_now - timedelta(days=target_day - 1)
                target_date = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                target_date_str = target_date.strftime('%Y-%m-%d %H:%M:%S')
                
                print(f'[ARIA] Setting Day {target_day}: {target_date_str}')
                
                try:
                    response = requests.post(
                        SHEETS_API_URL,
                        json={
                            'user_id': user_id,
                            'testday': True,
                            'first_interaction': target_date_str,
                            'reset_d7': True
                        },
                        timeout=10
                    )
                    
                    if user_id in d7_conversations:
                        del d7_conversations[user_id]
                    
                    if target_day == 7:
                        reply_message = f'✅ 已設定為 Day {target_day}\n📅 {target_date_str}\n\n現在可以測試 D7 觸發了！'
                    else:
                        reply_message = f'✅ 已設定為 Day {target_day}\n📅 {target_date_str}'
                    
                    send_line_reply(reply_token, reply_message)
                    return jsonify({'status': 'testday_set'}), 200
                    
                except Exception as e:
                    print(f'[ARIA ERROR] TESTDAY failed: {str(e)}')
                    reply_message = f'❌ 設定失敗：{str(e)}'
                    send_line_reply(reply_token, reply_message)
                    return jsonify({'status': 'error'}), 500
            else:
                reply_message = '❌ 格式錯誤\n正確用法：TESTDAY 7'
                send_line_reply(reply_token, reply_message)
                return jsonify({'status': 'invalid_format'}), 200
        
        # ========== TEST_D7 指令 ==========
        if user_message == 'TEST_D7':
            print(f'[ARIA] TEST_D7 triggered')
            
            user_data = get_user_data_by_user_id(user_id)
            if not user_data:
                reply_message = '請先驗證（輸入手機末5碼）'
                send_line_reply(reply_token, reply_message)
                return jsonify({'status': 'not_verified'}), 200
            
            group = user_data.get('group')
            
            if user_id in d7_conversations:
                del d7_conversations[user_id]
            
            emotion, trigger_sentence = trigger_d7('測試', group, user_id)
            _ = call_dify(group, '測試', user_id)
            d7_conversations[user_id] = 2
            
            reply_message = f'[測試模式] D7 觸發\n{trigger_sentence}'
            send_line_reply(reply_token, reply_message)
            return jsonify({'status': 'test_d7'}), 200
        
        # ========== D7 對話處理 ==========
        if user_id in d7_conversations:
            turn = d7_conversations[user_id]
            print(f'[ARIA] D7 conversation: turn={turn}')
            
            if turn <= 3:
                user_data = get_user_data_by_user_id(user_id)
                group = user_data.get('group')
                
                # ⭐ 映射到實際的腳本組別（E→A, F→B, G→C, H→D）
                script_group = D7_GROUP_MAPPING.get(group, 'A')
                print(f'[ARIA] Group {group} mapped to script group {script_group}')
                
                if turn == 2:
                    ai_reply = D7_SCRIPTS[script_group][2]
                elif turn == 3:
                    response_type = detect_user_response_type(user_message)
                    script_key = f'3_{response_type}'
                    ai_reply = D7_SCRIPTS[script_group].get(script_key, D7_SCRIPTS[script_group]['3_neutral'])
                    print(f'[ARIA] Response type: {response_type}')
                
                participant_code = user_data.get('code', '')
                current_day = user_data.get('current_day', '')
                script_type = 'd7_turn2' if turn == 2 else 'd7_turn3'
                
                log_conversation(user_id, participant_code, 'user', user_message, False, '', current_day)
                log_conversation(user_id, participant_code, 'ai', ai_reply, True, script_type, current_day)
                
                dify_reply = call_dify(group, user_message, user_id)
                mock_user_msg = f"[以下是我的回應]：{ai_reply}"
                call_dify(group, mock_user_msg, user_id)
                
                send_line_reply(reply_token, ai_reply)
                d7_conversations[user_id] += 1
                
                return jsonify({'status': 'success'}), 200
            else:
                print(f'[ARIA] D7 conversation ended')
                del d7_conversations[user_id]
        
        # ========== 檢查使用者是否已驗證 ==========
        user_data = get_user_data_by_user_id(user_id)
        
        if not user_data:
            if len(user_message) == 5 and user_message.isdigit():
                group_data = query_google_sheets_by_code(user_message)
                if group_data:
                    # 檢查是否為 E/F/G/H 組
                    assigned_group = group_data.get('group', '')
                    if assigned_group not in ['E', 'F', 'G', 'H']:
                        reply_message = '❌ 此代碼不適用於此 Bot，請確認您加入的是正確的 AI 伴侶。'
                        send_line_reply(reply_token, reply_message)
                        return jsonify({'status': 'wrong_bot'}), 200
                    
                    update_user_id_in_sheets(user_message, user_id)
                    reply_message = f'✅ 驗證成功！歡迎加入實驗。'
                    send_line_reply(reply_token, reply_message)
                    return jsonify({'status': 'verification success'}), 200
                else:
                    reply_message = '❌ 查無此代碼，請確認您的手機末5碼是否正確。'
                    send_line_reply(reply_token, reply_message)
                    return jsonify({'status': 'verification failed'}), 200
            else:
                reply_message = '你好！我是 Aria。請輸入您的手機末5碼以開始實驗。'
                send_line_reply(reply_token, reply_message)
                return jsonify({'status': 'awaiting verification'}), 200
        
        # ========== 已驗證，正常對話 ==========
        group = user_data.get('group')
        current_day = user_data.get('current_day', 0)
        d7_triggered = user_data.get('d7_triggered', False)
        
        print(f'[ARIA] User verified: group={group}, day={current_day}, d7={d7_triggered}')
        
        # ========== D7 觸發檢查 ==========
        if current_day == 7 and not d7_triggered:
            if is_sharing_personal_experience(user_message):
                print(f'[ARIA] D7 triggered!')
                emotion, trigger_sentence = trigger_d7(user_message, group, user_id)
                
                participant_code = user_data.get('code', '')
                log_conversation(user_id, participant_code, 'user', user_message, False, '', current_day)
                log_conversation(user_id, participant_code, 'ai', trigger_sentence, True, 'd7_trigger', current_day)
                
                _ = call_dify(group, user_message, user_id)
                d7_conversations[user_id] = 2
                
                send_line_reply(reply_token, trigger_sentence)
                return jsonify({'status': 'd7_triggered'}), 200
            else:
                print(f'[ARIA] Day 7: normal conversation')
                participant_code = user_data.get('code', '')
                log_conversation(user_id, participant_code, 'user', user_message, False, 'normal', current_day)
                
                ai_reply = call_dify(group, user_message, user_id)
                log_conversation(user_id, participant_code, 'ai', ai_reply, False, 'normal', current_day)
                
                send_line_reply(reply_token, ai_reply)
                return jsonify({'status': 'success'}), 200
        
        # 正常對話
        participant_code = user_data.get('code', '')
        log_conversation(user_id, participant_code, 'user', user_message, False, 'normal', current_day)
        
        ai_reply = call_dify(group, user_message, user_id)
        log_conversation(user_id, participant_code, 'ai', ai_reply, False, 'normal', current_day)
        
        send_line_reply(reply_token, ai_reply)
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        print(f'[ARIA ERROR] Webhook error: {str(e)}')
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ========== Google Sheets 函數 ==========

def query_google_sheets_by_code(code):
    try:
        response = requests.get(f'{SHEETS_API_URL}?code={code}', timeout=10)
        data = response.json()
        if data.get('found'):
            return data
        return None
    except Exception as e:
        print(f'[ARIA ERROR] Query error: {str(e)}')
        return None

def get_user_data_by_user_id(user_id):
    try:
        response = requests.get(f'{SHEETS_API_URL}?user_id={user_id}', timeout=10)
        data = response.json()
        if data.get('found'):
            return data
        return None
    except Exception as e:
        print(f'[ARIA ERROR] Get user data error: {str(e)}')
        return None

def update_user_id_in_sheets(code, user_id):
    try:
        tw_now = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
        response = requests.post(
            SHEETS_API_URL,
            json={'code': code, 'user_id': user_id, 'first_interaction': tw_now},
            timeout=10
        )
    except Exception as e:
        print(f'[ARIA ERROR] Update error: {str(e)}')

def clear_user_id_from_sheets(user_id):
    try:
        response = requests.post(
            SHEETS_API_URL,
            json={'clear_user_id': True, 'user_id': user_id},
            timeout=10
        )
    except Exception as e:
        print(f'[ARIA ERROR] Clear error: {str(e)}')

def update_last_interaction(user_id):
    try:
        global today_interacted, last_date_check
        tw_now = datetime.now(TW_TZ)
        current_date = tw_now.date()
        
        if current_date != last_date_check:
            today_interacted.clear()
            last_date_check = current_date
        
        is_first_today = user_id not in today_interacted
        if is_first_today:
            today_interacted.add(user_id)
        
        tw_now_str = tw_now.strftime('%Y-%m-%d %H:%M:%S')
        
        response = requests.post(
            SHEETS_API_URL,
            json={'user_id': user_id, 'last_interaction': tw_now_str, 'is_first_today': is_first_today},
            timeout=10
        )
    except Exception as e:
        print(f'[ARIA ERROR] Update last interaction error: {str(e)}')

# ========== D7 函數 ==========

def trigger_d7(user_message, group, user_id):
    try:
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
                        {'role': 'system', 'content': '你是情感分析專家。請判斷使用者訊息的情緒，只回答一個英文單字：Positive、Negative 或 Neutral。'},
                        {'role': 'user', 'content': f'使用者說：「{user_message}」\n\n這句話的情緒是？'}
                    ],
                    'temperature': 0,
                    'max_tokens': 10
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                ai_response = data['choices'][0]['message']['content'].strip()
                
                if 'Negative' in ai_response:
                    emotion = 'Negative'
                elif 'Positive' in ai_response:
                    emotion = 'Positive'
                else:
                    emotion = 'Neutral'
            else:
                emotion = detect_emotion_fallback(user_message)
        
        trigger_sentence = D7_TRIGGERS[emotion]
        
        requests.post(
            SHEETS_API_URL,
            json={'user_id': user_id, 'd7_trigger': True, 'emotion': emotion, 'trigger_sentence': trigger_sentence},
            timeout=10
        )
        
        print(f'[ARIA] D7 triggered: emotion={emotion}')
        return emotion, trigger_sentence
        
    except Exception as e:
        print(f'[ARIA ERROR] D7 trigger error: {str(e)}')
        emotion = detect_emotion_fallback(user_message)
        return emotion, D7_TRIGGERS[emotion]

def detect_emotion_fallback(user_message):
    negative_patterns = ['不開心', '不高興', '不快樂', '不爽', '不滿意', '不舒服']
    negative_keywords = ['難過', '傷心', '生氣', '煩', '累', '壓力', '慘', '糟']
    positive_keywords = ['開心', '高興', '快樂', '好棒', '太好了', '成功', '讚', '爽', '棒']
    
    if any(pattern in user_message for pattern in negative_patterns):
        return 'Negative'
    elif any(word in user_message for word in negative_keywords):
        return 'Negative'
    elif any(word in user_message for word in positive_keywords):
        return 'Positive'
    else:
        return 'Neutral'

# ========== Dify 函數 ==========

def call_dify(group, message, user_id):
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
        
        if user_id in user_conversations:
            request_data['conversation_id'] = user_conversations[user_id]
        
        response = requests.post(
            DIFY_API_URL,
            headers={'Authorization': f'Bearer {dify_key}', 'Content-Type': 'application/json'},
            json=request_data,
            timeout=30
        )
        
        data = response.json()
        ai_reply = data.get('answer', '抱歉，我現在無法回覆。')
        
        if 'conversation_id' in data:
            user_conversations[user_id] = data['conversation_id']
        
        update_last_interaction(user_id)
        return ai_reply
        
    except Exception as e:
        print(f'[ARIA ERROR] Dify error: {str(e)}')
        return '抱歉，系統暫時無法回應。'

# ========== LINE 函數 ==========

def send_line_reply(reply_token, message):
    try:
        requests.post(
            'https://api.line.me/v2/bot/message/reply',
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'},
            json={'replyToken': reply_token, 'messages': [{'type': 'text', 'text': message}]},
            timeout=10
        )
    except Exception as e:
        print(f'[ARIA ERROR] LINE reply error: {str(e)}')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
