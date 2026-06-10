"""
Golden-set evaluation harness for retrieval quality.

Populate GOLDEN_SET with (query, expected_source, expected_in_content) tuples
as you ingest manuals. Runs against the real (stubbed) RAG engine and reports
recall@3 — the fraction of queries where the expected source appears in the
top 3 results.

Run:  python -m tests.test_eval
"""

import sys

# Reuse the stubs from test_api
from tests.test_api import _install_stubs
_install_stubs()

import os
import tempfile

GOLDEN_SET: list[tuple[str, str, str]] = [
    # (query, expected_source, substring expected in content)
    # Example (populate once manuals are ingested):
    # ("how to reset the router", "router-manual.pdf", "factory reset"),
]


def run():
    if not GOLDEN_SET:
        print("GOLDEN_SET is empty — add (query, expected_source, expected_in_content) tuples.")
        print("Skipping eval (not a failure).")
        return 0

    _tmp = tempfile.mkdtemp()
    os.environ.setdefault("CONFIG_DIR", _tmp)
    os.environ.setdefault("CHROMA_PERSIST_DIR", _tmp + "/chroma")
    os.environ.setdefault("DOCUMENTS_DIR", _tmp + "/docs")

    from app.services import rag_engine

    hits = 0
    for query, expected_source, expected_content in GOLDEN_SET:
        results = rag_engine.query(query, n_results=3)
        sources = [r["source"] for r in results]
        contents = " ".join(r["content"] for r in results)
        source_hit = expected_source in sources
        content_hit = expected_content.lower() in contents.lower()
        status = "HIT" if source_hit else "MISS"
        print(f"[{status}] q={query!r} expected_src={expected_source} "
              f"got_sources={sources} content_match={content_hit}")
        if source_hit:
            hits += 1

    recall = hits / len(GOLDEN_SET)
    print(f"\nRecall@3: {hits}/{len(GOLDEN_SET)} = {recall:.1%}")
    return 0 if recall >= 0.5 else 1


if __name__ == "__main__":
    sys.exit(run())
