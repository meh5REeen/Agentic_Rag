"""
tools/pdf_generator.py
------------------------
Turns plain / markdown-ish LLM text into a polished, branded PDF: cover page
(for content with real structure), auto table of contents with real page
numbers, colored headings, bullet & numbered lists, tables, and a running
header/footer with "Page X of Y". Falls back to a compact single-flow layout
(no cover page, no TOC) for short, unstructured answers — e.g. a plain
chatbot response with no headings.

Structural parsing (headings/lists/tables/hr) is shared with docx_generator.py
via markdown_ir.py, so the PDF and Word outputs make identical decisions
about the same input.
"""

import html
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table,
    TableStyle, ListFlowable, ListItem, PageBreak, HRFlowable,
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfgen.canvas import Canvas
import re

from .markdown_ir import (
    parse_markdown_blocks, normalize_heading_levels,
    extract_title_and_summary, has_any_heading,
)

# ----------------------------------------------------------------------------
# Color scheme — sampled directly from the reference design
# ----------------------------------------------------------------------------
NAVY_DARK  = HexColor("#13356D")   # deepest tone in the header gradient bar
NAVY       = HexColor("#1C4587")   # title / heading color
NAVY_MID   = HexColor("#1C4EA0")   # mid gradient tone / rules
BLUE_LIGHT = HexColor("#4E83D8")   # lightest gradient tone
TEXT_DARK  = HexColor("#222222")   # body text
TEXT_GRAY  = HexColor("#555555")   # secondary text
MUTED_GRAY = HexColor("#8A8A8A")   # footer / citation text
RULE_GRAY  = HexColor("#DDDDDD")

PAGE_W, PAGE_H = letter
MARGIN = 0.85 * inch
TOP_MARGIN = 0.9 * inch
BOTTOM_MARGIN = 0.95 * inch


# ----------------------------------------------------------------------------
# Styles
# ----------------------------------------------------------------------------
def build_styles():
    styles = {}
    styles["CoverTitle"] = ParagraphStyle(
        "CoverTitle", fontName="Times-Bold", fontSize=30, leading=36,
        textColor=NAVY, alignment=TA_CENTER, spaceBefore=18, spaceAfter=10,
    )
    styles["CoverSubtitle"] = ParagraphStyle(
        "CoverSubtitle", fontName="Helvetica-Bold", fontSize=12.5, leading=17,
        textColor=TEXT_DARK, alignment=TA_LEFT, spaceBefore=4, spaceAfter=14,
    )
    styles["Label"] = ParagraphStyle(
        "Label", fontName="Helvetica-Bold", fontSize=11, leading=15,
        textColor=NAVY, spaceBefore=6, spaceAfter=2,
    )
    styles["MetaLine"] = ParagraphStyle(
        "MetaLine", fontName="Helvetica", fontSize=10.5, leading=15,
        textColor=TEXT_DARK, spaceAfter=1,
    )
    styles["CoverHeading"] = ParagraphStyle(
        "CoverHeading", fontName="Helvetica-Bold", fontSize=13, leading=17,
        textColor=NAVY, spaceBefore=16, spaceAfter=8,
    )
    styles["Heading1"] = ParagraphStyle(
        "Heading1", fontName="Helvetica-Bold", fontSize=16, leading=20,
        textColor=NAVY, spaceBefore=20, spaceAfter=10,
    )
    styles["Heading2"] = ParagraphStyle(
        "Heading2", fontName="Helvetica-Bold", fontSize=13, leading=17,
        textColor=NAVY_MID, spaceBefore=14, spaceAfter=6,
    )
    styles["Heading3"] = ParagraphStyle(
        "Heading3", fontName="Helvetica-Bold", fontSize=11, leading=15,
        textColor=NAVY_MID, spaceBefore=10, spaceAfter=4,
    )
    styles["Body"] = ParagraphStyle(
        "Body", fontName="Helvetica", fontSize=10.3, leading=15.5,
        textColor=TEXT_DARK, alignment=TA_JUSTIFY, spaceAfter=8,
    )
    styles["Bullet"] = ParagraphStyle(
        "Bullet", fontName="Helvetica", fontSize=10.3, leading=15,
        textColor=TEXT_DARK, alignment=TA_LEFT,
    )
    styles["TableCell"] = ParagraphStyle(
        "TableCell", fontName="Helvetica", fontSize=9.5, leading=13,
        textColor=TEXT_DARK,
    )
    styles["TableHeader"] = ParagraphStyle(
        "TableHeader", fontName="Helvetica-Bold", fontSize=9.5, leading=13,
        textColor=HexColor("#FFFFFF"),
    )
    return styles


def build_toc(styles):
    toc = TableOfContents()
    toc.dotsMinLevel = 0
    toc.levelStyles = [
        ParagraphStyle("TOC1", fontName="Helvetica-Bold", fontSize=11,
                        leading=16, textColor=NAVY, leftIndent=0, firstLineIndent=0),
        ParagraphStyle("TOC2", fontName="Helvetica", fontSize=10,
                        leading=14, textColor=TEXT_GRAY, leftIndent=16, firstLineIndent=0),
    ]
    return toc


# ----------------------------------------------------------------------------
# Inline markdown -> ReportLab mini-markup
# ----------------------------------------------------------------------------
def inline_markup(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="9">\1</font>', text)
    # Style bracketed source citations like [Document 1] subtly instead of leaving them raw
    text = re.sub(
        r"\[(Document\s*\d+|Source\s*\d+|\d+)\]",
        r'<font size="7" color="#8A8A8A">[\1]</font>',
        text, flags=re.IGNORECASE,
    )
    return text


def para(text, style):
    return Paragraph(inline_markup(text), style)


# ----------------------------------------------------------------------------
# Block IR -> ReportLab flowables
# ----------------------------------------------------------------------------
def make_table_flowable(rows):
    if not rows:
        return None
    styles = build_styles()
    ncols = len(rows[0])
    data = []
    for i, r in enumerate(rows):
        style = styles["TableHeader"] if i == 0 else styles["TableCell"]
        row_cells = list(r) + [""] * (ncols - len(r))
        data.append([Paragraph(inline_markup(c), style) for c in row_cells])
    col_width = (PAGE_W - 2 * MARGIN) / ncols
    t = Table(data, colWidths=[col_width] * ncols, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#FFFFFF"), HexColor("#F2F5FA")]),
        ("GRID", (0, 0), (-1, -1), 0.5, RULE_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def render_blocks(blocks, styles):
    flowables = []
    for b in blocks:
        kind = b["type"]
        if kind == "heading":
            flowables.append(para(b["text"], styles[f"Heading{b['level']}"]))
        elif kind == "paragraph":
            flowables.append(para(b["text"], styles["Body"]))
        elif kind == "bullet_list":
            items = [ListItem(para(x, styles["Bullet"])) for x in b["items"]]
            flowables.append(ListFlowable(
                items, bulletType="bullet", start="●",
                leftIndent=18, bulletColor=NAVY_MID, spaceBefore=2, spaceAfter=10,
            ))
        elif kind == "number_list":
            items = [ListItem(para(x, styles["Bullet"]), value=i + 1)
                     for i, x in enumerate(b["items"])]
            flowables.append(ListFlowable(
                items, bulletType="1", start="1",
                leftIndent=22, bulletColor=NAVY_MID, spaceBefore=2, spaceAfter=10,
            ))
        elif kind == "table":
            tbl = make_table_flowable(b["rows"])
            if tbl:
                flowables.append(Spacer(1, 4))
                flowables.append(tbl)
                flowables.append(Spacer(1, 10))
        elif kind == "hr":
            flowables.append(Spacer(1, 6))
            flowables.append(HRFlowable(width="100%", thickness=0.75, color=RULE_GRAY))
            flowables.append(Spacer(1, 10))
    return flowables


# ----------------------------------------------------------------------------
# Gradient bar (built from flowables so it reflows naturally, no fixed coords)
# ----------------------------------------------------------------------------
def gradient_bar(width, height=8, steps=40):
    def lerp(c1, c2, t):
        return tuple(c1[i] + (c2[i] - c1[i]) * t for i in range(3))

    stops = [(78, 131, 216), (28, 78, 160), (19, 53, 109)]
    seg = steps // (len(stops) - 1)
    colors = []
    for i in range(len(stops) - 1):
        for s in range(seg):
            t = s / seg
            colors.append(lerp(stops[i], stops[i + 1], t))
    colors.append(stops[-1])

    col_w = width / len(colors)
    data = [["" for _ in colors]]
    t = Table(data, colWidths=[col_w] * len(colors), rowHeights=[height])
    style = [("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
             ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]
    for i, c in enumerate(colors):
        style.append(("BACKGROUND", (i, 0), (i, 0), HexColor("#%02x%02x%02x" % tuple(int(v) for v in c))))
    t.setStyle(TableStyle(style))
    return t


# ----------------------------------------------------------------------------
# Numbered canvas: adds "Page X of Y" (needs the two-pass trick since total
# page count isn't known until the whole document has been laid out)
# ----------------------------------------------------------------------------
def make_numbered_canvas(doc_title):
    class NumberedCanvas(Canvas):
        def __init__(self, *args, **kwargs):
            Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                if self._pageNumber > 1:
                    self._draw_chrome(num_pages)
                Canvas.showPage(self)
            Canvas.save(self)

        def _draw_chrome(self, num_pages):
            self.saveState()
            self.setStrokeColor(NAVY_MID)
            self.setLineWidth(0.75)
            self.line(MARGIN, PAGE_H - 0.62 * inch, PAGE_W - MARGIN, PAGE_H - 0.62 * inch)
            self.setFont("Helvetica", 8.5)
            self.setFillColor(MUTED_GRAY)
            self.drawString(MARGIN, PAGE_H - 0.55 * inch, doc_title)
            self.setStrokeColor(RULE_GRAY)
            self.line(MARGIN, 0.75 * inch, PAGE_W - MARGIN, 0.75 * inch)
            self.setFont("Helvetica", 8.5)
            self.setFillColor(MUTED_GRAY)
            self.drawCentredString(PAGE_W / 2, 0.55 * inch,
                                    f"Page {self._pageNumber} of {num_pages}")
            self.restoreState()

    return NumberedCanvas


# ----------------------------------------------------------------------------
# Doc template with TOC + outline bookmark support
# ----------------------------------------------------------------------------
class ReportDocTemplate(BaseDocTemplate):
    _outline_last_level = -1

    def build(self, flowables, **kwargs):
        self._outline_last_level = -1
        return BaseDocTemplate.build(self, flowables, **kwargs)

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            style_name = flowable.style.name
            if style_name in ("Heading1", "Heading2"):
                text = flowable.getPlainText()
                level = 0 if style_name == "Heading1" else 1
                self.notify("TOCEntry", (level, text, self.page))
                key = f"bm-{self.page}-{abs(hash(text)) % 100000}"
                self.canv.bookmarkPage(key)
                outline_level = min(level, getattr(self, "_outline_last_level", -1) + 1)
                self._outline_last_level = outline_level
                self.canv.addOutlineEntry(text, key, level=outline_level, closed=False)


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def build_report_pdf(
    content,
    path,
    title=None,
    subtitle=None,
    prepared_for=None,
    prepared_by=None,
    date=None,
    executive_summary=None,
):
    """
    Renders `content` (markdown-ish text, e.g. straight from an LLM) into a
    PDF at `path`. Any metadata field left as None is auto-derived from the
    content (title, date) or simply omitted (prepared_for/by, executive
    summary) — never filled with placeholder text.
    """
    styles = build_styles()

    title, body, executive_summary = extract_title_and_summary(
        content, title=title, executive_summary=executive_summary
    )
    if date is None:
        date = datetime.now().strftime("%B %d, %Y")

    blocks = normalize_heading_levels(parse_markdown_blocks(body))
    has_headings = has_any_heading(blocks)
    # A "full report" gets the cover page + TOC treatment. A plain answer
    # with no headings and no report-style metadata (e.g. a chatbot response
    # being saved as a PDF) gets a compact single-flow document instead —
    # otherwise it'd be a near-empty title page followed by an empty TOC.
    is_full_report = has_headings or any([subtitle, prepared_for, prepared_by, executive_summary])

    doc = ReportDocTemplate(
        path, pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
        title=title,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="Main", frames=frame)])

    story = []
    story.append(gradient_bar(doc.width, height=8))
    story.append(Spacer(1, 22 if is_full_report else 16))
    title_style = styles["CoverTitle"] if is_full_report else ParagraphStyle(
        "CompactTitle", parent=styles["CoverTitle"], fontSize=22, leading=27, spaceAfter=6,
    )
    story.append(Paragraph(inline_markup(title), title_style))

    if not is_full_report:
        story.append(para(date, ParagraphStyle(
            "CompactDate", fontName="Helvetica", fontSize=9.5, textColor=MUTED_GRAY,
            spaceBefore=2, spaceAfter=18,
        )))
        story.extend(render_blocks(blocks, styles))
        doc.multiBuild(story, canvasmaker=make_numbered_canvas(title))
        return path

    # ---- Full cover page --------------------------------------------------
    story.append(Spacer(1, 14))
    if subtitle:
        story.append(para(subtitle, styles["CoverSubtitle"]))

    if prepared_for:
        story.append(para("Prepared for:", styles["Label"]))
        for line in prepared_for.split("\n"):
            story.append(para(line.strip(), styles["MetaLine"]))
        story.append(Spacer(1, 8))

    if prepared_by:
        story.append(para("Prepared by:", styles["Label"]))
        for line in prepared_by.split("\n"):
            story.append(para(line.strip(), styles["MetaLine"]))
        story.append(Spacer(1, 8))

    story.append(para("Date:", styles["Label"]))
    story.append(para(date, styles["MetaLine"]))

    if executive_summary:
        story.append(para("Executive Summary", styles["CoverHeading"]))
        story.append(para(executive_summary, styles["Body"]))

    story.append(para("Table of Contents", styles["CoverHeading"]))
    story.append(build_toc(styles))
    story.append(PageBreak())

    # ---- Body ---------------------------------------------------------------
    story.extend(render_blocks(blocks, styles))

    doc.multiBuild(story, canvasmaker=make_numbered_canvas(title))
    return path