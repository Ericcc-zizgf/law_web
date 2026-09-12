"""最小的前端與後端資料傳遞示範。"""

from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory


PROJECT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_DIR / "frontend"

app = Flask(__name__)

# 為了讓範例簡單，資料先暫存在記憶體中。
# 重新啟動後端後，這些資料就會清空。
messages = []


@app.get("/")
def index():
    """把前端 index.html 傳給瀏覽器。"""
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/api/messages")
def get_messages():
    """提供前端讀取目前所有訊息。"""
    return jsonify(messages)


@app.post("/api/messages")
def create_message():
    """接收前端送來的 JSON，驗證後再回傳建立的資料。"""
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    message = str(data.get("message", "")).strip()

    if not name or not message:
        return jsonify({"error": "姓名與訊息都必須填寫"}), 400

    new_message = {
        "id": len(messages) + 1,
        "name": name,
        "message": message,
    }
    messages.append(new_message)

    # 後端處理完成後，把 JSON 回傳給前端。
    return jsonify(new_message), 201


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5005, debug=True, use_reloader=False)
