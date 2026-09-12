# AWS Demo 部署說明

## 採用架構

依 `Supported AWS Services List 20260722.xlsx` 的權限，Demo 採用：

- Elastic Beanstalk：執行 Flask／Gunicorn
- EC2：由 Elastic Beanstalk 管理執行個體
- EFS：保存第一步與第二步上傳檔案
- Secrets Manager 或 SSM Parameter Store：保存研究 API 存取碼與 Gemini API Key
- CloudWatch Logs：保存應用程式與錯誤紀錄
- ACM：自訂網域時提供 HTTPS 憑證

App Runner 不在可用服務清單，因此不採用。S3、DynamoDB、S3 Vectors 均可用，保留給正式版改造。

## 部署前檔案

- `application.py`：Elastic Beanstalk WSGI 入口
- `Procfile`：以 Gunicorn 在 8000 埠啟動
- `requirements.txt`：AWS 網頁所需的精簡套件
- `.ebignore`：排除 2 GB 以上的 `.venv`、測試與本機產物
- `.platform/hooks/predeploy/10_mount_efs.sh`：部署時以 TLS 自動掛載 EFS
- `.ebextensions/01-efs-environment.config`：設定 EFS ID、掛載路徑與程式資料根目錄

## AWS 建立順序

1. 選定一個 AWS Region，後續 EFS、Elastic Beanstalk、Secrets Manager 都使用同一區域。
2. 建立 VPC 或使用活動提供的既有 VPC。
3. 建立 EFS，並在 Elastic Beanstalk 使用的每個子網建立 mount target。
4. EFS security group 開放 TCP 2049，但來源只允許 Elastic Beanstalk EC2 的 security group。
5. 專案部署時會將 EFS 根目錄掛載到 `/mnt/efs/legal-demo`，不需要登入 EC2 手動掛載。
6. 建立 Secrets Manager secrets：`legal-demo/research-access-code`、`legal-demo/gemini-api-key`。
7. 建立 Elastic Beanstalk Python 環境。Demo 可先使用 Single instance。
8. 將環境的健康檢查路徑設為 `/health`。
9. EFS 相關環境變數已由 `.ebextensions/01-efs-environment.config` 設定；其餘設定如下：

   - `EFS_FILE_SYSTEM_ID=fs-0fe868cb4981cfd1b`
   - `EFS_MOUNT_DIRECTORY=/mnt/efs/legal-demo`
   - `PERSISTENT_DATA_ROOT=/mnt/efs/legal-demo`
   - `RESEARCH_API_BASE_URL=https://temporal-law-api-867487539733.asia-east1.run.app`
   - `ALLOW_CUSTOM_RESEARCH_API_URL=false`
   - `RESEARCH_ACCESS_CODE`：由 Secrets Manager 注入
   - `GEMINI_API_KEY`：由 Secrets Manager 注入

10. 部署專案原始碼。
11. 將既有 `uploads/`、`case_uploads/`、`data/processed/json_web_uploads/` 搬到 EFS 對應位置。
12. 測試完成後，再以 ACM、Load Balancer 與自訂網域啟用 HTTPS。

## EB CLI 指令

先在 AWS 提供的終端環境完成憑證與 Region 設定，再於專案根目錄執行：

```bash
eb init
eb create legal-demo-env --single
eb deploy
eb status
eb open
```

實際環境名稱與 Region 依活動帳號規則選擇。不要把 Access Key、Secret Access Key、Gemini API Key 寫入檔案或指令歷史。

## 資料目錄

設定 `PERSISTENT_DATA_ROOT=/mnt/efs/legal-demo` 後，程式會使用：

```text
/mnt/efs/legal-demo/
├── uploads/
├── case_uploads/
└── data/processed/json_web_uploads/
```

沒有設定時仍使用專案內原本的資料夾，因此本機啟動方式不變。

## EFS 自動掛載

目前專案使用既有 EFS `fs-0fe868cb4981cfd1b`。Elastic Beanstalk 每次部署新版本時會：

1. 安裝 Amazon Linux 2023 的 `amazon-efs-utils`。
2. 將 EFS 根目錄以 TLS 掛載到 `/mnt/efs/legal-demo`。
3. 將 `_netdev,tls` 掛載設定寫入 `/etc/fstab`，讓 EC2 重開機後重新掛載。
4. 建立 `uploads/`、`case_uploads/` 與 `data/processed/json_web_uploads/`。
5. 將資料夾擁有者設為 Elastic Beanstalk 的 `webapp` 使用者。

部署前必須確認 EFS mount target 使用的 security group 已有以下 inbound rule：

```text
Type: NFS
Protocol: TCP
Port: 2049
Source: sg-00e2dc86f695d0571
```

其中來源是目前 `Law-web-env` 的 EC2 security group；請勿將 NFS 開放給 `0.0.0.0/0`。若日後重建 Elastic Beanstalk 環境，EC2 security group 可能改變，屆時要同步更新這條規則。

## 驗證清單

1. `GET /health` 回傳 `{"status":"ok"}`。
2. 首頁、第一步、第二步、第三步都能開啟。
3. 第一部上傳 PDF 後能產生 TXT 與 JSON。
4. 重新啟動環境後，第一步資料仍存在。
5. 第二步能建立 case_id、補充卷證並執行分析。
6. 重新整理後能還原案件與本 Session 歷次研究。
7. 第三步能讀取同一案件資料。
8. 瀏覽器開發者工具中沒有伺服器端 Gemini API Key。
9. 任意研究 API 網址不能覆蓋伺服器設定。
10. CloudWatch Logs 沒有 4xx／5xx 或 Gunicorn timeout。

## 正式版後續

Demo 穩定後可將 PDF／TXT／JSON 從 EFS 改存 S3，metadata 改存 DynamoDB；歷史案例比對則應由研究 API 或獨立的雲端檢索服務處理。這些改造不納入目前的 Demo 部署。
