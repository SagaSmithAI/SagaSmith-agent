"""ChromaDB-backed dense vector storage and retrieval for D&D content."""

from nanobot.dnd.vector.client import VectorStore
from nanobot.dnd.vector.search import chroma_dense_search

__all__ = ["VectorStore", "chroma_dense_search"]
