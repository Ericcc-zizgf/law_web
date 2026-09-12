import os
import re
import json
from pathlib import Path

from legal_tool.config import JSON_OUTPUT_DIR, TEXT_OUTPUT_DIR

def get_field(lines, pattern):
    for line in lines:
        match = re.search(pattern, line)
        if match:
            return match.group(1).strip()
    return None

def extract_section(lines, section_name):
    """
    擷取指定段落。
    可使用：
    全文、主文、事實、理由
    """

    section_patterns = {
        "全文": r"^\s*全\s*文\s*[:：]?\s*$",
        "主文": r"^\s*主\s*文\s*$",
        "事實": r"^\s*事\s*實\s*$",
        "理由": r"^\s*理\s*由\s*$"
    }

    if section_name not in section_patterns:
        raise ValueError(
            "section_name 必須是：全文、主文、事實、理由"
        )

    start_pattern = re.compile(
        section_patterns[section_name]
    )

    start = -1

    for index, line in enumerate(lines):
        if start_pattern.fullmatch(line.strip()):
            start = index
            break

    # 找不到指定段落
    if start == -1:
        return None

    # 各段落的結束標記
    if section_name == "全文":
        end_patterns = [
            r"^\s*主\s*文\s*$"
        ]

    elif section_name == "主文":
        end_patterns = [
            r"^\s*事\s*實\s*$",
            r"^\s*理\s*由\s*$",
            r"^[一二三四五六七八九十]+、"
        ]

    elif section_name == "事實":
        end_patterns = [
            r"^\s*理\s*由\s*$",
            r"^[一二三四五六七八九十]+、"
        ]

    elif section_name == "理由":
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

    compiled_end_patterns = [
        re.compile(pattern)
        for pattern in end_patterns
    ]

    end = len(lines)

    for index in range(start + 1, len(lines)):
        line = lines[index].strip()

        if any(
            pattern.search(line)
            for pattern in compiled_end_patterns
        ):
            end = index
            break

    content_lines = [
        line.strip()
        for line in lines[start + 1:end]
        if line.strip()
    ]

    # 合併 PDF 視覺換行
    return "".join(content_lines)

def has_fact_section(txt_path):
    text = Path(txt_path).read_text(
        encoding="utf-8",
        errors="replace"
    )

    pattern = re.compile(
        r"^\s*事\s*實\s*(?:[:：])?\s*$"
    )

    return any(
        pattern.fullmatch(line)
        for line in text.splitlines()
    )

# 建立輸入輸出檔案路徑
txt_path = TEXT_OUTPUT_DIR / "01.114年-違反噪音管制法事件-77(1)-不合法定程式不補正-不受理.pdf 的副本.txt"
output_dir = JSON_OUTPUT_DIR
output_dir.mkdir(parents=True, exist_ok=True)



with open(txt_path, "r", encoding="utf-8") as f:
    first_line = f.readline().strip()
    next_lines = f.readlines()
print(next_lines)
print(f"檔案類型：{first_line}")
case_no = get_field(next_lines, r"案\s*號\s*[：:]\s*(.*)")
print(f"案號：{case_no}")
m = get_field(next_lines, r"要\s旨\s*[：:]\s*(.*)")
print(f"要旨：{m}")
issue_date = get_field(next_lines, r"發\s*文\s*日\s*期\s*[：:]\s*(.*)")
print(f"發文日期：{issue_date}")
issue_no = get_field(next_lines, r"發\s*文\s*字\s*號\s*[：:]\s*(.*)")
print(f"發文字號：{issue_no}")
print(f"全文：{extract_section(next_lines, '全文')}")
f = extract_section(next_lines, '全文')
print(f"主文：{extract_section(next_lines, '主文')}")
mt = extract_section(next_lines, '主文')
if has_fact_section(txt_path) == True:
    print(f"事實：{extract_section(next_lines, '事實')}")
else:
    print(f"事實：null")
fac = extract_section(next_lines, '事實')
print(f"理由：{extract_section(next_lines, '理由')}")
rea = extract_section(next_lines, '理由')

data = {
    "文件種類": first_line,
    "案號": case_no,
    "要旨": m,
    "發文日期": issue_date,
    "發文字號": issue_no,
    "全文": f,
    "主文": mt,
    "事實": fac,
    "理由": rea,
}
print("data:")
print(json.dumps(
    data,
    ensure_ascii=False,
    indent=2
))
