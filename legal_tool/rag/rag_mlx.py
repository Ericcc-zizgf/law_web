"""以 JSON 為知識庫、使用 MLX 產生訴願決定書建議的簡易 RAG。"""

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

from legal_tool.config import JSON_OUTPUT_DIR


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


def tokenize(text):
    """將中文拆成單字與雙字詞，英文與數字保留為完整 token。"""
    tokens = []
    for part in TOKEN_PATTERN.findall(text or ""):
        if re.fullmatch(r"[\u4e00-\u9fff]", part):
            tokens.append(part)
        else:
            tokens.append(part.lower())

    chinese_chars = [token for token in tokens if len(token) == 1]
    tokens.extend(
        chinese_chars[index] + chinese_chars[index + 1]
        for index in range(len(chinese_chars) - 1)
    )
    return tokens


def flatten_hierarchy(nodes):
    """將階層式事實或理由展平成可加入檢索文字的內容。"""
    content = []
    for node in nodes or []:
        text = str(node.get("內容") or "").strip()
        if text:
            content.append(text)
        content.extend(flatten_hierarchy(node.get("子段落") or []))
    return content


def load_json_chunks(json_dir):
    """從每份 JSON 的理由階層建立可檢索的 chunk。"""
    chunks = []
    for path in sorted(Path(json_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"⚠️ 跳過無法讀取的 JSON：{path.name}，原因：{error}")
            continue

        metadata = data.get("metadata") or {}
        sections = data.get("理由") or []
        fact_text = "\n".join(flatten_hierarchy(data.get("事實") or []))

        for section in sections:
            content = str(section.get("內容") or "").strip()
            if not content:
                continue

            heading = section.get("編號", "")
            searchable_text = "\n".join([
                str(data.get("要旨") or ""),
                str(data.get("主文") or ""),
                fact_text,
                content,
            ])
            chunks.append({
                "source_file": (
                    metadata.get("顯示檔名")
                    or metadata.get("原始檔名")
                    or path.name
                ),
                "案號": data.get("案號"),
                "年度": metadata.get("年度"),
                "檔案編號": metadata.get("檔案編號"),
                "判決結果": metadata.get("判決結果") or data.get("主文"),
                "段落編號": heading,
                "段落層級": section.get("層級"),
                "內容": content,
                "_searchable_text": searchable_text,
            })

    return chunks


class BM25Index:
    """不依賴額外向量資料庫的本機 BM25 檢索器，適合先測試流程。"""

    def __init__(self, chunks):
        self.chunks = chunks
        self.documents = [Counter(tokenize(chunk["_searchable_text"])) for chunk in chunks]
        self.document_count = len(self.documents)
        self.average_length = (
            sum(sum(document.values()) for document in self.documents)
            / self.document_count
            if self.document_count else 0
        )
        self.document_frequency = Counter()
        for document in self.documents:
            self.document_frequency.update(document.keys())

    def search(self, query, top_k=5, max_per_source=2):
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return []

        scores = []
        k1 = 1.5
        b = 0.75
        for index, document in enumerate(self.documents):
            length = sum(document.values()) or 1
            score = 0.0
            for token in query_tokens:
                term_frequency = document.get(token, 0)
                if not term_frequency:
                    continue
                df = self.document_frequency[token]
                idf = math.log(1 + (self.document_count - df + 0.5) / (df + 0.5))
                denominator = term_frequency + k1 * (
                    1 - b + b * length / (self.average_length or 1)
                )
                score += idf * term_frequency * (k1 + 1) / denominator
            if score > 0:
                scores.append((score, index))

        scores.sort(reverse=True)
        results = []
        source_counts = Counter()
        for score, index in scores:
            source = self.chunks[index]["source_file"]
            if source_counts[source] >= max_per_source:
                continue

            result = dict(self.chunks[index])
            result.pop("_searchable_text", None)
            result["檢索分數"] = round(score, 4)
            results.append(result)
            source_counts[source] += 1

            if len(results) >= top_k:
                break
        return results


def build_prompt(petition, retrieved_chunks):
    """建立要求模型忠實引用檢索證據的 MLX prompt。"""
    evidence = []
    for index, chunk in enumerate(retrieved_chunks, start=1):
        evidence.append(
            f"【證據 {index}】\n"
            f"來源：{chunk['source_file']}\n"
            f"案號：{chunk.get('案號')}；判決結果：{chunk.get('判決結果')}；"
            f"理由段落：{chunk.get('段落編號')}\n"
            f"內容：{chunk['內容']}"
        )

    evidence_text = "\n\n".join(evidence) or "（找不到相似歷史案例）"
    return f"""你是協助行政機關整理訴願決定書的法務分析助手。

請根據【新訴願書】與【檢索到的歷史案例】提出草案建議。歷史案例只能作為法律論證與寫作格式的參考，不能直接套用其事實或結論。

【不可違反的事實規則】
- 新案件的事實只能來自【新訴願書】明確寫出的內容。
- 歷史案例中的人物、日期、地點、車輛、稽查結果、照片、檢測結果、金額與文號，不得移植到新案件。
- 如果新訴願書沒有提供關鍵事實，請寫「資料未提供，待人工確認」，不可自行補完。
- 不得因為相似案例是「駁回」或「撤銷」就直接套用同一判決結果。
- 不得捏造法條、公告、判決、案號或證據；檢索證據不足時必須明確說明。

請用繁體中文，並嚴格依照以下格式回答：
1. 初步爭點
2. 建議檢視的法規（說明法規與理由的對應）
3. 相似案例依據（標明證據編號、來源檔案與案號）
4. 建議判決結果（若事實不足，必須回答「目前無法判斷」）
5. 訴願決定書草案（包含主文、事實、理由）
6. 需要人工確認的事項

【結論一致性規則】
- 如果主文寫「訴願駁回」或「原處分維持」，理由結論不可寫「撤銷原處分」。
- 如果主文寫「撤銷原處分」，理由結論不可寫「原處分並無違誤，應予維持」。
- 產生答案前，請再次檢查主文、建議判決結果與理由最後一段是否一致。
- 事實不足時，草案中的未知內容請使用「[待確認]」，不要使用歷史案件資料代替。

【新訴願書】
{petition}

【檢索到的歷史案例】
{evidence_text}
"""


def load_mlx_generator(model_name):
    """載入一次 MLX 生成模型，讓草稿與審查共用同一個模型。"""
    from mlx_lm import load

    return load(model_name)


def generate_with_loaded_mlx(model, tokenizer, prompt, max_tokens=2500):
    """使用已載入的 MLX 模型生成文字。"""
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler

    messages = [{"role": "user", "content": prompt}]
    formatted_prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    return generate(
        model,
        tokenizer,
        prompt=formatted_prompt,
        max_tokens=max_tokens,
        sampler=make_sampler(temp=0.2),
        verbose=False,
    )


def generate_with_mlx(prompt, model_name, max_tokens=2500):
    """使用 MLX 7B 模型生成文字。"""
    model, tokenizer = load_mlx_generator(model_name)
    return generate_with_loaded_mlx(model, tokenizer, prompt, max_tokens)


def build_verifier_prompt(petition, draft, retrieved_chunks):
    """建立第二階段 AI 審查 prompt。"""
    evidence = "\n\n".join(
        f"【證據 {index}】來源：{chunk['source_file']}；案號：{chunk.get('案號')}\n"
        f"{chunk['內容']}"
        for index, chunk in enumerate(retrieved_chunks, start=1)
    )
    return f"""你是訴願決定書草稿的嚴格事實查核員。

請比較【新訴願書】、【檢索證據】與【模型草稿】，只輸出合法 JSON，不要輸出 Markdown 或說明文字。

你必須檢查：
1. 草稿是否把歷史案例的人物、日期、地點、車輛、金額、文號、照片、檢測結果或機關處理經過套到新案件。
2. 草稿中的法規是否能在檢索證據找到；找不到就列為法規來源不足。
3. 「建議判決結果」「主文」與「理由最後結論」是否互相矛盾。
4. 新訴願書沒有提供的關鍵事實是否被模型自行補完。

只有在沒有任何嚴重問題，而且未知內容都有標記「[待確認]」或「資料未提供」時，通過才可為 true。

輸出格式：
{{
  "通過": true,
  "歷史事實移植": [],
  "新文件未提供的事實": [],
  "主文理由衝突": false,
  "法規來源不足": [],
  "缺少資料": [],
  "修正要求": []
}}

【新訴願書】
{petition}

【檢索證據】
{evidence or "（無檢索證據）"}

【模型草稿】
{draft}
"""


def parse_json_object(text):
    """從模型回覆中取出第一個 JSON 物件；解析失敗時回傳 None。"""
    if not text:
        return None

    start = text.find("{")
    if start == -1:
        return None

    try:
        result, _ = json.JSONDecoder().raw_decode(text[start:])
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


def deterministic_review(draft):
    """先用固定規則攔截最明顯的主文與理由矛盾。"""
    issues = []
    if "目前無法判斷" in draft and "主文：訴願駁回" in draft:
        issues.append("建議結果為目前無法判斷，但主文卻寫訴願駁回")
    if "目前無法判斷" in draft and "主文：撤銷原處分" in draft:
        issues.append("建議結果為目前無法判斷，但主文卻寫撤銷原處分")
    if "訴願駁回" in draft and "撤銷原處分" in draft:
        issues.append("草稿同時出現訴願駁回與撤銷原處分")
    if "應予維持" in draft and "撤銷原處分" in draft:
        issues.append("理由同時出現應予維持與撤銷原處分")
    return issues


def build_revision_prompt(petition, draft, review, retrieved_chunks):
    """根據審查錯誤要求模型重新產生草稿。"""
    base_prompt = build_prompt(petition, retrieved_chunks)
    return f"""{base_prompt}

【前一版草稿的審查結果】
{json.dumps(review, ensure_ascii=False, indent=2)}

請重新產生完整修正版，務必修正所有問題：
- 歷史案例事實不得放入新案件；新訴願書沒有的內容一律寫「[待確認]」。
- 如果關鍵事實不足，建議判決結果必須是「目前無法判斷」。
- 如果建議結果是「目前無法判斷」，主文不可自行寫「訴願駁回」或「撤銷原處分」。
- 修正版的主文、建議判決結果與理由結論必須一致。
"""


def main():
    parser = argparse.ArgumentParser(description="JSON + MLX 訴願 RAG 測試工具")
    parser.add_argument("--json-dir", default=str(JSON_OUTPUT_DIR))
    parser.add_argument("--query-file", help="新訴願書 TXT 路徑")
    parser.add_argument("--query-text", help="直接輸入新訴願書文字")
    parser.add_argument(
        "--model", default="mlx-community/Qwen2.5-7B-Instruct-4bit",
        help="MLX 模型名稱或本機模型路徑",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--search-only", action="store_true", help="只測試檢索，不載入模型")
    args = parser.parse_args()

    if args.query_file:
        petition = Path(args.query_file).read_text(encoding="utf-8", errors="replace")
    elif args.query_text:
        petition = args.query_text
    else:
        raise SystemExit("請提供 --query-file 或 --query-text。")

    chunks = load_json_chunks(args.json_dir)
    if not chunks:
        raise SystemExit(f"在 {args.json_dir} 找不到可用的理由段落。")

    index = BM25Index(chunks)
    results = index.search(petition, top_k=args.top_k, max_per_source=2)
    print(f"知識庫理由段落：{len(chunks)} 個")
    print(f"找到相似段落：{len(results)} 個")
    print(json.dumps(results, ensure_ascii=False, indent=2))

    if args.search_only:
        return

    prompt = build_prompt(petition, results)
    try:
        answer = generate_with_mlx(prompt, args.model)
    except ImportError as error:
        raise SystemExit(
            "MLX 載入失敗。請確認你是在 Apple Silicon Mac 的本機終端機執行，"
            "並已安裝 mlx-lm。詳細原因：" + str(error)
        ) from error
    except Exception as error:
        raise SystemExit(
            "MLX 生成失敗，檢索結果仍然有效；請確認模型、記憶體與 Metal GPU 狀態。"
            f"詳細原因：{error}"
        ) from error

    print("\n===== MLX 建議結果 =====\n")
    print(answer)

    if (
        ("撤銷原處分" in answer and "應予維持" in answer)
        or ("訴願駁回" in answer and "撤銷原處分" in answer)
    ):
        print(
            "\n⚠️ 警告：生成內容可能同時包含撤銷與維持／駁回結論，"
            "請勿直接使用，必須人工重新審核。"
        )


if __name__ == "__main__":
    main()
