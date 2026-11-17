"""Command line interface: sift index | search | suggest | stats | shell | demo | bench."""

from __future__ import annotations

import argparse
import itertools
import os
import random
import re
import sys
import time
from typing import Iterator, List, Optional, Sequence

from . import __version__
from .index import Result, SearchEngine

DEFAULT_INDEX = "sift-index.json.gz"
DEFAULT_EXTENSIONS = (".txt", ".md", ".rst")
DEFAULT_MAX_BYTES = 5_000_000
_SKIP_DIRS = {"node_modules", "__pycache__"}
_BOLD = re.compile(r"\*\*(.+?)\*\*")

DEMO_DOCS = [
    ("Binary Search", "binary-search",
     "Binary search finds an item in a sorted array by repeatedly halving the search range. "
     "Each step compares the middle element with the target, so the running time is O(log n). "
     "Binary search needs random access and sorted data."),
    ("Hash Tables", "hash-tables",
     "A hash table maps keys to values using a hash function that turns each key into an array index. "
     "Lookups, inserts and deletes take O(1) time on average. "
     "Collisions are handled by chaining or open addressing."),
    ("Binary Search Trees", "binary-search-trees",
     "A binary search tree keeps keys in order: smaller keys go left and larger keys go right. "
     "Searching, inserting and deleting take O(log n) time when the tree is balanced, "
     "but O(n) when it degenerates into a list."),
    ("Graph Traversal", "graph-traversal",
     "Breadth-first search explores a graph level by level using a queue, and finds the shortest path "
     "in an unweighted graph. Depth-first search follows one branch as far as it can using a stack or recursion."),
    ("Heaps and Priority Queues", "heaps",
     "A heap is a tree stored in an array where each parent is smaller than its children. "
     "It gives fast access to the smallest item, and push and pop take O(log n) time. "
     "Priority queues, Dijkstra's algorithm and top-k search all use heaps."),
    ("Tries", "tries",
     "A trie stores words character by character along tree paths, so all words that share a prefix share a path. "
     "Tries power autocomplete because finding every word with a given prefix only needs a walk down the prefix."),
    ("Inverted Index", "inverted-index",
     "An inverted index maps each word to the list of documents that contain it. "
     "Search engines use it to answer a query without scanning every document. "
     "Each posting can also store word positions for phrase search."),
    ("Dynamic Programming", "dynamic-programming",
     "Dynamic programming solves a problem by saving the answers to overlapping subproblems. "
     "Memoization caches results top down, while tabulation fills a table bottom up. "
     "Fibonacci numbers and the knapsack problem are classic examples."),
    ("Sorting Algorithms", "sorting",
     "Merge sort splits the array in half, sorts each half, and merges them in O(n log n) time. "
     "Quicksort picks a pivot and partitions the array, and is fast in practice. "
     "Heap sort uses a heap and needs no extra memory."),
    ("LRU Cache", "lru-cache",
     "An LRU cache evicts the least recently used item when it is full. "
     "A hash map plus a doubly linked list gives O(1) get and put. "
     "Caches save time by avoiding repeated work."),
]

SHELL_HELP = """Type a query to search. Syntax:
  word            documents with more of the words rank higher
  "a phrase"      the exact phrase must appear
  -word           exclude documents containing the word
Commands:
  :all <query>      every word must appear
  :suggest <start>  autocomplete a word
  :stats            index size
  :quit             leave"""


class _CliError(Exception):
    pass


# ---- helpers --------------------------------------------------------------

def build_demo() -> SearchEngine:
    engine = SearchEngine()
    for title, slug, text in DEMO_DOCS:
        engine.add(text, title=title, source=f"demo/{slug}.txt")
    return engine


def find_files(root: str, extensions: Sequence[str], max_bytes: int) -> Iterator[str]:
    """Yield matching files under root in a stable order, skipping hidden folders."""
    if os.path.isfile(root):
        yield root
        return
    wanted = tuple(extensions)
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS)
        for name in sorted(files):
            if name.startswith(".") or not name.lower().endswith(wanted):
                continue
            path = os.path.join(folder, name)
            try:
                if os.path.getsize(path) <= max_bytes:
                    yield path
            except OSError:
                continue


def guess_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:80]
    return fallback


def format_result(rank: int, result: Result, color: bool) -> str:
    title = f"\033[1m{result.title}\033[0m" if color else result.title
    snippet = _BOLD.sub(lambda m: f"\033[1m{m.group(1)}\033[0m", result.snippet) if color else result.snippet
    lines = [f"{rank:>2}. {title}  (score {result.score:.2f})", f"    {snippet}"]
    if result.source:
        lines.append(f"    {result.source}")
    return "\n".join(lines)


def _run_search(engine: SearchEngine, query: str, k: int, match_all: bool, color: bool) -> None:
    results = engine.search(query, k=k, match_all=match_all)
    if not results:
        print("No results.")
        return
    for rank, result in enumerate(results, 1):
        print(format_result(rank, result, color))
        print()


def _print_stats(engine: SearchEngine) -> None:
    for key, value in engine.stats().items():
        print(f"{key.replace('_', ' '):<16}{value:>12,}" if isinstance(value, int)
              else f"{key.replace('_', ' '):<16}{value:>12,.2f}")


def _load(args: argparse.Namespace) -> SearchEngine:
    if args.demo:
        return build_demo()
    try:
        return SearchEngine.load(args.index)
    except FileNotFoundError:
        raise _CliError(f"index file not found: {args.index} "
                        f"(build one with 'sift index <folder>', or add --demo)")
    except ValueError as error:
        raise _CliError(str(error))


# ---- commands -------------------------------------------------------------

def cmd_index(args: argparse.Namespace) -> int:
    if not os.path.exists(args.path):
        raise _CliError(f"path not found: {args.path}")
    extensions = [e.lower() if e.startswith(".") else "." + e.lower() for e in args.ext]
    engine = SearchEngine()
    started = time.perf_counter()
    count = 0
    for path in find_files(args.path, extensions, args.max_bytes):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as error:
            print(f"skipped {path}: {error}", file=sys.stderr)
            continue
        if os.path.isfile(args.path):
            source = os.path.basename(path)
        else:
            source = os.path.relpath(path, args.path).replace(os.sep, "/")
        engine.add(text, title=guess_title(text, source), source=source)
        count += 1
    if count == 0:
        raise _CliError(f"no files with extensions {', '.join(extensions)} found in {args.path}")
    engine.save(args.output)
    stats = engine.stats()
    print(f"Indexed {count:,} documents ({stats['tokens']:,} words, {stats['terms']:,} unique terms) "
          f"in {time.perf_counter() - started:.2f}s -> {args.output}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    engine = _load(args)
    _run_search(engine, " ".join(args.query), args.k, args.all, sys.stdout.isatty())
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    words = _load(args).suggest(args.prefix, args.k)
    print("\n".join(words) if words else "No suggestions.")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    _print_stats(_load(args))
    return 0


def cmd_shell(args: argparse.Namespace) -> int:
    engine = _load(args)
    color = sys.stdout.isatty()
    print(f"Sift shell: {len(engine):,} documents. Type :help for syntax, :quit to leave.")
    while True:
        try:
            line = input("sift> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in (":quit", ":q", ":exit"):
            break
        if line == ":help":
            print(SHELL_HELP)
        elif line == ":stats":
            _print_stats(engine)
        elif line.startswith(":suggest"):
            words = engine.suggest(line[len(":suggest"):].strip())
            print("\n".join(words) if words else "No suggestions.")
        elif line.startswith(":all "):
            _run_search(engine, line[5:], 10, True, color)
        else:
            _run_search(engine, line, 10, False, color)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    engine = build_demo()
    color = sys.stdout.isatty()
    showcase = [
        ("ranked search", "binary search", False),
        ("exact phrase", '"binary search"', False),
        ("exclusion", "search -tree", False),
        ("stemming ('caching' finds 'cache', 'sorts' finds 'sorting')", "caching sorts", False),
    ]
    print(f"Sift demo: {len(engine)} documents about data structures\n")
    for label, query, match_all in showcase:
        print(f"== {label}: {query}")
        _run_search(engine, query, 3, match_all, color)
    print(f"== autocomplete: 'bi' -> {', '.join(engine.suggest('bi'))}")
    print("\nTry it yourself:  python -m sift shell --demo")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    rng = random.Random(2026)
    vocab = [f"w{i}" for i in range(args.vocab)]
    cumulative = list(itertools.accumulate(1.0 / (i + 1) for i in range(args.vocab)))  # Zipf-like
    engine = SearchEngine()
    started = time.perf_counter()
    for _ in range(args.docs):
        engine.add(" ".join(rng.choices(vocab, cum_weights=cumulative, k=args.words)))
    build = time.perf_counter() - started
    queries = [" ".join(rng.choices(vocab, cum_weights=cumulative, k=rng.randint(2, 3)))
               for _ in range(args.queries)]
    times = []
    for query in queries:
        begin = time.perf_counter()
        engine.search(query, k=10)
        times.append(time.perf_counter() - begin)
    times.sort()
    stats = engine.stats()
    print(f"Documents      {stats['documents']:,}   ({stats['tokens']:,} words, {stats['terms']:,} unique terms)")
    print(f"Indexing       {build:.2f}s   ({args.docs / build:,.0f} documents/second)")
    print(f"Queries        {len(times)} random 2-3 word queries, top 10")
    print(f"Latency        median {times[len(times) // 2] * 1000:.2f} ms   "
          f"95th percentile {times[min(len(times) - 1, int(len(times) * 0.95))] * 1000:.2f} ms   "
          f"slowest {times[-1] * 1000:.2f} ms")
    return 0


# ---- wiring ---------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sift", description="A small, fast, dependency-free search engine.")
    parser.add_argument("--version", action="version", version=f"sift {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    source = argparse.ArgumentParser(add_help=False)
    source.add_argument("-i", "--index", default=DEFAULT_INDEX, help=f"index file (default: {DEFAULT_INDEX})")
    source.add_argument("--demo", action="store_true", help="use the built-in demo documents instead of an index file")

    p = sub.add_parser("index", help="index a folder (or one file) into an index file")
    p.add_argument("path", help="folder or file to index")
    p.add_argument("-o", "--output", default=DEFAULT_INDEX, help=f"where to save the index (default: {DEFAULT_INDEX})")
    p.add_argument("--ext", nargs="+", default=list(DEFAULT_EXTENSIONS), help="file extensions to index")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="skip files larger than this")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", parents=[source], help="search an index")
    p.add_argument("query", nargs="+", help='words, "phrases" and -exclusions')
    p.add_argument("-k", type=int, default=10, help="number of results (default: 10)")
    p.add_argument("--all", action="store_true", help="require every word")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("suggest", parents=[source], help="autocomplete a word")
    p.add_argument("prefix")
    p.add_argument("-k", type=int, default=5)
    p.set_defaults(func=cmd_suggest)

    sub.add_parser("stats", parents=[source], help="show index size").set_defaults(func=cmd_stats)
    sub.add_parser("shell", parents=[source], help="interactive search prompt").set_defaults(func=cmd_shell)
    sub.add_parser("demo", help="run a short guided demo").set_defaults(func=cmd_demo)

    p = sub.add_parser("bench", help="measure speed on a synthetic corpus")
    p.add_argument("--docs", type=int, default=10000)
    p.add_argument("--words", type=int, default=100, help="words per document")
    p.add_argument("--vocab", type=int, default=20000, help="distinct words")
    p.add_argument("--queries", type=int, default=200)
    p.set_defaults(func=cmd_bench)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except _CliError as error:
        print(f"sift: {error}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        # The reader (for example `head`) closed the pipe early. That is not an error.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        return 0
