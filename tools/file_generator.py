"""
tools/file_generator.py
-------------------------
Called from run_pipeline.py as:

    filepath = generate_file(
        query=user_query,
        file_type=file_request["file_type"],
        content=response,
    )

Notes on that call site, since they shape everything below:
  - No `filename` is ever passed in, so one is generated from the query.
  - No `title`/metadata is ever passed in, so the title is derived from the
    query, and `content` (a plain LLM/RAG answer) is not assumed to contain
    markdown headings — pdf_generator / docx_generator already degrade
    gracefully (compact single-flow layout) when it doesn't.
  - `generate_file` now returns a dict instead of a bare path string:

        {"path": ..., "filename": ..., "file_type": ..., "extension": ...}

    so run_pipeline.py has the filename/extension available too, not just
    the path (see the run_pipeline.py diff in the accompanying notes).
"""

import os
import re
from datetime import datetime

from .pdf_generator import build_report_pdf
from .docx_generator import build_report_docx
from .markdown_ir import parse_markdown_blocks, normalize_heading_levels

OUTPUT_DIR = "generated_files"

# Accept whatever your file_detector.py actually emits without caring about
# its exact vocabulary — "word"/"doc"/"docx" all mean the same thing here.
FILE_TYPE_ALIASES = {
    "pdf": "pdf",
    "docx": "docx",
    "doc": "docx",
    "word": "docx",
    "txt": "txt",
    "text": "txt",
    "md": "md",
    "markdown": "md",
}

EXTENSIONS = {"pdf": ".pdf", "docx": ".docx", "txt": ".txt", "md": ".md"}

_LEADING_REQUEST_RE = re.compile(
    r"^\s*(please\s+)?(generate|create|write|make|draft|produce|save|export)\s+"
    r"(me\s+)?(a|an|the)?\s*(pdf|word|docx?|txt|text|markdown|md)?\s*"
    r"(file|document|doc|report|summary)?\s*(on|about|for|regarding|of|titled|called)?\s*[:\-]?\s*",
    re.IGNORECASE,
)


def _slugify(text, max_len=50):
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:max_len] or "document"


def _make_filename(query):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{_slugify(query)}_{stamp}"


def _derive_title(query):
    """Turns a request like 'generate a pdf report on Q3 revenue trends'
    into a cover-page-friendly title like 'Q3 revenue trends'."""
    q = query.strip()
    stripped = _LEADING_REQUEST_RE.sub("", q).strip(" .!?")
    candidate = stripped if stripped else q.strip(" .!?")
    if not candidate:
        return "Generated Report"
    return candidate[0].upper() + candidate[1:]


# ----------------------------------------------------------------------------
# Plain-text rendering (shared block IR -> readable .txt, no markdown symbols)
# ----------------------------------------------------------------------------
def _blocks_to_plain_text(blocks, title=None):
    lines = []
    if title:
        lines.append(title)
        lines.append("=" * len(title))
        lines.append("")
    for b in blocks:
        kind = b["type"]
        if kind == "heading":
            text = b["text"]
            underline = "-" * len(text) if b["level"] >= 2 else "=" * len(text)
            lines.append(text)
            lines.append(underline)
            lines.append("")
        elif kind == "paragraph":
            lines.append(_strip_inline_markup(b["text"]))
            lines.append("")
        elif kind == "bullet_list":
            for item in b["items"]:
                lines.append(f"- {_strip_inline_markup(item)}")
            lines.append("")
        elif kind == "number_list":
            for i, item in enumerate(b["items"], start=1):
                lines.append(f"{i}. {_strip_inline_markup(item)}")
            lines.append("")
        elif kind == "table":
            rows = b["rows"]
            widths = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(len(rows[0]))]
            for r_idx, row in enumerate(rows):
                cells = [c.ljust(widths[i]) for i, c in enumerate(row)]
                lines.append("  ".join(cells))
                if r_idx == 0:
                    lines.append("  ".join("-" * w for w in widths))
            lines.append("")
        elif kind == "hr":
            lines.append("-" * 40)
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def _strip_inline_markup(text):
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def create_txt(content, filename, title=None):
    path = os.path.join(OUTPUT_DIR, f"{filename}.txt")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    blocks = normalize_heading_levels(parse_markdown_blocks(content.strip()))
    text = _blocks_to_plain_text(blocks, title=title)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def create_md(content, filename, title=None):
    path = os.path.join(OUTPUT_DIR, f"{filename}.md")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    body = content.strip()
    if title and not re.match(r"^#\s+", body):
        body = f"# {title}\n\n{body}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(body + "\n")
    return path


def create_pdf(content, filename, **metadata):
    path = os.path.join(OUTPUT_DIR, f"{filename}.pdf")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    build_report_pdf(content, path, **metadata)
    return path


def create_docx(content, filename, **metadata):
    path = os.path.join(OUTPUT_DIR, f"{filename}.docx")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    build_report_docx(content, path, **metadata)
    return path


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def generate_file(file_type, query, content, filename=None, **metadata):
    """
    metadata (pdf/docx only, all optional): title, subtitle, prepared_for,
    prepared_by, date, executive_summary. Anything not passed is either
    auto-derived from `query`/`content` or simply omitted.

    Returns:
        {"path": str, "filename": str, "file_type": str, "extension": str}
    """
    if content is None or not content.strip():
        raise ValueError("generate_file requires non-empty `content` to render.")

    normalized_type = FILE_TYPE_ALIASES.get((file_type or "").strip().lower())
    if normalized_type is None:
        raise ValueError(f"Unsupported file type: {file_type!r}")

    derived_title = _derive_title(query)

    if filename is None:
        filename = _make_filename(derived_title)
    else:
        filename = os.path.splitext(filename)[0]  # strip any extension the caller included

    if normalized_type in ("pdf", "docx"):
        metadata.setdefault("title", derived_title)

    if normalized_type == "pdf":
        path = create_pdf(content, filename, **metadata)
    elif normalized_type == "docx":
        path = create_docx(content, filename, **metadata)
    elif normalized_type == "txt":
        path = create_txt(content, filename, title=metadata.get("title") or derived_title)
    elif normalized_type == "md":
        path = create_md(content, filename, title=metadata.get("title") or derived_title)
    else:  # pragma: no cover — guarded by the alias lookup above
        raise ValueError(f"Unsupported file type: {file_type!r}")

    return {
        "path": path,
        "filename": filename,
        "file_type": normalized_type,
        "extension": EXTENSIONS[normalized_type],
    }