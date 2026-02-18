from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

DIFY_API_URL = 'https://api.dify.ai/v1/chat-messages'
DIFY_API_KEY = os.environ.get('DIFY_API_KEY')
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        events = data.get('events', [])
        
        if not events:
            return jsonify({'status': 'no events'}), 200
        
        event = events[0]
        
        if event['type'] != 'message' or event['message']['type'] != 'text':
            return jsonify({'status': 'ignored'}), 200
        
        user_message = event['message']['text']
        reply_token = event['replyToken']
        user_id = event['source']['userId']
        
        # 呼叫 Dify API
        dify_response = requests.post(
            DIFY_API_URL,
            headers={
                'Authorization': f'Bearer {DIFY_API_KEY}',
                'Content-Type': 'application/json'
            },
            json={
                'inputs': {},
                'query': user_message,
                'user': user_id,
                'response_mode': 'blocking'
            }
        )
        
        dify_data = dify_response.json()
        ai_reply = dify_data.get('answer', '抱歉，我現在無法回覆。')
        
        # 回傳給 LINE
        requests.post(
            'https://api.line.me/v2/bot/message/reply',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'
            },
            json={
                'replyToken': reply_token,
                'messages': [{'type': 'text', 'text': ai_reply}]
            }
        )
        
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/', methods=['GET'])
def health():
    return 'OK', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
