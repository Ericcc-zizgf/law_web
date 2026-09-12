# 前端與後端資料傳遞示範

這是一個最小可執行範例：

```text
前端表單
   │ fetch POST /api/messages
   ▼
Flask 後端
   │ 驗證資料、暫存資料
   ▼
回傳 JSON
   │
   ▼
前端把結果顯示在畫面上
```

## 啟動方式

在專案根目錄執行：

```bash
./.venv/bin/python examples/frontend_backend_demo/app.py
```

然後開啟：

```text
http://127.0.0.1:5005/
```

## 檔案說明

```text
examples/frontend_backend_demo/
├── app.py                 # Flask 後端與 API
├── frontend/
│   └── index.html         # 前端畫面與 fetch()
└── README.md
```

## 最重要的三段程式碼

前端送資料：

```javascript
fetch('/api/messages', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload)
});
```

後端接收資料：

```python
data = request.get_json()
```

後端回傳資料：

```python
return jsonify(new_message), 201
```

這個範例使用記憶體暫存，所以重新啟動後端後，資料會清空；它的目的只是讓你看懂前後端如何互相傳資料。
