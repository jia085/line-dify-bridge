from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime

app = Flask(__name__)

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
        
        # ========== RESET 指令（測試用）==========
        if user_message == 'RESET':
            # 從 Google Sheets 清除 User ID
            clear_user_id_from_sheets(user_id)
            reply_message = '✅ 已重置，可以重新驗證。'
            send_line_reply(reply_token, reply_message)
            return jsonify({'status': 'reset'}), 200
        
        # ========== TEST 指令（測試用）==========
        if user_message.startswith('TEST_'):
            group = user_message.split('_')[1].upper()
            if group in ['A', 'B', 'C', 'D']:
                reply_message = f'⚠️ TEST 指令已停用。請使用正常驗證流程（輸入手機碼）。'
                send_line_reply(reply_token, reply_message)
                return jsonify({'status': 'test mode disabled'}), 200
        
        # ========== 檢查使用者是否已綁定組別 ==========
        # 從 Google Sheets 查詢這個 User ID 是否已經綁定
        user_data = get_user_data_by_user_id(user_id)
        
        if not user_data:
            # 尚未綁定，要求驗證
            if len(user_message) == 5 and user_message.isdigit():
                # 查詢 Google Sheets（用手機碼）
                group_data = query_google_sheets_by_code(user_message)
                if group_data:
                    group = group_data.get('group')
                    # 驗證成功後，寫入 Line_User_ID 到 Google Sheets
                    update_user_id_in_sheets(user_message, user_id)
                    reply_message = f'✅ 驗證成功！歡迎加入實驗。'
                    send_line_reply(reply_token, reply_message)
                    return jsonify({'status': 'verification success'}), 200
                else:
                    reply_message = '❌ 查無此代碼，請確認您的手機末5碼是否正確。'
                    send_line_reply(reply_token, reply_message)
                    return jsonify({'status': 'verification failed'}), 200
            else:
                # 尚未驗證，提示輸入手機碼
                reply_message = '你好！請輸入您的手機末5碼以開始實驗。'
                send_line_reply(reply_token, reply_message)
                return jsonify({'status': 'awaiting verification'}), 200
        
        # ========== 已綁定組別，正常對話 ==========
        group = user_data.get('group')
        ai_reply = call_dify(group, user_message, user_id)
        send_line_reply(reply_token, ai_reply)
        
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        print(f'Error: {str(e)}')
        return jsonify({'status': 'error', 'message': str(e)}), 500

def query_google_sheets_by_code(code):
    """用手機碼查詢 Google Sheets"""
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
    """用 User ID 查詢 Google Sheets（檢查是否已綁定）"""
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
    """驗證成功後，更新 Google Sheets 的 Line_User_ID"""
    try:
        print(f'[DEBUG] Updating User ID for code: {code}, user_id: {user_id}')
        
        response = requests.post(
            SHEETS_API_URL,
            json={
                'code': code,
                'user_id': user_id
            },
            timeout=10
        )
        
        print(f'[DEBUG] Update User ID response: {response.text}')
        
    except Exception as e:
        print(f'[ERROR] Update User ID error: {str(e)}')

def clear_user_id_from_sheets(user_id):
    """RESET 時，從 Google Sheets 清除 User ID"""
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
    """更新 Google Sheets 的 Last_Interaction"""
    try:
        today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        print(f'[DEBUG] Updating last interaction for user: {user_id}, date: {today}')
        
        response = requests.post(
            SHEETS_API_URL,
            json={
                'user_id': user_id,
                'last_interaction': today
            },
            timeout=10
        )
        
        print(f'[DEBUG] Update last interaction response: {response.text}')
        
    except Exception as e:
        print(f'[ERROR] Update sheets error: {str(e)}')

def call_dify(group, message, user_id):
    """呼叫對應組別的 Dify API"""
    try:
        dify_key = DIFY_KEYS.get(group)
        if not dify_key:
            return '系統錯誤：無法識別組別'
        
        response = requests.post(
            DIFY_API_URL,
            headers={
                'Authorization': f'Bearer {dify_key}',
                'Content-Type': 'application/json'
            },
            json={
                'inputs': {},
                'query': message,
                'user': user_id,
                'response_mode': 'blocking'
            },
            timeout=30
        )
        
        data = response.json()
        ai_reply = data.get('answer', '抱歉，我現在無法回覆。')
        
        # 更新 Google Sheets
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
