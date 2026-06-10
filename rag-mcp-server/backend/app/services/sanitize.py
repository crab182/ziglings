"""Sanitize chunk text to mitigate prompt-injection via poisoned documents."""

import logging
import re

logger = logging.getLogger(__name__)

_INSTRUCTION_TAGS_RE = re.compile(
    r"</?(?:IMPORTANT|system|instruction|tool_use|prompt|ignore|admin|override|command)[^>]*>",
    re.IGNORECASE,
)

_BOLD_INSTRUCTION_RE = re.compile(
    r"\*\*(?:IMPORTANT|SYSTEM|INSTRUCTION|ADMIN|OVERRIDE|NOTE TO AI|IGNORE)[:\s].*?\*\*",
    re.IGNORECASE,
)

_INJECTION_LINE_PREFIXES = (
    "ignore previous",
    "ignore all previous",
    "disregard",
    "you are now",
    "new instructions",
    "forget everything",
    "override:",
    "system prompt:",
    "act as",
)


def sanitize_chunk(text: str) -> str:
    """Remove instruction-like markup and injection patterns from chunk text."""
    original_len = len(text)

    text = _INSTRUCTION_TAGS_RE.sub("", text)
    text = _BOLD_INSTRUCTION_RE.sub("", text)

    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip().lower()
        if any(stripped.startswith(p) for p in _INJECTION_LINE_PREFIXES):
            continue
        cleaned.append(line)
    text = "\n".join(cleaned)

    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) < original_len:
        removed = original_len - len(text)
        logger.warning("Sanitizer removed %d chars from chunk (%d→%d)", removed, original_len, len(text))

    return text
