# 純 Python TXT 解析成 JSON：完整資料流

## 1. 這支程式解決什麼問題

`txt_to_json.py` 不使用模型重新抄寫整份法律文件，而是使用 Python 從 TXT 中擷取固定欄位與段落，降低長文輸出截斷、欄位錯置與委員會名單污染的問題。

目前擷取的欄位有：

- 文件種類
- 案號
- 要旨
- 發文日期
- 發文字號
- 全文：案件背景
- 主文：決定內容
- 事實：如果文件有獨立的「事實」標題
- 理由：從「理由」到委員會、救濟教示或日期以前
- metadata：年度、檔案編號、判決結果

這批資料的流向是：

```text
TXT → text_content → lines → 固定欄位與段落 → data dictionary → JSON 檔案
```

## 2. 讀取 TXT

```python
text_content = txt_path.read_text(
    encoding="utf-8",
    errors="replace"
)
```

`txt_path` 是目前 TXT 的檔案路徑。`read_text()` 會回傳一個完整的 Python 字串，例如：

```python
"訴願決定書\n案號：1141020110\n要旨：因違反噪音管制法事件提起訴願\n..."
```

`encoding="utf-8"` 用來正確讀取繁體中文；`errors="replace"` 遇到無法解碼的少數字元時，以替代字元取代，避免整批程式中斷。

接著檢查：

```python
if not text_content.strip():
    return False
```

`strip()` 會移除前後空白。如果檔案沒有有效內容，函式回傳 `False`，主程式會跳過這份檔案。

## 3. 將全文轉成文字行

```python
lines = text_content.splitlines()
first_line = lines[0].strip() if lines else None
next_lines = lines[1:]
```

原始字串：

```text
訴願決定書\n案 號：1141020110\n要 旨：因違反噪音管制法事件提起訴願
```

經過 `splitlines()` 後：

```python
lines = [
    "訴願決定書",
    "案 號：1141020110",
    "要 旨：因違反噪音管制法事件提起訴願"
]
```

因此：

```python
first_line = "訴願決定書"
next_lines = ["案 號：1141020110", "要 旨：因違反噪音管制法事件提起訴願"]
```

## 4. get_field()：擷取固定欄位

```python
def get_field(lines, pattern):
    regex = re.compile(pattern)
    for line in lines:
        match = regex.search(line)
        if match:
            return match.group(1).strip()
    return None
```

這個 function 會逐行搜尋符合正則表達式的文字。`group(1)` 代表正則中第一個括號擷取到的內容。

例如：

```python
case_no = get_field(
    next_lines,
    r"案\s*號\s*[：:]\s*(.*)"
)
```

這個正則允許以下格式：

```text
案號：1141020110
案 號：1141020110
案  號：1141020110
```

回傳結果是：

```python
"1141020110"
```

找不到時回傳：

```python
None
```

寫入 JSON 後，Python 的 `None` 會變成 JSON 的 `null`。

同樣方式擷取：

```python
summary = get_field(next_lines, r"要\s*旨\s*[：:]\s*(.*)")
issue_date = get_field(next_lines, r"發\s*文\s*日\s*期\s*[：:]\s*(.*)")
issue_no = get_field(next_lines, r"發\s*文\s*字\s*號\s*[：:]\s*(.*)")
```

## 5. extract_section()：擷取指定段落

呼叫方式：

```python
extract_section(lines, "全文")
extract_section(lines, "主文")
extract_section(lines, "事實")
extract_section(lines, "理由")
```

function 會先找開始標題，再找到結束標記。

例如：

```text
全文：
新北市政府訴願決定書...
主 文
訴願不受理。
理 由
一、按訴願法第...
```

呼叫 `extract_section(lines, "全文")` 後，只回傳：

```text
新北市政府訴願決定書...
```

不會包含「全文」標題，也不會包含「主文」之後的內容。

目前的結束規則是：

| 目標 | 遇到什麼停止 |
|---|---|
| 全文 | 主文 |
| 主文 | 事實、理由或一、 |
| 事實 | 理由 |
| 理由 | 委員會、救濟教示、日期或相關圖表 |

PDF 的換行通常只是視覺換行，不是真正段落，所以最後使用：

```python
return "".join(content_lines)
```

把以下內容合併：

```text
訴願人因違反噪音管制法事件，不服原處分機關民國 113 年
12 月 20 日裁處書...
```

變成連續文字，方便搜尋與建立 embedding。

## 6. has_fact_section()：判斷是否有事實段落

```python
def has_fact_section(lines):
    pattern = re.compile(
        r"^\s*事\s*實\s*(?:[:：])?\s*$"
    )
    return any(
        pattern.fullmatch(line.strip())
        for line in lines
    )
```

這個 function 只會回傳 `True` 或 `False`。

會辨識：

```text
事實
事 實
事實：
```

不會把一般句子誤判成標題，例如：

```text
本件事實如下：
```

如果沒有獨立的事實段落，JSON 會寫成：

```json
"事實": null
```

## 7. extract_metadata()：解析檔名

檔名範例：

```text
01.114年-違反噪音管制法事件-77(1)-不受理.pdf 的副本.txt
```

function 會取得：

```python
{
    "年度": "114",
    "檔案編號": "01",
    "判決結果": "不受理"
}
```

判決結果使用：

```python
body.rsplit("-", 1)[-1].strip()
```

從最右側的連字號切割，因此下列檔名也可以處理：

```text
21.114年-違反建築法事件-77(8)&79I-部分不受理&部分駁回.pdf 的副本.txt
```

結果會是：

```json
{
  "年度": "114",
  "檔案編號": "21",
  "判決結果": "部分不受理&部分駁回"
}
```

如果檔名格式不符合正則，三個欄位會回傳 `None`，避免程式自行猜測。

## 8. convert_one_file()：處理一份文件

這個 function 將一份 TXT 依序處理：

```text
txt_path
  ↓
text_content
  ↓
lines / next_lines
  ↓
固定欄位
  ↓
全文、主文、事實、理由
  ↓
metadata
  ↓
data dictionary
  ↓
JSON 檔案
```

最後組合成：

```python
data = {
    "metadata": extract_metadata(txt_path.name),
    "文件種類": first_line,
    "案號": case_no,
    "要旨": summary,
    "發文日期": issue_date,
    "發文字號": issue_no,
    "全文": full_text,
    "主文": main_text,
    "事實": fact_text,
    "理由": reason_text
}
```

## 9. JSON 輸出

```python
output_file.write_text(
    json.dumps(data, ensure_ascii=False, indent=2),
    encoding="utf-8"
)
```

`json.dumps()` 將 Python dictionary 轉成 JSON 字串：

- `ensure_ascii=False`：保留繁體中文，不轉成 `\\uXXXX`
- `indent=2`：讓 JSON 具有縮排，方便閱讀

輸出結果範例：

```json
{
  "metadata": {
    "年度": "114",
    "檔案編號": "01",
    "判決結果": "不受理"
  },
  "文件種類": "訴願決定書",
  "案號": "1141020110",
  "要旨": "因違反噪音管制法事件提起訴願",
  "發文日期": "民國 114 年 04 月 09 日",
  "發文字號": "新北府訴決字第 1140172313 號",
  "全文": "新北市政府訴願決定書案號...",
  "主文": "訴願不受理。",
  "事實": null,
  "理由": "一、按訴願法第47條..."
}
```

## 10. 未來資料比對的可用欄位

這份 JSON 保留了未來進行歷史案例比對時需要的核心資料：

- `全文`：比對案件背景與原處分
- `事實`：比對具體事件
- `理由`：檢索法律規範與涵攝分析
- `主文`：篩選最後決定結果
- `metadata`：依年度、檔案編號、判決結果過濾

目前「理由」已可依段落階層保存。未來若導入相似案例比對，可由研究 API 讀取這些段落，不必在本專案內維護本機模型或向量資料庫。

文件開頭的「相關法條」沒有放入 JSON，但理由內實際引用的法律條文會保留，因為理由中的法條才是本案真正適用法律的重要依據。

## 11. 目前測試結果與限制

目前已用 101 份 TXT 實際測試：

- 成功處理：101/101
- JSON 格式錯誤：0
- metadata 缺失：0

這套規則是依照目前的新北市訴願決定書格式設計，對同類型文件適用性高，但不能保證所有機關或所有訴願文件都相同。遇到找不到標題或檔名格式不符時，程式會回傳 `None`，而不是讓模型自行猜測。
