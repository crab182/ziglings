"""Local LLM answer generation via Ollama (OpenAI-compatible API)."""

import json
import logging
import os
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)

LLM_URL = os.environ.get("LLM_URL", "http://ollama:11434")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:14b")

_SYSTEM_PROMPT = """You are a helpful assistant that answers questions based ONLY on the provided context.
If the context doesn't contain enough information to answer, say so.
Cite context blocks inline with bracketed numbers matching the numbered context blocks, e.g. [1] or [2][3].
Only use numbers that exist in the context. Place citations immediately after the claim they support.
Keep answers concise and factual."""

# Truncate to ~24k words (~32k tokens) to stay within most model context windows
MAX_CONTEXT_WORDS = 24000


def _build_messages(query: str, context_chunks: list[dict]) -> list[dict]:
    context_parts = []
    for i, chunk in enumerate(context_chunks, 1):
        source = chunk.get("source", "unknown")
        section = chunk.get("metadata", {}).get("section_header", "")
        page = chunk.get("metadata", {}).get("page_number")
        citation = f"[{source}"
        if page:
            citation += f", p.{page}"
        if section:
            citation += f", {section}"
        citation += "]"
        context_parts.append(f"--- Context {i} {citation} ---\n{chunk['content']}")

    context_text = "\n\n".join(context_parts)
    words = context_text.split()
    if len(words) > MAX_CONTEXT_WORDS:
        context_text = " ".join(words[:MAX_CONTEXT_WORDS]) + "\n\n[Context truncated]"
        logger.warning("LLM context truncated from %d to %d words", len(words), MAX_CONTEXT_WORDS)

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {query}"},
    ]


async def generate_answer(query: str, context_chunks: list[dict]) -> dict:
    """Generate an answer using retrieved chunks as context. Returns {answer, model} or empty."""
    if not context_chunks:
        return {"answer": "", "model": ""}

    messages = _build_messages(query, context_chunks)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{LLM_URL}/v1/chat/completions",
                json={"model": LLM_MODEL, "messages": messages, "temperature": 0.1},
            )
            if resp.status_code != 200:
                logger.warning("Ollama returned %d: %s", resp.status_code, resp.text[:200])
                return {"answer": "", "model": ""}
            data = resp.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"answer": answer, "model": LLM_MODEL}
    except httpx.ConnectError:
        logger.info("Ollama not available at %s (CPU-only mode)", LLM_URL)
        return {"answer": "", "model": ""}
    except Exception:
        logger.exception("LLM generation failed")
        return {"answer": "", "model": ""}


async def generate_answer_stream(query: str, context_chunks: list[dict]) -> AsyncIterator[str]:
    """Stream answer text deltas. Yields nothing if the LLM is unavailable."""
    if not context_chunks:
        return

    messages = _build_messages(query, context_chunks)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
            async with client.stream(
                "POST",
                f"{LLM_URL}/v1/chat/completions",
                json={"model": LLM_MODEL, "messages": messages, "temperature": 0.1, "stream": True},
            ) as resp:
                if resp.status_code != 200:
                    logger.warning("Ollama stream returned %d", resp.status_code)
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        delta = json.loads(payload)["choices"][0].get("delta", {}).get("content", "")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
    except (httpx.ConnectError, httpx.ReadTimeout):
        logger.info("Ollama not available for streaming at %s", LLM_URL)
        return
    except Exception:
        logger.exception("LLM stream failed")
        return


async def is_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{LLM_URL}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False
