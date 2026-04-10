"""
RAG Retriever — векторный поиск по Travel KB через ChromaDB.
Возвращает top-K чанков для инжекта в промпт ItineraryAgent.
"""
import os
from dataclasses import dataclass

from config import cfg
from middleware.guardrail import sanitize_external


@dataclass
class RetrievalResult:
    chunks: list[str]
    sources: list[str]
    top_score: float          # cosine distance лучшего чанка (меньше = лучше)
    used_fallback: bool       # True если все чанки за порогом


class Retriever:
    def __init__(self):
        self._collection = None
        self._model = None

    def _load(self) -> None:
        """Ленивая инициализация — не грузим при импорте."""
        if self._collection is not None:
            return

        index_dir = cfg.KB_INDEX_DIR
        if not os.path.exists(index_dir):
            raise RuntimeError(
                f"Travel KB index not found at '{index_dir}'.\n"
                "Run: python scripts/download_models.py && python scripts/build_index.py"
            )

        import chromadb
        from sentence_transformers import SentenceTransformer

        client = chromadb.PersistentClient(path=index_dir)
        self._collection = client.get_collection("travel_kb")
        self._model = SentenceTransformer(cfg.EMBEDDING_MODEL)

    def search(self, destination: str, preferences: list[str] | None = None) -> RetrievalResult:
        """
        Ищет релевантный контент для destination.
        Возвращает top-K чанков, отфильтрованных по score threshold.
        """
        self._load()

        # Формируем запрос
        parts = [destination, "достопримечательности рестораны советы",
                 "attractions restaurants tips"]
        if preferences:
            parts.extend(preferences[:3])
        query = " ".join(parts)

        embedding = self._model.encode([query], convert_to_numpy=True)

        results = self._collection.query(
            query_embeddings=embedding.tolist(),
            n_results=min(cfg.RAG_TOP_K, self._collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        if not docs:
            return RetrievalResult(chunks=[], sources=[], top_score=1.0, used_fallback=True)

        top_score = distances[0]

        # Фильтруем по threshold
        relevant = [
            (doc, meta, dist)
            for doc, meta, dist in zip(docs, metas, distances)
            if dist < cfg.RAG_SCORE_THRESHOLD
        ]

        if not relevant:
            return RetrievalResult(chunks=[], sources=[], top_score=top_score, used_fallback=True)

        # Санитизация от prompt injection
        chunks = [sanitize_external(doc) for doc, _, _ in relevant]
        sources = [meta.get("source", "kb") + "/" + meta.get("section", "")
                   for _, meta, _ in relevant]

        # Обрезаем до ~1500 токенов суммарно (~6000 символов)
        total_chars = 0
        trimmed_chunks = []
        for chunk in chunks:
            if total_chars + len(chunk) > 6000:
                break
            trimmed_chunks.append(chunk)
            total_chars += len(chunk)

        return RetrievalResult(
            chunks=trimmed_chunks,
            sources=sources[:len(trimmed_chunks)],
            top_score=top_score,
            used_fallback=False,
        )

    def count(self) -> int:
        """Количество чанков в индексе (для healthcheck)."""
        self._load()
        return self._collection.count()


# Singleton — один объект на процесс
retriever = Retriever()
