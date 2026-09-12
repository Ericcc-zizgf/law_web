import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests
from flask import Flask, Response, jsonify, render_template_string, request, send_from_directory, stream_with_context
from flask_cors import CORS

from legal_tool.config import (
    CASE_UPLOAD_DIR,
    FRONTEND_PAGES_DIR,
    RAG_INDEX_DIR,
    WEB_JSON_OUTPUT_DIR,
    PROJECT_ROOT,
    UPLOAD_DIR,
    get_server_port,
)
from legal_tool.services.library_ingestion import (
    CASE_ANALYSIS_CATEGORY,
    HISTORICAL_APPEAL_CATEGORY,
    LIBRARY_CATEGORIES,
    ingest_library_pdf,
    list_historical_documents,
)
from legal_tool.services.historical_similarity import (
    load_historical_chunks,
    search_historical_chunks,
    search_semantic_historical_chunks,
)
from legal_tool.services.semantic_rag import (
    SemanticRagError,
    embedding_client_from_environment,
    semantic_index_status,
)

BASE_DIR = PROJECT_ROOT
# 網頁上傳流程專用；舊有批次／測試 JSON 仍留在 data/processed/json。
JSON_OUTPUT_DIR = WEB_JSON_OUTPUT_DIR
UPLOAD_FOLDER = UPLOAD_DIR
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
CASE_UPLOAD_FOLDER = CASE_UPLOAD_DIR
CASE_UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
allowed_cors_origins = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "").split(",")
    if origin.strip()
]
if allowed_cors_origins:
    CORS(app, origins=allowed_cors_origins)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
RESEARCH_API_DEFAULT_URL = os.environ.get(
    "RESEARCH_API_BASE_URL",
    "https://temporal-law-api-867487539733.asia-east1.run.app",
).strip().rstrip("/")
ALLOW_CUSTOM_RESEARCH_API_URL = os.environ.get(
    "ALLOW_CUSTOM_RESEARCH_API_URL", "false"
).strip().lower() in {"1", "true", "yes", "on"}
RESEARCH_ACCESS_CODE = os.environ.get("RESEARCH_ACCESS_CODE", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
RAG_ENABLED = os.environ.get("RAG_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on",
}
RAG_REQUIRE_LAW_FILTER = os.environ.get(
    "RAG_REQUIRE_LAW_FILTER", "true"
).strip().lower() in {"1", "true", "yes", "on"}
try:
    RAG_MIN_SCORE = max(0.0, min(1.0, float(os.environ.get("RAG_MIN_SCORE", "0.35"))))
except ValueError:
    RAG_MIN_SCORE = 0.35


@app.get("/")
def index():
    """提供訴願書判決工具平台首頁。"""
    return send_from_directory(FRONTEND_PAGES_DIR, "platform_home.html")


@app.get("/health")
def health():
    """供 Elastic Beanstalk 健康檢查使用，不回傳任何機密資訊。"""
    return jsonify(status="ok")


@app.get("/api/research/config")
def research_proxy_config():
    """回傳可公開的代理設定狀態，絕不回傳金鑰內容。"""
    return jsonify(
        api_base_url=RESEARCH_API_DEFAULT_URL,
        allow_custom_api_url=ALLOW_CUSTOM_RESEARCH_API_URL,
        access_code_configured=bool(RESEARCH_ACCESS_CODE),
        gemini_api_key_configured=bool(GEMINI_API_KEY),
    )


@app.get("/data-library")
def data_library():
    """第一步：既有的 PDF 歷史資料匯入與閱讀工具。"""
    return send_from_directory(FRONTEND_PAGES_DIR, "pdf_manager_ui_v6.html")


@app.get("/case-analysis")
def case_analysis():
    """第二步：待審訴願書批次上傳與分析工作區。"""
    return send_from_directory(FRONTEND_PAGES_DIR, "case_analysis.html")


@app.get("/assets/<path:filename>")
def frontend_asset(filename):
    """提供前端共用的 API client 與其他瀏覽器資源。"""
    return send_from_directory(PROJECT_ROOT / "frontend" / "assets", filename)


def research_api_base_url():
    """取得研究 API 網址；AWS 預設鎖定伺服器設定，避免任意代理請求。"""
    requested_value = (
        request.headers.get("X-Research-Api-Base-Url")
        or request.form.get("api_base_url")
        or ((request.get_json(silent=True) or {}).get("api_base_url"))
    )
    value = requested_value if ALLOW_CUSTOM_RESEARCH_API_URL else RESEARCH_API_DEFAULT_URL
    value = value or RESEARCH_API_DEFAULT_URL
    value = str(value).strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("研究 API 網址格式不正確")
    if parsed.username or parsed.password:
        raise ValueError("研究 API 網址不可包含帳號或密碼")
    return value


def research_api_headers():
    """建立上游授權標頭；AWS 環境變數優先，瀏覽器設定僅供本機開發。"""
    headers = {}
    access_code = RESEARCH_ACCESS_CODE or request.headers.get("X-Access-Code")
    gemini_api_key = GEMINI_API_KEY or request.headers.get("X-Gemini-Api-Key")
    if access_code:
        headers["X-Access-Code"] = access_code
    if gemini_api_key:
        headers["X-Gemini-Api-Key"] = gemini_api_key
    return headers


def proxy_research_response(upstream):
    """把研究 API 的 JSON 錯誤與成功回應原樣轉回前端。"""
    content_type = upstream.headers.get("Content-Type", "application/json")
    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=content_type.split(";", 1)[0],
    )


@app.post("/api/research/case-documents")
def proxy_research_case_document():
    """代理第二步卷證上傳，避免瀏覽器直接跨域呼叫研究 API。"""
    uploaded_file = request.files.get("file")
    if uploaded_file is None or not uploaded_file.filename:
        return jsonify(error="請至少選擇一份案件文件"), 400
    try:
        base_url = research_api_base_url()
        form_data = {"role": request.form.get("role") or "appeal_petition"}
        if request.form.get("case_id"):
            form_data["case_id"] = request.form["case_id"]
        files = {
            "file": (
                uploaded_file.filename,
                uploaded_file.stream,
                uploaded_file.mimetype or "application/octet-stream",
            )
        }
        upstream = requests.post(
            f"{base_url}/v1/case-documents",
            headers=research_api_headers(),
            data=form_data,
            files=files,
            timeout=120,
        )
        return proxy_research_response(upstream)
    except (requests.RequestException, ValueError) as error:
        return jsonify(error=f"研究 API 連線失敗：{error}"), 502


@app.get("/api/research/case-documents")
def proxy_research_case_documents():
    """代理第二步卷證清單查詢。"""
    try:
        base_url = research_api_base_url()
        upstream = requests.get(
            f"{base_url}/v1/case-documents",
            params={"case_id": request.args.get("case_id", "")},
            headers=research_api_headers(),
            timeout=30,
        )
        return proxy_research_response(upstream)
    except (requests.RequestException, ValueError) as error:
        return jsonify(error=f"研究 API 連線失敗：{error}"), 502


@app.get("/api/research/case-documents/<document_id>")
def proxy_research_case_document_file(document_id):
    """代理朋友 API 的單一卷證閱讀／下載網址。"""
    try:
        base_url = research_api_base_url()
        headers = research_api_headers()
        if request.headers.get("Range"):
            headers["Range"] = request.headers["Range"]
        upstream = requests.get(
            f"{base_url}/v1/case-documents/{document_id}",
            headers=headers,
            timeout=120,
        )
        response_headers = {}
        for header in ("Content-Disposition", "Content-Length", "Content-Range", "Accept-Ranges"):
            if upstream.headers.get(header):
                response_headers[header] = upstream.headers[header]
        return Response(
            upstream.content,
            status=upstream.status_code,
            headers=response_headers,
            content_type=upstream.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0],
        )
    except (requests.RequestException, ValueError) as error:
        return jsonify(error=f"研究 API 連線失敗：{error}"), 502


@app.get("/api/research/cases")
def proxy_research_cases():
    """代理朋友研究網站的案件 Session 清單。"""
    try:
        base_url = research_api_base_url()
        upstream = requests.get(
            f"{base_url}/v1/cases",
            params={"limit": request.args.get("limit", "50")},
            headers=research_api_headers(),
            timeout=30,
        )
        return proxy_research_response(upstream)
    except (requests.RequestException, ValueError) as error:
        return jsonify(error=f"研究 API 連線失敗：{error}"), 502


@app.post("/api/research/cases")
def proxy_research_create_case():
    """代理建立新的案件 Session。"""
    try:
        base_url = research_api_base_url()
        upstream = requests.post(
            f"{base_url}/v1/cases",
            headers={**research_api_headers(), "Content-Type": "application/json"},
            json=request.get_json(silent=True) or {},
            timeout=30,
        )
        return proxy_research_response(upstream)
    except (requests.RequestException, ValueError) as error:
        return jsonify(error=f"研究 API 連線失敗：{error}"), 502


@app.get("/api/research/cases/<case_id>/runs")
def proxy_research_case_runs(case_id):
    """代理指定案件的研究紀錄。"""
    try:
        base_url = research_api_base_url()
        upstream = requests.get(
            f"{base_url}/v1/cases/{case_id}/runs",
            params={"limit": request.args.get("limit", "30")},
            headers=research_api_headers(),
            timeout=30,
        )
        return proxy_research_response(upstream)
    except (requests.RequestException, ValueError) as error:
        return jsonify(error=f"研究 API 連線失敗：{error}"), 502


@app.get("/api/research/agent-runs/<run_id>")
def proxy_research_agent_run(run_id):
    """代理讀取單次 Agent 稽核軌跡。"""
    try:
        base_url = research_api_base_url()
        upstream = requests.get(
            f"{base_url}/v1/agent-runs/{run_id}",
            headers=research_api_headers(),
            timeout=30,
        )
        return proxy_research_response(upstream)
    except (requests.RequestException, ValueError) as error:
        return jsonify(error=f"研究 API 連線失敗：{error}"), 502


@app.get("/api/research/official-article-history")
def proxy_research_article_history():
    """代理查詢官方法條沿革。"""
    try:
        base_url = research_api_base_url()
        upstream = requests.get(
            f"{base_url}/v1/official-article-history",
            params={
                "fname": request.args.get("fname", ""),
                "article": request.args.get("article", ""),
            },
            headers=research_api_headers(),
            timeout=60,
        )
        return proxy_research_response(upstream)
    except (requests.RequestException, ValueError) as error:
        return jsonify(error=f"研究 API 連線失敗：{error}"), 502


@app.post("/api/research/ask")
def proxy_research_ask():
    """代理研究 API 的 SSE 分析串流，讓前端保持同源請求。"""
    try:
        base_url = research_api_base_url()
        upstream = requests.post(
            f"{base_url}/v1/ask?stream=1",
            headers={**research_api_headers(), "Content-Type": "application/json"},
            json=request.get_json(silent=True) or {},
            stream=True,
            timeout=(15, 600),
        )
    except (requests.RequestException, ValueError) as error:
        return jsonify(error=f"研究 API 連線失敗：{error}"), 502

    if not upstream.ok:
        return proxy_research_response(upstream)

    def stream_response():
        try:
            for chunk in upstream.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(
        stream_with_context(stream_response()),
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "text/event-stream").split(";", 1)[0],
    )


@app.get("/case-notes")
def case_notes():
    """第二步：使用第一步的 PDF 閱讀與筆記工具檢視待審案件。"""
    return send_from_directory(FRONTEND_PAGES_DIR, "pdf_manager_ui_v6.html")


@app.get("/decision-recommendation")
def decision_recommendation():
    """第三步預留頁：判決建議生成。"""
    return render_placeholder_page(
        active_step=3,
        step="步驟 03",
        title="判決建議生成",
        description="此頁將依案件分析與歷史資料比對結果，彙整可供承辦人校閱的判決建議。",
    )


def render_placeholder_page(active_step: int, step: str, title: str, description: str):
    """提供第二、三步的工作頁面原型，保留之後接上分析服務的區域。"""
    return render_template_string(
        """
        <!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>{{ title }}・訴願書判決工具平台</title>
        <style>
          :root{--navy:#102a43;--teal:#0f766e;--amber:#b7791f;--ink:#20313f;--muted:#647585;--line:#d4e0e4;--canvas:#f5f8f8;--paper:#fff}
          *{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}a{color:inherit;text-decoration:none}
          .process-rail{position:fixed;inset:0 auto 0 0;z-index:5;width:88px;display:flex;flex-direction:column;align-items:center;padding:28px 0;background:#fff;border-right:1px solid var(--line)}
          .process-rail-title{margin:0 0 32px;color:#82909b;font-size:10px;font-weight:800;letter-spacing:.16em;writing-mode:vertical-rl}.process-steps{position:relative;display:flex;flex-direction:column;align-items:center;gap:58px;margin:0;padding:12px 0;list-style:none}.process-steps:before{content:"";position:absolute;top:32px;bottom:32px;left:50%;width:1px;background:#d8e1e5;transform:translateX(-50%)}
          .process-step{--rail-color:#aebdc5;position:relative;z-index:1;display:flex;align-items:center;justify-content:center;width:38px;height:38px;border:2px solid var(--rail-color);border-radius:50%;background:#fff;color:#71818c;font-size:10px;font-weight:800;text-decoration:none;transition:transform .18s ease,border-color .18s ease,color .18s ease,box-shadow .18s ease,background-color .18s ease}.process-step[data-step="1"]{--rail-color:var(--navy)}.process-step[data-step="2"]{--rail-color:var(--teal)}.process-step[data-step="3"]{--rail-color:var(--amber)}
          .process-step:hover,.process-step:focus-visible{transform:scale(1.14);outline:none;color:var(--rail-color)}.process-step.active{background:var(--rail-color);color:#fff;box-shadow:0 0 0 5px color-mix(in srgb,var(--rail-color) 18%,transparent),0 0 14px color-mix(in srgb,var(--rail-color) 32%,transparent)}.process-step-label{position:absolute;left:48px;padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:#fff;color:#3e515e;font-size:12px;font-weight:700;white-space:nowrap;opacity:0;pointer-events:none;transform:translateX(-5px);transition:opacity .18s ease,transform .18s ease}.process-step:hover .process-step-label,.process-step:focus-visible .process-step-label{opacity:1;transform:translateX(0)}
          main{width:100%;max-width:none;margin:0;padding:40px 32px 76px 120px}.workspace-header{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;min-height:74px;margin-bottom:26px}.workspace-actions{display:flex;align-items:center;gap:10px;flex:0 0 auto}.brand-lockup{display:flex;align-items:flex-start;gap:15px}.brand-mark{display:grid;place-items:center;width:46px;height:46px;flex:0 0 auto;border-radius:14px;background:var(--ink);color:#fff;font-size:19px;font-weight:800;letter-spacing:.08em;box-shadow:0 8px 18px rgba(24,43,58,.16)}.workspace-header h1{margin:0;color:var(--ink);font-size:clamp(24px,3vw,34px);line-height:1.18;letter-spacing:-.04em}.workspace-subtitle{max-width:620px;margin:9px 0 0;color:var(--muted);font-size:14px;line-height:1.65}.workspace-badge{padding:8px 11px;border:1px solid #d79a9a;border-radius:999px;color:#a33f45;font-size:13px;font-weight:700;white-space:nowrap;background:#fff8f8;transition:background-color .18s ease,border-color .18s ease,color .18s ease}.workspace-badge:hover,.workspace-badge:focus-visible{border-color:#bc6267;color:#8f2f37;background:#fff0f0;outline:none}
          .workspace-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(320px,.85fr);gap:22px;align-items:stretch}.panel{border:1px solid var(--line);border-radius:18px;background:var(--paper);box-shadow:0 8px 24px rgba(16,42,67,.04)}.panel-heading{display:flex;gap:15px;align-items:flex-start;padding:26px 28px 18px}.panel-index{display:grid;place-items:center;flex:0 0 auto;width:34px;height:34px;border-radius:10px;background:#e8f3f1;color:var(--teal);font-size:12px;font-weight:800}.panel h2{margin:0;color:var(--navy);font-size:21px;line-height:1.4}.panel-heading p{margin:5px 0 0;color:var(--muted);font-size:14px;line-height:1.6}
          .dropzone{--upload-progress:0%;position:relative;overflow:hidden;margin:0 26px 26px;padding:34px 24px;border:1px dashed #9eb8b7;border-radius:14px;background:#f7fbfa;text-align:center;transition:border-color .2s ease,background-color .2s ease,transform .2s ease}.dropzone:before{content:"";position:absolute;inset:0 auto 0 0;z-index:0;width:var(--upload-progress);border-radius:inherit;background:linear-gradient(90deg,rgba(141,202,181,.62),rgba(224,242,235,.82));opacity:0;pointer-events:none;transition:width .16s ease,opacity .2s ease}.dropzone>*{position:relative;z-index:1}.dropzone.has-upload-progress:before{opacity:1}.dropzone.is-dragging{border-color:var(--teal);background:#eaf7f3;transform:scale(1.006)}.dropzone.is-uploading{border-color:var(--teal);background:#f3faf7}.dropzone.is-uploading .outline-action{opacity:.55;pointer-events:none}.dropzone.is-progress-fading:before{opacity:0;transition:opacity 1s ease}.drop-icon{display:grid;place-items:center;width:48px;height:48px;margin:0 auto 14px;border-radius:14px;background:#dcefeb;color:var(--teal);font-size:26px}.dropzone strong{display:block;color:var(--ink);font-size:17px}.dropzone p{margin:7px 0 18px;color:var(--muted);font-size:14px}.case-file-input{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}.outline-action{display:inline-flex;padding:9px 14px;border:1px solid #9eb8b7;border-radius:8px;color:var(--teal);font-size:14px;font-weight:750;background:#fff;cursor:pointer;transition:color .18s ease,background-color .18s ease,border-color .18s ease,transform .18s ease}.outline-action:hover,.outline-action:focus-visible{border-color:var(--teal);color:#fff;background:var(--teal);transform:translateY(-1px);outline:none}.upload-progress{position:absolute;inset:0;z-index:2;display:flex;align-items:flex-end;justify-content:flex-end;padding:0 20px 12px;pointer-events:none;opacity:1;transition:opacity 1s ease}.upload-progress[hidden]{display:none!important}.dropzone.is-progress-fading .upload-progress{opacity:0}.upload-progress-copy{display:flex;flex-direction:column;align-items:flex-end;gap:2px;min-width:150px;color:rgba(82,98,112,.68);font-size:12px;font-weight:700;text-align:right;text-shadow:0 1px 1px rgba(255,255,255,.35)}.upload-progress-percent{color:rgba(82,98,112,.72);font-size:13px;font-variant-numeric:tabular-nums}.upload-progress.is-error .upload-progress-copy{color:#a33f45}.upload-progress-track{display:none}
          .panel-top{display:flex;align-items:center;justify-content:space-between;padding:26px 28px 18px}.panel-top h2{font-size:19px}.status-chip,.tag{display:inline-flex;align-items:center;padding:6px 9px;border-radius:99px;background:#f1f4f5;color:#71818c;font-size:12px;font-weight:750}.status-chip.is-uploading{color:#0f766e;background:#e6f4f1}.status-chip.is-complete{color:#2f7654;background:#e8f5ed}.status-chip.is-error{color:#a33f45;background:#fff0f0}.queue-empty{min-height:202px;margin:0 26px 26px;padding:36px 24px;display:grid;place-items:center;border:1px solid #edf1f2;border-radius:14px;color:#8a98a0;text-align:center;font-size:14px;line-height:1.7}.queue-empty strong{display:block;color:#51636f;font-size:16px}.queue-list{display:flex;flex-direction:column;gap:9px;min-height:202px;margin:0 26px 26px}.queue-list[hidden]{display:none}.queue-item{display:grid;grid-template-columns:36px minmax(0,1fr);gap:11px;align-items:center;padding:12px;border:1px solid #dbe7e5;border-radius:11px;background:#fbfdfc}.queue-file-icon{display:grid;place-items:center;width:36px;height:42px;border:1px solid #bdd5cf;border-radius:8px;color:var(--teal);background:#f2faf7;font-size:10px;font-weight:800}.queue-file-copy{min-width:0}.queue-file-name{overflow:hidden;color:var(--ink);font-size:13px;font-weight:750;text-overflow:ellipsis;white-space:nowrap}.queue-file-meta{margin-top:4px;color:#819099;font-size:12px}
          .analysis-panel{margin-top:22px;padding:26px 28px 28px}.section-heading{display:flex;align-items:end;justify-content:space-between;gap:16px;margin-bottom:20px}.section-heading h2{font-size:21px}.section-heading p{margin:0;color:var(--muted);font-size:13px}.analysis-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.analysis-card{min-height:145px;padding:18px;border:1px solid var(--line);border-radius:12px;background:#fbfcfc}.analysis-card span{color:#8a98a0;font-size:12px;font-weight:800;letter-spacing:.08em}.analysis-card h3{margin:14px 0 8px;color:var(--navy);font-size:17px}.analysis-card p{margin:0;color:#87949c;font-size:14px;line-height:1.6}
          .recommendation-grid{display:grid;grid-template-columns:300px minmax(0,1fr);gap:22px;align-items:stretch}.case-list,.recommendation-preview{padding:26px 28px}.case-list h2,.recommendation-preview h2,.review-panel h2{margin:0;color:var(--navy);font-size:21px}.case-count{margin:7px 0 22px;color:var(--muted);font-size:14px}.case-empty{padding:24px 0;color:#87949c;font-size:14px;line-height:1.7}.case-empty strong{display:block;margin-bottom:8px;color:#51636f;font-size:16px}.third-case-queue{display:grid;gap:9px;margin-top:20px;max-height:420px;overflow:auto}.third-case-queue[hidden]{display:none}.third-case-card{display:grid;grid-template-columns:36px minmax(0,1fr);gap:11px;align-items:center;width:100%;padding:12px;border:1px solid #dbe7e5;border-radius:11px;background:#fbfdfc;color:var(--ink);text-align:left;transition:border-color .18s ease,background-color .18s ease,box-shadow .18s ease,transform .18s ease}.third-case-card:hover{border-color:#9fc9c1;background:#f6fbf9;box-shadow:0 5px 14px rgba(15,118,110,.08);transform:translateY(-1px)}.third-case-card.is-selected{border-color:var(--teal);background:#eef8f5;box-shadow:inset 3px 0 0 var(--teal),0 5px 14px rgba(15,118,110,.1)}.third-case-icon{display:grid;place-items:center;width:36px;height:42px;padding:0;border:1px solid #bdd5cf;border-radius:8px;color:var(--teal);background:#f2faf7;font:800 10px inherit;cursor:pointer;transition:border-color .18s ease,background-color .18s ease,color .18s ease,transform .18s ease,box-shadow .18s ease}.third-case-icon:hover,.third-case-icon:focus-visible{border-color:var(--teal);color:#fff;background:var(--teal);outline:none;box-shadow:0 5px 12px rgba(15,118,110,.18);transform:translateY(-1px)}.third-case-icon:disabled{cursor:wait;opacity:.58}.third-case-copy{min-width:0;width:100%;padding:5px 4px;border:0;color:inherit;background:transparent;font:inherit;text-align:left;cursor:pointer}.third-case-copy:disabled{cursor:wait}.third-case-copy:focus-visible{border-radius:7px;outline:2px solid rgba(15,118,110,.42);outline-offset:3px}.third-case-name{display:block;overflow:hidden;color:var(--ink);font-size:13px;font-weight:750;text-overflow:ellipsis;white-space:nowrap}.third-case-meta{display:block;margin-top:4px;overflow:hidden;color:#819099;font-size:12px;line-height:1.45;text-overflow:ellipsis;white-space:nowrap}.preview-header{display:flex;align-items:start;justify-content:space-between;gap:16px}.preview-tag{color:#a2742b;background:#fff7df}.preview-empty{min-height:213px;margin-top:22px;padding:44px 24px;display:grid;place-items:center;border:1px dashed #d8dfe1;border-radius:14px;text-align:center;color:#87949c;font-size:14px;line-height:1.7}.preview-empty strong{display:block;margin-bottom:8px;color:#51636f;font-size:17px}.review-panel{margin-top:22px;padding:26px 28px}.review-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:20px}.review-item{padding:17px;border-top:3px solid var(--line);background:#fbfcfc}.review-item:nth-child(1){border-color:var(--navy)}.review-item:nth-child(2){border-color:var(--teal)}.review-item:nth-child(3){border-color:var(--amber)}.review-item strong{display:block;margin-bottom:8px;color:var(--navy);font-size:15px}.review-item span{color:#87949c;font-size:13px}
          @media(max-width:860px){.workspace-grid,.recommendation-grid{grid-template-columns:1fr}.analysis-grid,.review-grid{grid-template-columns:1fr}}
          @media(max-width:720px){.process-rail{width:64px;padding-top:22px}.process-rail-title{margin-bottom:24px}.process-steps{gap:42px}.process-step{width:34px;height:34px}.process-step-label{display:none}main{width:100%;margin:0;padding:34px 18px 56px 80px}.workspace-header{flex-direction:column;gap:14px;min-height:0}.workspace-actions{width:100%;flex-wrap:wrap}.workspace-badge{align-self:flex-start}.workspace-subtitle{font-size:14px}.panel-heading,.panel-top,.analysis-panel,.case-list,.recommendation-preview,.review-panel{padding-left:20px;padding-right:20px}.dropzone,.queue-empty{margin-left:18px;margin-right:18px}}
          .api-settings{margin-top:22px;padding:18px 22px}.api-settings-header{display:flex;align-items:center;justify-content:space-between;gap:14px}.api-settings-header h2{margin:0;color:var(--navy);font-size:17px}.api-settings-status{color:var(--muted);font-size:12px}.api-settings-status.is-warning{color:#a2742b}.api-settings-status.is-success{color:#2f7654}.api-settings-toggle{min-height:34px;padding:6px 11px;border:1px solid var(--line);border-radius:8px;color:var(--navy);background:#fff;cursor:pointer;font:inherit;font-size:12px;font-weight:750}.api-settings-toggle:hover,.api-settings-toggle:focus-visible{border-color:var(--teal);color:var(--teal);outline:none}.api-settings[hidden]{display:none}.api-settings-grid{display:grid;grid-template-columns:minmax(220px,1.3fr) minmax(160px,.8fr) minmax(160px,.8fr) auto;gap:10px;align-items:end;margin-top:14px}.api-settings-field{min-width:0}.api-settings-field label{display:block;margin-bottom:5px;color:var(--muted);font-size:12px;font-weight:700}.api-settings-field input{width:100%;min-height:36px;padding:7px 9px;border:1px solid var(--line);border-radius:8px;color:var(--ink);background:#fff;font:inherit;font-size:12px}.api-settings-save,.recommendation-action{min-height:36px;padding:7px 13px;border:1px solid var(--teal);border-radius:8px;color:#fff;background:var(--teal);cursor:pointer;font:inherit;font-size:12px;font-weight:750}.api-settings-save:hover,.recommendation-action:hover{background:#0a5d56}.recommendation-action:disabled{opacity:.5;cursor:not-allowed}.api-settings-note{margin:9px 0 0;color:var(--muted);font-size:12px;line-height:1.5}.decision-draft{max-height:420px;margin:22px 0 0;padding:18px;border:1px solid var(--line);border-radius:12px;background:#fbfcfc;color:var(--ink);font:inherit;font-size:14px;line-height:1.8;white-space:pre-wrap;overflow:auto}.decision-draft[hidden]{display:none}.preview-header .recommendation-action{flex:0 0 auto}
          @media(max-width:720px){.api-settings-grid{grid-template-columns:1fr}.preview-header{flex-direction:column}.preview-header .recommendation-action{width:100%}}
        </style></head><body>
          <aside class="process-rail" aria-label="訴願書判決工作流程"><p class="process-rail-title">流程</p><nav class="process-steps" aria-label="工作步驟">
            <a class="process-step{% if active_step == 1 %} active{% endif %}" data-step="1" href="/data-library"{% if active_step == 1 %} aria-current="step"{% endif %}>01<span class="process-step-label">匯入歷史資料</span></a>
            <a class="process-step{% if active_step == 2 %} active{% endif %}" data-step="2" href="/case-analysis"{% if active_step == 2 %} aria-current="step"{% endif %}>02<span class="process-step-label">分析主要爭議點</span></a>
            <a class="process-step{% if active_step == 3 %} active{% endif %}" data-step="3" href="/decision-recommendation"{% if active_step == 3 %} aria-current="step"{% endif %}>03<span class="process-step-label">生成判決建議</span></a>
          </nav></aside>
          <main>
            <header class="workspace-header">
              <div class="brand-lockup"><div class="brand-mark" aria-hidden="true">{{ page_number }}</div><div><h1>{{ title }}</h1><p class="workspace-subtitle">{{ description }}</p></div></div>
              <div class="workspace-actions"><button class="api-settings-toggle" id="toggleApiSettings" type="button" aria-expanded="false" aria-controls="apiSettingsPanel">⚙ API 設定</button><a class="workspace-badge" href="/">← 返回工具平台</a></div>
            </header>

            <section class="panel api-settings" id="apiSettingsPanel" aria-label="研究 API 設定" hidden>
              <div class="api-settings-header"><div><h2>研究 API 設定</h2><span class="api-settings-status" id="apiSettingsStatus">尚未設定存取碼</span></div></div>
              <div class="api-settings-body" id="apiSettingsBody">
                <div class="api-settings-grid">
                  <div class="api-settings-field"><label for="apiBaseUrl">API 網址</label><input id="apiBaseUrl" type="url" autocomplete="off"></div>
                  <div class="api-settings-field"><label for="apiAccessCode">存取碼</label><input id="apiAccessCode" type="password" autocomplete="off" placeholder="必要時填寫"></div>
                  <div class="api-settings-field"><label for="apiGeminiKey">Gemini API Key</label><input id="apiGeminiKey" type="password" autocomplete="off" placeholder="可選"></div>
                  <button class="api-settings-save" id="saveApiSettings" type="button">儲存設定</button>
                </div>
                <p class="api-settings-note">與第二步共用瀏覽器設定；判決建議請先在第二步完成案件分析。</p>
              </div>
            </section>

            <section class="recommendation-grid" aria-label="判決建議工作區">
              <section class="panel case-list"><h2>待校閱案件</h2><p class="case-count" id="thirdCaseCount">完成第二步分析後，案件會出現在這裡。</p><div class="third-case-queue" id="thirdCaseQueue" hidden></div><div class="case-empty" id="thirdCaseEmpty"><strong>尚未選取案件</strong>請先完成待審訴願書的上傳與爭議點分析。</div></section>
              <section class="panel recommendation-preview"><div class="preview-header"><div><h2>判決建議預覽</h2><p class="case-count">依案件資料與參考案例產生草稿。</p></div><div><span class="status-chip preview-tag" id="thirdStatus">等待案件</span><button class="recommendation-action" id="generateRecommendation" type="button" disabled>生成判決建議</button></div></div><div class="preview-empty" id="decisionDraftEmpty"><div><strong>尚未產生判決建議</strong>完成第二步分析後，按下按鈕呼叫研究 API。</div></div><pre class="decision-draft" id="decisionDraft" hidden></pre></section>
            </section>
            <section class="panel review-panel" aria-label="判決建議校閱區"><h2>承辦人校閱重點</h2><div class="review-grid"><div class="review-item"><strong>處理方向</strong><span id="reviewDirection">等待案件分析結果</span></div><div class="review-item"><strong>理由與法條</strong><span id="reviewReasons">等待比對參考資料</span></div><div class="review-item"><strong>引用依據</strong><span id="reviewSources">等待產生可追溯來源</span></div></div></section>
          </main>
          <script src="/assets/temporal_law_api.js"></script>
          <script>
            (() => {
              const api = window.temporalLawApi;
              const baseUrl = document.getElementById('apiBaseUrl');
              const accessCode = document.getElementById('apiAccessCode');
              const geminiKey = document.getElementById('apiGeminiKey');
              const settingsStatus = document.getElementById('apiSettingsStatus');
              const saveSettings = document.getElementById('saveApiSettings');
              const toggleSettings = document.getElementById('toggleApiSettings');
              const settingsPanel = document.getElementById('apiSettingsPanel');
              const thirdCaseQueue = document.getElementById('thirdCaseQueue');
              const thirdCaseCount = document.getElementById('thirdCaseCount');
              const caseEmpty = document.getElementById('thirdCaseEmpty');
              const status = document.getElementById('thirdStatus');
              const generate = document.getElementById('generateRecommendation');
              const draftEmpty = document.getElementById('decisionDraftEmpty');
              const draft = document.getElementById('decisionDraft');
              const reviewDirection = document.getElementById('reviewDirection');
              const reviewReasons = document.getElementById('reviewReasons');
              const reviewSources = document.getElementById('reviewSources');

              const settings = api.getSettings();
              baseUrl.value = settings.apiBaseUrl;
              accessCode.value = settings.accessCode;
              geminiKey.value = settings.geminiApiKey;
              settingsStatus.textContent = settings.accessCode ? '已載入 API 設定' : '請輸入 API 存取碼';
              settingsStatus.className = `api-settings-status${settings.accessCode ? ' is-success' : ' is-warning'}`;
              saveSettings.addEventListener('click', () => {
                if (!accessCode.value.trim()) {
                  settingsStatus.textContent = '請先輸入 API 存取碼';
                  settingsStatus.className = 'api-settings-status is-warning';
                  accessCode.focus();
                  return;
                }
                const saved = api.saveSettings({ apiBaseUrl: baseUrl.value, accessCode: accessCode.value, geminiApiKey: geminiKey.value });
                baseUrl.value = saved.apiBaseUrl;
                settingsStatus.textContent = '設定已儲存';
                settingsStatus.className = 'api-settings-status is-success';
              });
              toggleSettings.addEventListener('click', () => {
                const isOpen = !settingsPanel.hidden;
                settingsPanel.hidden = isOpen;
                toggleSettings.setAttribute('aria-expanded', String(!isOpen));
              });

              const readStoredValue = key => {
                const currentValue = localStorage.getItem(key);
                if (currentValue !== null) return currentValue;
                const legacyValue = sessionStorage.getItem(key);
                if (legacyValue !== null) {
                  localStorage.setItem(key, legacyValue);
                  return legacyValue;
                }
                return null;
              };
              let stored = {};
              try { stored = JSON.parse(readStoredValue('case-analysis-last-result') || '{}'); } catch (_error) { stored = {}; }
              let activeCaseId = stored.caseId || readStoredValue('case-analysis-active-id') || '';
              let activeCase = null;
              let reviewCases = [];

              const readReviewCases = () => {
                try {
                  const parsed = JSON.parse(readStoredValue('case-analysis-queue') || '[]');
                  return Array.isArray(parsed) ? parsed.filter(file => file && file.name) : [];
                } catch (_error) {
                  return [];
                }
              };

              const showResult = data => {
                const text = data.decision_draft || data.decision || '';
                if (text) {
                  draft.textContent = text;
                  draft.hidden = false;
                  draftEmpty.hidden = true;
                }
                const issues = data.issues || [];
                const sources = data.sources || [];
                reviewDirection.textContent = issues.length ? issues.map(item => item.title).slice(0, 2).join('；') : 'API 未回傳爭議點摘要';
                reviewReasons.textContent = data.decision_draft ? '已取得判決書草案，請承辦人逐段校閱' : 'API 尚未回傳判決書草案';
                reviewSources.textContent = sources.length ? `${sources.length} 筆卷內或官方參考依據` : 'API 未回傳參考依據';
              };

              const clearReviewPreview = () => {
                draft.textContent = '';
                draft.hidden = true;
                draftEmpty.hidden = false;
                reviewDirection.textContent = '等待案件分析結果';
                reviewReasons.textContent = '等待比對參考資料';
                reviewSources.textContent = '等待產生可追溯來源';
              };

              const selectReviewCase = file => {
                activeCase = file;
                activeCaseId = file.caseId || file.id || '';
                if (activeCaseId) localStorage.setItem('case-analysis-active-id', activeCaseId);
                const selectedResult = file.analysisResult || (
                  stored.caseId && stored.caseId === file.caseId ? stored.result : null
                );
                stored = {
                  caseId: activeCaseId,
                  name: file.name,
                  size: file.size,
                  ...(selectedResult ? { result: selectedResult } : {})
                };
                caseEmpty.hidden = true;
                generate.disabled = !file.caseId;
                status.textContent = selectedResult ? '已完成第二步分析' : (file.caseId ? '可生成判決建議' : '等待 API 案件編號');
                status.className = `status-chip${selectedResult || file.caseId ? ' is-complete' : ''}`;
                if (selectedResult) showResult(selectedResult);
                else clearReviewPreview();
                renderReviewQueue();
              };

              const renderReviewQueue = () => {
                thirdCaseQueue.replaceChildren();
                thirdCaseCount.textContent = reviewCases.length ? `共 ${reviewCases.length} 件` : '完成第二步分析後，案件會出現在這裡。';
                thirdCaseQueue.hidden = reviewCases.length === 0;
                caseEmpty.hidden = reviewCases.length > 0;
                if (!reviewCases.length) {
                  return;
                }
                reviewCases.forEach(file => {
                  const card = document.createElement('article');
                  card.className = `third-case-card${(file.caseId === activeCaseId || file.id === activeCaseId) ? ' is-selected' : ''}`;
                  const icon = document.createElement('button');
                  icon.type = 'button';
                  icon.className = 'third-case-icon';
                  icon.textContent = 'PDF';
                  icon.disabled = !file.pdfUrl;
                  icon.title = file.pdfUrl ? '進入 PDF 筆記頁面' : '尚未取得 PDF';
                  icon.setAttribute('aria-label', file.pdfUrl ? `開啟 ${file.name} 的筆記頁面` : `${file.name} 尚未取得 PDF`);
                  if (file.pdfUrl) {
                    icon.addEventListener('click', event => {
                      event.stopPropagation();
                      const query = new URLSearchParams({ file: file.pdfUrl, name: file.name });
                      window.location.href = `/case-notes?${query.toString()}`;
                    });
                  }
                  const copy = document.createElement('button');
                  copy.type = 'button';
                  copy.className = 'third-case-copy';
                  copy.disabled = !file.caseId || file.error;
                  copy.setAttribute('aria-label', `選取待校閱案件 ${file.name}`);
                  const name = document.createElement('span');
                  name.className = 'third-case-name';
                  name.textContent = file.name;
                  const meta = document.createElement('span');
                  meta.className = 'third-case-meta';
                  const documentCount = Array.isArray(file.documents) ? file.documents.length : 0;
                  const sizeLabel = file.size ? `${Number(file.size).toLocaleString()} bytes` : 'PDF 文件';
                  const stateLabel = file.analysisResult ? '爭議點分析完成' : (file.caseId ? '待生成判決建議' : '尚未建立 API 案件');
                  meta.textContent = `${sizeLabel}・${stateLabel}${documentCount ? `・卷證 ${documentCount} 件` : ''}`;
                  copy.append(name, meta);
                  card.append(icon, copy);
                  copy.addEventListener('click', event => {
                    event.stopPropagation();
                    selectReviewCase(file);
                  });
                  card.addEventListener('click', event => {
                    if (!event.target.closest('.third-case-icon')) selectReviewCase(file);
                  });
                  thirdCaseQueue.append(card);
                });
              };

              reviewCases = readReviewCases();
              renderReviewQueue();
              const initialCase = reviewCases.find(file => file.caseId === activeCaseId || file.id === activeCaseId) || reviewCases[0];
              if (initialCase) selectReviewCase(initialCase);

              generate.addEventListener('click', async () => {
                if (!activeCase?.caseId) return;
                generate.disabled = true;
                status.textContent = 'API 生成中…';
                status.className = 'status-chip is-uploading';
                try {
                  const nextResult = await api.analyzeCase({
                    caseId: activeCase.caseId,
                    question: '請依本案卷證、主要爭議點與相關法規，生成一份可供承辦人校閱的訴願決定書草案，並保留引用依據。',
                    useLocalCache: true,
                    onEvent: (event, data) => {
                      if (event === 'status') status.textContent = data.message || 'API 生成中…';
                      if (event === 'step') status.textContent = data.step?.decision || data.step?.name || 'API 生成中…';
                    }
                  });
                  const nextPayload = { ...stored, caseId: activeCase.caseId, name: activeCase.name, result: nextResult };
                  localStorage.setItem('case-analysis-last-result', JSON.stringify(nextPayload));
                  stored = nextPayload;
                  showResult(nextResult);
                  status.textContent = '判決建議已生成';
                  status.className = 'status-chip is-complete';
                } catch (error) {
                  status.textContent = `生成失敗：${error.message}`;
                  status.className = 'status-chip is-error';
                } finally {
                  generate.disabled = false;
                }
              });
            })();
          </script>
        </body></html>
        """,
        active_step=active_step,
        page_number=f"{active_step:02d}",
        step=step,
        title=title,
        description=description,
    )


@app.get("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


@app.get("/case-uploads/<path:filename>")
def case_uploaded_file(filename):
    """提供第二步待審案件的 PDF/TXT；不與第一步歷史資料共用路徑。"""
    return send_from_directory(CASE_UPLOAD_FOLDER, filename, as_attachment=True)


@app.post("/convert")
def convert_pdf():
    category = str(
        request.form.get("category") or HISTORICAL_APPEAL_CATEGORY
    ).strip()
    if category not in LIBRARY_CATEGORIES:
        return jsonify(error="不支援的法律資料分類"), 400

    files = request.files.getlist("files") or request.files.getlist("file")
    if not files or all(not file.filename for file in files):
        return jsonify(error="請至少上傳一個 PDF 檔案"), 400

    converted_files = []
    for file in files:
        if not file.filename:
            continue
        try:
            converted = ingest_library_pdf(
                uploaded_file=file,
                category=category,
                upload_dir=UPLOAD_FOLDER,
                json_dir=JSON_OUTPUT_DIR,
            )
        except Exception as error:
            return jsonify(error=f"無法處理 {file.filename}：{error}"), 400

        if not converted.get("duplicate"):
            converted["pdf_url"] = f"/uploads/{converted['pdf_name']}"
            converted["txt_url"] = f"/uploads/{converted['txt_name']}"
            converted["json_url"] = (
                f"/api/documents/{converted['document_id']}/json"
                if converted.get("json_name")
                else None
            )
        converted_files.append(converted)

    duplicate_count = sum(file.get("duplicate", False) for file in converted_files)
    completed_count = len(converted_files) - duplicate_count
    return jsonify(
        message=(
            f"已完成 {completed_count} 份 PDF 轉換"
            + (f"，略過 {duplicate_count} 份已上傳文件" if duplicate_count else "")
        ),
        files=converted_files,
    )


@app.post("/convert-case")
def convert_case_pdf():
    """第二步專用的 PDF 擷取流程，只保存案件 PDF/TXT，不建立歷史 JSON。"""
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files or all(not file.filename for file in files):
        return jsonify(error="請至少上傳一個 PDF 檔案"), 400

    converted_files = []
    for file in files:
        if not file.filename:
            continue
        try:
            converted = ingest_library_pdf(
                uploaded_file=file,
                category=CASE_ANALYSIS_CATEGORY,
                upload_dir=CASE_UPLOAD_FOLDER,
                json_dir=CASE_UPLOAD_FOLDER / "metadata",
            )
        except Exception as error:
            return jsonify(error=f"無法處理 {file.filename}：{error}"), 400

        converted["pdf_url"] = f"/case-uploads/{converted['pdf_name']}"
        converted["txt_url"] = f"/case-uploads/{converted['txt_name']}"
        converted["json_url"] = None
        converted_files.append(converted)

    return jsonify(
        message=f"已完成 {len(converted_files)} 份待審訴願書 PDF 擷取",
        files=converted_files,
    )


@app.post("/api/historical-similarity/search")
def search_historical_similarity():
    """以待審案件文字對歷史理由進行可追溯的 AWS 語意 RAG。"""
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query_text") or "").strip()
    case_txt_name = str(payload.get("case_txt_name") or "").strip()
    if query:
        # 爭議點比對只需要 API 摘要，不接受無上限的請求內容。
        query = query[:12000]
    else:
        if not re.fullmatch(r"[0-9a-f]{32}\.txt", case_txt_name):
            return jsonify(error="待審案件文字檔識別碼無效"), 400
        case_txt_path = CASE_UPLOAD_FOLDER / case_txt_name
        if not case_txt_path.is_file():
            return jsonify(error="找不到待審案件的文字檔，請重新上傳該案件"), 404
        try:
            query = case_txt_path.read_text(encoding="utf-8").strip()
        except OSError as error:
            return jsonify(error=f"待審案件文字檔無法讀取：{error}"), 500
    if not query:
        return jsonify(error="沒有可比對的案件或爭議點文字"), 422

    try:
        top_k = int(payload.get("top_k", 5))
    except (TypeError, ValueError):
        return jsonify(error="搜尋結果數量格式無效"), 400

    chunks = load_historical_chunks(JSON_OUTPUT_DIR)
    law_category = str(payload.get("law_category") or "").strip()
    year = str(payload.get("year") or "").strip()
    retrieval_mode = str(payload.get("retrieval_mode") or "semantic").strip().lower()
    index_stats = {"indexed_vectors": 0, "candidate_chunks": 0, "new_vectors": 0}

    if retrieval_mode == "keyword":
        matches = search_historical_chunks(
            query,
            chunks,
            top_k=top_k,
            law_category=law_category,
            year=year,
        )
        label = "關鍵字關聯度（診斷模式）"
        score_type = "keyword_bm25_relevance"
        embedding_provider = None
        embedding_model = None
    else:
        if not RAG_ENABLED:
            return jsonify(error="AWS 語意 RAG 尚未啟用"), 503
        if RAG_REQUIRE_LAW_FILTER and not law_category:
            return jsonify(
                error="尚未辨識主要法律類別，請先完成案件分析後再執行同法語意搜尋"
            ), 422
        try:
            embedding_client = embedding_client_from_environment()
            matches, index_stats = search_semantic_historical_chunks(
                query,
                chunks,
                index_dir=RAG_INDEX_DIR,
                embedding_client=embedding_client,
                top_k=top_k,
                law_category=law_category,
                year=year,
                minimum_score=RAG_MIN_SCORE,
            )
        except (SemanticRagError, ValueError, OSError) as error:
            app.logger.exception("AWS semantic RAG failed")
            return jsonify(error=str(error), retrieval_mode="semantic"), 503
        label = "AWS 語意 RAG"
        score_type = "hybrid_bedrock_titan_bm25"
        embedding_provider = embedding_client.provider
        embedding_model = embedding_client.model_id

    for match in matches:
        document_id = match["document_id"]
        match["pdf_url"] = f"/uploads/{document_id}.pdf"
        match["json_url"] = f"/api/documents/{document_id}/json"
        match["reader_url"] = "/data-library?" + urlencode({
            "document_id": document_id,
            "focus_text": match["focus_text"],
            "focus_issue": "相似理由",
            "focus_exact": "0",
        })

    return jsonify(
        label=label,
        retrieval_mode=retrieval_mode,
        score_type=score_type,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        indexed_documents=len({chunk["document_id"] for chunk in chunks}),
        indexed_chunks=len(chunks),
        indexed_vectors=index_stats.get("indexed_vectors", 0),
        candidate_chunks=index_stats.get("candidate_chunks", 0),
        new_vectors=index_stats.get("new_vectors", 0),
        law_category=law_category,
        minimum_score=RAG_MIN_SCORE if retrieval_mode != "keyword" else None,
        query_mode="issue_summary" if payload.get("query_text") else "case_text",
        matches=matches,
    )


@app.get("/api/historical-similarity/status")
def historical_similarity_status():
    """回傳 RAG 索引狀態；加上 probe=1 時才實際測試 Bedrock。"""
    try:
        embedding_client = embedding_client_from_environment()
        status = semantic_index_status(RAG_INDEX_DIR, embedding_client)
        should_probe = request.args.get("probe", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if should_probe and RAG_ENABLED:
            embedding_client.embed("繁體中文法律語意檢索連線測試")
    except (SemanticRagError, ValueError, OSError) as error:
        return jsonify(enabled=RAG_ENABLED, ready=False, error=str(error)), 503
    return jsonify(
        enabled=RAG_ENABLED,
        configured=RAG_ENABLED,
        ready=True if should_probe and RAG_ENABLED else None,
        probe_performed=should_probe and RAG_ENABLED,
        require_law_filter=RAG_REQUIRE_LAW_FILTER,
        **status,
    )


@app.get("/api/documents/<document_id>/json")
def document_json(document_id):
    """提供筆記頁讀取單一歷史訴願決定書的階層式 JSON。"""
    if not re.fullmatch(r"[0-9a-f]{32}", document_id):
        return jsonify(error="無效的文件識別碼"), 400

    json_path = JSON_OUTPUT_DIR / f"{document_id}.json"
    if not json_path.is_file():
        return jsonify(error="找不到這份文件的 JSON 資料"), 404

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return jsonify(error=f"JSON 資料無法讀取：{error}"), 500
    return jsonify(data)


@app.get("/api/library/documents")
def library_documents():
    """列出第一步已完成匯入、可再次開啟的歷史訴願決定書。"""
    category = str(
        request.args.get("category") or HISTORICAL_APPEAL_CATEGORY
    ).strip()
    if category not in LIBRARY_CATEGORIES:
        return jsonify(error="不支援的法律資料分類"), 400
    if category != HISTORICAL_APPEAL_CATEGORY:
        return jsonify(files=[])

    files = list_historical_documents(UPLOAD_FOLDER, JSON_OUTPUT_DIR)
    for file in files:
        file["pdf_url"] = f"/uploads/{file['pdf_name']}"
        file["txt_url"] = (
            f"/uploads/{file['txt_name']}" if file.get("txt_name") else None
        )
        file["json_url"] = f"/api/documents/{file['document_id']}/json"
    return jsonify(files=files)


@app.delete("/api/library/documents/<document_id>")
def delete_library_document(document_id):
    """刪除一份歷史訴願決定書及其轉檔資料。"""
    if not re.fullmatch(r"[0-9a-f]{32}", document_id):
        return jsonify(error="無效的文件識別碼"), 400

    # 只接受由本系統建立的 UUID 檔名，絕不直接使用前端傳入的路徑或檔名。
    json_path = JSON_OUTPUT_DIR / f"{document_id}.json"
    if not json_path.is_file():
        return jsonify(error="找不到這份文件，可能已被刪除"), 404

    try:
        document_data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return jsonify(error=f"JSON 資料無法讀取：{error}"), 500

    metadata = document_data.get("metadata") if isinstance(document_data, dict) else {}
    if not isinstance(metadata, dict) or metadata.get("文件ID") != document_id:
        return jsonify(error="這不是可刪除的文件庫資料"), 409

    display_name = str(metadata.get("顯示檔名") or metadata.get("原始檔名") or "未命名文件")
    files_to_remove = [
        UPLOAD_FOLDER / f"{document_id}.pdf",
        UPLOAD_FOLDER / f"{document_id}.txt",
        json_path,
    ]
    removed_files = []
    try:
        for file_path in files_to_remove:
            if file_path.is_file():
                file_path.unlink()
                removed_files.append(file_path.name)
    except OSError as error:
        return jsonify(error=f"刪除文件時發生錯誤：{error}"), 500

    return jsonify(
        message="已刪除文件及其轉檔資料",
        document_id=document_id,
        display_name=display_name,
        removed_files=removed_files,
    )


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify(error="檔案總大小超過 50 MB 限制"), 413


if __name__ == "__main__":
    port = get_server_port()
    print(f"訴願書判決工具平台已啟動：http://127.0.0.1:{port}/")
    app.run(host="127.0.0.1", debug=True, use_reloader=False, port=port)
