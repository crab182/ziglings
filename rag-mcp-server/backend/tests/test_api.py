"""
Lightweight QA suite for the RAG MCP backend.

Runs the real FastAPI app through a TestClient with the heavy ML
dependencies (chromadb, sentence-transformers, smbclient, cryptography)
replaced by in-memory stubs, so the full request/response and auth paths
are exercised without GPUs or a vector DB.

Run:  python -m tests.test_api      (from backend/)
  or:  python backend/tests/test_api.py
"""

import os
import sys
import tempfile
import types
from pathlib import Path

# --------------------------------------------------------------------------
# Install stubs for heavy deps BEFORE importing the app
# --------------------------------------------------------------------------

def _install_stubs():
    # chromadb package
    chromadb = types.ModuleType("chromadb")
    chromadb_config = types.ModuleType("chromadb.config")

    class _Col:
        def __init__(self, name): self.name = name
        def count(self): return 0
        def upsert(self, **kw): pass
        def query(self, **kw): return {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        def get(self, **kw): return {"ids": [], "documents": [], "metadatas": []}
        def delete(self, **kw): pass

    class _Client:
        def __init__(self): self._cols = {"default": _Col("default")}
        def get_or_create_collection(self, name, **kw): return self._cols.setdefault(name, _Col(name))
        def list_collections(self): return list(self._cols.keys())
        def get_collection(self, name): return self._cols[name]
        def delete_collection(self, name): self._cols.pop(name, None)

    chromadb.PersistentClient = lambda **kw: _Client()
    chromadb.ClientAPI = _Client

    class _Settings:
        def __init__(self, **kw): pass
    chromadb_config.Settings = _Settings
    chromadb.config = chromadb_config

    # sentence_transformers (encode returns a numpy-like list with .tolist())
    class _NdList(list):
        def tolist(self): return list(self)

    st = types.ModuleType("sentence_transformers")

    class SentenceTransformer:
        def __init__(self, *a, **kw): pass
        def get_sentence_embedding_dimension(self): return 384
        def encode(self, texts, **kw):
            items = texts if isinstance(texts, list) else [texts]
            return _NdList([[0.1] * 384 for _ in items])
    st.SentenceTransformer = SentenceTransformer

    # smbclient
    smbclient = types.ModuleType("smbclient")
    smbclient.register_session = lambda *a, **kw: None
    smbclient.scandir = lambda *a, **kw: []
    smbclient.open_file = lambda *a, **kw: (_ for _ in ()).throw(IOError("stub"))

    # cryptography.fernet
    crypto = types.ModuleType("cryptography")
    fernet = types.ModuleType("cryptography.fernet")
    import base64 as _b64
    import os as _os

    class Fernet:
        def __init__(self, key): self.key = key
        @staticmethod
        def generate_key(): return _b64.urlsafe_b64encode(_os.urandom(32))
        def encrypt(self, data): return _b64.urlsafe_b64encode(b"ENC:" + data)
        def decrypt(self, token): return _b64.urlsafe_b64decode(token)[4:]
    fernet.Fernet = Fernet
    crypto.fernet = fernet

    for name, mod in {
        "chromadb": chromadb,
        "chromadb.config": chromadb_config,
        "sentence_transformers": st,
        "smbclient": smbclient,
        "cryptography": crypto,
        "cryptography.fernet": fernet,
    }.items():
        sys.modules.setdefault(name, mod)


_install_stubs()

# Point backend imports + isolated data dir
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

_tmp = tempfile.mkdtemp()
os.environ["CONFIG_DIR"] = _tmp
os.environ["CHROMA_PERSIST_DIR"] = _tmp + "/chroma"
os.environ["DOCUMENTS_DIR"] = _tmp + "/docs"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
_failures = []


def _make_pdf(text: str) -> bytes:
    """Build a minimal single-page PDF with a correct xref table and extractable text."""
    content = f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET".encode()
    objs = [
        b"<</Type /Catalog /Pages 2 0 R>>",
        b"<</Type /Pages /Kids [3 0 R] /Count 1>>",
        b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources <</Font <</F1 5 0 R>>>>>>",
        b"<</Length %d>>\nstream\n%s\nendstream" % (len(content), content),
        b"<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<</Size %d /Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref_pos)
    return bytes(out)


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    line = f"[{status}] {name}"
    if not cond and detail:
        line += f" — {detail}"
    print(line)
    if not cond:
        _failures.append((name, detail))


def run():
    # Health & diagnostics
    r = client.get("/health")
    check("health", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")

    r = client.get("/api/debug")
    check("debug endpoint", r.status_code == 200, f"{r.status_code}: {r.text[:300]}")
    if r.status_code == 200:
        for sub, info in r.json().items():
            check(f"  subsystem:{sub}", info.get("status") in ("ok", "not started"),
                  f"{info}")

    # Bootstrap
    r = client.get("/api/admin/bootstrap-required")
    check("bootstrap required (fresh)", r.json().get("bootstrap_required") is True)

    r = client.post("/api/admin/api-keys", json={"name": "admin", "is_admin": True})
    check("create bootstrap key", r.status_code == 200, f"{r.status_code}: {r.text[:300]}")
    key = r.json().get("key") if r.status_code == 200 else None
    check("key returned", bool(key))

    r = client.get("/api/admin/bootstrap-required")
    check("bootstrap false after key", r.json().get("bootstrap_required") is False)

    hdr = {"Authorization": f"Bearer {key}"}

    # Auth enforcement
    check("status 401 no header", client.get("/api/admin/status").status_code == 401)
    check("status 403 bad key",
          client.get("/api/admin/status", headers={"Authorization": "Bearer wrong"}).status_code == 403)

    # Authenticated reads
    for path in ["/api/admin/status", "/api/admin/api-keys", "/api/admin/config",
                 "/api/documents/collections", "/api/smb/saved"]:
        r = client.get(path, headers=hdr)
        check(f"GET {path}", r.status_code == 200, f"{r.status_code}: {r.text[:300]}")

    # Query
    r = client.post("/api/documents/query", headers=hdr,
                    json={"query": "hello", "collection": "default", "n_results": 3})
    check("query", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")

    # Ingest text (admin)
    r = client.post("/api/documents/ingest-text", headers=hdr,
                    json={"text": "device manual content", "source": "manual1", "collection": "default"})
    check("ingest-text admin", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")

    # Edge cases
    check("bad collection name rejected",
          client.post("/api/documents/query", headers=hdr,
                      json={"query": "x", "collection": "../etc", "n_results": 3}).status_code == 422)
    check("empty query rejected",
          client.post("/api/documents/query", headers=hdr,
                      json={"query": "", "collection": "default"}).status_code == 422)
    check("n_results over cap rejected",
          client.post("/api/documents/query", headers=hdr,
                      json={"query": "x", "collection": "default", "n_results": 9999}).status_code == 422)
    check("delete default collection blocked",
          client.delete("/api/documents/collections/default", headers=hdr).status_code == 400)
    check("duplicate key name conflict",
          client.post("/api/admin/api-keys", headers=hdr, json={"name": "admin"}).status_code == 409)

    # PDF parsing + upload (real PDF bytes through pypdf)
    pdf_bytes = _make_pdf("Router Model X1 - Quick Start Manual")
    try:
        from app.services.document_parser import parse_file, can_parse
        check("pdf recognized as parseable", can_parse("manual.pdf"))
        text = parse_file(content=pdf_bytes, filename="manual.pdf")
        check("pdf text extracted", "Router" in text, f"extracted: {text[:120]!r}")
    except Exception as e:
        check("pdf parse", False, f"{type(e).__name__}: {e}")

    r = client.post(
        "/api/documents/upload",
        headers=hdr,
        files={"file": ("manual.pdf", pdf_bytes, "application/pdf")},
        data={"collection": "default"},
    )
    check("pdf upload", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        check("pdf produced chunks", r.json().get("chunks_created", 0) >= 1, f"{r.json()}")

    # Path-traversal filename rejected on upload
    r = client.post(
        "/api/documents/upload",
        headers=hdr,
        files={"file": ("../../etc/passwd", b"hello world text", "text/plain")},
        data={"collection": "default"},
    )
    check("path-traversal filename handled", r.status_code in (200, 400),
          f"{r.status_code}: {r.text[:150]}")

    print("\n" + "=" * 50)
    print(f"RESULT: {len(_failures)} failure(s)")
    for n, d in _failures:
        print(f"  - {n}: {str(d)[:200]}")
    return 0 if not _failures else 1


if __name__ == "__main__":
    sys.exit(run())
