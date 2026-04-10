"""
Сборка ChromaDB индекса из Markdown файлов Travel KB.
Запускать один раз после download_models.py.

Использование:
    python src/scripts/build_index.py
    python src/scripts/build_index.py --kb-dir data/travel_kb/cities --output data/travel_kb/index
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path
from config import cfg


def split_by_sections(text: str, source: str) -> list[dict]:
    """Разбивает Markdown на чанки по H2-заголовкам."""
    chunks = []
    sections = re.split(r"\n## ", text)
    for i, section in enumerate(sections):
        if i == 0:
            # Первая секция — H1 и общая информация
            content = section.strip()
            title = "General"
        else:
            lines = section.split("\n", 1)
            title = lines[0].strip()
            content = (lines[1].strip() if len(lines) > 1 else "")

        if len(content) > 50:  # Пропускаем слишком короткие
            chunks.append({
                "id": f"{source}_{i}_{title[:20].replace(' ', '_')}",
                "content": f"## {title}\n\n{content}" if i > 0 else content,
                "metadata": {"source": source, "section": title},
            })
    return chunks


def build(kb_dir: str, output_dir: str) -> None:
    import chromadb
    from sentence_transformers import SentenceTransformer

    kb_path = Path(kb_dir)
    md_files = list(kb_path.glob("*.md"))
    if not md_files:
        print(f"ERROR: No .md files found in {kb_dir}")
        sys.exit(1)

    print(f"Found {len(md_files)} KB files: {[f.stem for f in md_files]}")
    print(f"Loading embedding model: {cfg.EMBEDDING_MODEL}")
    model = SentenceTransformer(cfg.EMBEDDING_MODEL)

    print(f"Initializing ChromaDB at: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)
    client = chromadb.PersistentClient(path=output_dir)

    # Пересоздаём коллекцию при каждом build
    try:
        client.delete_collection("travel_kb")
    except Exception:
        pass
    collection = client.create_collection(
        name="travel_kb",
        metadata={"hnsw:space": "cosine"},
    )

    all_chunks = []
    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8")
        chunks = split_by_sections(text, source=md_file.stem)
        all_chunks.extend(chunks)
        print(f"  {md_file.name}: {len(chunks)} chunks")

    print(f"\nEmbedding {len(all_chunks)} chunks...")
    texts = [c["content"] for c in all_chunks]
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=True)

    collection.add(
        ids=[c["id"] for c in all_chunks],
        documents=texts,
        embeddings=embeddings.tolist(),
        metadatas=[c["metadata"] for c in all_chunks],
    )

    print(f"\nIndex built: {collection.count()} chunks in ChromaDB")
    print(f"Location: {output_dir}")
    print("Done. Run 'python main.py' to start.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-dir", default=cfg.KB_DIR)
    parser.add_argument("--output", default=cfg.KB_INDEX_DIR)
    args = parser.parse_args()
    build(args.kb_dir, args.output)
