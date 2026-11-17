"""A prefix tree for autocomplete."""

from __future__ import annotations

import heapq
from typing import Dict, List


class _Node:
    __slots__ = ("children", "count")

    def __init__(self) -> None:
        self.children: Dict[str, "_Node"] = {}
        self.count = 0


class Trie:
    """Words that share a prefix share a path, so one walk finds them all."""

    def __init__(self) -> None:
        self._root = _Node()

    def insert(self, word: str, count: int = 1) -> None:
        node = self._root
        for char in word:
            child = node.children.get(char)
            if child is None:
                child = node.children[char] = _Node()
            node = child
        node.count += count

    def suggest(self, prefix: str, k: int = 5) -> List[str]:
        """The k most frequent words starting with prefix (ties: alphabetical).

        Cost is O(len(prefix) + size of the subtree under the prefix).
        """
        node = self._root
        for char in prefix:
            node = node.children.get(char)
            if node is None:
                return []
        found = []
        stack = [(node, prefix)]
        while stack:
            current, word = stack.pop()
            if current.count:
                found.append((current.count, word))
            for char, child in current.children.items():
                stack.append((child, word + char))
        best = heapq.nsmallest(k, found, key=lambda item: (-item[0], item[1]))
        return [word for _, word in best]
