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

歷史案例比對已接入 AWS Bedrock Titan Text Embeddings V2：第二步會先依主要法律強制篩選歷史案件，再以語意向量、BM25 關鍵字與共同法條進行混合排序。向量索引快取在 EFS；結果會回傳相似度、比對理由、命中的歷史原文與閱讀連結，點擊後可回到原始歷史 PDF 並嘗試反白對應段落。BM25 單獨搜尋只保留為診斷模式，不是正式流程。

## 第一步 PDF → TXT 詳細處理架構

此流程只處理「歷史訴願決定書」。PDF 轉 TXT 完成後，系統才會接著建立 JSON；TXT 是兩個階段之間可檢查、可追溯的中間產物。

```text
瀏覽器：pdf_manager_ui_v6.html
    │ 使用者選取一或多份 PDF
    │ multipart/form-data：files[]、category=歷史訴願決定書
    ▼
POST /convert（legal_tool.web_app.convert_pdf）
    │ 檢查分類與至少一份 PDF
    ▼
ingest_library_pdf（legal_tool.services.library_ingestion）
    ├── 檢查副檔名必須為 .pdf
    ├── 產生 UUID 文件 ID，例如 8d2f...a91c
    ├── 清理 Finder「的副本」檔名，保留原始檔名於 metadata
    ├── 先保存 PDF：uploads/<文件ID>.pdf
    ├── 計算 SHA-256 檔案雜湊，檢查是否已經匯入過
    │     └── 若重複：刪除剛存入的暫存 PDF，回傳 duplicate，不再轉檔
    ▼
extract_pdf_text（legal_tool.processing.pdf_text_utils）
    ├── pdfplumber 逐頁執行 page.extract_text()
    ├── 每一頁後加入空白行，避免跨頁文字黏在一起
    └── clean_pdf_lines 清理文字雜訊
          ├── BOM、零寬字元、(cid:數字) 字型殘留
          ├── 網站查閱時間、URL 頁尾
          ├── 獨立頁碼，例如 3 / 12
          └── 連續空白行
    ▼
UTF-8 TXT：uploads/<文件ID>.txt
    │
    ├── 回傳 pdf_url、txt_url、文件 ID 與處理狀態
    └── 下一階段：TXT → 階層式 JSON
```

### 各層職責

| 層級 | 程式位置 | 負責內容 |
|---|---|---|
| 前端 | `frontend/pages/pdf_manager_ui_v6.html` | 選檔、上傳進度與顯示轉換結果 |
| HTTP 路由 | `legal_tool/web_app.py` 的 `/convert` | 接收檔案、呼叫匯入服務、回傳 JSON 結果 |
| 匯入服務 | `legal_tool/services/library_ingestion.py` | UUID、檔名整理、重複檔案檢查、檔案保存與後續 JSON 建立 |
| PDF 文字處理 | `legal_tool/processing/pdf_text_utils.py` | 逐頁擷取與雜訊清理 |
| 保存位置 | `UPLOAD_DIR` | 本機為 `uploads/`；AWS 設定 EFS 後為 `/mnt/efs/legal-demo/uploads/` |

### 轉換成功、重複與失敗

| 狀態 | 意義 | 系統行為 |
|---|---|---|
| `completed` | PDF 已成功擷取為 TXT | 保存 PDF、TXT，並繼續建立 JSON |
| `duplicate` | 檔案內容的 SHA-256 與既有文件相同 | 不重複保存、不重複轉換，前端提示既有檔名 |
| `needs_review` | PDF 與 TXT 成功，但 JSON 缺少案號、主文或理由 | 保留檔案，讓使用者在筆記頁檢查 |
| 上傳失敗 | 非 PDF、沒有檔案或擷取不到文字 | 回傳 400 與原因，不建立不完整資料 |

> 掃描型 PDF 如果沒有可選取的文字，`pdfplumber` 無法直接擷取，系統會提示「PDF 沒有可擷取的文字」。這類檔案需要先經 OCR，才可進入此流程。

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
| `RAG_ENABLED` | 是否啟用 AWS 語意 RAG | `true` |
| `RAG_AWS_REGION` | Bedrock Runtime 區域 | `us-east-1` |
| `RAG_EMBEDDING_MODEL_ID` | Bedrock embedding 模型 | `amazon.titan-embed-text-v2:0` |
| `RAG_EMBEDDING_DIMENSIONS` | embedding 維度 | `1024` |
| `RAG_REQUIRE_LAW_FILTER` | 是否強制只搜尋同法律類別 | `true` |
| `RAG_MIN_SCORE` | 低於此綜合相似度的結果不顯示 | `0.35` |
| `RESEARCH_API_BASE_URL` | 研究 API 的基礎網址 | 你的研究 API 網址 |
| `RESEARCH_ACCESS_CODE` | 研究 API 存取碼 | 透過 Secrets Manager 注入 |
| `GEMINI_API_KEY` | 研究 API 所需的 Gemini 金鑰 | 透過 Secrets Manager 注入 |
| `ALLOW_CUSTOM_RESEARCH_API_URL` | 是否允許瀏覽器改寫 API 網址 | `false` |

在 Elastic Beanstalk 可至「設定 → 更新、監控和記錄 → 執行階段環境變數」設定。金鑰請使用 Secrets Manager，不要寫進程式碼或 `.env` 後提交。

## AWS 部署重點

目前部署採用：

- **Elastic Beanstalk**：執行 Flask／Gunicorn。
- **EFS**：保存使用者上傳檔案，避免環境重新部署後消失。
- **Amazon Bedrock**：以 Titan Text Embeddings V2 產生歷史理由與當前爭點的語意向量。
- **CodePipeline**：GitHub `main` 分支推送後自動部署。
- **Secrets Manager**：保存 API 存取碼與金鑰。

完整 EFS、環境變數與驗證流程請見 [docs/aws_deployment.md](docs/aws_deployment.md)。

## 測試

執行第一步匯入流程的自動測試：

```bash
./.venv/bin/python -m unittest tests.test_library_ingestion -v
```

測試包含 PDF／TXT／JSON 建立、階層段落解析、檔名清理、重複檔案檢查、第一步 API 與歷史理由段落檢索。

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
- 第二步的「歷史相似案例」目前是本機 BM25 文字關聯檢索，資料來源是 EFS／本機保存的歷史 JSON 與待審案件 TXT，不依賴研究 API。
- 若改為語意 RAG，建議保留 `/api/historical-similarity/search` 的回傳格式，僅將其內部檢索替換為 AWS 向量服務，並維持 `document_id`、`focus_text`、`reason` 與 `score`，才能繼續支援 PDF 原文反白與可追溯性。
