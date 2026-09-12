"""第一步資料庫工作區的 PDF 匯入服務。"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from legal_tool.processing.pdf_text_utils import extract_pdf_text
from legal_tool.processing.txt_to_json import build_json_data


HISTORICAL_APPEAL_CATEGORY = "歷史訴願決定書"
CASE_ANALYSIS_CATEGORY = "待審訴願書"
LIBRARY_CATEGORIES = {
    "司法院釋字及行政判解",
    "行政函釋",
    "相關法規",
    HISTORICAL_APPEAL_CATEGORY,
}

# 第二步只共用 PDF/TXT 擷取邏輯，不應被第一步的歷史資料庫分類讀取。
CASE_UPLOAD_CATEGORIES = {CASE_ANALYSIS_CATEGORY}


def normalize_source_filename(filename: str) -> str:
    """保留中文名稱，但移除 Finder 產生的「.pdf 的副本.pdf」尾綴。"""
    original_name = Path(filename or "").name.strip()
    return re.sub(
        r"(?:\.pdf)?\s*的副本(?=\.pdf$)",
        "",
        original_name,
        flags=re.IGNORECASE,
    )


def extract_case_law_category(filename: str) -> str:
    """從訴願決定書檔名擷取案件法律／案由類別。"""
    clean_name = Path(normalize_source_filename(filename)).stem
    match = re.search(r"-(?P<law>.+?)事件(?:-|$)", clean_name)
    if match:
        return match.group("law").strip() or "未分類"
    return "未分類"


def count_hierarchy_nodes(nodes) -> int:
    """計算事實或理由階層中所有節點（包含子段落）。"""
    total = 0
    for node in nodes or []:
        total += 1
        total += count_hierarchy_nodes(node.get("子段落") or [])
    return total


def validate_historical_json(data: dict) -> list[str]:
    """檢查可供後續比對使用的核心欄位是否存在。"""
    errors = []
    if not data.get("案號"):
        errors.append("未辨識到案號")
    if not data.get("主文"):
        errors.append("未辨識到主文")
    if not data.get("理由"):
        errors.append("未辨識到理由")
    return errors


def calculate_file_hash(path: Path) -> str:
    """以檔案內容建立穩定識別碼，避免同檔不同名稱被重複匯入。"""
    digest = sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_duplicate_historical_document(
    upload_dir: Path,
    json_dir: Path,
    display_name: str,
    file_hash: str,
) -> dict | None:
    """以內容雜湊優先、乾淨檔名次之，找出已匯入的同一份歷史資料。"""
    for json_path in Path(json_dir).glob("*.json"):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        metadata = data.get("metadata") or {}
        document_id = str(metadata.get("文件ID") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", document_id):
            continue
        existing_pdf_path = Path(upload_dir) / f"{document_id}.pdf"
        if not existing_pdf_path.is_file():
            continue

        existing_name = normalize_source_filename(
            str(metadata.get("顯示檔名") or metadata.get("原始檔名") or "")
        )
        existing_hash = metadata.get("檔案雜湊") or calculate_file_hash(existing_pdf_path)
        if existing_hash == file_hash:
            return {
                "document_id": document_id,
                "display_name": existing_name or display_name,
            }
    return None


def numeric_sort_value(value) -> int:
    """將檔案編號轉成可排序數字；缺值一律排在最後。"""
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 10**12


def list_historical_documents(upload_dir, json_dir) -> list[dict]:
    """列出由網頁匯入且 PDF、JSON 仍存在的歷史訴願決定書。"""
    upload_dir = Path(upload_dir)
    json_dir = Path(json_dir)
    documents = []

    for json_path in json_dir.glob("*.json"):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        metadata = data.get("metadata") or {}
        document_id = str(metadata.get("文件ID") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", document_id):
            continue

        pdf_path = upload_dir / f"{document_id}.pdf"
        txt_path = upload_dir / f"{document_id}.txt"
        if not pdf_path.is_file():
            continue

        validation_errors = validate_historical_json(data)
        documents.append({
            "document_id": document_id,
            "display_name": (
                metadata.get("顯示檔名")
                or metadata.get("原始檔名")
                or f"{document_id}.pdf"
            ),
            "category": metadata.get("資料分類") or HISTORICAL_APPEAL_CATEGORY,
            "law_category": metadata.get("案件法律類別") or extract_case_law_category(
                metadata.get("顯示檔名") or metadata.get("原始檔名") or ""
            ),
            "year": str(metadata.get("年度") or ""),
            "file_number": str(metadata.get("檔案編號") or ""),
            "pdf_name": pdf_path.name,
            "pdf_size": pdf_path.stat().st_size,
            "txt_name": txt_path.name if txt_path.is_file() else None,
            "json_name": json_path.name,
            "processing_status": "needs_review" if validation_errors else "completed",
            "validation_errors": validation_errors,
            "parsed_summary": {
                "案號": data.get("案號"),
                "主文": data.get("主文"),
                "事實節點數": count_hierarchy_nodes(data.get("事實")),
                "理由節點數": count_hierarchy_nodes(data.get("理由")),
            },
            "updated_at": json_path.stat().st_mtime,
        })

    return sorted(
        documents,
        key=lambda document: (
            -numeric_sort_value(document["year"]),
            numeric_sort_value(document["file_number"]),
            document["display_name"],
        ),
    )


def ingest_library_pdf(uploaded_file, category: str, upload_dir, json_dir) -> dict:
    """儲存 PDF/TXT；歷史訴願決定書另外建立階層式 JSON。"""
    if category not in LIBRARY_CATEGORIES | CASE_UPLOAD_CATEGORIES:
        raise ValueError("不支援的法律資料分類")

    original_name = Path(uploaded_file.filename or "").name.strip()
    if not original_name or Path(original_name).suffix.lower() != ".pdf":
        raise ValueError(f"{original_name or '未命名檔案'} 不是 PDF 檔案")

    upload_dir = Path(upload_dir)
    json_dir = Path(json_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)

    document_id = uuid4().hex
    pdf_filename = f"{document_id}.pdf"
    txt_filename = f"{document_id}.txt"
    json_filename = f"{document_id}.json"
    pdf_path = upload_dir / pdf_filename
    txt_path = upload_dir / txt_filename
    json_path = json_dir / json_filename
    created_paths = []

    try:
        uploaded_file.save(pdf_path)
        created_paths.append(pdf_path)

        display_name = normalize_source_filename(original_name)
        file_hash = calculate_file_hash(pdf_path)
        if category == HISTORICAL_APPEAL_CATEGORY:
            duplicate = find_duplicate_historical_document(
                upload_dir, json_dir, display_name, file_hash
            )
            if duplicate:
                pdf_path.unlink(missing_ok=True)
                created_paths.remove(pdf_path)
                return {
                    "source_name": original_name,
                    "display_name": duplicate["display_name"],
                    "category": category,
                    "duplicate": True,
                    "duplicate_of": duplicate["document_id"],
                    "processing_status": "duplicate",
                }

        text_content = extract_pdf_text(pdf_path)
        if not text_content.strip():
            raise ValueError("PDF 沒有可擷取的文字，可能是掃描影像檔")

        txt_path.write_text(text_content, encoding="utf-8")
        created_paths.append(txt_path)

        result = {
            "document_id": document_id,
            "source_name": original_name,
            "display_name": display_name,
            "category": category,
            "pdf_name": pdf_filename,
            "txt_name": txt_filename,
            "json_name": None,
            "processing_status": "completed",
            "validation_errors": [],
            "parsed_summary": None,
            "law_category": extract_case_law_category(display_name),
        }

        if category != HISTORICAL_APPEAL_CATEGORY:
            result["json_status"] = "not_applicable"
            return result

        json_data = build_json_data(text_content, display_name)
        if json_data is None:
            raise ValueError("無法建立訴願決定書 JSON")

        metadata = json_data.setdefault("metadata", {})
        metadata.update({
            "文件ID": document_id,
            "原始檔名": original_name,
            "顯示檔名": display_name,
            "資料分類": category,
            "結構版本": "1.0",
            "檔案雜湊": file_hash,
            "案件法律類別": extract_case_law_category(display_name),
        })

        validation_errors = validate_historical_json(json_data)
        json_path.write_text(
            json.dumps(json_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        created_paths.append(json_path)

        result.update({
            "json_name": json_filename,
            "json_status": "needs_review" if validation_errors else "completed",
            "processing_status": "needs_review" if validation_errors else "completed",
            "validation_errors": validation_errors,
            "parsed_summary": {
                "案號": json_data.get("案號"),
                "主文": json_data.get("主文"),
                "事實節點數": count_hierarchy_nodes(json_data.get("事實")),
                "理由節點數": count_hierarchy_nodes(json_data.get("理由")),
            },
            "year": str(metadata.get("年度") or ""),
            "file_number": str(metadata.get("檔案編號") or ""),
            "law_category": str(metadata.get("案件法律類別") or "未分類"),
        })
        return result
    except Exception:
        for path in reversed(created_paths):
            path.unlink(missing_ok=True)
        raise
