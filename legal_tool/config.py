"""專案共用路徑設定。"""

import os
import socket
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PERSISTENT_DATA_ROOT = Path(
    os.environ.get("PERSISTENT_DATA_ROOT") or PROJECT_ROOT
).expanduser().resolve()
DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "inputs"
TEXT_OUTPUT_DIR = DATA_DIR / "processed" / "text"
JSON_OUTPUT_DIR = DATA_DIR / "processed" / "json"
# 網頁第一步上傳後產生的 JSON，與既有批次／測試 JSON 分開保存。
WEB_JSON_OUTPUT_DIR = (
    PERSISTENT_DATA_ROOT / "data" / "processed" / "json_web_uploads"
)
REPORT_DIR = DATA_DIR / "processed" / "reports"
NOTEBOOK_OUTPUT_DIR = DATA_DIR / "processed" / "notebook"
LEGACY_JSON_OUTPUT_DIR = DATA_DIR / "processed" / "json_legacy_model"
VECTOR_DB_DIR = DATA_DIR / "vector_db" / "chroma_legal_db"
UPLOAD_DIR = PERSISTENT_DATA_ROOT / "uploads"
# 第二步待審訴願案件的檔案區，與第一步歷史資料庫完全分開。
CASE_UPLOAD_DIR = PERSISTENT_DATA_ROOT / "case_uploads"
EXAMPLE_DIR = DATA_DIR / "examples"
FRONTEND_PAGES_DIR = PROJECT_ROOT / "frontend" / "pages"


def get_server_port(start_port=5000, attempts=20):
    """取得可用的本機埠號；若設定 PORT，則優先使用該埠號。"""
    configured_port = os.environ.get("PORT")
    if configured_port:
        try:
            port = int(configured_port)
        except ValueError as error:
            raise ValueError("PORT 必須是整數埠號") from error
        if not 1 <= port <= 65535:
            raise ValueError("PORT 必須介於 1 到 65535 之間")
        return port

    for port in range(start_port, start_port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.15)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port

    raise RuntimeError(
        f"找不到可用的本機埠號（已檢查 {start_port} 到 {start_port + attempts - 1}）"
    )
