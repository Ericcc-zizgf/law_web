import argparse
from pathlib import Path

from legal_tool.config import EXAMPLE_DIR
from legal_tool.processing.pdf_text_utils import extract_pdf_text


def main():
    parser = argparse.ArgumentParser(description="使用 pdfplumber 擷取單一 PDF")
    parser.add_argument("pdf_path", type=Path, help="要擷取的 PDF 檔案")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=EXAMPLE_DIR / "pdfplumber_output.txt",
        help="TXT 輸出位置",
    )
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        extract_pdf_text(args.pdf_path.expanduser()),
        encoding="utf-8",
    )
    print(f"儲存成功，路徑為 {args.output}")


if __name__ == "__main__":
    main()
