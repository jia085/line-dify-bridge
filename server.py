from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime
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

# 儲存對話 ID（記憶體存儲）
user_conversations = {}

# 儲存今天已互動的使用者（每天重置）
today_interacted = set()
last_date_check = datetime.now(TW_TZ).date()

@app.route('/', methods=['GET'])
def health():
    return 'OK', 200

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        return 'Webhook endpoint is ready', 200
    
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
        
        # ========== RESET 指令 ==========
        if user_message == 'RESET':
            clear_user_id_from_sheets(user_id)
            if user_id in user_conversations:
                del user_conversations[user_id]
            if user_id in today_interacted:
                today_interacted.remove(user_id)
            reply_message = '✅ 已重置，可以重新驗證。'
            send_line_reply(reply_token, reply_message)
            return jsonify({'status': 'reset'}), 200
        
        # ========== 檢查使用者是否已綁定 ==========
        user_data = get_user_data_by_user_id(user_id)
        
        if not user_data:
            # 尚未綁定，要求驗證
            if len(user_message) == 5 and user_message.isdigit():
                group_data = query_google_sheets_by_code(user_message)
                if group_data:
                    # 驗證成功
                    update_user_id_in_sheets(user_message, user_id)
                    reply_message = f'✅ 驗證成功！歡迎加入實驗。'
                    send_line_reply(reply_token, reply_message)
                    return jsonify({'status': 'verification success'}), 200
                else:
                    reply_message = '❌ 查無此代碼，請確認您的手機末5碼是否正確。'
                    send_line_reply(reply_token, reply_message)
                    return jsonify({'status': 'verification failed'}), 200
            else:
                reply_message = '你好！請輸入您的手機末5碼以開始實驗。'
                send_line_reply(reply_token, reply_message)
                return jsonify({'status': 'awaiting verification'}), 200
        
        # ========== 已綁定，正常對話 ==========
        group = user_data.get('group')
        current_day = user_data.get('current_day', 0)
        d14_triggered = user_data.get('d14_triggered', False)
        
        # 檢查是否需要觸發 D14
        if current_day == 14 and not d14_triggered:
            emotion, trigger_sentence = trigger_d14(user_message, group, user_id)
            ai_reply = trigger_sentence
        else:
            ai_reply = call_dify(group, user_message, user_id)
        
        send_line_reply(reply_token, ai_reply)
        
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        print(f'Error: {str(e)}')
        return jsonify({'status': 'error', 'message': str(e)}), 500

def query_google_sheets_by_code(code):
    """用手機碼查詢"""
    try:
        response = requests.get(f'{SHEETS_API_URL}?code={code}', timeout=10)
        data = response.json()
        if data.get('found'):
            return data
        return None
    except Exception as e:
        print(f'Google Sheets query error: {str(e)}')
        return None

def get_user_data_by_user_id(user_id):
    """用 User ID 查詢"""
    try:
        response = requests.get(f'{SHEETS_API_URL}?user_id={user_id}', timeout=10)
        data = response.json()
        if data.get('found'):
            return data
        return None
    except Exception as e:
        print(f'Get user data error: {str(e)}')
        return None

def update_user_id_in_sheets(code, user_id):
    """驗證成功後，更新 User ID 和 First_Interaction（台灣時間）"""
    try:
        # 取得台灣時間（精確到秒）
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
        global today_interacted, last_date_check
        
        # 取得台灣時間
        tw_now = datetime.now(TW_TZ)
        
        # 檢查是否新的一天
        current_date = tw_now.date()
        if current_date != last_date_check:
            today_interacted.clear()
            last_date_check = current_date
        
        # 判斷是否今天第一次互動
        is_first_today = user_id not in today_interacted
        if is_first_today:
            today_interacted.add(user_id)
        
        # 格式化為字串（精確到秒）
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

def trigger_d14(user_message, group, user_id):
    """D14 衝突觸發"""
    try:
        positive_keywords = ['開心', '高興', '快樂', '好棒', '太好了', '成功']
        negative_keywords = ['難過', '傷心', '生氣', '煩', '累', '壓力']
        
        if any(word in user_message for word in positive_keywords):
            emotion = 'Positive'
            trigger_sentence = '這件事有那麼值得開心嗎？'
        elif any(word in user_message for word in negative_keywords):
            emotion = 'Negative'
            trigger_sentence = '你是不是又想太多了？事情應該沒那麼嚴重吧。'
        else:
            emotion = 'Neutral'
            trigger_sentence = '你是不是又想太多了？'
        
        requests.post(
            SHEETS_API_URL,
            json={
                'user_id': user_id,
                'd14_trigger': True,
                'emotion': emotion,
                'trigger_sentence': trigger_sentence
            },
            timeout=10
        )
        
        print(f'[DEBUG] D14 triggered: {user_id}, emotion: {emotion}')
        
        return emotion, trigger_sentence
        
    except Exception as e:
        print(f'[ERROR] D14 trigger error: {str(e)}')
        return 'Neutral', '你是不是又想太多了？'

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
        
        if user_id in user_conversations:
            request_data['conversation_id'] = user_conversations[user_id]
            print(f'[DEBUG] Using conversation: {user_conversations[user_id]}')
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
        
        data = response.json()
        ai_reply = data.get('answer', '抱歉，我現在無法回覆。')
        
        if 'conversation_id' in data:
            user_conversations[user_id] = data['conversation_id']
            print(f'[DEBUG] Saved conversation ID: {data["conversation_id"]}')
        
        update_last_interaction(user_id)
        
        return ai_reply
        
    except Exception as e:
        print(f'Dify API error: {str(e)}')
        return '抱歉，系統暫時無法回應。'

def send_line_reply(reply_token, message):
    """發送 LINE 回覆"""
    try:
        requests.post(
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
    except Exception as e:
        print(f'LINE reply error: {str(e)}')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
```

---

## 📦 **確認 pytz 已安裝**

Render 會自動安裝 `requirements.txt` 裡的套件

確認你的 `requirements.txt` 有：
```
Flask==2.3.0
requests==2.31.0
gunicorn==20.1.0
pytz==2023.3
```

如果沒有 pytz，加上去，然後 Commit

---

## 🧪 **測試**

### **部署完成後：**

1. **RESET**
2. **驗證（00001）**
3. **查看 Google Sheets G 欄**

應該顯示：
```
2026-02-21 21:30:45  ← 台灣時間，精確到秒 ✅
```

而不是：
```
2026-02-21  ← 只有日期 ❌
2026-02-21 13:30:45  ← UTC 時間（晚8小時）❌
```

---

## ⏰ **時間格式說明**

### **修改後的格式：**
```
First_Interaction:  2026-02-21 21:30:45
Last_Interaction:   2026-02-21 21:32:18
```

### **時區說明：**
```
台灣（GMT+8）= UTC + 8 小時

如果 UTC 是 13:30
台灣就是 21:30 ✅
