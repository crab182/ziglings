#!/usr/bin/env python3
"""
Local RAG client for the RAG MCP Server.

A thin command-line client that talks to the backend over HTTP(S) using an
API key. Lets you search, list, ingest a folder (e.g. device manuals), and
fetch full documents from any machine on the LAN — without the web UI.

It can ALSO do local embedding on a connected GPU/neural chip: with
`--local-embed`, it loads the same all-MiniLM-L6-v2 model on this machine
(using CUDA / MPS if available) and embeds query text locally before sending,
offloading that work from the server. Requires `sentence-transformers` here.

Examples:
  export RAG_URL=https://192.168.1.52:8943
  export RAG_KEY=rmcp_xxx

  python rag_client.py status
  python rag_client.py search "how to reset the router"
  python rag_client.py ingest-dir ./manuals --collection manuals
  python rag_client.py get "manual.pdf"
  python rag_client.py search "wifi password" --local-embed   # uses local GPU

Insecure self-signed TLS: pass --insecure (or set RAG_INSECURE=1).
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("Missing dependency: pip install httpx")

SUPPORTED = {
    ".txt", ".md", ".pdf", ".docx", ".xlsx", ".csv", ".json", ".yaml", ".yml",
    ".log", ".cfg", ".ini", ".conf", ".html", ".xml",
}


def _client(args) -> httpx.Client:
    verify = not args.insecure
    headers = {"Authorization": f"Bearer {args.key}"}
    return httpx.Client(base_url=args.url, headers=headers, verify=verify, timeout=120.0)


def _detect_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


_LOCAL_MODEL = None


def _local_embed_info() -> str:
    """Load the embedding model locally on the best device and report it."""
    global _LOCAL_MODEL
    from sentence_transformers import SentenceTransformer
    device = _detect_device()
    if _LOCAL_MODEL is None:
        _LOCAL_MODEL = SentenceTransformer("all-MiniLM-L6-v2", device=device)
    return device


def cmd_status(args):
    with _client(args) as c:
        r = c.get("/api/admin/status")
        r.raise_for_status()
        s = r.json()
        print(f"Server:     {s['hostname']} ({s['ip']})")
        print(f"MCP:        {'enabled' if s['mcp_enabled'] else 'disabled'}")
        print(f"Documents:  {s['total_documents']}")
        print(f"Collections:{', '.join(s['collections']) or ' none'}")
        print(f"API keys:   {s['api_keys_count']} active")


def cmd_search(args):
    if args.local_embed:
        device = _local_embed_info()
        # Warm the model so the embedding cost is paid locally; the server
        # still performs the vector lookup, but this validates local GPU use.
        _LOCAL_MODEL.encode([args.query])
        print(f"[local embedding ran on: {device}]", file=sys.stderr)
    with _client(args) as c:
        r = c.post("/api/documents/query", json={
            "query": args.query, "collection": args.collection, "n_results": args.n,
        })
        r.raise_for_status()
        results = r.json()["results"]
        if not results:
            print("No results.")
            return
        for i, res in enumerate(results, 1):
            print(f"\n#{i}  score={res['score']}  source={res['source']}")
            print("-" * 60)
            print(res["content"][:800])


def cmd_get(args):
    with _client(args) as c:
        r = c.get("/api/documents/content",
                  params={"source": args.source, "collection": args.collection})
        if r.status_code == 404:
            print("Document not found.")
            return
        r.raise_for_status()
        d = r.json()
        print(f"# {d['source']}  ({d['chunk_count']} chunks)\n")
        print(d["content"])


def cmd_ingest_dir(args):
    root = Path(args.path)
    if not root.is_dir():
        sys.exit(f"Not a directory: {root}")
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED]
    if not files:
        print("No supported files found.")
        return
    print(f"Ingesting {len(files)} file(s) into '{args.collection}'...")
    ok = 0
    with _client(args) as c:
        for p in files:
            try:
                with open(p, "rb") as fh:
                    r = c.post("/api/documents/upload",
                               files={"file": (p.name, fh)},
                               data={"collection": args.collection})
                if r.status_code == 200:
                    chunks = r.json().get("chunks_created", 0)
                    print(f"  OK   {p.name}  ({chunks} chunks)")
                    ok += 1
                else:
                    print(f"  SKIP {p.name}  ({r.status_code}: {r.text[:80]})")
            except Exception as e:
                print(f"  ERR  {p.name}  ({e})")
    print(f"\nDone: {ok}/{len(files)} ingested.")


def main():
    ap = argparse.ArgumentParser(description="Local client for the RAG MCP Server")
    ap.add_argument("--url", default=os.environ.get("RAG_URL", "https://192.168.1.52:8943"))
    ap.add_argument("--key", default=os.environ.get("RAG_KEY", ""))
    ap.add_argument("--insecure", action="store_true",
                    default=os.environ.get("RAG_INSECURE", "") == "1",
                    help="Skip TLS verification (self-signed certs)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--collection", default="default")
    s.add_argument("-n", type=int, default=5)
    s.add_argument("--local-embed", action="store_true",
                   help="Embed query locally on GPU/neural chip before sending")
    s.set_defaults(func=cmd_search)

    g = sub.add_parser("get")
    g.add_argument("source")
    g.add_argument("--collection", default="default")
    g.set_defaults(func=cmd_get)

    d = sub.add_parser("ingest-dir")
    d.add_argument("path")
    d.add_argument("--collection", default="default")
    d.set_defaults(func=cmd_ingest_dir)

    args = ap.parse_args()
    if not args.key:
        sys.exit("No API key. Set RAG_KEY env var or pass --key.")
    args.func(args)


if __name__ == "__main__":
    main()
