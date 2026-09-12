import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

from legal_tool.processing.txt_to_json import (
    extract_metadata,
    split_hierarchical_sections,
)
from legal_tool.services.library_ingestion import (
    HISTORICAL_APPEAL_CATEGORY,
    extract_case_law_category,
    ingest_library_pdf,
)
from legal_tool.web_app import app


SAMPLE_DECISION_TEXT = """新北市政府訴願決定書
案號：1130000001
要旨：測試案件
發文日期：民國 113 年 1 月 1 日
發文字號：新北府訴決字第 1130000001 號
主文
原處分撤銷。
事實
一、訴願人不服原處分，提起訴願。
（一）原處分機關曾進行現場稽查。
理由
一、程序部分應先予審查。
（一）本件訴願合法。
二、實體部分原處分認定尚有疑義。
"""


class LibraryIngestionTests(unittest.TestCase):
    def test_historical_pdf_creates_pdf_txt_and_nested_json(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            uploaded_file = FileStorage(
                stream=io.BytesIO(b"%PDF-test"),
                filename="20.113年-測試事件-81I-訴願有理由-撤銷另處.pdf 的副本.pdf",
                content_type="application/pdf",
            )

            with patch(
                "legal_tool.services.library_ingestion.extract_pdf_text",
                return_value=SAMPLE_DECISION_TEXT,
            ):
                result = ingest_library_pdf(
                    uploaded_file,
                    HISTORICAL_APPEAL_CATEGORY,
                    root / "uploads",
                    root / "json",
                )

            self.assertEqual(result["processing_status"], "completed")
            self.assertEqual(result["parsed_summary"]["事實節點數"], 2)
            self.assertEqual(result["parsed_summary"]["理由節點數"], 3)
            self.assertTrue((root / "uploads" / result["pdf_name"]).is_file())
            self.assertTrue((root / "uploads" / result["txt_name"]).is_file())
            self.assertTrue((root / "json" / result["json_name"]).is_file())

    def test_finder_copy_suffix_does_not_pollute_result(self):
        metadata = extract_metadata(
            "20.113年-測試事件-81I-訴願有理由-撤銷另處.pdf 的副本.pdf"
        )
        self.assertEqual(metadata["判決結果"], "撤銷另處")

    def test_extract_case_law_category_from_filename(self):
        self.assertEqual(
            extract_case_law_category("21.114年-違反建築法事件-77(8)-部分駁回.pdf"),
            "違反建築法",
        )

    def test_duplicate_historical_pdf_is_skipped_by_content(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            upload_dir = root / "uploads"
            json_dir = root / "json"

            with patch(
                "legal_tool.services.library_ingestion.extract_pdf_text",
                return_value=SAMPLE_DECISION_TEXT,
            ):
                first_result = ingest_library_pdf(
                    FileStorage(
                        stream=io.BytesIO(b"same-pdf-content"),
                        filename="01.113年-測試事件.pdf",
                        content_type="application/pdf",
                    ),
                    HISTORICAL_APPEAL_CATEGORY,
                    upload_dir,
                    json_dir,
                )
                duplicate_result = ingest_library_pdf(
                    FileStorage(
                        stream=io.BytesIO(b"same-pdf-content"),
                        filename="另一個名稱.pdf",
                        content_type="application/pdf",
                    ),
                    HISTORICAL_APPEAL_CATEGORY,
                    upload_dir,
                    json_dir,
                )

            self.assertFalse(first_result.get("duplicate", False))
            self.assertTrue(duplicate_result["duplicate"])
            self.assertEqual(duplicate_result["duplicate_of"], first_result["document_id"])
            self.assertEqual(len(list(upload_dir.glob("*.pdf"))), 1)
            self.assertEqual(len(list(json_dir.glob("*.json"))), 1)

    def test_stale_ocr_quote_does_not_hide_later_reason_sections(self):
        reason_text = """一、第一部分：
（一）先說明法規「引文開始
甲乙丙
甲乙丙
甲乙丙
甲乙丙
甲乙丙
甲乙丙
甲乙丙
甲乙丙
甲乙丙
甲乙丙
甲乙丙
甲乙丙
甲乙丙
（五）第五個理由。
二、第二部分：
（一）第二部分的理由。
三、結論。
"""
        sections = split_hierarchical_sections(
            reason_text,
            sequential_top_level=True,
        )

        self.assertEqual([section["編號"] for section in sections], ["一", "二", "三"])
        self.assertEqual(
            [section["編號"] for section in sections[0]["子段落"]],
            ["一", "五"],
        )
        self.assertEqual(sections[1]["子段落"][0]["編號"], "一")

    def test_unnumbered_fact_preamble_gets_semantic_label(self):
        sections = split_hierarchical_sections(
            "緣原處分機關先行調查本案事實。\n一、訴願人不服原處分。",
            preamble_label="事實概述",
        )

        self.assertEqual(sections[0]["層級"], 0)
        self.assertIsNone(sections[0]["編號"])
        self.assertEqual(sections[0]["名稱"], "事實概述")
        self.assertEqual(sections[1]["編號"], "一")

    def test_convert_api_returns_readable_nested_json_url(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            upload_dir = root / "uploads"
            json_dir = root / "json"
            client = app.test_client()

            with (
                patch("legal_tool.web_app.UPLOAD_FOLDER", upload_dir),
                patch("legal_tool.web_app.JSON_OUTPUT_DIR", json_dir),
                patch(
                    "legal_tool.services.library_ingestion.extract_pdf_text",
                    return_value=SAMPLE_DECISION_TEXT,
                ),
            ):
                response = client.post(
                    "/convert",
                    data={
                        "category": HISTORICAL_APPEAL_CATEGORY,
                        "files": (
                            io.BytesIO(b"%PDF-test"),
                            "20.113年-測試事件-81I-訴願有理由-撤銷另處.pdf",
                        ),
                    },
                    content_type="multipart/form-data",
                )

                self.assertEqual(response.status_code, 200)
                converted = response.get_json()["files"][0]
                self.assertEqual(converted["processing_status"], "completed")
                self.assertEqual(converted["law_category"], "測試")
                self.assertTrue(converted["pdf_url"].startswith("/uploads/"))
                self.assertTrue(converted["json_url"].startswith("/api/documents/"))

                json_response = client.get(converted["json_url"])
                self.assertEqual(json_response.status_code, 200)
                json_data = json_response.get_json()
                self.assertEqual(json_data["案號"], "1130000001")
                self.assertEqual(json_data["事實"][0]["子段落"][0]["層級"], 2)
                self.assertEqual(json_data["理由"][0]["子段落"][0]["內容"], "本件訴願合法。")

                library_response = client.get(
                    "/api/library/documents",
                    query_string={"category": HISTORICAL_APPEAL_CATEGORY},
                )
                self.assertEqual(library_response.status_code, 200)
                library_file = library_response.get_json()["files"][0]
                self.assertEqual(library_file["document_id"], converted["document_id"])
                self.assertEqual(library_file["json_url"], converted["json_url"])
                self.assertEqual(library_file["year"], "113")
                self.assertEqual(library_file["file_number"], "20")
                self.assertEqual(library_file["law_category"], "測試")

                delete_response = client.delete(
                    f"/api/library/documents/{converted['document_id']}"
                )
                self.assertEqual(delete_response.status_code, 200)
                self.assertEqual(
                    delete_response.get_json()["document_id"], converted["document_id"]
                )
                self.assertFalse((upload_dir / converted["pdf_name"]).exists())
                self.assertFalse((upload_dir / converted["txt_name"]).exists())
                self.assertFalse((json_dir / converted["json_name"]).exists())
                self.assertEqual(client.get(converted["json_url"]).status_code, 404)


if __name__ == "__main__":
    unittest.main()
