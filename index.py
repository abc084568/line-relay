import os
import time
import hmac
import hashlib
import base64
import requests
from flask import Flask, request, Response, jsonify

app = Flask(__name__)

# 從 Vercel 環境變數讀取設定
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN', '')
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET', '')
MY_USER_ID = os.environ.get('MY_USER_ID', '')
KV_URL = os.environ.get('KV_REST_API_URL', '') or os.environ.get('UPSTASH_REDIS_REST_URL', '')
KV_TOKEN = os.environ.get('KV_REST_API_TOKEN', '') or os.environ.get('UPSTASH_REDIS_REST_TOKEN', '')

# --- 讀寫雲端暫存 (Upstash Redis) 的小工具 ---
def kv_set(key, value):
    if not KV_URL: return
    headers = {"Authorization": f"Bearer {KV_TOKEN}"}
    # 設定 300 秒後自動過期清除，不佔空間
    requests.post(f"{KV_URL}/set/{key}?EX=300", headers=headers, data=value.encode('utf-8'))

def kv_get(key, delete_after=False):
    if not KV_URL: return None
    headers = {"Authorization": f"Bearer {KV_TOKEN}"}
    r = requests.get(f"{KV_URL}/get/{key}", headers=headers)
    res = r.json().get("result")
    if res and delete_after:
        requests.post(f"{KV_URL}/del/{key}", headers=headers)
    return res


@app.route('/')
def home():
    return "LINE Vercel Relay is running!"


# 1. 接收你電腦上傳的截圖，並主動推播給 LINE
@app.route('/api/upload', methods=['POST'])
def upload_image():
    data = request.json
    img_b64 = data.get('image')
    if not img_b64:
        return jsonify({"error": "No image"}), 400

    # 把圖片轉成 Base64 暫存起來，並清空上一次舊的回覆文字
    kv_set("latest_img", img_b64)
    kv_get("latest_ans", delete_after=True)

    # 組合圖片網址 (加上時間戳記防止 LINE 快取舊圖)
    vercel_host = request.headers.get('Host')
    img_url = f"https://{vercel_host}/api/image.png?t={int(time.time())}"

    # 呼叫 LINE API 發送圖片到你手機
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": MY_USER_ID,
        "messages": [
            {
                "type": "image",
                "originalContentUrl": img_url,
                "previewImageUrl": img_url
            },
            {
                "type": "text",
                "text": "📸 請直接打字回覆圖片中的內容："
            }
        ]
    }
    r = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)
    return jsonify({"status": "sent", "line_status": r.status_code})


# 2. 讓 LINE 伺服器來抓取剛剛那張截圖
@app.route('/api/image.png', methods=['GET'])
def get_image():
    img_b64 = kv_get("latest_img")
    if not img_b64:
        return "Not Found", 404
    img_bytes = base64.b64decode(img_b64)
    return Response(img_bytes, mimetype='image/png')


# 3. 接收你在手機 LINE 打字回傳的 Webhook
@app.route('/api/callback', methods=['POST'])
def callback():
    body = request.get_data(as_text=True)
    events = request.json.get('events', [])
    
    for event in events:
        if event.get('type') == 'message' and event.get('message', {}).get('type') == 'text':
            user_text = event['message']['text'].strip()
            reply_token = event['replyToken']

            # 把你打的字存進暫存區，等你的電腦來領走
            kv_set("latest_ans", user_text)

            # 用免費的 Reply 告訴你已經收到了
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
            }
            payload = {
                "replyToken": reply_token,
                "messages": [{"type": "text", "text": f"✅ 已傳回電腦：{user_text}"}]
            }
            requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=payload)

    return 'OK'


# 4. 讓你的電腦來輪詢（領取）你剛打好的字
@app.route('/api/poll', methods=['GET'])
def poll_answer():
    ans = kv_get("latest_ans", delete_after=True) # 領走後立刻清空，避免重複填寫
    return jsonify({"answer": ans})