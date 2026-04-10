"""
Предварительная загрузка embedding модели.
Запускать ОДИН РАЗ перед демо при наличии интернета.
Модель кэшируется локально (~120MB) и используется offline.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import cfg


def download():
    print(f"Downloading embedding model: {cfg.EMBEDDING_MODEL}")
    print("This may take a few minutes (~120MB)...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(cfg.EMBEDDING_MODEL)
    # Проверяем что модель работает
    test = model.encode(["test sentence"], convert_to_numpy=True)
    print(f"OK — model loaded, embedding dim: {test.shape[1]}")
    print("Model cached locally. Ready for offline use.")


if __name__ == "__main__":
    download()
