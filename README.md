# Sift

**A small, fast search engine you can read in an afternoon.**

Sift turns a pile of text files into something you can search: ranked results, exact phrases, exclusions, highlighted snippets, and autocomplete. It is written in pure Python with no dependencies, so it runs anywhere Python runs.

```
$ sift demo

== exact phrase: "binary search"
 1. Binary Search  (score 3.23)
    **Binary** **search** finds an item in a sorted array by repeatedly halving the **search** range. …
    demo/binary-search.txt

 2. Binary Search Trees  (score 2.43)
    A **binary** **search** tree keeps keys in order: smaller keys go left and larger keys go right. …
    demo/binary-search-trees.txt
```

## Why Sift exists

Most search tools are either too small to be useful (`grep` has no ranking) or too big to understand (a full search server is a project of its own). Sift sits in the middle. It uses the same core ideas as real search engines, an inverted index and BM25 ranking, in a few hundred lines you can read, change, and trust.

Use it to:

- Add search to a notes folder, a docs site, a personal knowledge base, or a small app.
- Learn how search engines work, with working code and tests for every idea.
- Prototype search before you commit to heavier infrastructure.

## Features

- **Ranked results.** BM25, the scoring method behind most keyword search, so the best matches come first.
- **Exact phrases.** `"binary search"` only matches the words side by side.
- **Exclusions.** `-tree` removes documents you do not want.
- **Word forms.** `searching`, `searches`, and `searched` all find `search`.
- **Highlighted snippets.** Each result shows where your words appear.
- **Autocomplete.** Type `bi`, get `binary`, ordered by how often each word appears.
- **Saved indexes.** Build once, search instantly. Files are plain JSON, optionally gzipped.
- **Command line and library.** Use `sift` in a terminal or `import sift` in code.
- **Zero dependencies.** Python 3.9 or newer, nothing to install besides Sift itself.

## Quick start

Download or clone this project, open a terminal in its folder, and run:

```
pip install .
sift demo
```

That prints a guided tour over ten built-in documents about data structures. To explore on your own:

```
sift shell --demo
```

### Search your own files

```
sift index ~/notes                      # builds sift-index.json.gz
sift search "binary search" -k 5        # ranked results
sift search '"exact phrase" -draft'     # phrase plus exclusion
sift suggest bin                        # autocomplete
sift shell                              # interactive prompt
```

Sift indexes `.txt`, `.md`, and `.rst` files by default. Use `--ext py html` to choose others. It skips hidden folders and files over 5 MB.

You can also run it without installing: `python -m sift demo`.

## Query syntax

| You type | What it does |
|---|---|
| `binary search` | Finds documents with either word. Documents with more of the words rank higher. |
| `"binary search"` | The exact phrase must appear, words side by side. |
| `-tree` | Removes documents containing the word. |
| `-"binary tree"` | Removes documents containing the phrase. |
| `--all` (command line) or `match_all=True` (library) | Every word must appear. |

Capital letters and punctuation never matter. A query with only exclusions returns nothing, because there is nothing to rank.

## Use it as a library

```python
from sift import SearchEngine

engine = SearchEngine()
engine.add("Binary search halves a sorted array at every step.", title="Binary Search")
engine.add("A hash table maps keys to values in O(1) time on average.", title="Hash Tables")
engine.add("A binary search tree keeps its keys in sorted order.", title="Binary Search Trees")

for hit in engine.search('"binary search" -tree'):
    print(hit.title, round(hit.score, 2))
    print(hit.snippet)
# Binary Search 1.01
# **Binary** **search** halves a sorted array at every step

engine.suggest("s")                  # ['search', 'sorted', 'step']

engine.save("my-index.json.gz")      # a .gz name means compressed
engine = SearchEngine.load("my-index.json.gz")
```

| Call | What it does |
|---|---|
| `SearchEngine(k1=1.5, b=0.75)` | Create an engine. The two numbers tune ranking (see below). |
| `add(text, title="", source="")` | Index a document and return its id. Reusing a `source` replaces the old document. |
| `remove(doc_id)` | Delete a document. Returns `False` if the id is unknown. |
| `search(query, k=10, match_all=False)` | Returns up to `k` `Result` objects: `doc_id`, `title`, `source`, `score`, `snippet`. |
| `suggest(prefix, k=5)` | The most frequent indexed words that start with `prefix`. |
| `get(doc_id)` | The stored `Document`. |
| `stats()` | Document, term, and word counts. |
| `save(path)` / `SearchEngine.load(path)` | Write and read an index. |

Titles are shown in results but not searched. If you want the title to be searchable, include it in the text.

The engine is safe to share between threads inside one process.

## How it works

```
 add a document                               run a query
 ──────────────                               ───────────
 text                                         "binary search" -tree
   │ split into words, lowercase, stem          │ same steps as documents
   ▼                                            ▼
 tokens with positions                        terms + phrases + exclusions
   │                                            │ look up each term
   ▼                                            ▼
 inverted index                               candidate documents
 term ─► { doc id ─► [positions] }              │ check phrases, drop exclusions
                                                ▼
                                              BM25 score for each candidate
                                                │ keep the best k with a heap
                                                ▼
                                              results with highlighted snippets
```

### The data structures

| Job | Structure | Cost |
|---|---|---|
| Find documents containing a word | Hash map from word to postings (the inverted index) | O(1) per word |
| Check that words are side by side | Positions stored in each posting, compared with a set | O(occurrences) |
| Pick the best k results | Heap (`heapq.nlargest`) | O(c log k) for c candidates |
| Autocomplete | Trie (prefix tree) | O(prefix + matching subtree) |
| Remove a document cleanly | Re-read its words, delete its postings | O(words in the document) |
| Replace on re-index | Hash map from source name to document id | O(1) |
| Save and load | JSON, gzip optional, atomic write | O(index size) |

### How results are ranked

Sift scores each candidate with BM25. For every query word, the score adds up three ideas:

1. **Rare words matter more.** "the" appears everywhere and says nothing. "trie" appears in few documents and says a lot. This is called IDF (inverse document frequency).
2. **Repeating a word helps, but less each time.** Ten mentions are not ten times better than one.
3. **Long documents are penalized.** A short document that mentions a word is more likely to be about it than a long one that mentions it in passing.

The formula for one word in one document is:

```
score = idf × tf × (k1 + 1) / (tf + k1 × (1 − b + b × length / average length))
idf   = ln(1 + (N − df + 0.5) / (df + 0.5))
```

where `tf` is how often the word appears in the document, `df` is how many documents contain it, and `N` is the total number of documents. The defaults `k1=1.5` and `b=0.75` are the standard choices. Raise `b` to punish long documents more; lower it to ignore length.

Ties are broken by document id, so the same query always returns the same order.

### Design choices

- **No stop-word list.** BM25 already makes common words nearly worthless, and keeping them makes phrase search exact.
- **Stemming is light on purpose.** It is a small suffix stripper, not the full Porter algorithm. It can produce stems that are not real words (`cache` becomes `cach`), which is harmless because documents and queries are stemmed the same way.
- **Candidates first, scores second.** Phrases and exclusions are checked on the candidate set before any scoring happens.
- **Index files are JSON, not pickle.** Loading an index never runs code.
- **Safe saving.** The index is written to a temporary file and moved into place, so a crash never leaves a half-written index.
- **Snippets are built at query time** from the top results only, so they cost nothing for documents you never display.

## Command reference

| Command | What it does |
|---|---|
| `sift index PATH [-o FILE] [--ext ...] [--max-bytes N]` | Index a folder or one file and save it. |
| `sift search QUERY... [-i FILE] [-k N] [--all]` | Search an index. |
| `sift suggest PREFIX [-i FILE] [-k N]` | Autocomplete a word. |
| `sift stats [-i FILE]` | Show document, term, and word counts. |
| `sift shell [-i FILE]` | Interactive prompt. Commands: `:all`, `:suggest`, `:stats`, `:help`, `:quit`. |
| `sift demo` | A short guided tour. |
| `sift bench [--docs N] [--words N] [--vocab N] [--queries N]` | Measure speed on synthetic text. |

The default index file is `sift-index.json.gz`. Add `--demo` to `search`, `suggest`, `stats`, or `shell` to use the built-in documents instead of a file. Errors print one clear line to stderr and exit with code 2.

## Performance

Measured with `sift bench` on one cloud sandbox with Python 3.12. The text is synthetic and Zipf-shaped, so a few words appear in almost every document, which makes queries harder than typical real text. Your numbers will differ.

| Corpus | Indexing | Median query | 95th percentile |
|---|---|---|---|
| 10,000 documents, 1 million words | 2.4 to 3.4 s (about 3,000 documents/s) | 5.6 ms | 14.9 ms |
| 50,000 documents, 5 million words | 15.8 s | 44 ms | 91 ms |

Queries are top 10 results from random two or three word searches. Run the same test yourself with `sift bench` and `sift bench --docs 50000`.

Query time grows with the length of the posting lists of the query's words, so it grows roughly in step with corpus size. That is the honest cost of a simple, exact design.

## Limits

- **Everything lives in memory.** An index is fully loaded when you open it. It suits thousands to low hundreds of thousands of documents, not billions.
- **Keyword search, not meaning search.** "automobile" will not find "car". There are no synonyms and no typo correction.
- **Light English stemming.** Other languages are tokenized correctly (accents and non-Latin letters work), but are not stemmed.
- **No skip lists or early termination yet.** Very common query words are scored against every document that contains them.
- **One writer at a time.** The engine is thread-safe within a process, but two processes should not write the same index file at once.
- **The index stores the full text.** That makes snippets possible and makes the index file larger than the source files. Treat it as sensitive if your documents are. On Linux and macOS the file is created readable only by you.
- **Do not load indexes from sources you do not trust.** Loading cannot run code, but a crafted file can give wrong results or use a lot of memory.

## Project layout

```
sift/
  text.py     words, stemming, positions
  trie.py     autocomplete tree
  index.py    inverted index, BM25, phrases, snippets, save and load
  cli.py      the sift command
tests/
  test_sift.py     42 tests
pyproject.toml     package and the sift command
.github/workflows/tests.yml   tests on Python 3.9 to 3.13
```

The engine itself (`text.py`, `trie.py`, `index.py`) is about 470 lines.

## Development

```
python -m unittest -v
```

The 42 tests cover word forms, ranking behavior (rare words, repetition, document length, ties), phrases, exclusions, snippets, autocomplete, removal and replacement, saving and loading (plain and gzip, plus corrupt files), and every command line path. Ranking tests are written so that breaking the scoring formula makes them fail.

## Contributing

Issues and pull requests are welcome. Please keep the project small and dependency-free, and add a test with every change. Run `python -m unittest` before you open a pull request.

## License

MIT. See the `LICENSE` file.
