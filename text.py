"""Turn raw text into searchable terms.

The same code runs on documents and on queries, so a query word and a
document word always meet in the same form.
"""

from __future__ import annotations

import re
from typing import List, NamedTuple

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


class Token(NamedTuple):
    term: str   # normalised form that goes into the index
    start: int  # character offset where the word starts in the original text
    end: int    # character offset where it ends


def stem(word: str) -> str:
    """Strip common English endings so "searches" and "searching" meet at "search".

    This is a small suffix stripper, not the full Porter algorithm. It can
    produce stems that are not real words ("cache" becomes "cach"); that is
    harmless because documents and queries are stemmed the same way.
    """
    if len(word) <= 3 or not word.isalpha():
        return word
    if word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith(("xes", "zes", "ches", "shes")) and len(word) > 4:
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            if len(word) >= 3 and word[-1] == word[-2] and word[-1] not in "lsz":
                word = word[:-1]  # "running" -> "runn" -> "run"
            break
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]  # "cache" and "caches" both end at "cach"
    return word


def tokenize(text: str) -> List[Token]:
    """Split text into lowercase, stemmed words, remembering where each one sits."""
    return [
        Token(stem(m.group().casefold()), m.start(), m.end())
        for m in _WORD.finditer(text)
    ]
