import json
import re
from pathlib import Path

from legal_tool.config import JSON_OUTPUT_DIR, TEXT_OUTPUT_DIR


def get_field(lines, pattern):
    regex = re.compile(pattern)
    for line in lines:
        match = regex.search(line)
        if match:
            return match.group(1).strip()
    return None


def extract_section(lines, section_name):
    section_patterns = {
        "全文": r"^\s*全\s*文\s*[:：]?\s*$",
        "主文": r"^\s*主\s*文\s*$",
        "事實": r"^\s*事\s*實\s*(?:[:：])?\s*$",
        "理由": r"^\s*理\s*由\s*$"
    }

    if section_name not in section_patterns:
        raise ValueError("section_name 必須是：全文、主文、事實、理由")

    start_regex = re.compile(section_patterns[section_name])
    start = -1
    for index, line in enumerate(lines):
        if start_regex.fullmatch(line.strip()):
            start = index
            break

    if start == -1:
        return None

    if section_name == "全文":
        end_patterns = [r"^\s*主\s*文\s*$"]
    elif section_name == "主文":
        end_patterns = [
            r"^\s*事\s*實\s*$",
            r"^\s*理\s*由\s*$",
            r"^[一二三四五六七八九十]+、"
        ]
    elif section_name == "事實":
        # 事實內可能有「一、訴願意旨」等內容，不能用一、作為結束標記。
        end_patterns = [r"^\s*理\s*由\s*$"]
    else:
        end_patterns = [
            r"^\s*訴願審議委員會主任委員",
            r"^\s*主任委員\s+",
            r"^\s*委員\s+",
            r"^\s*如不服本決定",
            r"^\s*如對原處分不服",
            r"^\s*[1-3][.、]\s*如",
            r"^\s*中華民國\s+\d+\s+年",
            r"^\s*相關圖表"
        ]

    end_regexes = [re.compile(pattern) for pattern in end_patterns]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index].strip()
        if any(regex.search(line) for regex in end_regexes):
            end = index
            break

    content_lines = [
        line.strip()
        for line in lines[start + 1:end]
        if line.strip()
    ]

    # 保留換行，方便後續依照「一、」「二、」等理由編號切分。
    return "\n".join(content_lines)


def chinese_number_to_int(number_text):
    """將常見的中文數字編號轉成整數，例如「十一」轉成 11。"""
    digit_values = {
        "零": 0, "〇": 0, "○": 0,
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9
    }
    unit_values = {"十": 10, "百": 100, "千": 1000}

    total = 0
    current = 0
    for char in number_text:
        if char in digit_values:
            current = digit_values[char]
        elif char in unit_values:
            unit = unit_values[char]
            total += (current or 1) * unit
            current = 0
        else:
            return None

    return total + current


def split_hierarchical_sections(
    section_text,
    sequential_top_level=False,
    preamble_label="前文",
):
    """將事實或理由文字依編號拆成可巢狀的階層資料。

    支援的常見格式：
    第一層：一、二、三、
    第二層：（一）、（二）、或 (一)、(二)、
    第三層：1.、2.、或 1、2、

    只有出現在行首的編號才會被視為段落標記，避免誤切正文中的法律條文。
    理由文字可將 sequential_top_level 設為 True，要求第一層編號依序出現。
    沒有編號、但位於第一個編號段落前的文字，會保留為第 0 層前文，
    並使用呼叫端提供的語意標籤，不硬編一個不存在的「一、」。
    """
    if not section_text:
        return []

    marker_patterns = [
        (1, re.compile(
            r"^\s*([零〇○一二三四五六七八九十百千]+)、\s*(.*)$"
        )),
        (2, re.compile(
            r"^\s*[（(]([零〇○一二三四五六七八九十百千]+|\d+)[）)]\s*(.*)$"
        )),
        (3, re.compile(
            # 避免把「1. 5」這類小數或百分比誤認成段落編號。
            r"^\s*(\d+)(?:、|[.](?!\s*\d))\s*(.*)$"
        )),
    ]

    roots = []
    stack = []
    preamble = []
    expected_top_level = 1
    # PDF OCR 偶爾會把「」與『』辨識錯配；引號若跨太多行仍未關閉，
    # 不能再讓它阻擋真正的段落標記。
    quote_stack = []
    max_quote_lines = 12

    def flush_preamble():
        if not preamble:
            return
        roots.append({
            "層級": 0,
            "編號": None,
            "名稱": preamble_label,
            "內容": "".join(preamble).strip(),
            "子段落": [],
        })
        preamble.clear()

    for line_index, raw_line in enumerate(section_text.splitlines()):
        line = raw_line.strip()
        if not line:
            continue

        line_inside_quote = bool(quote_stack)
        stale_quote = (
            line_inside_quote
            and line_index - quote_stack[-1][1] > max_quote_lines
        )

        level = None
        marker = None
        text = None
        for candidate_level, pattern in marker_patterns:
            match = pattern.match(line)
            if match:
                # 法條引用內也可能有「一、」「二、」；這些不是理由層級。
                if line_inside_quote and not stale_quote:
                    continue
                if candidate_level == 1 and sequential_top_level:
                    candidate_number = chinese_number_to_int(match.group(1))
                    if candidate_number != expected_top_level:
                        continue
                level = candidate_level
                marker = match.group(1)
                text = match.group(2).strip()
                break

        # 已跨越多行仍未關閉的引號是 OCR 雜訊；若此行辨識為段落，
        # 清除它，讓後續的（五）、二、三、能正常回到原文結構。
        if level is not None and stale_quote:
            quote_stack.clear()

        for char in line:
            if char == "「":
                quote_stack.append(("」", line_index))
            elif char == "『":
                quote_stack.append(("』", line_index))
            elif quote_stack and char == quote_stack[-1][0]:
                quote_stack.pop()

        if level is None:
            if stack:
                stack[-1]["內容"] += line
            else:
                preamble.append(line)
            continue

        if level == 1 and sequential_top_level:
            expected_top_level += 1

        flush_preamble()
        node = {
            "層級": level,
            "編號": marker,
            "內容": text,
            "子段落": [],
        }

        while stack and stack[-1]["層級"] >= level:
            stack.pop()

        if stack:
            stack[-1]["子段落"].append(node)
        else:
            roots.append(node)
        stack.append(node)

    flush_preamble()
    return roots


def has_fact_section(lines):
    pattern = re.compile(r"^\s*事\s*實\s*(?:[:：])?\s*$")
    return any(pattern.fullmatch(line.strip()) for line in lines)


def extract_metadata(filename):
    """從檔名擷取年度、檔案編號與判決結果。"""
    # Finder 複製檔案時可能在副檔名後加上「的副本」。
    # 先移除這些檔名尾綴，再解析判決結果，避免把 .pdf/的副本誤當成結果。
    filename = Path(filename).name
    filename = re.sub(
        r"(?:\.pdf)?\s*的副本(?=\.(?:pdf|txt)$|$)",
        "",
        filename,
        flags=re.IGNORECASE,
    )
    filename = re.sub(r"\.txt$", "", filename, flags=re.IGNORECASE)
    filename = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
    pattern = re.compile(
        r"^(?P<file_no>\d+)\.(?P<year>\d+)年-(?P<body>.+)$"
    )

    match = pattern.match(filename)
    if not match:
        return {
            "年度": None,
            "檔案編號": None,
            "判決結果": None
        }

    body = match.group("body")
    result = body.rsplit("-", 1)[-1].strip()

    return {
        "年度": match.group("year"),
        "檔案編號": match.group("file_no"),
        "判決結果": result
    }


def find_document_type(lines):
    """在文件前段尋找真正的文件種類，避免被查閱日期等頁首文字干擾。"""
    document_type_patterns = [
        r"^\s*訴願決定書\s*$",
        r"^\s*行政訴訟判決書?\s*$"
    ]

    for index, line in enumerate(lines[:30]):
        cleaned_line = line.strip()
        if any(
            re.fullmatch(pattern, cleaned_line)
            for pattern in document_type_patterns
        ):
            return index, cleaned_line

    return None, None


def build_json_data(text_content, filename):
    """將文字內容解析成一份 JSON 字典，但尚未寫入檔案。"""
    if not text_content.strip():
        return None

    lines = text_content.splitlines()
    document_type_index, first_line = find_document_type(lines)

    if document_type_index is None:
        # 找不到已知文件種類時，保留原本的 fallback 行為。
        document_type_index = 0 if lines else -1
        first_line = lines[0].strip() if lines else None

    next_lines = lines[document_type_index + 1:]

    fact_text = (
        extract_section(next_lines, "事實")
        if has_fact_section(next_lines)
        else ""
    )
    reason_text = extract_section(next_lines, "理由") or ""

    return {
        "metadata": extract_metadata(filename),
        "文件種類": first_line,
        "案號": get_field(next_lines, r"案\s*號\s*[：:]\s*(.*)"),
        "要旨": get_field(next_lines, r"要\s*旨\s*[：:]\s*(.*)"),
        "發文日期": get_field(next_lines, r"發\s*文\s*日\s*期\s*[：:]\s*(.*)"),
        "發文字號": get_field(next_lines, r"發\s*文\s*字\s*號\s*[：:]\s*(.*)"),
        "全文": extract_section(next_lines, "全文"),
        "主文": extract_section(next_lines, "主文"),
        "事實": split_hierarchical_sections(
            fact_text,
            preamble_label="事實概述",
        ),
        "理由": split_hierarchical_sections(
            reason_text,
            sequential_top_level=True,
            preamble_label="理由概述",
        ),
    }


def convert_text_to_json(text_content, filename, output_dir):
    """將文字內容解析並寫成 JSON，回傳是否成功。"""
    data = build_json_data(text_content, filename)
    if data is None:
        return False

    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{Path(filename).stem}.json"
    output_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return True


def convert_one_file(txt_path, output_dir):
    """將單一 TXT 解析成 JSON。"""
    text_content = txt_path.read_text(
        encoding="utf-8",
        errors="replace"
    )
    return convert_text_to_json(text_content, txt_path.name, output_dir)


def main():
    input_dir = TEXT_OUTPUT_DIR

    # 使用新資料夾，避免覆蓋原本的模型輸出。
    output_dir = JSON_OUTPUT_DIR
    output_dir.mkdir(exist_ok=True)

    txt_files = sorted(input_dir.glob("*.txt"))
    print(f"共找到 {len(txt_files)} 個待處理的文字檔案。")

    success_count = 0
    for index, txt_path in enumerate(txt_files, start=1):
        print(f"[{index}/{len(txt_files)}] {txt_path.name}")
        try:
            if convert_one_file(txt_path, output_dir):
                success_count += 1
                print("done")
            else:
                print("pass")
        except Exception as error:
            print(f"轉換失敗：{error}")

    print(f"完成：{success_count}/{len(txt_files)}")
    print(f"輸出資料夾：{output_dir}")


if __name__ == "__main__":
    main()
