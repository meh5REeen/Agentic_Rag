import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)

FILE_KEYWORDS = [
    # Generic file requests
    "file",
    "document",
    "download",
    "downloadable",
    "export",
    "save as",
    "generate file",
    "create file",
    "make file",

    # PDF
    "pdf",
    ".pdf",
    "generate pdf",
    "create pdf",
    "make pdf",
    "save as pdf",
    "export pdf",

    # Word
    "word",
    "doc",
    "docx",
    ".doc",
    ".docx",
    "word document",
    "microsoft word",
    "generate document",
    "create document",
    "write a document",
    "save as word",

    # Text
    "txt",
    ".txt",
    "text file",
    "plain text",
    "generate text file",
    "save as txt",

    # Excel
    "excel",
    "xlsx",
    "xls",
    ".xlsx",
    ".xls",
    "spreadsheet",
    "workbook",
    "csv",
    "table",
    "export to excel",
    "save as excel",
    "generate spreadsheet",

    # Common document requests
    "report",
    "research report",
    "analysis report",
    "summary",
    "notes",
    "assignment",
    "essay",
    "invoice",
    "resume",
    "cv",
    "letter",
    "form",
    "template"
]

FILE_ACTIONS = {
    "create", "generate", "make", "write",
    "save", "export", "download", "convert"
}

FILE_FORMATS = {
    "pdf", "doc", "docx", "word",
    "txt", "text",
    "excel", "xlsx", "xls", "spreadsheet", "csv"
}
def detect_file_request(query):
    q = query.lower()

    result = {
        "generate": False,
        "file_type": "",
        "title": "",
        "description": ""
    }

    # Detect file type
    if any(k in q for k in ["pdf", ".pdf"]):
        result["file_type"] = "pdf"

    elif any(k in q for k in ["docx", "doc", "word", "document"]):
        result["file_type"] = "docx"

    elif any(k in q for k in ["txt", "text file", "plain text"]):
        result["file_type"] = "txt"

    elif any(k in q for k in ["excel", "spreadsheet", "xlsx", "xls", "csv"]):
        result["file_type"] = "xlsx"

    # If a file type was detected, it's a file request
    if result["file_type"]:
        result["generate"] = True

        # Optional defaults
        result["title"] = "Generated File"
        result["description"] = f"A {result['file_type'].upper()} file generated from the user's request."

    return result

# def detect_file_request(user_query):

#     prompt = f"""
# You are a file generation intent detector.

# Analyze the user's request.

# Determine whether the user wants a file created.

# Return ONLY valid JSON.

# Allowed file types:
# - pdf
# - docx
# - pptx
# - xlsx
# - none

# Return format:

# {{
#     "generate": true,
#     "file_type": "pdf",
#     "title": "",
#     "description": ""
# }}

# User request:
# {user_query}
# """

#     response = client.chat.completions.create(
#         model="qwen/qwen3.6-27b",
#         temperature=0,
#         response_format={
#             "type": "json_object"
#         },
#         messages=[
#             {
#                 "role": "system",
#                 "content": "You are an intent classifier."
#             },
#             {
#                 "role": "user",
#                 "content": prompt
#             }
#         ]
#     )

#     return json.loads(
#         response.choices[0].message.content
#     )

if __name__ == "__main__":
    from file_detector import detect_file_request


query = "Create a pdf about RAG systems"

result = detect_file_request(query)

print(result)