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

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _record(self, verb, path, **kw):
        self.calls.append((verb, path, kw.get("json")))
        return FakeClient._responses.get((verb, path), FakeResponse(404, text="not stubbed"))

    async def post(self, path, **kw):
        return self._record("POST", path, **kw)

    async def delete(self, path, **kw):
        return self._record("DELETE", path, **kw)

    async def get(self, path, **kw):
        return self._record("GET", path, **kw)

    _responses: dict = {}


def _call(name, args, *, is_admin=True, responses=None):
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
        result = asyncio.run(server.handle_tool_call(name, args, caller_is_admin=is_admin))
    finally:
        server.httpx.AsyncClient = real
    return result, captured["client"]


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

    def test_new_tools_admin_gated(self):
        self.assertIn("create_collection", server.ADMIN_TOOLS)
        self.assertIn("delete_collection", server.ADMIN_TOOLS)


if __name__ == "__main__":
    unittest.main()
