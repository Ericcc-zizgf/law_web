"""使用 MLX embedding + ChromaDB cosine similarity 的訴願 RAG。"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from legal_tool.config import JSON_OUTPUT_DIR, VECTOR_DB_DIR

from legal_tool.rag.rag_mlx import (
    build_prompt,
    build_revision_prompt,
    build_verifier_prompt,
    deterministic_review,
    generate_with_loaded_mlx,
    load_json_chunks,
    load_mlx_generator,
    parse_json_object,
)


def make_id(chunk):
    """用來源檔案與段落編號產生穩定的 Chroma ID。"""
    raw = f"{chunk['source_file']}::{chunk['段落編號']}::{chunk['內容']}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def safe_metadata(chunk):
    """相容最外層或 metadata 巢狀層級的欄位讀取。"""
    inner_meta = chunk.get("metadata") or {}
    return {
        "source_file": str(chunk.get("source_file") or ""),
        "案號": str(chunk.get("案號") or inner_meta.get("案號") or ""),
        "年度": str(chunk.get("年度") or inner_meta.get("年度") or ""),
        "檔案編號": str(chunk.get("檔案編號") or inner_meta.get("檔案編號") or ""),
        "判決結果": str(chunk.get("判決結果") or inner_meta.get("判決結果") or ""),
        "段落編號": str(chunk.get("段落編號") or ""),
        "段落層級": str(chunk.get("段落層級") or inner_meta.get("段落層級") or ""),
    }


def load_embedding_model(model_name):
    """載入可在 Apple Silicon 上執行的 MLX embedding 模型。"""
    # mlx-embedding-models 0.0.11 仍呼叫 Transformers 4 的
    # batch_encode_plus；Transformers 5 已移除該別名，但 __call__
    # 提供相同的批次 tokenization 功能，因此在程式內補上相容轉接。
    from transformers import PreTrainedTokenizerBase

    if not hasattr(PreTrainedTokenizerBase, "batch_encode_plus"):
        def batch_encode_plus(self, *args, **kwargs):
            return self(*args, **kwargs)

        PreTrainedTokenizerBase.batch_encode_plus = batch_encode_plus

    from mlx_embedding_models.embedding import EmbeddingModel

    return EmbeddingModel.from_registry(model_name)


def build_vector_database(json_dir, database_dir, embedding_model_name, rebuild=False):
    """將 JSON 理由段落向量化並保存到 ChromaDB。"""
    import chromadb

    chunks = load_json_chunks(json_dir)
    if not chunks:
        raise ValueError(f"在 {json_dir} 找不到可用的理由段落。")

    client = chromadb.PersistentClient(path=str(database_dir))
    # JSON 結構改為階層式後，使用新 collection，避免讀到舊版平面 metadata。
    collection_name = "legal_reasons_cosine_v2"

    if rebuild:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=collection_name,
        configuration={"hnsw": {"space": "cosine"}},
    )

    model = load_embedding_model(embedding_model_name)
    ids = [make_id(chunk) for chunk in chunks]
    existing_ids = set(collection.get().get("ids") or []) if collection.count() else set()
    pending_indexes = [
        index for index, chunk_id in enumerate(ids)
        if chunk_id not in existing_ids
    ]
    if not pending_indexes:
        return client, collection, model, collection.count()

    pending_ids = [ids[index] for index in pending_indexes]
    documents = [chunks[index]["內容"] for index in pending_indexes]
    metadatas = [safe_metadata(chunks[index]) for index in pending_indexes]
    embeddings = model.encode(documents).tolist()

    collection.upsert(
        ids=pending_ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )

    return client, collection, model, collection.count()


def query_vector_database(collection, model, petition, top_k=5,
                        min_similarity=0.60, max_per_source=2):
    """查詢向量資料庫，將 cosine distance 轉成 similarity。"""
    candidate_count = min(max(top_k * 5, 20), collection.count())
    query_embedding = model.encode([petition]).tolist()
    result = collection.query(
        query_embeddings=query_embedding,
        n_results=candidate_count,
        include=["documents", "metadatas", "distances"],
    )

    output = []
    source_counts = Counter()
    for document, metadata, distance in zip(
        result["documents"][0],
        result["metadatas"][0],
        result["distances"][0],
    ):
        similarity = 1.0 - float(distance)
        if similarity < min_similarity:
            continue

        source = metadata.get("source_file", "")
        if source_counts[source] >= max_per_source:
            continue

        output.append({
            "source_file": source,
            "案號": metadata.get("案號"),
            "年度": metadata.get("年度"),
            "檔案編號": metadata.get("檔案編號"),
            "判決結果": metadata.get("判決結果"),
            "段落編號": metadata.get("段落編號"),
            "段落層級": metadata.get("段落層級"),
            "內容": document,
            "cosine_similarity": round(similarity, 4),
            "相似度百分比": round(similarity * 100, 2),
        })
        source_counts[source] += 1

        if len(output) >= top_k:
            break

    return output


def main():
    parser = argparse.ArgumentParser(description="MLX embedding + ChromaDB 訴願 RAG")
    parser.add_argument("--json-dir", default=str(JSON_OUTPUT_DIR))
    parser.add_argument("--database-dir", default=str(VECTOR_DB_DIR))
    parser.add_argument("--query-file", required=True, help="新訴願書 TXT 路徑")
    parser.add_argument(
        "--embedding-model", default="bge-small",
        help="MLX embedding registry 名稱，預設為 bge-small"
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-similarity", type=float, default=0.60)
    parser.add_argument("--rebuild", action="store_true", help="重建向量索引")
    parser.add_argument("--search-only", action="store_true")
    args = parser.parse_args()

    petition = Path(args.query_file).read_text(encoding="utf-8", errors="replace")
    database_dir = Path(args.database_dir).expanduser().resolve()
    _, collection, embedding_model, chunk_count = build_vector_database(
        args.json_dir,
        database_dir,
        args.embedding_model,
        rebuild=args.rebuild,
    )
    results = query_vector_database(
        collection,
        embedding_model,
        petition,
        top_k=args.top_k,
        min_similarity=args.min_similarity,
    )

    print(f"\n===== 爭議點相似度結果 =====")
    print(f"向量資料庫理由段落：{chunk_count} 個")
    print(f"找到 {len(results)} 筆通過門檻的結果。\n")

    for idx, item in enumerate(results, 1):
        print(f"[{idx}] 相似度：{item['cosine_similarity']:.4f} （{item['相似度百分比']}%）")
        print(f"  來源檔案：{item['source_file']}")
        print(f"  案號：{item['案號']}")
        print(f"  年度：{item['年度']}")
        print(f"  檔案編號：{item['檔案編號']}")
        print(f"  判決結果：{item['判決結果']}")
        print(f"  段落編號：{item['段落編號']}")
        print(f"  內文：\n    {item['內容']}\n")
        print("-" * 60)

    if args.search_only:
        return

    model_name = "mlx-community/Qwen2.5-7B-Instruct-4bit"
    generator_model, tokenizer = load_mlx_generator(model_name)
    prompt = build_prompt(petition, results)
    final_answer = None
    final_review = None
    max_retries = 2

    for attempt in range(max_retries + 1):
        draft = generate_with_loaded_mlx(
            generator_model,
            tokenizer,
            prompt,
            max_tokens=2500,
        )
        verifier_prompt = build_verifier_prompt(petition, draft, results)
        verifier_text = generate_with_loaded_mlx(
            generator_model,
            tokenizer,
            verifier_prompt,
            max_tokens=1200,
        )
        review = parse_json_object(verifier_text)
        rule_issues = deterministic_review(draft)

        if review is None:
            review = {
                "通過": False,
                "修正要求": ["審查器沒有回傳有效 JSON，禁止直接輸出草稿。"],
            }
        if rule_issues:
            review["通過"] = False
            review.setdefault("修正要求", []).extend(rule_issues)

        final_review = review
        if review.get("通過") is True and not rule_issues:
            final_answer = draft
            break

        if attempt < max_retries:
            print(f"⚠️ 第 {attempt + 1} 次審查未通過，重新生成中……")
            prompt = build_revision_prompt(
                petition,
                draft,
                review,
                results,
            )

    print("\n===== 審查結果 =====\n")
    print(json.dumps(final_review, ensure_ascii=False, indent=2))

    if final_answer is None:
        print("\n===== MLX 建議結果 =====\n")
        print("已阻擋輸出：模型重試後仍有歷史事實移植、資料不足或結論矛盾問題。")
        return

    print("\n===== MLX 建議結果 =====\n")
    print(final_answer)


if __name__ == "__main__":
    main()
