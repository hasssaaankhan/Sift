"""The search engine: an inverted index with BM25 ranking."""

from __future__ import annotations

import gzip
import heapq
import json
import math
import os
import re
import tempfile
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .text import Token, tokenize
from .trie import Trie

__all__ = ["SearchEngine", "Document", "Result"]

FORMAT_NAME = "sift-index"
FORMAT_VERSION = 1

# Either a quoted phrase ("like this") or a bare word, each with an optional "-".
_QUERY_PART = re.compile(r'(-?)"([^"]*)"|(-?)(\S+)')


@dataclass(frozen=True)
class Document:
    id: int
    title: str
    source: str
    text: str
    length: int  # number of words


@dataclass(frozen=True)
class Result:
    doc_id: int
    title: str
    source: str
    score: float
    snippet: str  # matching words are wrapped in **double asterisks**


def _parse(query: str) -> Tuple[List[str], List[List[str]], List[List[str]]]:
    """Split a query into optional words, required phrases and excluded phrases."""
    optional: List[str] = []
    required: List[List[str]] = []
    excluded: List[List[str]] = []
    for match in _QUERY_PART.finditer(query):
        if match.group(2) is not None:  # "quoted phrase"
            negated, raw = match.group(1), match.group(2)
            terms = [t.term for t in tokenize(raw)]
            if terms:
                (excluded if negated else required).append(terms)
        else:  # bare word
            negated, raw = match.group(3), match.group(4)
            terms = [t.term for t in tokenize(raw)]
            if negated:
                excluded.extend([term] for term in terms)
            else:
                optional.extend(terms)
    return optional, required, excluded


class SearchEngine:
    """Add documents, then search them.

    k1 controls how quickly repeated words stop adding score (typical 1.2-2.0).
    b controls how strongly long documents are penalised (0 = never, 1 = fully).
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        if k1 < 0:
            raise ValueError("k1 must not be negative")
        if not 0 <= b <= 1:
            raise ValueError("b must be between 0 and 1")
        self.k1 = k1
        self.b = b
        self._lock = threading.RLock()
        self._docs: Dict[int, Document] = {}
        self._by_source: Dict[str, int] = {}
        # term -> {doc id -> positions of that term in the document}
        self._postings: Dict[str, Dict[int, List[int]]] = {}
        self._surface: Counter = Counter()  # lowercase words as written, for autocomplete
        self._trie: Optional[Trie] = None
        self._total_length = 0
        self._next_id = 0

    # ---- writing ----------------------------------------------------------

    def add(self, text: str, title: str = "", source: str = "") -> int:
        """Index a document and return its id.

        Adding a document whose non-empty `source` already exists replaces the
        old one, so re-indexing a folder never creates duplicates. The title is
        shown in results but not searched; put it in the text if it should be.
        """
        with self._lock:
            if source and source in self._by_source:
                self._remove(self._by_source[source])
            doc_id = self._next_id
            self._next_id += 1
            tokens = tokenize(text)
            positions: Dict[str, List[int]] = {}
            for position, token in enumerate(tokens):
                positions.setdefault(token.term, []).append(position)
            for term, where in positions.items():
                self._postings.setdefault(term, {})[doc_id] = where
            for token in tokens:
                self._surface[text[token.start:token.end].casefold()] += 1
            doc = Document(doc_id, title or source or f"Document {doc_id}", source, text, len(tokens))
            self._docs[doc_id] = doc
            if source:
                self._by_source[source] = doc_id
            self._total_length += doc.length
            self._trie = None
            return doc_id

    def remove(self, doc_id: int) -> bool:
        """Delete a document. Returns False if the id is unknown."""
        with self._lock:
            if doc_id not in self._docs:
                return False
            self._remove(doc_id)
            return True

    def _remove(self, doc_id: int) -> None:
        doc = self._docs.pop(doc_id)
        tokens = tokenize(doc.text)
        for term in {t.term for t in tokens}:
            posting = self._postings[term]
            del posting[doc_id]
            if not posting:
                del self._postings[term]
        for token in tokens:
            word = doc.text[token.start:token.end].casefold()
            self._surface[word] -= 1
            if self._surface[word] <= 0:
                del self._surface[word]
        if doc.source:
            self._by_source.pop(doc.source, None)
        self._total_length -= doc.length
        self._trie = None

    # ---- reading ----------------------------------------------------------

    def get(self, doc_id: int) -> Document:
        with self._lock:
            return self._docs[doc_id]

    def __len__(self) -> int:
        with self._lock:
            return len(self._docs)

    def __contains__(self, doc_id: object) -> bool:
        with self._lock:
            return doc_id in self._docs

    def stats(self) -> Dict[str, float]:
        with self._lock:
            n = len(self._docs)
            return {
                "documents": n,
                "terms": len(self._postings),
                "tokens": self._total_length,
                "avg_doc_length": round(self._total_length / n, 2) if n else 0.0,
            }

    def search(self, query: str, k: int = 10, match_all: bool = False) -> List[Result]:
        """Return the k best documents for a query.

        Query syntax:
          word            documents with more of the words rank higher
          "exact phrase"  the phrase must appear, words side by side
          -word           documents containing the word are removed
          -"a phrase"     documents containing the phrase are removed
        With match_all=True every bare word is required, too.
        """
        if k < 1:
            raise ValueError("k must be at least 1")
        optional, required, excluded = _parse(query)
        scoring_terms = list(dict.fromkeys(optional + [t for phrase in required for t in phrase]))
        if not scoring_terms:
            return []
        with self._lock:
            candidates: Set[int] = set()
            for term in scoring_terms:
                candidates.update(self._postings.get(term, ()))
            if match_all:
                for term in set(optional):
                    candidates.intersection_update(self._postings.get(term, ()))
            for phrase in required:
                candidates = {d for d in candidates if self._has_phrase(d, phrase)}
            for phrase in excluded:
                candidates = {d for d in candidates if not self._has_phrase(d, phrase)}
            if not candidates:
                return []

            scores = self._score(scoring_terms, candidates)
            top = heapq.nlargest(k, scores.items(), key=lambda item: (item[1], -item[0]))
            wanted = set(scoring_terms)
            return [
                Result(
                    doc_id,
                    self._docs[doc_id].title,
                    self._docs[doc_id].source,
                    score,
                    self._snippet(self._docs[doc_id], wanted, required),
                )
                for doc_id, score in top
            ]

    def suggest(self, prefix: str, k: int = 5) -> List[str]:
        """Autocomplete: the most frequent indexed words that start with prefix."""
        if k < 1:
            raise ValueError("k must be at least 1")
        with self._lock:
            if self._trie is None:
                trie = Trie()
                for word, count in self._surface.items():
                    trie.insert(word, count)
                self._trie = trie
            return self._trie.suggest(prefix.strip().casefold(), k)

    # ---- ranking ----------------------------------------------------------

    def _score(self, terms: List[str], candidates: Set[int]) -> Dict[int, float]:
        """BM25: rare words count more, repeats count less, long documents count less."""
        n = len(self._docs)
        avg_length = self._total_length / n or 1.0
        scores: Dict[int, float] = dict.fromkeys(candidates, 0.0)
        for term in terms:
            posting = self._postings.get(term)
            if not posting:
                continue
            df = len(posting)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for doc_id, positions in posting.items():
                if doc_id not in scores:
                    continue
                tf = len(positions)
                length = self._docs[doc_id].length
                norm = self.k1 * (1 - self.b + self.b * length / avg_length)
                scores[doc_id] += idf * tf * (self.k1 + 1) / (tf + norm)
        return scores

    def _has_phrase(self, doc_id: int, terms: List[str]) -> bool:
        """True if the terms appear in this document directly one after another."""
        lists = []
        for term in terms:
            positions = self._postings.get(term, {}).get(doc_id)
            if not positions:
                return False
            lists.append(positions)
        if len(lists) == 1:
            return True
        later = [set(p) for p in lists[1:]]
        return any(
            all(start + offset + 1 in later[offset] for offset in range(len(later)))
            for start in lists[0]
        )

    # ---- snippets ---------------------------------------------------------

    def _snippet(self, doc: Document, wanted: Set[str], phrases: List[List[str]]) -> str:
        tokens = tokenize(doc.text)
        hit = self._first_hit(tokens, wanted, phrases)
        if hit is None:
            return " ".join(doc.text.split())[:200]
        low = max(0, hit - 8)
        high = min(len(tokens) - 1, low + 29)
        pieces: List[str] = []
        cursor = tokens[low].start
        for token in tokens[low:high + 1]:
            pieces.append(doc.text[cursor:token.start])
            word = doc.text[token.start:token.end]
            pieces.append(f"**{word}**" if token.term in wanted else word)
            cursor = token.end
        body = " ".join("".join(pieces).split())
        return ("… " if low > 0 else "") + body + (" …" if high < len(tokens) - 1 else "")

    @staticmethod
    def _first_hit(tokens: List[Token], wanted: Set[str], phrases: List[List[str]]) -> Optional[int]:
        terms = [t.term for t in tokens]
        for phrase in phrases:
            size = len(phrase)
            for i in range(len(terms) - size + 1):
                if terms[i:i + size] == phrase:
                    return i
        for i, term in enumerate(terms):
            if term in wanted:
                return i
        return None

    # ---- saving and loading ----------------------------------------------

    def save(self, path: str) -> None:
        """Write the whole index to a JSON file (gzip-compressed if the name ends in .gz)."""
        with self._lock:
            data = {
                "format": FORMAT_NAME,
                "version": FORMAT_VERSION,
                "k1": self.k1,
                "b": self.b,
                "next_id": self._next_id,
                "docs": [
                    {"id": d.id, "title": d.title, "source": d.source, "text": d.text, "length": d.length}
                    for d in self._docs.values()
                ],
                "postings": {
                    term: {str(doc_id): where for doc_id, where in posting.items()}
                    for term, posting in self._postings.items()
                },
                "surface": dict(self._surface),
            }
        payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
        if str(path).endswith(".gz"):
            payload = gzip.compress(payload)
        # Write to a temporary file first so a crash never leaves a half-written index.
        directory = os.path.dirname(os.path.abspath(path))
        fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".sift-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            os.replace(temp_path, path)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str) -> "SearchEngine":
        """Read an index written by save(). Raises ValueError if the file is not one."""
        with open(path, "rb") as handle:
            raw = handle.read()
        try:
            if str(path).endswith(".gz"):
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8"))
            if data["format"] != FORMAT_NAME or data["version"] != FORMAT_VERSION:
                raise ValueError("unsupported format or version")
            engine = cls(k1=float(data["k1"]), b=float(data["b"]))
            for item in data["docs"]:
                doc = Document(int(item["id"]), str(item["title"]), str(item["source"]),
                               str(item["text"]), int(item["length"]))
                engine._docs[doc.id] = doc
                engine._total_length += doc.length
                if doc.source:
                    engine._by_source[doc.source] = doc.id
            for term, posting in data["postings"].items():
                engine._postings[str(term)] = {
                    int(doc_id): [int(p) for p in where] for doc_id, where in posting.items()
                }
            for posting in engine._postings.values():
                if not posting.keys() <= engine._docs.keys():
                    raise ValueError("postings refer to unknown documents")
            engine._surface = Counter({str(w): int(c) for w, c in data["surface"].items()})
            engine._next_id = int(data["next_id"])
        except (OSError, EOFError, KeyError, TypeError, ValueError, AttributeError) as error:
            raise ValueError(f"{path} is not a valid Sift index ({error})") from error
        return engine
