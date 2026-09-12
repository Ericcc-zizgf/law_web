"""PDF 文字擷取與清理共用工具。"""

import re
from pathlib import Path

import pdfplumber


# 目前資料中的網站查閱標記，例如：2026/3/20 上午8:36 查閱內容
VIEW_MARKER_PATTERN = re.compile(
    r"^\s*\d{4}/\d{1,2}/\d{1,2}\s+(?:上午|下午)\s*"
    r"\d{1,2}:\d{2}\s+查閱內容\s*$"
)

# PDF 列印頁尾常會把網址和頁碼一起擷取出來。
URL_PATTERN = re.compile(r"^\s*https?://.*$")
PAGE_NUMBER_PATTERN = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
CID_PATTERN = re.compile(r"\(cid:\d+\)")


def is_noise_line(line):
    """判斷一行是否為網站查閱資訊、網址或獨立頁碼。"""
    cleaned = line.strip()
    if not cleaned:
        return False

    return any(
        pattern.fullmatch(cleaned)
        for pattern in (
            VIEW_MARKER_PATTERN,
            URL_PATTERN,
            PAGE_NUMBER_PATTERN,
        )
    )


def clean_pdf_lines(lines):
    """清理 PDF 擷取結果，但保留法律正文的換行與文字。"""
    cleaned_lines = []
    previous_blank = False

    for raw_line in lines:
        line = raw_line.replace("\ufeff", "").replace("\u200b", "")
        # PDF 字型無法對應時，pdfminer 可能輸出 (cid:0) 之類的標記。
        line = CID_PATTERN.sub("", line).strip()

        if is_noise_line(line):
            continue

        if not line:
            # 避免每一頁產生大量連續空白行，但保留段落分隔。
            if not previous_blank:
                cleaned_lines.append("")
            previous_blank = True
            continue

        cleaned_lines.append(line)
        previous_blank = False

    while cleaned_lines and not cleaned_lines[0]:
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()

    return cleaned_lines


def extract_pdf_text(pdf_path):
    """從 PDF 擷取文字並移除已知的頁首頁尾雜訊。"""
    raw_lines = []

    with pdfplumber.open(Path(pdf_path)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            raw_lines.extend(page_text.splitlines())
            # 保留頁面之間的分隔，避免兩頁最後與第一行黏在一起。
            raw_lines.append("")

    return "\n".join(clean_pdf_lines(raw_lines))
