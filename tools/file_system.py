import asyncio

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


server_params = StdioServerParameters(
    command="npx",
    args=[
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "E:/agentic_rag"
    ]
)


async def filesystem_call(tool_name, arguments):

    async with stdio_client(server_params) as (read, write):

        async with ClientSession(read, write) as session:

            await session.initialize()

            result = await session.call_tool(
                tool_name,
                arguments
            )

            return result


import os
from dotenv import load_dotenv
from groq import Groq

from docx import Document

# NEW: the formatted PDF builder (cover page, colored headings, auto TOC,
# tables, page numbers) lives in pdf_generator.py, sitting next to this file.
from pdf_generator import build_report_pdf

load_dotenv()


client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)


def generate_content(query, content=None):

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        temperature=0.3,
        messages=[
            {
                "role": "system",
                "content": """
                You are a professional document writer.
                Generate well-structured content suitable for a formal business report.
                If reference content is provided, use it as the basis for the document.
                Reference content: {content}

                Formatting rules (Markdown):
                - Start with a single '# Title' line for the document title.
                - Use '## Section' for main sections and '### Subsection' for
                  subsections where useful.
                - Use '-' for bullet lists and '1.' for ordered/sequential steps.
                - Use **bold** for emphasis, and Markdown pipe tables
                  (| col | col |) for any tabular/comparison data.
                - Do not include page numbers, a table of contents, or a
                  cover page yourself — those are generated automatically.
                """.replace("{content}", content or "")
            },
            {
                "role": "user",
                "content": query
            }
        ]
    )

    return response.choices[0].message.content


def create_pdf(
        content,
        filename,
        title=None,
        subtitle=None,
        prepared_for=None,
        prepared_by=None,
        date=None,
        executive_summary=None,
):
    """
    Renders `content` (Markdown-ish text, typically straight from
    generate_content) into a branded, multi-page PDF: gradient header bar,
    colored headings, an automatic table of contents with real page
    numbers, tables, lists, and a running footer.

    Every metadata field beyond `content` and `filename` is optional —
    anything left as None is simply auto-derived from the content (title,
    date) or omitted from the cover page (prepared_for/by, executive
    summary) rather than filled with placeholder text.
    """

    path = f"generated_files/{filename}.pdf"

    os.makedirs("generated_files", exist_ok=True)

    build_report_pdf(
        content,
        path,
        title=title,
        subtitle=subtitle,
        prepared_for=prepared_for,
        prepared_by=prepared_by,
        date=date,
        executive_summary=executive_summary,
    )

    return path


def create_docx(content, filename):

    path = f"generated_files/{filename}.docx"

    os.makedirs(
        "generated_files",
        exist_ok=True
    )

    doc = Document()

    doc.add_paragraph(content)

    doc.save(path)

    return path


def generate_file(
        file_type,
        query,
        filename="generated_document",
        content=None,
        **metadata,
):
    """
    metadata (pdf only, all optional): title, subtitle, prepared_for,
    prepared_by, date, executive_summary.
    """

    if content is None:
        content = generate_content(query)

    if file_type == "pdf":

        return create_pdf(
            content,
            filename,
            **metadata,
        )

    elif file_type == "docx":

        return create_docx(
            content,
            filename
        )

    else:
        raise ValueError(
            "Unsupported file type"
        )