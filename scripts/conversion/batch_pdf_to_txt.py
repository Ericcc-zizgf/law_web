import argparse
from pathlib import Path

from legal_tool.config import TEXT_OUTPUT_DIR
from legal_tool.processing.pdf_text_utils import extract_pdf_text


def convert_pdf_to_txt(input_folder, output_folder):
    input_path = Path(input_folder).expanduser().resolve()
    output_path = Path(output_folder).expanduser().resolve()

    # 1. 確保輸出資料夾存在
    output_path.mkdir(parents=True, exist_ok=True)

    # 2. 檢查有無找到輸入資料夾
    if not input_path.is_dir():
        raise FileNotFoundError(f"找不到輸入資料夾：{input_path}")

    # 3. 取得輸入資料夾中的PDF檔
    pdf_files = sorted(input_path.glob("*.pdf"))

    if not pdf_files:
        print(f"在 {input_path} 裡面找不到任何 PDF 檔案！")
        return

    print(f"開始執行批次轉檔，總共找到 {len(pdf_files)} 個 PDF 檔案……")

    # 4. 將資料夾中的pdf全部轉txt
    success_count = 0
    for pdf_path in pdf_files:
        txt_path = output_path / f"{pdf_path.stem}.txt"
        try:
            txt_path.write_text(
                extract_pdf_text(pdf_path),
                encoding="utf-8"
            )

            print(f"✅ 成功轉檔：{pdf_path.name} -> {txt_path.name}")
            success_count += 1

        except Exception as e:
            print(f"❌ 轉檔失敗 [{pdf_path.name}]，錯誤原因：{e}")
    print(f"批次轉檔完成！成功：{success_count} / 總數：{len(pdf_files)}")


def interactive_convert(output_folder):
    """持續詢問 PDF 資料夾，直到使用者輸入 q 離開。"""
    while True:
        input_dir = input(
            "\n請拖曳 PDF 資料夾到這裡，或輸入路徑（輸入 q 離開）："
        ).strip()

        # 支援輸入 q、Q，以及拖曳路徑時可能出現的外層引號。
        if input_dir.strip("\"'").lower() == "q":
            print("已離開程式。")
            break

        input_dir = input_dir.strip("\"'")
        if not input_dir:
            print("未輸入路徑，請重新輸入。")
            continue

        try:
            convert_pdf_to_txt(input_dir, output_folder)
        except FileNotFoundError as error:
            print(f"❌ {error}")
        except Exception as error:
            print(f"❌ 處理失敗：{error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批次將 PDF 轉換成乾淨的 TXT")
    parser.add_argument(
        "input_dir", nargs="?",
        help="PDF 輸入資料夾；未提供時會在終端機詢問"
    )
    parser.add_argument(
        "-o", "--output-dir", default=str(TEXT_OUTPUT_DIR),
        help=f"TXT 輸出資料夾，預設為 {TEXT_OUTPUT_DIR}"
    )
    args = parser.parse_args()

    if args.input_dir:
        input_dir = args.input_dir.strip("\"'")
        if input_dir.lower() == "q":
            raise SystemExit("已離開程式。")
        convert_pdf_to_txt(input_dir, args.output_dir)
    else:
        interactive_convert(args.output_dir)


# 保留舊函式名稱，避免其他程式仍在呼叫時失效。
convert_PDF_to_TXT = convert_pdf_to_txt
