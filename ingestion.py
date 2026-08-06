import os
import base64
from dotenv import load_dotenv
from langchain_community.document_loaders import (
    Docx2txtLoader,
    TextLoader,
)
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
import fitz
from langchain_core.documents import Document
from llm_client import call_gemini
from openai import OpenAI
import whisper

load_dotenv()

OLLAMA_EMBEDDING_API_BASE = os.getenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:8b")
QWEN_VISION_MODEL = os.getenv("QWEN_VISION_MODEL", "qwen/qwen3.6-27b")
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY"),
)
def get_embedding_model():
    embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-base-en-v1.5",
    model_kwargs={
        "device":"cpu"
    },
    encode_kwargs={
        "normalize_embeddings":True,
        "batch_size":64
    }
    )
    return embeddings
    
from db import (
    ensure_document_tables,
    register_document,
    register_document_chunk,
    get_document_id_by_filename,
)
from graph_store import get_graph_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PERSIST_DIR = os.path.join(BASE_DIR, "chroma_db")
def scope_metadata(project_id=None):
    return {
        "project_id": project_id if project_id else "general",
        "scope": "project" if project_id else "general",
    }

def persist_document_to_db_and_graph(file_path, filename, project_id=None, title=None, uploaded_by_user_id=None, source_url=None, storage_type="local", metadata=None):
    ensure_document_tables()
    graph_store = get_graph_store()
    try:
        graph_store.ensure_constraints()
        document_id = register_document(
            uploaded_by_user_id=uploaded_by_user_id,
            filename=filename,
            file_path=file_path,
            title=title,    
            mime_type="application/pdf",
            source_url=source_url,
            storage_type=storage_type,
            metadata=metadata or {},
        )
        graph_store.create_document(
            doc_id=str(document_id),
            title=title or os.path.splitext(filename)[0],
            filename=filename,
            file_path=file_path,
            source_url=source_url,
            metadata=metadata or {},
        )
        return document_id
    finally:
        graph_store.close()


def persist_document_chunks(document_id, chunks):
    for idx, chunk in enumerate(chunks):
        metadata = dict(chunk.metadata or {})
        register_document_chunk(
            document_id=document_id,
            chunk_index=idx,
            page_number=metadata.get("page"),
            text=chunk.page_content,
            metadata=metadata,
        )

    graph_store = get_graph_store()
    try:
        graph_store.ensure_constraints()
        for idx, chunk in enumerate(chunks):
            metadata = dict(chunk.metadata or {})
            graph_store.add_chunk(str(document_id), idx, metadata.get("page"), chunk.page_content, metadata)
    finally:
        graph_store.close()
def extract_text_only(page, page_num, filename, filepath):
    text = page.get_text()
    return Document(
        page_content=text,
        metadata={
            "source": filename,
            "file_path": filepath,
            "page": page_num + 1,
            "type": "text_only"
        }
    )


def transcribe_audio(filepath, filename):
    print(f"Transcribing: {filename}")
    
    model = whisper.load_model("base")
    result = model.transcribe(filepath)
    
    documents = []
    segments = result.get("segments", [])
    
    if not segments:
        return [Document(
            page_content=result["text"].strip(),
            metadata={"source": filename, "page": 1, "type": "transcript"}
        )]
    
    current_text = ""
    current_start = 0
    chunk_num = 1
    
    for seg in segments:
        current_text += " " + seg["text"].strip()
        
        if len(current_text) >= 1000:
            documents.append(Document(
                page_content=current_text.strip(),
                metadata={
                    "source": filename,
                    "page": chunk_num,           
                    "timestamp_start": current_start,
                    "timestamp_end": seg["end"],
                    "type": "transcript"
                }
            ))
            current_text = ""
            current_start = seg["end"]
            chunk_num += 1
    
    # Remaining text
    if current_text.strip():
        documents.append(Document(
            page_content=current_text.strip(),
            metadata={
                "source": filename,
                "file_path": filepath,
                "page": chunk_num,
                "timestamp_start": current_start,
                "type": "transcript"
            }
        ))
    
    print(f"  → {len(documents)} transcript chunks from {filename}")
    return documents

def page_has_meaningful_images(page):
    images = page.get_images(full=True)
    
    if not images:
        return False
    
    page_area = page.rect.width * page.rect.height
    
    for img in images:
        xref = img[0]
        img_rect = page.get_image_rects(xref)
        
        if img_rect:
            img_area = img_rect[0].width * img_rect[0].height
            if img_area / page_area > 0.25:
                return True
    
    return False

def load_pdf_smart(filepath):
    filename = os.path.basename(filepath)
    print(f"Processing pdf:")
    documents = []
    pdf = fitz.open(filepath)
    vision_pages=0
    text_pages=0
    for page_num in range(len(pdf)):
        page = pdf[page_num]


        if page_has_meaningful_images(page):
            print(f"Page {page_num + 1} : has images ")
            # doc = extract_with_vision(page, page_num, filename, filepath)
            doc= Document(
                page_content="This page contains images. Vision extraction is not implemented yet.",
                metadata={
                    "source": filename,
                    "file_path": filepath,
                    "page": page_num + 1,
                    "type": "vision"
                }
            )
            vision_pages += 1
        else:
            print(f"Page {page_num + 1} text only.")
            doc = extract_text_only(page, page_num, filename, filepath)
            text_pages += 1
        
        documents.append(doc)

    pdf.close()
    print(f"Text pages:{text_pages}")
    print(f"Vision Pages:{vision_pages}")
    return documents

# def extract_with_vision(page, page_num, filename, filepath):
#     pass
#     mat = fitz.Matrix(2, 2)
#     pix = page.get_pixmap(matrix=mat)
#     img_bytes = pix.tobytes("png")
#     base64_image = base64.b64encode(img_bytes).decode("utf-8")
    
#     response = client.chat.completions.create(
#         model="gpt-4o",
#         messages=[
#             {
#                 "role": "user",
#                 "content": [
#                     {
#                         "type": "image_url",
#                         "image_url": {
#                             "url": f"data:image/png;base64,{base64_image}",
#                             "detail": "high"
#                         }
#                     },
#                     {
#                         "type": "text",
#                         "text": """Extract ALL content from this page completely and accurately.

# Include:
# - All text exactly as written
# - Describe any images, diagrams, charts, or figures in detail
# - Transcribe any tables with their structure
# - Describe any graphs with their data points and labels
# - Note any visual elements that carry meaning

# Format your response as:
# TEXT CONTENT:
# [all text from the page]

# VISUAL CONTENT:
# [detailed description of all images, diagrams, charts, tables]"""
#                     }
#                 ]
#             }
#         ],
#         max_tokens=2000
#     )
    
#     content = response.choices[0].message.content
    
#     return Document(
#         page_content=content,
#         metadata={
#             "source": filename,
#             "file_path": filepath,
#             "page": page_num + 1,
#             "type": "vision"
#         }
#     )


def extract_with_vision(page, page_num, filename, filepath):
     pass

#     mat = fitz.Matrix(2, 2)
#     pix = page.get_pixmap(matrix=mat)

#     img_bytes = pix.tobytes("png")
#     base64_image = base64.b64encode(img_bytes).decode()

#     prompt = f"""
# Extract ALL content from this page completely and accurately.

# Include:
# - All text exactly as written
# - Describe any images, diagrams, charts, or figures in detail
# - Transcribe any tables with their structure
# - Describe any graphs with their data points and labels
# - Note any visual elements that carry meaning


# Format your response as:

# VISUAL CONTENT:
# [detailed description of all images, diagrams, charts, tables]
# """
    
#     completion = client.chat.completions.create(
#         model=QWEN_VISION_MODEL,
#         messages=[
#             {
#                 "role": "user",
#                 "content": [
#                     {
#                         "type": "text",
#                         "text": prompt,
#                     },
#                     {
#                         "type": "image_url",
#                         "image_url": {
#                             # This is NOT an internet URL.
#                             # It is a Base64 data URI understood by vision models.
#                             "url": f"data:image/png;base64,{base64_image}"
#                         },
#                     },
#                 ],
#             }
#         ],
        
#         temperature=0,
#         max_tokens=2000,
#         extra_body={
#             "enable_thinking": False
#         }
#     )
#     content = completion.choices[0].message.content

#     if not content:
#         print("FULL RESPONSE:")
#         print(completion)

#     content = "No information extracted from this image."
#     return Document(
#         page_content=content,
#         metadata={
#             "source": filename,
#             "file_path": filepath,
#             "page": page_num + 1,
#             "type": "vision",
#         },
#     )

def ingest_uploaded_file(file_path, filename, project_id=None, uploaded_by_user_id=None, vectorstore=None):
    """
    Process a single uploaded file: load -> tag scope metadata -> register
    document row -> chunk -> embed into Chroma -> register chunk rows.
    Returns the new document_id.
    """
    if filename.endswith(".pdf"):
        loaded = load_pdf_smart(file_path)
    elif filename.endswith(".docx"):
        loaded = Docx2txtLoader(file_path).load()
    elif filename.endswith(".txt"):
        loaded = TextLoader(file_path).load()
    elif filename.endswith((".mp4", ".mp3", ".wav")):
        loaded = transcribe_audio(file_path, filename)
    else:
        raise ValueError(f"Unsupported file type: {filename}")

    for doc in loaded:
        doc.metadata = dict(doc.metadata or {})
        doc.metadata.setdefault("file_path", file_path)
        doc.metadata.update(scope_metadata(project_id))   # project_id / scope, never null

    document_id = persist_document_to_db_and_graph(
        file_path, filename,
        project_id=project_id,
        title=os.path.splitext(filename)[0],
        uploaded_by_user_id=uploaded_by_user_id,
    )

    chunks = chunk_docs(loaded)
    for idx, chunk in enumerate(chunks):
        chunk.metadata = dict(chunk.metadata or {})
        chunk.metadata["document_id"] = document_id   # so retrieved chunks trace back to this doc
        chunk.metadata["chunk_index"] = idx

    if vectorstore is None:
        embedding_model = get_embedding_model()
        vectorstore = Chroma(persist_directory=PERSIST_DIR, embedding_function=embedding_model)

    vectorstore.add_documents(chunks)
    persist_document_chunks(document_id, chunks)

    return document_id

def load_docs(path='documents', project_id=None, uploaded_by_user_id=None):
    if path is None:
        path = os.path.join(BASE_DIR, "documents")
    documents = []

    for filename in os.listdir(path):
        file_path = os.path.join(path, filename)
        loader = None
        if filename.endswith(".pdf"):
            loaded = load_pdf_smart(file_path)
        elif filename.endswith(".docx"):
            loader = Docx2txtLoader(file_path)
            loaded = loader.load()
        elif filename.endswith(".txt"):
            loader = TextLoader(file_path)
            loaded = loader.load()
        elif filename.endswith((".mp4", ".mp3", ".wav")):
            loaded = transcribe_audio(file_path, filename)
        else:
            print(f"Skipping unsupported file: {filename}")
            continue

        existing_doc_id = get_document_id_by_filename(filename)
        if existing_doc_id is None:
            existing_doc_id = persist_document_to_db_and_graph(
                file_path, filename,
                title=os.path.splitext(filename)[0],
                uploaded_by_user_id=uploaded_by_user_id,
                project_id=project_id,
            )

        for doc in loaded:
            doc.metadata = dict(doc.metadata or {})
            doc.metadata.setdefault("file_path", file_path)
            doc.metadata.update(scope_metadata(project_id))   # <-- never null now
            if existing_doc_id is not None:
                doc.metadata["document_id"] = existing_doc_id

        documents.extend(loaded)
        print(f"Loaded:{filename} ({len(loaded)} pages/sections)")

    return documents

def chunk_docs(documents):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100,
        length_function=len
    )

    chunks = splitter.split_documents(documents)
    print(f"\nTotal chunks created: {len(chunks)}")
    return chunks

def embed_in_chroma(chunks):
    embedding_model = get_embedding_model()
    vector_store = Chroma.from_documents(
        documents=chunks,
        embedding=embedding_model,
        persist_directory="./chroma_db"
    )
    print(f"Successfully stored {len(chunks)} chunks in ChromaDB")
    return vector_store

if __name__ == "__main__":
    docs = load_docs()
    chunks = chunk_docs(docs)
    embed_in_chroma(chunks)

