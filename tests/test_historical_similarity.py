import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legal_tool.services.historical_similarity import (
    load_historical_chunks,
    search_historical_chunks,
    search_semantic_historical_chunks,
    tokenize,
)


class FakeEmbeddingClient:
    provider = "test"
    model_id = "test-legal-embedding"
    dimensions = 3

    def __init__(self):
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        source = str(text)
        if any(term in source for term in ("建築", "施工", "建造", "蓋房")):
            return [1.0, 0.0, 0.0]
        if any(term in source for term in ("噪音", "音量")):
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


class HistoricalSimilarityTests(unittest.TestCase):
    def test_returns_best_document_reason_and_traceable_metadata(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "building.json").write_text(
                json.dumps({
                    "metadata": {
                        "文件ID": "a" * 32,
                        "顯示檔名": "114年-違反建築法事件.pdf",
                        "年度": "114",
                        "檔案編號": "1",
                        "案件法律類別": "違反建築法",
                        "判決結果": "駁回",
                    },
                    "要旨": "未經許可擅自施工",
                    "主文": "訴願駁回。",
                    "理由": [{
                        "編號": "一",
                        "內容": "現場稽查照片及施工紀錄足以認定違規施工。",
                        "子段落": [],
                    }],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "noise.json").write_text(
                json.dumps({
                    "metadata": {
                        "文件ID": "b" * 32,
                        "顯示檔名": "114年-違反噪音管制法事件.pdf",
                        "年度": "114",
                        "案件法律類別": "違反噪音管制法",
                    },
                    "理由": [{"編號": "一", "內容": "夜間噪音測量超標。", "子段落": []}],
                }, ensure_ascii=False),
                encoding="utf-8",
            )

            chunks = load_historical_chunks(root)
            results = search_historical_chunks(
                "施工現場稽查照片是否足以認定違規",
                chunks,
                law_category="建築法",
            )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["document_id"], "a" * 32)
            self.assertEqual(results[0]["paragraph_path"], ["一"])
            self.assertGreater(results[0]["score"], 0)
            self.assertIn("現場稽查照片", results[0]["focus_text"])

    def test_api_returns_reader_url_for_local_case_text(self):
        from legal_tool import web_app

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            case_dir = root / "case_uploads"
            json_dir = root / "history_json"
            case_dir.mkdir()
            json_dir.mkdir()
            text_name = f"{'c' * 32}.txt"
            case_dir.joinpath(text_name).write_text(
                "施工現場稽查照片足以認定違規施工。", encoding="utf-8"
            )
            json_dir.joinpath(f"{'a' * 32}.json").write_text(
                json.dumps({
                    "metadata": {
                        "文件ID": "a" * 32,
                        "顯示檔名": "歷史建築法案件.pdf",
                        "案件法律類別": "違反建築法",
                    },
                    "理由": [{
                        "編號": "一",
                        "內容": "現場稽查照片足以認定違規施工。",
                    }],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            original_case_dir = web_app.CASE_UPLOAD_FOLDER
            original_json_dir = web_app.JSON_OUTPUT_DIR
            try:
                web_app.CASE_UPLOAD_FOLDER = case_dir
                web_app.JSON_OUTPUT_DIR = json_dir
                response = web_app.app.test_client().post(
                    "/api/historical-similarity/search",
                    json={
                        "case_txt_name": text_name,
                        "retrieval_mode": "keyword",
                    },
                )
            finally:
                web_app.CASE_UPLOAD_FOLDER = original_case_dir
                web_app.JSON_OUTPUT_DIR = original_json_dir

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["label"], "關鍵字關聯度（診斷模式）")
            self.assertEqual(payload["matches"][0]["document_id"], "a" * 32)
            self.assertIn("document_id=", payload["matches"][0]["reader_url"])

    def test_semantic_rag_finds_paraphrase_and_reuses_efs_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            json_dir = root / "history"
            index_dir = root / "rag_index"
            json_dir.mkdir()
            json_dir.joinpath("building.json").write_text(
                json.dumps({
                    "metadata": {
                        "文件ID": "a" * 32,
                        "顯示檔名": "歷史建築法案件.pdf",
                        "案件法律類別": "違反建築法",
                    },
                    "理由": [{
                        "編號": "三",
                        "內容": "行為人未取得建造執照即興建構造物，依法裁處並無不合。",
                    }],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            json_dir.joinpath("noise.json").write_text(
                json.dumps({
                    "metadata": {
                        "文件ID": "b" * 32,
                        "顯示檔名": "歷史噪音案件.pdf",
                        "案件法律類別": "違反噪音管制法",
                    },
                    "理由": [{"編號": "二", "內容": "夜間音量超過管制標準。"}],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            chunks = load_historical_chunks(json_dir)
            embedding_client = FakeEmbeddingClient()

            first, first_stats = search_semantic_historical_chunks(
                "沒有申請核准就蓋房子是否可以處罰",
                chunks,
                index_dir=index_dir,
                embedding_client=embedding_client,
                law_category="建築法",
            )
            calls_after_first_search = len(embedding_client.calls)
            second, second_stats = search_semantic_historical_chunks(
                "沒有申請核准就蓋房子是否可以處罰",
                chunks,
                index_dir=index_dir,
                embedding_client=embedding_client,
                law_category="建築法",
            )

            self.assertEqual(first[0]["document_id"], "a" * 32)
            self.assertGreater(first[0]["semantic_score"], 0.9)
            self.assertEqual(first[0]["paragraph_path"], ["三"])
            self.assertEqual(first_stats["new_vectors"], 1)
            self.assertEqual(second_stats["new_vectors"], 0)
            # 第二次只需要重算查詢向量，歷史段落向量直接取自索引。
            self.assertEqual(len(embedding_client.calls), calls_after_first_search + 1)
            self.assertEqual(second[0]["matched_excerpt"], first[0]["matched_excerpt"])

    def test_law_name_normalizes_pollution_variant(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            root.joinpath("air.json").write_text(
                json.dumps({
                    "metadata": {
                        "文件ID": "d" * 32,
                        "案件法律類別": "違反空氣汙染防制法",
                    },
                    "理由": [{"編號": "一", "內容": "排放污染物超過法定標準，應依法裁處。"}],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            chunks = load_historical_chunks(root)
            results = search_historical_chunks(
                "空氣污染防制法排放標準",
                chunks,
                law_category="空氣污染防制法",
            )

            self.assertEqual(results[0]["document_id"], "d" * 32)

    def test_semantic_api_returns_traceable_reader_url(self):
        from legal_tool import web_app

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            json_dir = root / "history"
            json_dir.mkdir()
            json_dir.joinpath("building.json").write_text(
                json.dumps({
                    "metadata": {
                        "文件ID": "e" * 32,
                        "顯示檔名": "可追溯歷史案件.pdf",
                        "案件法律類別": "違反建築法",
                    },
                    "理由": [{
                        "編號": "五",
                        "內容": "未取得建造執照即施工，主管機關得依法命令停工。",
                    }],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            original_json_dir = web_app.JSON_OUTPUT_DIR
            original_index_dir = web_app.RAG_INDEX_DIR
            try:
                web_app.JSON_OUTPUT_DIR = json_dir
                web_app.RAG_INDEX_DIR = root / "rag_index"
                with patch(
                    "legal_tool.web_app.embedding_client_from_environment",
                    return_value=FakeEmbeddingClient(),
                ):
                    response = web_app.app.test_client().post(
                        "/api/historical-similarity/search",
                        json={
                            "query_text": "沒有取得許可就蓋房子",
                            "law_category": "建築法",
                        },
                    )
            finally:
                web_app.JSON_OUTPUT_DIR = original_json_dir
                web_app.RAG_INDEX_DIR = original_index_dir

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["label"], "AWS 語意 RAG")
            self.assertEqual(payload["matches"][0]["paragraph_path"], ["五"])
            self.assertIn("document_id=", payload["matches"][0]["reader_url"])
            self.assertIn("focus_text=", payload["matches"][0]["reader_url"])

    def test_query_tokens_exclude_numeric_document_noise(self):
        tokens = tokenize("114 年 1 月 10 日新北府訴字第 123 號，違反建築法第 73 條")

        self.assertNotIn("114", tokens)
        self.assertNotIn("10", tokens)
        self.assertIn("建築法", tokens)
        self.assertIn("條73", tokens)
