"""Shared text cleanup helpers for LLM outputs."""
import re

_THINKING_BLOCK_RE = re.compile(
    r"<think>[\s\S]*?(?:</think>|$)",
    re.IGNORECASE,
)
_THINKING_PROCESS_RE = re.compile(r"Thinking Process:.*", re.DOTALL)


def strip_thinking_tags(text: str) -> str:
    """
    Remove model reasoning blocks from output.

    Handles closed tags, unclosed/partial tags, and content before the
    final answer after a closing tag.
    """
    if not text:
        return ""

    if re.search(r"</think>", text, re.IGNORECASE):
        text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]

    text = _THINKING_BLOCK_RE.sub("", text)
    text = _THINKING_PROCESS_RE.sub("", text)
    return text.strip()
