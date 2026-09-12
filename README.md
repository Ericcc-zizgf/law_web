# 訴願書判決工具平台

這是一個以 Flask 建立的訴願決定書資料整理與案件分析原型，主要包含：

1. 歷史訴願決定書匯入與 PDF 文字擷取
2. TXT 轉換成階層式 JSON
3. 以 JSON 建立法律理由段落的向量資料庫
4. 輸入爭議點並搜尋相似歷史案例
5. 網頁工作平台與三步驟工作流程

目前第一步「歷史資料匯入」已完成 PDF 上傳、TXT 擷取、階層式 JSON 解析、持久清單、PDF／JSON 並排檢查與反白筆記；第二步已整合待審案件卷證上傳、案件清單、研究 API 代理與爭議點分析；第三步可沿用同一案件與分析結果進入判決建議校閱流程。

> 公開儲存庫只保存程式碼。訴願書 PDF、案件卷證、解析 JSON、向量資料庫與 API 密鑰均不納入版本控制；AWS 部署時由 EFS 與 Secrets Manager 保存。

## 一、完整資料夾結構

```text
法制局專案web/
│
├── app.py
├── application.py                   # Elastic Beanstalk WSGI 入口
├── Procfile                         # AWS Gunicorn 啟動設定
├── requirements.txt
├── requirements-rag.txt             # Apple Silicon 本機 RAG／MLX 額外套件
├── README.md
├── .gitignore
│
├── legal_tool/                         # 正式使用的後端與核心模組
│   ├── __init__.py
│   ├── config.py                       # 所有共用路徑設定
│   ├── web_app.py                      # Flask 路由與 API
│   ├── processing/                     # 文件解析與轉換
│   │   ├── __init__.py
│   │   ├── pdf_text_utils.py           # PDF 文字擷取與清理
│   │   └── txt_to_json.py              # TXT 解析成階層式 JSON
│   ├── services/                       # 網頁流程使用的服務層
│   │   ├── __init__.py
│   │   └── library_ingestion.py        # 單份 PDF→TXT→JSON、驗證與資料庫清單
│   └── rag/                            # RAG 與向量檢索
│       ├── __init__.py
│       ├── rag_mlx.py                  # MLX 文字生成與輔助函式
│       └── rag_mlx_vector.py           # Embedding、ChromaDB 與相似度搜尋
│
├── frontend/                           # 網頁畫面
│   └── pages/
│       ├── platform_home.html          # 首頁與三步驟流程圖
│       ├── pdf_manager_ui_v6.html      # 第一步：歷史資料匯入工作區
│       └── case_analysis.html          # 第二步：待審訴願書上傳與分析工作區
│
├── scripts/                            # 需要時手動執行的工具
│   ├── __init__.py
│   ├── conversion/                     # 批次資料轉換工具
│   │   ├── __init__.py
│   │   ├── batch_pdf_to_txt.py         # PDF 批次轉 TXT
│   │   ├── batch_pdf_to_json.py        # PDF 批次轉階層式 JSON
│   │   └── clean_json_metadata.py      # 清理 JSON metadata
│   └── loaders/                        # 單檔格式載入測試工具
│       ├── __init__.py
│       ├── markitdown_pdf_loader.py    # 單一 PDF 轉 Markdown 測試
│       └── pdfplumber_pdf_loader.py    # 單一 PDF 文字擷取測試
│
├── tests/                              # 測試與人工驗證
│   ├── __init__.py
│   ├── test_library_ingestion.py       # 第一步轉檔與 API 整合測試
│   ├── convert_to_JSON_test.py         # 舊版 JSON 解析測試
│   └── rag_issue_test.py               # 互動式爭議點相似度測試
│
├── notebooks/                          # Jupyter Notebook 教學與分析
│   ├── analyze_txt_fixed_text.ipynb    # 分析 TXT 固定格式與共同文字
│   └── batch_pdf_to_txt_walkthrough.ipynb
│                                       # 逐步說明 PDF 轉 TXT 的資料流
│
├── data/                               # 非程式碼資料
│   ├── inputs/                         # 測試或待分析的輸入文字
│   │   ├── rag_test_petition.txt
│   │   └── 測試爭議點.txt
│   ├── processed/                      # 程式產生的處理結果
│   │   ├── text/                       # PDF 轉出的 TXT
│   │   ├── json/                       # 舊有批次／測試階層式 JSON
│   │   ├── json_web_uploads/           # 網頁上傳後產生的階層式 JSON
│   │   ├── reports/                    # TXT 格式分析報表
│   │   ├── notebook/                   # Notebook 產生的輸出
│   │   └── json_legacy_model/          # 舊版模型轉換輸出位置
│   ├── vector_db/                      # 向量資料庫
│   │   └── chroma_legal_db/
│   └── examples/                       # 單檔測試產物與範例輸出
│
├── uploads/                            # 網頁上傳 PDF 與逐份轉出的 TXT（UUID 檔名）
├── docs/                               # 技術說明文件
│   └── notion_txt_to_json_analysis.md
└── archive/                            # 不再使用但保留的舊檔案
    ├── convert_txt_to_JSON_legacy.py   # 舊版 AI 模型轉 JSON
    ├── json_output_python.zip          # 舊版 JSON 壓縮備份
    └── tempCodeRunnerFile.py           # 編輯器暫存檔
```

## 二、根目錄檔案說明

### `app.py`

網站的啟動入口。它只負責匯入 `legal_tool.web_app` 裡的 Flask app，因此保留這個檔案後，原本的啟動方式仍然有效：

```bash
./.venv/bin/python app.py
```

### `requirements.txt`

記錄網站執行需要的跨平台套件，例如 Flask、pdfplumber、requests 與 Gunicorn。
若要在 Apple Silicon 本機執行 ChromaDB／MLX RAG 工具，另外安裝：

```bash
./.venv/bin/pip install -r requirements-rag.txt
```

AWS Demo 的架構、環境變數與驗證步驟請見 `docs/aws_deployment.md`。

### `legal_tool/config.py`

集中管理專案路徑。批次轉檔、JSON、報表與向量資料庫的預設位置都從這裡取得，不要在每支程式裡重複寫絕對路徑。

## 三、後端模組說明

### `legal_tool/web_app.py`

Flask 網站主程式，負責：

- 顯示首頁
- 顯示三個工作頁面
- 接收網頁上傳的 PDF
- 呼叫第一步匯入服務產生 TXT 與階層式 JSON
- 提供已匯入文件清單與單份 JSON 讀取 API

目前的主要路由如下：

| 網址 | 功能 | 對應內容 |
|---|---|---|
| `/` | 工具平台首頁 | `frontend/pages/platform_home.html` |
| `/data-library` | 歷史資料匯入 | `frontend/pages/pdf_manager_ui_v6.html` |
| `/case-analysis` | 待審訴願書上傳與分析 | `frontend/pages/case_analysis.html` |
| `/decision-recommendation` | 判決建議生成原型 | `web_app.py` 內的頁面模板 |
| `/convert` | PDF 上傳；依分類執行 PDF→TXT→JSON | `web_app.py` |
| `/convert-case` | 第二步待審案件文件上傳與文字擷取 | `web_app.py` |
| `/api/library/documents` | 讀取已匯入的歷史訴願書清單 | `web_app.py` |
| `/api/documents/<文件ID>/json` | 讀取單份巢狀 JSON | `web_app.py` |
| `/api/research/*` | 伺服器端代理案件、卷證、分析與歷次研究 API | `web_app.py` |
| `/uploads/<檔名>` | 提供已上傳 PDF／TXT | `web_app.py` |

### `legal_tool/services/library_ingestion.py`

第一步網頁專用的匯入服務，負責把各模組串成同一筆文件資料：

1. 產生不受中文檔名或「的副本」影響的 UUID 文件 ID
2. 將原始 PDF 儲存為 `uploads/<文件ID>.pdf`
3. 擷取文字並儲存為 `uploads/<文件ID>.txt`
4. 歷史訴願決定書再解析為 `data/processed/json_web_uploads/<文件ID>.json`
5. 檢查案號、主文與理由等必要欄位，回傳 `completed` 或 `needs_review`
6. 從 JSON metadata 重建文件庫，因此重新整理或重新啟動伺服器後資料仍會出現

### `legal_tool/processing/pdf_text_utils.py`

共用 PDF 文字處理模組，負責：

- 使用 `pdfplumber` 讀取 PDF
- 清理頁碼、網址與已知雜訊
- 保留訴願書的段落換行
- 提供給網頁與批次轉檔程式共用

### `legal_tool/processing/txt_to_json.py`

將 TXT 解析成目前正式使用的階層式 JSON，負責：

- 擷取案號、要旨、日期、發文字號
- 找出主文、事實、理由
- 將「一、」「二、」「（一）」「（二）」等編號轉成階層
- 避免把法律條文內的編號誤切成段落
- 清理檔名中的「的副本」等 Finder 尾綴

### `legal_tool/rag/rag_mlx.py`

RAG 的文字處理與 MLX 相關輔助功能，例如載入 JSON 段落、建立提示詞，以及必要的模型生成函式。

### `legal_tool/rag/rag_mlx_vector.py`

負責建立與查詢向量資料庫：

1. 讀取 `data/processed/json/` 的階層式 JSON
2. 取出理由段落
3. 使用 embedding 模型轉成向量
4. 儲存到 `data/vector_db/chroma_legal_db/`
5. 再次執行時只補入新出現的理由段落，不會因資料庫已有內容而跳過新 JSON
6. 根據輸入的爭議點計算 cosine similarity

## 四、資料處理流程

### 歷史訴願書建庫流程

```text
PDF 資料夾
    │
    ▼
scripts/conversion/batch_pdf_to_txt.py
    │ 使用 legal_tool.processing.pdf_text_utils
    ▼
data/processed/text/*.txt
    │
    ▼
legal_tool.processing.txt_to_json
或 scripts/conversion/batch_pdf_to_json.py
    ▼
data/processed/json/*.json
    │
    ▼
legal_tool.rag.rag_mlx_vector
    ▼
data/vector_db/chroma_legal_db/
```

### 第一步網頁即時匯入流程

```text
前端選擇「歷史訴願決定書」並上傳 PDF
    │ POST /convert（multipart/form-data，傳檔案內容與 category）
    ▼
legal_tool.services.library_ingestion.ingest_library_pdf
    ├── uploads/<文件ID>.pdf
    ├── uploads/<文件ID>.txt
    └── data/processed/json_web_uploads/<文件ID>.json
            │
            ├── metadata：原始檔名、顯示檔名、分類、結構版本
            ├── 案號／要旨／發文日期／發文字號／主文
            └── 事實[]／理由[]／子段落[]
                    │
                    ▼
前端筆記頁：左側 PDF，右側巢狀 JSON／擷取筆記分頁
```

瀏覽器上傳的不是本機檔案路徑，而是 PDF 的實際位元內容。後端回傳 UUID 對應的 `pdf_url`、`txt_url`、`json_url` 與解析狀態；前端保存這些識別資訊，並以 `/api/library/documents` 與伺服器資料同步。

### 爭議點測試流程

```text
輸入爭議點
    ▼
tests/rag_issue_test.py
    ▼
查詢 ChromaDB 向量資料庫
    ▼
輸出相似度、來源檔案、案號、理由段落與內容
```

## 五、網站流程

```text
首頁 /
  │
  ├── /data-library
  │       第一步：匯入歷史訴願書與法律資料
  │
  ├── /case-analysis
  │       第二步：批次上傳待審訴願書、分析爭議點
  │
  └── /decision-recommendation
          第三步：產生判決建議供承辦人校閱
```

首頁、第一步與第二步的頁面放在 `frontend/pages/`；第三步目前仍由 `legal_tool/web_app.py` 的原型模板產生。

## 六、如何啟動網站

在專案根目錄執行：

```bash
./.venv/bin/python app.py
```

請使用終端機啟動訊息顯示的網址，例如：

```text
http://127.0.0.1:5000/
```

程式會從 `5000` 開始自動尋找可用埠。如果 `5000` 已被占用，可能會改用 `5001`、`5002` 等埠號。你也可以手動指定：

```bash
PORT=5050 ./.venv/bin/python app.py
```

## 七、如何執行批次工具

要測試第一步完整網頁資料流，可執行：

```bash
./.venv/bin/python -m unittest tests.test_library_ingestion -v
```

這會驗證 PDF／TXT／JSON 檔案建立、階層節點、Finder「的副本」檔名清理，以及三個網頁 API 能否互相讀回資料。

### PDF 批次轉 TXT

```bash
./.venv/bin/python -m scripts.conversion.batch_pdf_to_txt <PDF資料夾>
```

預設輸出到：

```text
data/processed/text/
```

### PDF 批次轉 JSON

```bash
./.venv/bin/python -m scripts.conversion.batch_pdf_to_json <PDF資料夾>
```

批次／測試流程預設輸出到：

```text
data/processed/json/
```

網頁第一步上傳後的 JSON 則獨立放在：

```text
data/processed/json_web_uploads/
```

### 清理 JSON metadata

```bash
./.venv/bin/python -m scripts.conversion.clean_json_metadata
```

如果要指定資料夾：

```bash
./.venv/bin/python -m scripts.conversion.clean_json_metadata data/processed/json
```

### 測試爭議點相似度

```bash
./.venv/bin/python -m tests.rag_issue_test \
  --query-file data/inputs/測試爭議點.txt
```

如果要重新建立向量資料庫：

```bash
./.venv/bin/python -m tests.rag_issue_test \
  --query-file data/inputs/測試爭議點.txt \
  --rebuild
```

## 八、Notebook 使用方式

Notebook 位於 `notebooks/`：

- `batch_pdf_to_txt_walkthrough.ipynb`：逐步觀察 PDF 轉 TXT 的輸入、函式參數、回傳值與輸出檔案
- `analyze_txt_fixed_text.ipynb`：分析多份 TXT 的固定文字、格式標記與共同模板

Notebook 產生的資料會放在：

```text
data/processed/notebook/
data/processed/reports/
```

## 九、哪些是正式流程，哪些不是

### 正式流程會使用的檔案

- `app.py`
- `legal_tool/`
- `frontend/pages/`
- `scripts/conversion/`
- `data/processed/json/`（批次／測試輸出）
- `data/processed/json_web_uploads/`（網頁上傳輸出）
- `data/vector_db/`

### 教學或分析用途

- `notebooks/`
- `tests/convert_to_JSON_test.py`
- `tests/rag_issue_test.py`
- `scripts/loaders/`

### 已停止使用但保留的舊檔案

- `archive/convert_txt_to_JSON_legacy.py`

這支程式是以前使用 AI 模型將 TXT 轉 JSON 的版本。目前正式流程改用 `legal_tool/processing/txt_to_json.py` 的 Python 規則解析，不再使用 AI 模型轉 JSON。

## 十、修改路徑時要注意什麼

如果要更換輸入或輸出資料夾，優先修改：

```text
legal_tool/config.py
```

不要只修改單一腳本裡的路徑，否則可能造成：

- 批次轉檔輸出到 A 資料夾
- RAG 卻從 B 資料夾讀取
- Notebook 找不到 TXT
- 向量資料庫使用舊資料

目前 `app.py`、批次腳本、RAG 測試與 Notebook 都已按照新的資料夾結構更新。
