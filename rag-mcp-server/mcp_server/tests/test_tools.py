"""
Tests for MCP server tool dispatch — specifically the create_collection and
delete_collection tools added for desktop client parity.

The server's handle_tool_call() opens its own httpx.AsyncClient inside the
function, so we monkeypatch the module-level `httpx.AsyncClient` with a fake
that records the verb/path/headers and returns canned responses. This keeps
the test independent of the backend.

Run:  python -m pytest mcp_server/tests/test_tools.py -v
  or: python -m unittest mcp_server.tests.test_tools
"""

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MCP_DIR))

os.environ.setdefault("MCP_BACKEND_KEY", "test-backend-key")

import server  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text or (json.dumps(json_body) if json_body is not None else "")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Records every request; returns whatever the test has queued for that path."""

    def __init__(self, *, base_url="", timeout=None, headers=None, **_):
        self.base_url = base_url
        self.headers = headers or {}
        self.calls = []  # (verb, path, json_body)
        self.requests = []  # full request metadata for tests that assert params

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _record(self, verb, path, **kw):
        self.calls.append((verb, path, kw.get("json")))
        self.requests.append({
            "verb": verb,
            "path": path,
            "json": kw.get("json"),
            "params": kw.get("params"),
        })
        return FakeClient._responses.get((verb, path), FakeResponse(404, text="not stubbed"))

    async def post(self, path, **kw):
        return self._record("POST", path, **kw)

    async def delete(self, path, **kw):
        return self._record("DELETE", path, **kw)

    async def get(self, path, **kw):
        return self._record("GET", path, **kw)

    _responses: dict = {}


def _call(name, args, *, is_admin=True, responses=None, collections=None):
    """Run handle_tool_call with FakeClient stubbed in for httpx.AsyncClient."""
    FakeClient._responses = responses or {}
    captured = {"client": None}

    def _factory(*a, **kw):
        c = FakeClient(*a, **kw)
        captured["client"] = c
        return c

    real = server.httpx.AsyncClient
    server.httpx.AsyncClient = _factory
    try:
        result = asyncio.run(server.handle_tool_call(
            name, args, caller_is_admin=is_admin, caller_collections=collections
        ))
    finally:
        server.httpx.AsyncClient = real
    return result, captured["client"]


def _jsonrpc_async(result, *, is_admin=True, responses=None, collections=None):
    """Run _handle_async_jsonrpc with FakeClient stubbed in for httpx.AsyncClient."""
    FakeClient._responses = responses or {}
    captured = {"client": None}

    def _factory(*a, **kw):
        c = FakeClient(*a, **kw)
        captured["client"] = c
        return c

    real = server.httpx.AsyncClient
    server.httpx.AsyncClient = _factory
    try:
        response = asyncio.run(server._handle_async_jsonrpc(
            result, is_admin, caller_collections=collections
        ))
    finally:
        server.httpx.AsyncClient = real
    return response, captured["client"]


class CreateCollectionTests(unittest.TestCase):
    def test_admin_required(self):
        result, _ = _call("create_collection", {"name": "demo"}, is_admin=False)
        self.assertTrue(result["isError"])
        self.assertIn("admin", result["content"][0]["text"].lower())

    def test_success_dual_format(self):
        result, client = _call(
            "create_collection",
            {"name": "demo"},
            responses={("POST", "/api/documents/collections/demo"):
                       FakeResponse(200, {"name": "demo", "created": True})},
        )
        self.assertFalse(result["isError"])
        self.assertEqual(client.calls[0], ("POST", "/api/documents/collections/demo", None))
        # Server-issued credential is forwarded, client token is NOT.
        self.assertEqual(client.headers.get("Authorization"), "Bearer test-backend-key")
        # Dual-format: markdown then JSON.
        self.assertEqual(len(result["content"]), 2)
        self.assertIn("demo", result["content"][0]["text"])
        self.assertEqual(json.loads(result["content"][1]["text"]),
                         {"name": "demo", "created": True})

    def test_400_returns_friendly_detail(self):
        # Defense-in-depth: even though we validate client-side, a backend
        # 400 (e.g. if the two regexes ever diverged) should still surface
        # as friendly text, not a raise_for_status crash.
        result, _ = _call(
            "create_collection",
            {"name": "demo"},
            responses={("POST", "/api/documents/collections/demo"):
                       FakeResponse(400, {"detail": "Invalid collection name"})},
        )
        self.assertTrue(result["isError"])
        self.assertIn("Invalid collection name", result["content"][0]["text"])


class DeleteCollectionTests(unittest.TestCase):
    def test_admin_required(self):
        result, _ = _call("delete_collection", {"name": "demo"}, is_admin=False)
        self.assertTrue(result["isError"])
        self.assertIn("admin", result["content"][0]["text"].lower())

    def test_success_dual_format(self):
        result, client = _call(
            "delete_collection",
            {"name": "demo"},
            responses={("DELETE", "/api/documents/collections/demo"):
                       FakeResponse(200, {"name": "demo", "deleted": True})},
        )
        self.assertFalse(result["isError"])
        self.assertEqual(client.calls[0], ("DELETE", "/api/documents/collections/demo", None))
        self.assertEqual(len(result["content"]), 2)
        self.assertEqual(json.loads(result["content"][1]["text"]),
                         {"name": "demo", "deleted": True})

    def test_default_collection_400_surfaces_message(self):
        result, _ = _call(
            "delete_collection",
            {"name": "default"},
            responses={("DELETE", "/api/documents/collections/default"):
                       FakeResponse(400, {"detail": "Cannot delete default collection"})},
        )
        self.assertTrue(result["isError"])
        self.assertEqual(result["content"][0]["text"], "Cannot delete default collection")


class PathTraversalTests(unittest.TestCase):
    """Regression: cursor[bot] flagged that httpx normalizes `..` segments,
    so an unvalidated `name` interpolated into the URL path could redirect
    the request to a backend admin endpoint (api-keys, MCP toggle, etc.) sent
    with the server's admin-tier MCP_BACKEND_KEY."""

    def test_create_rejects_traversal_payload_without_request(self):
        result, client = _call(
            "create_collection",
            {"name": "../../admin/mcp/toggle?enabled=false"},
            responses={},
        )
        self.assertTrue(result["isError"])
        # No HTTP call must be made at all.
        self.assertEqual(client.calls, [])
        self.assertIn("Collection name", result["content"][0]["text"])

    def test_delete_rejects_traversal_payload_without_request(self):
        result, client = _call(
            "delete_collection",
            {"name": "../../admin/api-keys/victim"},
            responses={},
        )
        self.assertTrue(result["isError"])
        self.assertEqual(client.calls, [])

    def test_rejects_empty_name(self):
        for tool in ("create_collection", "delete_collection"):
            with self.subTest(tool=tool):
                result, client = _call(tool, {"name": ""}, responses={})
                self.assertTrue(result["isError"])
                self.assertEqual(client.calls, [])

    def test_rejects_overlong_name(self):
        result, client = _call(
            "create_collection",
            {"name": "a" * 65},
            responses={},
        )
        self.assertTrue(result["isError"])
        self.assertEqual(client.calls, [])

    def test_rejects_disallowed_chars(self):
        for bad in ("a/b", "a b", "a.b", "a:b", "a%2fb"):
            with self.subTest(bad=bad):
                result, client = _call(
                    "create_collection", {"name": bad}, responses={},
                )
                self.assertTrue(result["isError"])
                self.assertEqual(client.calls, [])


class ToolsListTests(unittest.TestCase):
    def test_new_tools_registered(self):
        names = {t["name"] for t in server.TOOLS}
        self.assertIn("create_collection", names)
        self.assertIn("delete_collection", names)
        self.assertIn("delete_document", names)
        self.assertIn("get_collection_stats", names)

    def test_new_tools_admin_gated(self):
        self.assertIn("create_collection", server.ADMIN_TOOLS)
        self.assertIn("delete_collection", server.ADMIN_TOOLS)
        self.assertIn("delete_document", server.ADMIN_TOOLS)
        # Read-only stats must NOT require admin.
        self.assertNotIn("get_collection_stats", server.ADMIN_TOOLS)


class DeleteDocumentTests(unittest.TestCase):
    def test_admin_required(self):
        # Admin gate fires before any client is created (client stays None).
        result, client = _call("delete_document", {"source": "x.pdf"}, is_admin=False)
        self.assertTrue(result["isError"])
        self.assertIsNone(client)

    def test_success_dual_format_and_url_encoded(self):
        result, client = _call(
            "delete_document",
            {"source": "router.pdf", "collection": "manuals"},
            responses={("DELETE", "/api/documents/router.pdf"):
                       FakeResponse(200, {"deleted_chunks": 7, "filename": "router.pdf"})},
        )
        self.assertFalse(result["isError"])
        self.assertEqual(client.calls[0][:2], ("DELETE", "/api/documents/router.pdf"))
        self.assertEqual(len(result["content"]), 2)
        self.assertEqual(json.loads(result["content"][1]["text"])["deleted_chunks"], 7)

    def test_traversal_source_is_encoded_not_raw(self):
        # A slashy source must be percent-encoded, never a raw ../ path segment.
        result, client = _call(
            "delete_document",
            {"source": "../../admin/api-keys/victim", "collection": "default"},
            responses={},  # 404 fallback; we only assert the path shape
        )
        for verb, path, _ in client.calls:
            self.assertNotIn("../", path)

    def test_bad_collection_rejected_without_request(self):
        result, client = _call("delete_document", {"source": "x", "collection": "a/b"}, is_admin=True)
        self.assertTrue(result["isError"])
        self.assertEqual(client.calls, [])
        self.assertIn("Collection name", result["content"][0]["text"])


class CollectionStatsTests(unittest.TestCase):
    _COLS = {("GET", "/api/documents/collections"): None}

    def _stats_resp(self):
        return {("GET", "/api/documents/collections"): FakeResponse(200, {"collections": [
            {"name": "default", "document_count": 3, "chunk_count": 40},
            {"name": "manuals", "document_count": 1, "chunk_count": 12},
        ]})}

    def test_all_collections(self):
        result, _ = _call("get_collection_stats", {}, is_admin=False, responses=self._stats_resp())
        self.assertFalse(result["isError"])
        self.assertIn("manuals", result["content"][0]["text"])
        self.assertEqual(len(json.loads(result["content"][1]["text"])), 2)

    def test_filtered(self):
        result, _ = _call("get_collection_stats", {"collection": "manuals"}, responses=self._stats_resp())
        self.assertEqual(json.loads(result["content"][1]["text"]),
                         [{"name": "manuals", "document_count": 1, "chunk_count": 12}])

    def test_unknown_collection(self):
        result, _ = _call("get_collection_stats", {"collection": "nope"}, responses=self._stats_resp())
        self.assertTrue(result["isError"])


class CollectionACLTests(unittest.TestCase):
    """Per-collection ACLs on scoped (non-admin) keys."""

    def _cols_resp(self):
        return {("GET", "/api/documents/collections"): FakeResponse(200, {"collections": [
            {"name": "default", "document_count": 3, "chunk_count": 40},
            {"name": "manuals", "document_count": 1, "chunk_count": 12},
        ]})}

    def test_scoped_search_denied_outside_scope(self):
        result, client = _call("search_documents", {"query": "x", "collection": "default"},
                               is_admin=False, collections=["manuals"])
        self.assertTrue(result["isError"])
        self.assertIn("cannot access collection 'default'", result["content"][0]["text"])
        self.assertIsNone(client)  # denied before any backend call

    def test_scoped_search_allowed_in_scope(self):
        result, client = _call(
            "search_documents", {"query": "x", "collection": "manuals"},
            is_admin=False, collections=["manuals"],
            responses={("POST", "/api/documents/query"): FakeResponse(200, {"results": []})},
        )
        self.assertFalse(result["isError"])

    def test_scoped_default_collection_implied(self):
        # Omitting `collection` means "default" — still subject to the ACL.
        result, client = _call("get_document", {"source": "a.pdf"},
                               is_admin=False, collections=["manuals"])
        self.assertTrue(result["isError"])
        self.assertIsNone(client)

    def test_admin_bypasses_acl(self):
        result, _ = _call(
            "search_documents", {"query": "x", "collection": "default"},
            is_admin=True, collections=["manuals"],
            responses={("POST", "/api/documents/query"): FakeResponse(200, {"results": []})},
        )
        self.assertFalse(result["isError"])

    def test_list_collections_filtered(self):
        result, _ = _call("list_collections", {}, is_admin=False, collections=["manuals"],
                          responses=self._cols_resp())
        self.assertFalse(result["isError"])
        payload = json.loads(result["content"][1]["text"])
        names = [c["name"] if isinstance(c, dict) else c for c in payload]
        self.assertEqual(names, ["manuals"])

    def test_stats_filtered_for_scoped_key(self):
        result, _ = _call("get_collection_stats", {}, is_admin=False, collections=["manuals"],
                          responses=self._cols_resp())
        self.assertEqual(json.loads(result["content"][1]["text"]),
                         [{"name": "manuals", "document_count": 1, "chunk_count": 12}])

    def test_stats_denied_outside_scope(self):
        result, _ = _call("get_collection_stats", {"collection": "default"},
                          is_admin=False, collections=["manuals"], responses=self._cols_resp())
        self.assertTrue(result["isError"])
        self.assertIn("cannot access", result["content"][0]["text"])

    def _status_resp(self):
        return {("GET", "/api/admin/status"): FakeResponse(200, {
            "hostname": "srv", "ip": "1.2.3.4", "mcp_enabled": True,
            "total_documents": 55, "collections": ["default", "manuals", "hr_confidential"],
            "active_credentials": 3,
        })}

    def test_server_status_filtered_for_scoped_key(self):
        # Scoped keys must not enumerate out-of-scope collection names or see
        # aggregates that span them (get_server_status leak regression).
        result, _ = _call("get_server_status", {}, is_admin=False, collections=["manuals"],
                          responses=self._status_resp())
        self.assertFalse(result["isError"])
        payload = json.loads(result["content"][1]["text"])
        self.assertEqual(payload["collections"], ["manuals"])
        self.assertNotIn("total_documents", payload)
        self.assertNotIn("active_credentials", payload)
        self.assertNotIn("hr_confidential", result["content"][0]["text"])

    def test_server_status_unfiltered_for_admin(self):
        result, _ = _call("get_server_status", {}, is_admin=True, responses=self._status_resp())
        payload = json.loads(result["content"][1]["text"])
        self.assertEqual(len(payload["collections"]), 3)
        self.assertEqual(payload["total_documents"], 55)


class ResourceACLTests(unittest.TestCase):
    """Per-collection ACLs on MCP Resources, which bypass handle_tool_call."""

    def test_resource_list_filters_and_only_fetches_allowed_collections(self):
        response, client = _jsonrpc_async(
            {"_async_resource_list": True, "id": 7},
            is_admin=False,
            collections=["manuals"],
            responses={
                ("GET", "/api/documents/collections"): FakeResponse(200, {"collections": [
                    {"name": "default", "document_count": 3, "chunk_count": 40},
                    {"name": "manuals", "document_count": 1, "chunk_count": 12},
                    {"name": "hr_confidential", "document_count": 4, "chunk_count": 80},
                ]}),
                ("GET", "/api/documents/list"): FakeResponse(200, {"documents": ["router.pdf"]}),
            },
        )

        resources = response["result"]["resources"]
        self.assertEqual(resources, [{
            "uri": "rag://collections/manuals/documents/router.pdf",
            "name": "router.pdf",
            "description": "Document in collection 'manuals'",
            "mimeType": "text/plain",
        }])
        list_requests = [r for r in client.requests if r["path"] == "/api/documents/list"]
        self.assertEqual([r["params"] for r in list_requests], [{"collection": "manuals"}])

    def test_resource_read_denied_outside_scope_without_backend_request(self):
        response, client = _jsonrpc_async(
            {
                "_async_resource_read": True,
                "id": 8,
                "params": {"uri": "rag://collections/default/documents/router.pdf"},
            },
            is_admin=False,
            collections=["manuals"],
        )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("cannot access collection 'default'", response["error"]["message"])
        self.assertEqual(client.requests, [])


class AskDocumentsTests(unittest.TestCase):
    _RESP = {("POST", "/api/documents/ask"): FakeResponse(200, {
        "answer": "The reset button is on the back.", "model": "qwen",
        "query": "q", "sources": [{"source": "manual.pdf", "score": 0.9, "excerpt": "..."}],
    })}

    def test_dual_format_answer(self):
        result, client = _call("ask_documents", {"query": "where is reset?"}, responses=self._RESP)
        self.assertFalse(result["isError"])
        self.assertEqual(len(result["content"]), 2)
        self.assertIn("reset button", result["content"][0]["text"])
        self.assertIn("manual.pdf", result["content"][0]["text"])
        self.assertEqual(json.loads(result["content"][1]["text"])["model"], "qwen")

    def test_scoped_key_denied_outside_scope(self):
        result, client = _call("ask_documents", {"query": "x", "collection": "default"},
                               is_admin=False, collections=["manuals"])
        self.assertTrue(result["isError"])
        self.assertIsNone(client)  # denied before any backend call


if __name__ == "__main__":
    unittest.main()
