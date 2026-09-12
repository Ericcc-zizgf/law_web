import argparse
from pathlib import Path

from markitdown import MarkItDown

from legal_tool.config import EXAMPLE_DIR


def main():
    parser = argparse.ArgumentParser(description="使用 MarkItDown 擷取單一 PDF")
    parser.add_argument("pdf_path", type=Path, help="要擷取的 PDF 檔案")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=EXAMPLE_DIR / "markitdown_output.md",
        help="Markdown 輸出位置",
    )
    args = parser.parse_args()

    result = MarkItDown().convert(str(args.pdf_path.expanduser()), extract_pages=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.markdown, encoding="utf-8")


if __name__ == "__main__":
    main()
