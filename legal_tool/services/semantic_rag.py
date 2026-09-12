"""AWS Bedrock 語意檢索與 EFS 向量快取。

歷史決定書原文與結構化 JSON 仍由既有資料庫管理；本模組只保存可重建的
embedding 索引。每筆向量都帶著文件 ID、理由段落路徑及內容雜湊，搜尋結果
因此能回到原 PDF 並定位逐字原文。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol


INDEX_VERSION = 1
MAX_EMBEDDING_INPUT_CHARACTERS = 48_000
RETRYABLE_BEDROCK_ERRORS = {
    "InternalServerException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "ThrottlingException",
}


class SemanticRagError(RuntimeError):
    """語意索引或 Bedrock 呼叫無法完成。"""


class EmbeddingClient(Protocol):
    provider: str
    model_id: str
    dimensions: int

    def embed(self, text: str) -> list[float]: ...


class BedrockTitanEmbeddingClient:
    """透過 EC2 instance profile 呼叫 Amazon Titan Text Embeddings V2。"""

    provider = "amazon-bedrock"

    def __init__(
        self,
        *,
        region: str = "us-east-1",
        model_id: str = "amazon.titan-embed-text-v2:0",
        dimensions: int = 1024,
        max_attempts: int = 3,
    ):
        if dimensions not in {256, 512, 1024}:
            raise ValueError("Titan embedding dimensions 必須是 256、512 或 1024")
        self.region = region
        self.model_id = model_id
        self.dimensions = dimensions
        self.max_attempts = max(1, max_attempts)
        self._runtime_client = None

    def _client(self):
        if self._runtime_client is None:
            try:
                import boto3
            except ImportError as error:
                raise SemanticRagError(
                    "缺少 boto3，請先重新部署 requirements.txt"
                ) from error
            self._runtime_client = boto3.client(
                "bedrock-runtime",
                region_name=self.region,
            )
        return self._runtime_client

    def embed(self, text: str) -> list[float]:
        input_text = str(text or "").strip()[:MAX_EMBEDDING_INPUT_CHARACTERS]
        if not input_text:
            raise SemanticRagError("無法為空白文字建立 embedding")

        request_body = json.dumps({
            "inputText": input_text,
            "dimensions": self.dimensions,
            "normalize": True,
        })
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._client().invoke_model(
                    modelId=self.model_id,
                    body=request_body,
                    accept="application/json",
                    contentType="application/json",
                )
                payload = json.loads(response["body"].read())
                vector = payload.get("embedding")
                return _validate_vector(vector, self.dimensions)
            except SemanticRagError:
                raise
            except Exception as error:
                error_code = str(
                    getattr(error, "response", {})
                    .get("Error", {})
                    .get("Code", "")
                )
                if error_code in RETRYABLE_BEDROCK_ERRORS and attempt < self.max_attempts:
                    time.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                detail = error_code or error.__class__.__name__
                raise SemanticRagError(
                    f"Amazon Bedrock embedding 呼叫失敗（{detail}）"
                ) from error
        raise SemanticRagError("Amazon Bedrock embedding 呼叫失敗")


def embedding_client_from_environment() -> BedrockTitanEmbeddingClient:
    """使用 Elastic Beanstalk 環境變數建立 Bedrock client。"""
    try:
        dimensions = int(os.environ.get("RAG_EMBEDDING_DIMENSIONS", "1024"))
    except ValueError as error:
        raise SemanticRagError("RAG_EMBEDDING_DIMENSIONS 必須是整數") from error
    return BedrockTitanEmbeddingClient(
        region=os.environ.get("RAG_AWS_REGION", os.environ.get("AWS_REGION", "us-east-1")),
        model_id=os.environ.get(
            "RAG_EMBEDDING_MODEL_ID",
            "amazon.titan-embed-text-v2:0",
        ),
        dimensions=dimensions,
    )


def _validate_vector(vector, dimensions: int) -> list[float]:
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise SemanticRagError(
            f"Bedrock 回傳的 embedding 維度錯誤，預期 {dimensions}"
        )
    try:
        values = [float(value) for value in vector]
    except (TypeError, ValueError) as error:
        raise SemanticRagError("Bedrock 回傳了無效的 embedding") from error
    if not all(math.isfinite(value) for value in values):
        raise SemanticRagError("Bedrock embedding 含有非有限數值")
    return values


def _content_digest(chunk: dict) -> str:
    identity = json.dumps({
        "chunk_id": chunk.get("chunk_id"),
        "search_text": chunk.get("search_text"),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _index_path(index_dir: Path, embedding_client: EmbeddingClient) -> Path:
    safe_model_name = "".join(
        character if character.isalnum() else "-"
        for character in embedding_client.model_id
    ).strip("-")
    return Path(index_dir) / f"historical-{safe_model_name}-{embedding_client.dimensions}.json"


@contextmanager
def _index_lock(index_path: Path):
    """避免多個 Gunicorn worker 同時覆寫同一份 EFS 索引。"""
    import fcntl

    lock_path = index_path.with_suffix(index_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _empty_index(embedding_client: EmbeddingClient) -> dict:
    return {
        "version": INDEX_VERSION,
        "provider": embedding_client.provider,
        "model_id": embedding_client.model_id,
        "dimensions": embedding_client.dimensions,
        "items": {},
    }


def _load_index(index_path: Path, embedding_client: EmbeddingClient) -> dict:
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return _empty_index(embedding_client)
    if (
        payload.get("version") != INDEX_VERSION
        or payload.get("provider") != embedding_client.provider
        or payload.get("model_id") != embedding_client.model_id
        or payload.get("dimensions") != embedding_client.dimensions
        or not isinstance(payload.get("items"), dict)
    ):
        return _empty_index(embedding_client)
    return payload


def _save_index(index_path: Path, payload: dict) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=index_path.parent,
            prefix=f".{index_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_name = temporary_file.name
            json.dump(payload, temporary_file, ensure_ascii=False, separators=(",", ":"))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, index_path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def ensure_chunk_embeddings(
    chunks: list[dict],
    *,
    index_dir: Path,
    embedding_client: EmbeddingClient,
) -> tuple[dict[str, list[float]], dict]:
    """建立缺少或內容已變更的向量，回傳目前查詢需要的向量。"""
    index_path = _index_path(index_dir, embedding_client)
    with _index_lock(index_path):
        payload = _load_index(index_path, embedding_client)
        items = payload["items"]
        changed = False
        generated_count = 0
        vectors = {}
        for chunk in chunks:
            chunk_id = str(chunk.get("chunk_id") or "")
            digest = _content_digest(chunk)
            cached = items.get(chunk_id) or {}
            vector = cached.get("embedding") if cached.get("digest") == digest else None
            try:
                vector = _validate_vector(vector, embedding_client.dimensions)
            except SemanticRagError:
                vector = embedding_client.embed(chunk["search_text"])
                items[chunk_id] = {"digest": digest, "embedding": vector}
                changed = True
                generated_count += 1
            vectors[chunk_id] = vector
        if changed:
            payload["updated_at"] = int(time.time())
            _save_index(index_path, payload)
        return vectors, {
            "index_path": str(index_path),
            "indexed_vectors": len(items),
            "new_vectors": generated_count,
        }


def cosine_similarity(first: list[float], second: list[float]) -> float:
    if len(first) != len(second) or not first:
        return 0.0
    dot_product = sum(left * right for left, right in zip(first, second))
    first_norm = math.sqrt(sum(value * value for value in first))
    second_norm = math.sqrt(sum(value * value for value in second))
    if not first_norm or not second_norm:
        return 0.0
    return dot_product / (first_norm * second_norm)


def semantic_index_status(
    index_dir: Path,
    embedding_client: EmbeddingClient,
) -> dict:
    index_path = _index_path(index_dir, embedding_client)
    payload = _load_index(index_path, embedding_client)
    return {
        "provider": embedding_client.provider,
        "model_id": embedding_client.model_id,
        "dimensions": embedding_client.dimensions,
        "indexed_vectors": len(payload.get("items") or {}),
        "updated_at": payload.get("updated_at"),
    }
