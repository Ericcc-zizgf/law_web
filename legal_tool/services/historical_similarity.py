"""歷史訴願決定書的可追溯混合式語意檢索。

歷史 JSON 的理由階層會展平為可引用段落。正式模式使用 Amazon Bedrock
embedding 與 BM25 混合排序；關鍵字模式保留供診斷與離線測試。
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

from legal_tool.services.semantic_rag import (
    EmbeddingClient,
    cosine_similarity,
    ensure_chunk_embeddings,
)


TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
CHINESE_RUN_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
ARTICLE_PATTERN = re.compile(r"第\s*(\d+(?:\s*之\s*\d+)?)\s*條")
GENERIC_TERMS = {
    "本件", "訴願", "願人", "原處", "處分", "分機", "機關", "理由",
    "事實", "依法", "規定", "應予", "部分", "民國", "下同", "其中",
    "案件", "系爭", "查得", "卷查", "該函", "處所", "文到", "資料",
}


def tokenize(text: str) -> list[str]:
    """產生適合繁體中文法律文字的詞組，排除日期、案號與文書套語。"""
    source = str(text or "")
    normalized = re.sub(r"\s+", "", source)
    tokens = [f"條{article.replace('之', '-')}" for article in ARTICLE_PATTERN.findall(source)]
    for run in CHINESE_RUN_PATTERN.findall(source):
        # 中文單字會讓「原、處、分、函」等文書雜訊主宰排名；改採雙字與三字詞。
        tokens.extend(run[index:index + 2] for index in range(len(run) - 1))
        tokens.extend(run[index:index + 3] for index in range(len(run) - 2))
    tokens.extend(token.lower() for token in TOKEN_PATTERN.findall(normalized))
    return [
        token for token in tokens
        if token not in GENERIC_TERMS and not token.isdigit() and len(token) >= 2
    ]


def _paragraph_label(node: dict, index: int) -> str:
    return str(node.get("編號") or node.get("名稱") or f"段落{index}").strip()


def _walk_hierarchy(nodes, section: str, path: tuple[str, ...] = ()):
    """將 JSON 階層展成獨立、可引用的段落。"""
    for index, node in enumerate(nodes or [], start=1):
        if not isinstance(node, dict):
            continue
        label = _paragraph_label(node, index)
        paragraph_path = (*path, label)
        content = str(node.get("內容") or "").strip()
        if content:
            yield {
                "section": section,
                "paragraph_path": list(paragraph_path),
                "content": content,
            }
        yield from _walk_hierarchy(
            node.get("子段落") or [], section, paragraph_path
        )


def load_historical_chunks(json_dir: Path) -> list[dict]:
    """讀取歷史 JSON，建立理由段落檢索資料；壞檔案會被安全略過。"""
    chunks = []
    for json_path in sorted(Path(json_dir).glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if not isinstance(data, dict):
            continue
        metadata = data.get("metadata") or {}
        document_id = str(metadata.get("文件ID") or "").strip()
        if not re.fullmatch(r"[0-9a-f]{32}", document_id):
            continue

        context = "\n".join(
            value for value in (
                f"案件法律類別：{metadata.get('案件法律類別') or '未分類'}",
                f"要旨：{data.get('要旨') or ''}",
                f"主文：{data.get('主文') or ''}",
            ) if value.strip()
        )
        for paragraph in _walk_hierarchy(data.get("理由") or [], "理由"):
            content = paragraph["content"]
            if len(tokenize(content)) < 4:
                continue
            chunks.append({
                "chunk_id": ":".join([
                    document_id,
                    "reason",
                    *paragraph["paragraph_path"],
                ]),
                "document_id": document_id,
                "display_name": str(
                    metadata.get("顯示檔名")
                    or metadata.get("原始檔名")
                    or json_path.name
                ),
                "year": str(metadata.get("年度") or ""),
                "file_number": str(metadata.get("檔案編號") or ""),
                "law_category": str(metadata.get("案件法律類別") or "未分類"),
                "outcome": str(metadata.get("判決結果") or data.get("主文") or ""),
                "section": paragraph["section"],
                "paragraph_path": paragraph["paragraph_path"],
                "content": content,
                "search_text": f"{context}\n理由 {' > '.join(paragraph['paragraph_path'])}\n{content}",
            })
    return chunks


def _focus_quote(text: str, maximum_length: int = 360) -> str:
    """保留可安全放入閱讀器網址、且能在 PDF 中反白的原文片段。"""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= maximum_length:
        return cleaned
    sentence = re.split(r"(?<=[。；！？])", cleaned)[0].strip()
    if len(sentence) >= 20:
        return sentence[:maximum_length]
    return cleaned[:maximum_length]


def _matches_law_category(candidate: str, requested: str) -> bool:
    """讓「廢棄物清理法」可對上檔案分類「違反廢棄物清理法」。"""
    normalized_candidate = _normalize_law_category(candidate)
    normalized_requested = _normalize_law_category(requested)
    return not normalized_requested or (
        normalized_requested in normalized_candidate
        or normalized_candidate in normalized_requested
    )


def _normalize_law_category(value: str) -> str:
    normalized = re.sub(r"[\s　()（）·・]", "", str(value or ""))
    normalized = normalized.removeprefix("違反")
    # 歷史檔名常見「汙／污染」異體，官方法規名稱使用「污染」。
    return normalized.replace("汙染", "污染")


def _filter_chunks(
    chunks: list[dict],
    *,
    law_category: str = "",
    year: str = "",
) -> list[dict]:
    return [
        chunk for chunk in chunks
        if _matches_law_category(chunk["law_category"], law_category)
        and (not year or chunk["year"] == str(year))
    ]


def _bm25_scores(query_tokens: set[str], candidates: list[dict]) -> list[tuple[float, list[str]]]:
    if not query_tokens or not candidates:
        return [(0.0, []) for _ in candidates]
    documents = [Counter(tokenize(chunk["search_text"])) for chunk in candidates]
    document_count = len(documents)
    average_length = sum(sum(document.values()) for document in documents) / document_count
    document_frequency = Counter()
    for document in documents:
        document_frequency.update(document.keys())

    scores = []
    for document in documents:
        length = sum(document.values()) or 1
        score = 0.0
        matched_terms = []
        for token in query_tokens:
            frequency = document.get(token, 0)
            if not frequency:
                continue
            matched_terms.append(token)
            inverse_frequency = math.log(
                1 + (document_count - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5)
            )
            denominator = frequency + 1.5 * (
                1 - 0.75 + 0.75 * length / (average_length or 1)
            )
            score += inverse_frequency * frequency * 2.5 / denominator
        scores.append((score, matched_terms))
    return scores


def search_historical_chunks(
    query: str,
    chunks: list[dict],
    *,
    top_k: int = 5,
    law_category: str = "",
    year: str = "",
) -> list[dict]:
    """以 BM25 取得每份歷史文件最佳的理由段落。

    回傳的 score 是 0 到 1 的「文字關聯度」，不是語意 embedding 分數。
    """
    query_tokens = set(tokenize(query))
    if not query_tokens or not chunks:
        return []

    candidates = _filter_chunks(chunks, law_category=law_category, year=year)
    if not candidates:
        return []
    ranked = []
    for index, (score, matched_terms) in enumerate(_bm25_scores(query_tokens, candidates)):
        if score:
            ranked.append((score, candidates[index], matched_terms))

    ranked.sort(key=lambda item: item[0], reverse=True)
    results = []
    seen_documents = set()
    for raw_score, chunk, matched_terms in ranked:
        if chunk["document_id"] in seen_documents:
            continue
        seen_documents.add(chunk["document_id"])
        # 將無上限的 BM25 原始分數轉為穩定的 0～1 關聯度，供測試畫面比較。
        relevance = raw_score / (raw_score + 8)
        terms = sorted(set(matched_terms), key=lambda item: (-len(item), item))[:8]
        excerpt = _focus_quote(chunk["content"])
        results.append({
            **{key: value for key, value in chunk.items() if key != "search_text"},
            "score": round(relevance, 4),
            "score_type": "keyword_bm25_relevance",
            "matched_terms": terms,
            "reason": (
                f"命中詞：{'、'.join(terms) if terms else '無'}；"
                f"比對段落：{' > '.join(chunk['paragraph_path'])}"
            ),
            "matched_excerpt": excerpt,
            "focus_text": excerpt,
        })
        if len(results) >= max(1, min(int(top_k), 10)):
            break
    return results


def search_semantic_historical_chunks(
    query: str,
    chunks: list[dict],
    *,
    index_dir: Path,
    embedding_client: EmbeddingClient,
    top_k: int = 5,
    law_category: str = "",
    year: str = "",
    minimum_score: float = 0.35,
) -> tuple[list[dict], dict]:
    """以 Bedrock 語意向量、BM25 與法條重疊進行可追溯混合排序。"""
    query = str(query or "").strip()
    candidates = _filter_chunks(chunks, law_category=law_category, year=year)
    if not query or not candidates:
        return [], {
            "indexed_vectors": 0,
            "candidate_chunks": len(candidates),
            "new_vectors": 0,
        }

    vector_map, index_stats = ensure_chunk_embeddings(
        candidates,
        index_dir=index_dir,
        embedding_client=embedding_client,
    )
    query_vector = embedding_client.embed(query)
    query_tokens = set(tokenize(query))
    keyword_scores = _bm25_scores(query_tokens, candidates)
    query_articles = {
        article.replace(" ", "").replace("之", "-")
        for article in ARTICLE_PATTERN.findall(query)
    }

    ranked = []
    for chunk, (raw_keyword_score, matched_terms) in zip(candidates, keyword_scores):
        semantic_score = max(0.0, min(1.0, cosine_similarity(
            query_vector,
            vector_map[chunk["chunk_id"]],
        )))
        keyword_score = raw_keyword_score / (raw_keyword_score + 8) if raw_keyword_score else 0.0
        chunk_articles = {
            article.replace(" ", "").replace("之", "-")
            for article in ARTICLE_PATTERN.findall(chunk["search_text"])
        }
        shared_articles = sorted(query_articles & chunk_articles)
        article_score = 1.0 if shared_articles else 0.0
        # 語意是主排序訊號；關鍵詞與法條只協助法律文本的精確度。
        hybrid_score = 0.75 * semantic_score + 0.20 * keyword_score + 0.05 * article_score
        ranked.append((hybrid_score, semantic_score, keyword_score, chunk, matched_terms, shared_articles))

    ranked.sort(key=lambda item: item[0], reverse=True)
    results = []
    seen_documents = set()
    for hybrid_score, semantic_score, keyword_score, chunk, matched_terms, shared_articles in ranked:
        if hybrid_score < minimum_score:
            continue
        if chunk["document_id"] in seen_documents:
            continue
        seen_documents.add(chunk["document_id"])
        terms = sorted(set(matched_terms), key=lambda item: (-len(item), item))[:8]
        excerpt = _focus_quote(chunk["content"])
        explanation_parts = [
            f"同法律類別：{chunk['law_category']}",
            f"語意相近 {round(semantic_score * 100)}%",
        ]
        if shared_articles:
            explanation_parts.append(
                "共同法條：" + "、".join(f"第 {article.replace('-', '之')} 條" for article in shared_articles)
            )
        if terms:
            explanation_parts.append("共同用語：" + "、".join(terms))
        explanation_parts.append("原文位置：理由 " + " > ".join(chunk["paragraph_path"]))
        results.append({
            **{key: value for key, value in chunk.items() if key != "search_text"},
            "score": round(hybrid_score, 4),
            "semantic_score": round(semantic_score, 4),
            "keyword_score": round(keyword_score, 4),
            "score_type": "hybrid_bedrock_titan_bm25",
            "matched_terms": terms,
            "matched_articles": shared_articles,
            "reason": "；".join(explanation_parts),
            "matched_excerpt": excerpt,
            "focus_text": excerpt,
        })
        if len(results) >= max(1, min(int(top_k), 10)):
            break
    return results, {**index_stats, "candidate_chunks": len(candidates)}
