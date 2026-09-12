# 訴願書判決工具平台

這是一個以 Flask 建立的訴願案件工作平台。系統將歷史訴願決定書整理成可閱讀的結構化資料，並將待審案件與卷證交由研究 API 分析爭議主張、相關法規與判決建議。

目前專案不包含 MLX、ChromaDB 或本機向量資料庫。這些舊版實驗工具已移除，避免它們與實際網站流程混淆。

## 目前功能

1. **第一步：歷史訴願決定書匯入**
   - 上傳 PDF、擷取文字並產生階層式 JSON。
   - 依年份與法律類別瀏覽資料。
   - 在筆記閱讀頁並排閱讀 PDF 與 JSON，支援重點反白。
   - 重複上傳同一文件時，系統會辨識並略過。

2. **第二步：待審案件分析**
   - 上傳待審訴願書並建立案件。
   - 透過研究 API 上傳案件卷證、讀取案件 Session 與歷次研究。
   - 顯示爭議主張、相關法規、支持／反向／不足證據與補件建議。

3. **第三步：判決建議校閱**
   - 沿用第二步選取的案件與分析結果。
   - 可開啟案件 PDF 筆記，並校閱研究 API 回傳的判決建議。

## 架構

```text
使用者瀏覽器
    │
    ├── 第一步：歷史 PDF
    │       └── Flask → PDF 文字擷取 → 階層式 JSON → EFS
    │
    └── 第二、三步：待審案件與卷證
            └── Flask 代理 → 研究 API → 爭點／法規／草案

Elastic Beanstalk：執行 Flask 與 Gunicorn
EFS：保存歷史資料、待審案件 PDF、TXT 與第一步 JSON
Secrets Manager：保存研究 API 存取碼與 Gemini API Key
```

歷史案例「相似案件比對」尚未實作為網站功能。未來應由研究 API 或獨立的雲端檢索服務提供，而不是在 Elastic Beanstalk 上執行本機 MLX 模型。

## 專案結構

```text
法制局專案web/
├── app.py                         # 本機啟動入口
├── application.py                 # Elastic Beanstalk WSGI 入口
├── Procfile                       # Gunicorn 啟動設定
├── requirements.txt               # 網站執行套件
├── legal_tool/
│   ├── config.py                  # 共用路徑與連接埠設定
│   ├── web_app.py                 # Flask 路由與研究 API 代理
│   ├── processing/
│   │   ├── pdf_text_utils.py      # PDF 文字擷取與清理
│   │   └── txt_to_json.py         # TXT 轉階層式 JSON
│   └── services/
│       └── library_ingestion.py   # 第一部匯入、驗證與重複檔案檢查
├── frontend/
│   ├── assets/                    # 研究 API 前端用戶端
│   └── pages/                     # 首頁、第一步與第二步頁面
├── scripts/conversion/            # 手動批次 PDF／JSON 轉換工具
├── tests/                         # 第一部資料匯入測試
├── docs/aws_deployment.md         # AWS 部署與 EFS 設定說明
└── data/                          # 本機開發產物；不提交到 GitHub
```

## 資料保存位置

### 本機開發

未設定環境變數時，資料會存放在專案根目錄：

```text
uploads/                           # 第一步歷史 PDF 與 TXT
case_uploads/                      # 第二步待審案件 PDF、TXT 與 metadata
data/processed/json_web_uploads/  # 第一步網頁產生的 JSON
```

### AWS 部署

設定下列環境變數後，以上資料會保存到 EFS：

```text
PERSISTENT_DATA_ROOT=/mnt/efs/legal-demo
```

實際路徑為：

```text
/mnt/efs/legal-demo/
├── uploads/
├── case_uploads/
└── data/processed/json_web_uploads/
```

> 請勿把 PDF、JSON、案件卷證或 API 金鑰提交到 GitHub。公開儲存庫只保存程式碼。

## 本機啟動

建立並啟用虛擬環境後安裝套件：

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

啟動網站：

```bash
./.venv/bin/python app.py
```

系統會從 5000 開始自動尋找可用連接埠。若要指定連接埠：

```bash
PORT=5050 ./.venv/bin/python app.py
```

開啟終端機顯示的網址，例如 `http://127.0.0.1:5000/`。

## 環境變數

| 名稱 | 用途 | AWS 建議設定 |
|---|---|---|
| `PERSISTENT_DATA_ROOT` | 上傳檔案與第一步 JSON 的保存根目錄 | `/mnt/efs/legal-demo` |
| `RESEARCH_API_BASE_URL` | 研究 API 的基礎網址 | 你的研究 API 網址 |
| `RESEARCH_ACCESS_CODE` | 研究 API 存取碼 | 透過 Secrets Manager 注入 |
| `GEMINI_API_KEY` | 研究 API 所需的 Gemini 金鑰 | 透過 Secrets Manager 注入 |
| `ALLOW_CUSTOM_RESEARCH_API_URL` | 是否允許瀏覽器改寫 API 網址 | `false` |

在 Elastic Beanstalk 可至「設定 → 更新、監控和記錄 → 執行階段環境變數」設定。金鑰請使用 Secrets Manager，不要寫進程式碼或 `.env` 後提交。

## AWS 部署重點

目前部署採用：

- **Elastic Beanstalk**：執行 Flask／Gunicorn。
- **EFS**：保存使用者上傳檔案，避免環境重新部署後消失。
- **CodePipeline**：GitHub `main` 分支推送後自動部署。
- **Secrets Manager**：保存 API 存取碼與金鑰。

完整 EFS、環境變數與驗證流程請見 [docs/aws_deployment.md](docs/aws_deployment.md)。

## 測試

執行第一步匯入流程的自動測試：

```bash
./.venv/bin/python -m unittest tests.test_library_ingestion -v
```

測試包含 PDF／TXT／JSON 建立、階層段落解析、檔名清理、重複檔案檢查及第一步 API。

## 批次轉換工具

以下工具僅供整理既有歷史資料，不是網站部署必需項目。

```bash
# 批次 PDF 轉 TXT
./.venv/bin/python -m scripts.conversion.batch_pdf_to_txt <PDF資料夾>

# 批次 PDF 轉 JSON
./.venv/bin/python -m scripts.conversion.batch_pdf_to_json <PDF資料夾>

# 清理既有 JSON metadata
./.venv/bin/python -m scripts.conversion.clean_json_metadata data/processed/json
```

## 開發範圍與下一步

- 目前網站的爭點、法規、卷證與草案功能以研究 API 回傳資料為準。
- 歷史訴願決定書已能結構化保存與閱讀，但尚未接入自動相似案例搜尋。
- 若要實作相似案例搜尋，建議先與研究 API 定義「建立索引」與「查詢相似案例」兩個端點，再由第二步顯示可追溯的案例來源與原文段落。
