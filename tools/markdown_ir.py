"""
markdown_ir.py
---------------
Turns loose, LLM-generated markdown into a small intermediate representation
(a flat list of block dicts) that both pdf_generator.py and docx_generator.py
build on. Keeping this parsing logic in one place means the PDF and Word
outputs make identical structural decisions about the same input — same
headings, same lists, same tables — even though each renders them with a
completely different library.

Block shapes:
    {"type": "heading", "level": int, "text": str}   # level is RAW '#' depth
    {"type": "paragraph", "text": str}
    {"type": "bullet_list", "items": [str, ...]}
    {"type": "number_list", "items": [str, ...]}
    {"type": "table", "rows": [[str, ...], ...]}      # rows[0] is the header row
    {"type": "hr"}

`text` fields still contain raw inline markdown (**bold**, _italic_, `code`,
[Document 1] citations) — each renderer applies its own inline conversion
since PDF (ReportLab mini-XML) and Word (python-docx runs) need different
final representations.
"""

import re


def parse_markdown_blocks(md_text):
    blocks = []
    text = md_text.strip()
    if not text:
        return blocks

    chunks = re.split(r"\n\s*\n", text)

    def is_table_block(lines):
        return len(lines) >= 2 and all("|" in l for l in lines)

    def parse_table(lines):
        rows = []
        for l in lines:
            if re.match(r"^\|?\s*[:\-\s|]+\s*\|?$", l):
                continue  # separator row (---|---)
            cells = [c.strip() for c in l.strip().strip("|").split("|")]
            rows.append(cells)
        if not rows:
            return None
        ncols = max(len(r) for r in rows)
        for r in rows:
            while len(r) < ncols:
                r.append("")
        return rows

    for chunk in chunks:
        lines = [l.rstrip() for l in chunk.split("\n") if l.strip()]
        if not lines:
            continue

        if is_table_block(lines):
            rows = parse_table(lines)
            if rows:
                blocks.append({"type": "table", "rows": rows})
            continue

        bullet_buffer = []
        number_buffer = []
        para_buffer = []

        def flush_para():
            if para_buffer:
                blocks.append({"type": "paragraph", "text": " ".join(para_buffer)})
                para_buffer.clear()

        def flush_bullets():
            if bullet_buffer:
                blocks.append({"type": "bullet_list", "items": list(bullet_buffer)})
                bullet_buffer.clear()

        def flush_numbers():
            if number_buffer:
                blocks.append({"type": "number_list", "items": list(number_buffer)})
                number_buffer.clear()

        for line in lines:
            h_match = re.match(r"^(#{1,6})\s+(.*)", line)
            b_match = re.match(r"^[-*•]\s+(.*)", line)
            n_match = re.match(r"^\d+[.)]\s+(.*)", line)
            hr_match = re.match(r"^-{3,}$", line)

            if h_match:
                flush_para(); flush_bullets(); flush_numbers()
                blocks.append({"type": "heading", "level": len(h_match.group(1)),
                                "text": h_match.group(2)})
            elif b_match:
                flush_para(); flush_numbers()
                bullet_buffer.append(b_match.group(1))
            elif n_match:
                flush_para(); flush_bullets()
                number_buffer.append(n_match.group(1))
            elif hr_match:
                flush_para(); flush_bullets(); flush_numbers()
                blocks.append({"type": "hr"})
            else:
                flush_bullets(); flush_numbers()
                para_buffer.append(line)

        flush_para(); flush_bullets(); flush_numbers()

    return blocks


def normalize_heading_levels(blocks):
    """
    Remaps whatever '#' depths actually appear onto 1..3 so a document that
    only ever uses '##' still gets treated as top-level sections instead of
    everything looking like a nested sub-heading. Mutates and returns `blocks`.
    """
    depths = sorted({b["level"] for b in blocks if b["type"] == "heading"})
    mapping = {d: min(i + 1, 3) for i, d in enumerate(depths)}
    for b in blocks:
        if b["type"] == "heading":
            b["level"] = mapping.get(b["level"], min(b["level"], 3))
    return blocks


def extract_title_and_summary(body_text, title=None, executive_summary=None):
    """
    Shared metadata extraction used by both builders:
      - pulls a leading '# Title' line out as the title if none was given
      - drops a leading heading that duplicates an explicitly-given title
        (so it isn't rendered twice: once on the cover, once in the body)
      - pulls a leading 'Executive Summary' section out of the body if the
        model already produced one, so it doesn't get duplicated either

    Returns (title, body, executive_summary) — `body` has the consumed
    pieces removed.
    """
    body = body_text.strip()
    m = re.match(r"^#\s+(.+)", body)
    if title is None:
        title = m.group(1).strip() if m else "Generated Report"
        if m:
            body = body[m.end():].lstrip()
    elif m and m.group(1).strip().lower() == title.strip().lower():
        body = body[m.end():].lstrip()

    if executive_summary is None:
        m2 = re.search(r"^#{1,2}\s*Executive Summary\s*\n(.+?)(?=\n#{1,2}\s|\Z)",
                        body, flags=re.IGNORECASE | re.DOTALL)
        if m2:
            executive_summary = m2.group(1).strip()
            body = body[:m2.start()] + body[m2.end():]

    return title, body, executive_summary


def has_any_heading(blocks):
    return any(b["type"] == "heading" for b in blocks)