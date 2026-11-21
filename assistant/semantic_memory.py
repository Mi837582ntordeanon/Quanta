"""Optional local semantic search for explicitly saved memories.

The small multilingual ONNX model runs on CPU. It is never loaded or fetched
until the user starts semantic search from the Memory panel. Until then callers
can keep using the fast lexical fallback.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from pathlib import Path
from typing import Any

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_SIZE_GB = 0.22


class SemanticMemoryIndex:
    def __init__(self, cache_dir: Path, index_path: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.index_path = Path(index_path)
        self._lock = threading.RLock()
        self._state = "not_started"
        self._error = ""
        self._embedder: Any = None
        self._vectors = self._read_vectors()
        self._worker: threading.Thread | None = None

    def _read_vectors(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for key, item in data.items():
            if not isinstance(item, dict) or not isinstance(item.get("vector"), list):
                continue
            vector = item["vector"]
            if len(vector) != 384:
                continue
            try:
                result[str(key)] = {"digest": str(item.get("digest") or ""),
                                    "vector": [float(part) for part in vector]}
            except (TypeError, ValueError):
                continue
        return result

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
            error = self._error
            indexed = len(self._vectors)
        try:
            import importlib.util
            installed = importlib.util.find_spec("fastembed") is not None
        except (ImportError, ValueError):
            installed = False
        if not installed and state == "not_started":
            state = "dependency_missing"
        return {
            "status": state,
            "model": MODEL_NAME,
            "size_gb": MODEL_SIZE_GB,
            "indexed": indexed,
            "installed": installed,
            "error": error,
        }

    def start(self, memories: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        with self._lock:
            if self._state in {"loading", "ready"}:
                return self.status()
            self._state = "loading"
            self._error = ""
            self._worker = threading.Thread(target=self._load_worker,
                                            args=(list(memories or []),),
                                            name="quanta-memory-embedding", daemon=True)
            self._worker.start()
            return self.status()

    def _load_worker(self, memories: list[dict[str, Any]]) -> None:
        try:
            from fastembed import TextEmbedding

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            embedder = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(self.cache_dir), threads=2)
            with self._lock:
                self._embedder = embedder
                self._state = "ready"
                self._error = ""
            # Build the initial index away from the chat's streaming thread.
            self._index_memories(memories)
        except ImportError:
            with self._lock:
                self._state = "dependency_missing"
                self._error = "Установи зависимости из requirements.txt и повтори загрузку."
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._state = "error"
                self._error = str(exc)[:500]

    @staticmethod
    def _digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()

    def _embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        assert self._embedder is not None
        return [[float(value) for value in vector] for vector in self._embedder.embed(texts)]

    def _index_memories(self, memories: list[dict[str, Any]]) -> None:
        with self._lock:
            if self._state != "ready" or self._embedder is None:
                return
            items = [item for item in memories if str(item.get("text") or "").strip()]
            current_ids = {str(item.get("id") or "") for item in items}
            previous_count = len(self._vectors)
            self._vectors = {key: value for key, value in self._vectors.items() if key in current_ids}
            missing = []
            for item in items:
                identifier = str(item.get("id") or "")
                text = str(item.get("text") or "").strip()
                digest = self._digest(text)
                cached = self._vectors.get(identifier)
                if not cached or cached.get("digest") != digest:
                    missing.append((identifier, digest, text))
            if missing:
                vectors = self._embed_many([text for _identifier, _digest, text in missing])
                for (identifier, digest, _text), vector in zip(missing, vectors):
                    if len(vector) == 384:
                        self._vectors[identifier] = {"digest": digest, "vector": vector}
            if missing or len(self._vectors) != previous_count:
                self._save_vectors()

    def remove_memory(self, identifier: str) -> None:
        with self._lock:
            if self._vectors.pop(str(identifier), None) is not None:
                self._save_vectors()

    def search(self, query: str, memories: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]] | None:
        """Return semantically ranked memories, or None while unavailable."""
        query = " ".join(str(query or "").split())
        if not query:
            return None
        with self._lock:
            if self._state != "ready" or self._embedder is None:
                return None
            items = [item for item in memories if str(item.get("text") or "").strip()]
            self._index_memories(items)
            query_vector = self._embed_many([query])[0]
            ranked = []
            for item in items:
                record = self._vectors.get(str(item.get("id") or ""))
                if not record:
                    continue
                score = cosine(query_vector, record["vector"])
                if score >= 0.20:
                    ranked.append((score, str(item.get("updated") or ""), item))
            ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
            return [row[2] for row in ranked[:max(1, min(int(limit), 100))]]

    def _save_vectors(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._vectors, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.index_path)


def cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)
