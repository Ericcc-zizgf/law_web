"""互動式測試訴願爭議點的 cosine similarity，不產生法律建議。"""

import argparse
import json
from pathlib import Path

from legal_tool.config import JSON_OUTPUT_DIR, VECTOR_DB_DIR
from legal_tool.rag.rag_mlx_vector import (
    build_vector_database,
    query_vector_database,
)


def read_issue_points():
    """讓使用者貼上多行爭議點，空白行結束本次測試。"""
    print("\n請貼上這次要測試的爭議點。")
    print("可貼多行；輸入空白行開始檢索，輸入 q 離開。")

    lines = []
    while True:
        line = input()
        if not lines and line.strip().strip("\"'").lower() == "q":
            return None
        if not line.strip():
            return "\n".join(lines).strip()
        lines.append(line)


def main():
    parser = argparse.ArgumentParser(description="互動式訴願爭議點相似度測試")
    parser.add_argument("--json-dir", default=str(JSON_OUTPUT_DIR))
    parser.add_argument("--database-dir", default=str(VECTOR_DB_DIR))
    parser.add_argument("--embedding-model", default="bge-small")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-similarity", type=float, default=0.60)
    parser.add_argument("--rebuild", action="store_true", help="重新建立向量資料庫")
    args = parser.parse_args()

    _, collection, embedding_model, chunk_count = build_vector_database(
        args.json_dir,
        Path(args.database_dir).expanduser().resolve(),
        args.embedding_model,
        rebuild=args.rebuild,
    )
    print(f"向量資料庫已就緒，共 {chunk_count} 個理由段落。")
    print(f"目前門檻：cosine similarity >= {args.min_similarity:.2f}")

    while True:
        issue_points = read_issue_points()
        if issue_points is None:
            print("已離開爭議點測試。")
            break
        if not issue_points:
            print("沒有輸入內容，請重新測試。")
            continue

        results = query_vector_database(
            collection,
            embedding_model,
            issue_points,
            top_k=args.top_k,
            min_similarity=args.min_similarity,
        )

        print("\n===== 爭議點相似度結果 =====")
        print(f"找到 {len(results)} 筆通過門檻的結果。")
        if not results:
            print("沒有結果，請降低 --min-similarity 或改寫爭議點。")
            continue

        for index, result in enumerate(results, start=1):
            print(f"\n[{index}] 相似度：{result['cosine_similarity']:.4f} "
                  f"（{result['相似度百分比']:.2f}%）")
            print(f"來源：{result['source_file']}")
            print(f"案號：{result['案號']}；理由段落：{result['段落編號']}")
            print(f"內容：{result['內容']}")

        print("\n本次只評估爭議點語意相似度，不判斷法律是否適用。")


if __name__ == "__main__":
    main()
