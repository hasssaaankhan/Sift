"""Sift: a small, fast, dependency-free search engine."""

__version__ = "1.0.0"

from .index import Document, Result, SearchEngine  # noqa: E402

__all__ = ["SearchEngine", "Document", "Result", "__version__"]
