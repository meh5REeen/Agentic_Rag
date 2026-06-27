import chromadb
import os
from dotenv import load_dotenv
from langchain_community.document_loaders import (
    Docx2txtLoader,
    TextLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
import fitz
from langchain_core.documents import Document
from openai import OpenAI
import base64
load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PERSIST_DIR = os.path.join(BASE_DIR, "chroma_db")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
def extract_text_only(page, page_num, filename):
    text = page.get_text()
    return Document(
        page_content=text,
        metadata={
            "source": filename,
            "page": page_num + 1,
            "type": "text_only"
        }
    )

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
            doc = extract_with_vision(page,page_num,filename)
            vision_pages += 1
        else:
            print(f"Page {page_num + 1} text only.")
            doc = extract_text_only(page,page_num,filename)
            text_pages += 1
        
        documents.append(doc)

    pdf.close()
    print(f"Text pages:{text_pages}")
    print(f"Vision Pages:{vision_pages}")
    return documents

def extract_with_vision(page, page_num, filename):
    mat = fitz.Matrix(2, 2)
    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("png")
    base64_image = base64.b64encode(img_bytes).decode("utf-8")
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}",
                            "detail": "high"
                        }
                    },
                    {
                        "type": "text",
                        "text": """Extract ALL content from this page completely and accurately.

Include:
- All text exactly as written
- Describe any images, diagrams, charts, or figures in detail
- Transcribe any tables with their structure
- Describe any graphs with their data points and labels
- Note any visual elements that carry meaning

Format your response as:
TEXT CONTENT:
[all text from the page]

VISUAL CONTENT:
[detailed description of all images, diagrams, charts, tables]"""
                    }
                ]
            }
        ],
        max_tokens=2000
    )
    
    content = response.choices[0].message.content
    
    return Document(
        page_content=content,
        metadata={
            "source": filename,
            "page": page_num + 1,
            "type": "vision"
        }
    )

def load_docs(path='documents'):
    if path is None:
        path = os.path.join(BASE_DIR, "documents")
    documents=[]

    for filename in os.listdir(path):
        file_path = os.path.join(path,filename)
        loader = None
        if filename.endswith(".pdf"):
            loaded = load_pdf_smart(file_path)
        elif filename.endswith(".docx"):
            loader = Docx2txtLoader(file_path)
            loaded = loader.load()
        elif filename.endswith(".txt"):
            loader = TextLoader(file_path)
            loaded = loader.load()
        else:
            print(f"Skipping unsupported file: {filename}")
            continue

        
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
    print(f"/nTotal chunks created: {len(chunks)}")
    return chunks

def embed_in_chroma(chunks):
    embedding_model = OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key= os.getenv("OPENAI_API_KEY")
        
    )
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

