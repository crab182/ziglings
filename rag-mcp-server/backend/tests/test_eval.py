"""
Self-contained retrieval eval harness.

Installs a FUNCTIONAL in-memory vector store (unlike test_api's no-op stub) so
BM25 + RRF ranking is actually exercised, ingests keyword-distinct fixtures,
and asserts the expected source ranks #1 for each golden query. The stub
embedder returns identical vectors, so ranking is driven by the BM25 half of
hybrid search — which is exactly the part worth guarding against regressions.

Run:  python -m tests.test_eval   (from backend/)
"""

import os
import sys
import tempfile
import types


def _install_functional_stubs():
    # Functional chromadb: really stores and returns documents.
    chromadb = types.ModuleType("chromadb")
    chromadb_config = types.ModuleType("chromadb.config")

    class _Col:
        def __init__(self, name):
            self.name = name
            self._ids, self._docs, self._metas = [], [], []

        def count(self):
            return len(self._ids)

        def upsert(self, ids, documents, embeddings=None, metadatas=None):
            for i, did in enumerate(ids):
                if did in self._ids:
                    idx = self._ids.index(did)
                    self._docs[idx] = documents[i]
                    self._metas[idx] = (metadatas or [{}] * len(ids))[i]
                else:
                    self._ids.append(did)
                    self._docs.append(documents[i])
                    self._metas.append((metadatas or [{}] * len(ids))[i])

        def query(self, query_embeddings=None, n_results=5, include=None):
            n = min(n_results, len(self._ids))
            return {
                "ids": [self._ids[:n]],
                "documents": [self._docs[:n]],
                "metadatas": [self._metas[:n]],
                "distances": [[0.0] * n],  # identical stub embeddings
            }

        def get(self, ids=None, where=None, include=None):
            if ids is not None:
                out_i, out_d, out_m = [], [], []
                for did in ids:
                    if did in self._ids:
                        idx = self._ids.index(did)
                        out_i.append(did); out_d.append(self._docs[idx]); out_m.append(self._metas[idx])
                return {"ids": out_i, "documents": out_d, "metadatas": out_m}
            if where:
                key, val = next(iter(where.items()))
                sel = [i for i, m in enumerate(self._metas) if m.get(key) == val]
            else:
                sel = list(range(len(self._ids)))
            return {
                "ids": [self._ids[i] for i in sel],
                "documents": [self._docs[i] for i in sel],
                "metadatas": [self._metas[i] for i in sel],
            }

        def delete(self, ids=None):
            if not ids:
                return
            keep = [i for i, did in enumerate(self._ids) if did not in ids]
            self._ids = [self._ids[i] for i in keep]
            self._docs = [self._docs[i] for i in keep]
            self._metas = [self._metas[i] for i in keep]

    class _Client:
        def __init__(self): self._cols = {}
        def get_or_create_collection(self, name, **kw): return self._cols.setdefault(name, _Col(name))
        def list_collections(self): return list(self._cols.keys())
        def get_collection(self, name): return self._cols[name]
        def delete_collection(self, name): self._cols.pop(name, None)

    chromadb.PersistentClient = lambda **kw: _Client._singleton
    _Client._singleton = _Client()
    chromadb.ClientAPI = _Client

    class _Settings:
        def __init__(self, **kw): pass
    chromadb_config.Settings = _Settings
    chromadb.config = chromadb_config

    st = types.ModuleType("sentence_transformers")

    class _Nd(list):
        def tolist(self): return list(self)

    class SentenceTransformer:
        def __init__(self, *a, **kw): pass
        def get_sentence_embedding_dimension(self): return 384
        def encode(self, texts, **kw):
            items = texts if isinstance(texts, list) else [texts]
            return _Nd([[0.1] * 384 for _ in items])

    class CrossEncoder:
        def __init__(self, *a, **kw): pass
        def predict(self, pairs):
            # Deterministic keyword-overlap score so rerank is meaningful.
            out = []
            for q, d in pairs:
                qs = set(q.lower().split())
                ds = set(d.lower().split())
                out.append(len(qs & ds))
            return out
    st.SentenceTransformer = SentenceTransformer
    st.CrossEncoder = CrossEncoder

    for name, mod in {"chromadb": chromadb, "chromadb.config": chromadb_config,
                      "sentence_transformers": st}.items():
        sys.modules[name] = mod


_install_functional_stubs()

_tmp = tempfile.mkdtemp()
os.environ["CONFIG_DIR"] = _tmp
os.environ["CHROMA_PERSIST_DIR"] = _tmp + "/chroma"
os.environ["DOCUMENTS_DIR"] = _tmp + "/docs"
os.environ["ENABLE_HYDE"] = "0"
os.environ["EXPAND_CONTEXT"] = "0"

sys.path.insert(0, ".")
from app.services import rag_engine  # noqa: E402

COLLECTION = "evalset"

FIXTURES = {
    "router_manual.md": (
        "Acme Router X1 factory reset. To restore the router hold the recessed "
        "reset pinhole for ten seconds until the power LED blinks amber. The wifi "
        "SSID returns to AcmeX1-default."
    ),
    "nas_manual.md": (
        "Bytestor NAS RAID configuration. The Bytestor NAS supports RAID5 and RAID6 "
        "across up to eight drive bays. Rebuild after replacing a failed disk is "
        "initiated from the Storage pool tab."
    ),
    "printer_manual.md": (
        "PrintPro 900 replacing toner. Open the front cartridge door, twist the spent "
        "toner cartridge counter-clockwise, and insert the new PrintPro TN-900 toner "
        "until it clicks."
    ),
}

GOLDEN_SET = [
    ("factory reset router pinhole power LED", "router_manual.md"),
    ("RAID5 drive bays NAS storage pool", "nas_manual.md"),
    ("replace toner cartridge PrintPro", "printer_manual.md"),
    ("wifi SSID AcmeX1 default reset", "router_manual.md"),
    ("rebuild after failed disk drive bays", "nas_manual.md"),
]

_failures = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        _failures.append(name)


def run():
    for src, text in FIXTURES.items():
        rag_engine.ingest_text(text, source=src, collection_name=COLLECTION)

    hits = 0
    for query, expected in GOLDEN_SET:
        results = rag_engine.query(query, collection_name=COLLECTION, n_results=1)
        sources = [r["source"] for r in results]
        top1 = sources[0] if sources else None
        hit = top1 == expected
        check(f"top-1: {query[:36]!r} → {expected}", hit, f"got {sources}")
        if hit:
            hits += 1

    recall = hits / len(GOLDEN_SET)
    print(f"\nTop-1 accuracy: {hits}/{len(GOLDEN_SET)} = {recall:.0%}")
    check("top-1 >= 0.8", recall >= 0.8, f"{recall:.0%}")
    return 0 if not _failures else 1


if __name__ == "__main__":
    sys.exit(run())
