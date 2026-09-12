"""直接將 PDF 批次轉換成 JSON。"""

import argparse
from pathlib import Path

from legal_tool.config import JSON_OUTPUT_DIR
from legal_tool.processing.pdf_text_utils import extract_pdf_text
from legal_tool.processing.txt_to_json import convert_text_to_json


def convert_pdf_to_json(input_folder, output_folder=JSON_OUTPUT_DIR):
    """將資料夾內所有 PDF 擷取、解析並輸出成 JSON。"""
    input_path = Path(input_folder).expanduser().resolve()
    output_path = Path(output_folder).expanduser().resolve()

    if not input_path.is_dir():
        raise FileNotFoundError(f"找不到輸入資料夾：{input_path}")

    pdf_files = sorted(input_path.glob("*.pdf"))
    if not pdf_files:
        print(f"在 {input_path} 裡面找不到任何 PDF 檔案！")
        return

    output_path.mkdir(parents=True, exist_ok=True)
    success_count = 0

    print(f"開始將 {len(pdf_files)} 個 PDF 直接轉換成 JSON……")
    for pdf_path in pdf_files:
        try:
            text_content = extract_pdf_text(pdf_path)
            success = convert_text_to_json(
                text_content,
                pdf_path.name,
                output_path
            )
            if success:
                print(f"✅ {pdf_path.name} -> {pdf_path.stem}.json")
                success_count += 1
            else:
                print(f"⚠️ 空白檔案：{pdf_path.name}")
        except Exception as error:
            print(f"❌ 轉換失敗 [{pdf_path.name}]：{error}")

    print(f"完成：{success_count}/{len(pdf_files)}")
    print(f"JSON 輸出資料夾：{output_path}")


def interactive_convert(output_folder=JSON_OUTPUT_DIR):
    """持續詢問 PDF 資料夾，直到輸入 q 離開。"""
    while True:
        input_dir = input(
            "\n請拖曳 PDF 資料夾到這裡，或輸入路徑（輸入 q 離開）："
        ).strip()
        input_dir = input_dir.strip("\"'")

        if input_dir.lower() == "q":
            print("已離開程式。")
            break
        if not input_dir:
            print("未輸入路徑，請重新輸入。")
            continue

        try:
            convert_pdf_to_json(input_dir, output_folder)
        except Exception as error:
            print(f"❌ 處理失敗：{error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="直接將 PDF 轉換成 JSON")
    parser.add_argument(
        "input_dir", nargs="?",
        help="PDF 輸入資料夾；未提供時會在終端機詢問"
    )
    parser.add_argument(
        "-o", "--output-dir", default=str(JSON_OUTPUT_DIR),
        help=f"JSON 輸出資料夾，預設為 {JSON_OUTPUT_DIR}"
    )
    args = parser.parse_args()

    if args.input_dir:
        convert_pdf_to_json(args.input_dir, args.output_dir)
    else:
        interactive_convert(args.output_dir)
