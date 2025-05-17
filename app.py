import os, traceback, firebase_admin
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import TextSendMessage, MessageEvent, TextMessage, MemberJoinedEvent, PostbackEvent
from openai import OpenAI
from firebase_admin import credentials, db

SYSTEM_PROMPT = {
    "role": "system",
    "content": "你是一個萬能聊天助手，什麼都能聊。"
}

# 初始化 Firebase Admin SDK
cred = credentials.Certificate("/etc/secrets/serviceAccountKey.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://linedata-75073-default-rtdb.asia-southeast1.firebasedatabase.app/'
})

# Flask + LineBot 設定
app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))
handler     = WebhookHandler(os.getenv('CHANNEL_SECRET'))
@app.route("/ping", methods=["GET"])
def ping():
    return "OK", 200
# OpenAI Grok 3 Mini Beta
client = OpenAI(
    api_key=os.getenv("XAI_API_KEY"),
    base_url="https://api.x.ai/v1"
)

conversation_histories = {}

def save_history_rtdb(user_id, msgs):
    ref = db.reference(f"histories/{user_id}")
    ref.set(msgs)
    
def load_history_rtdb(user_id):
    ref = db.reference(f"histories/{user_id}")
    data = ref.get()
    return data if isinstance(data, list) else []
    
def append_message(user_id, role, content):
    history = conversation_histories.setdefault(user_id, [])
    history.append({"role": role, "content": content})
    conversation_histories[user_id] = history[-20:]
    save_history_rtdb(user_id, conversation_histories[user_id])

def GPT_response(user_id, text):
    # 1. 如果是第一次，先從 Firebase 載入歷史
    if user_id not in conversation_histories:
        conversation_histories[user_id] = load_history_rtdb(user_id)

    # 2. 加入使用者最新輸入
    append_message(user_id, "user", text)
    msgs = conversation_histories[user_id]

    # 3. 在最前面插入 system 訊息
    messages = [SYSTEM_PROMPT] + msgs

    # 4. 呼叫模型
    resp = client.chat.completions.create(
        model="grok-3-beta",
        messages=messages,
        max_tokens=5000,
        temperature=0.7
    )
    answer = resp.choices[0].message.content

    # 5. 把助理回覆加回歷史
    append_message(user_id, "assistant", answer)
    return answer

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body      = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    uid  = event.source.user_id
    msg  = event.message.text
    try:
        answer = GPT_response(uid, msg)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(answer))
    except Exception:
        traceback.print_exc()
        line_bot_api.reply_message(event.reply_token,
            TextSendMessage('API 錯誤，請檢查金鑰及額度。')
        )

@handler.add(MemberJoinedEvent)
def welcome(event):
    uid     = event.joined.members[0].user_id
    gid     = event.source.group_id
    profile = line_bot_api.get_group_member_profile(gid, uid)
    name    = profile.display_name
    line_bot_api.reply_message(event.reply_token,
        TextSendMessage(f'{name} 歡迎加入！')
    )

@handler.add(PostbackEvent)
def handle_postback(event):
    # 處理 Postback
    pass

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
