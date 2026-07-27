# slim-bookworm is pinned explicitly: it ships sqlite3 >= 3.40, which ChromaDB
# requires to even start. Older "bullseye"-based slim images ship sqlite 3.34
# and Chroma will throw "unsupported version of sqlite3" on boot.
FROM python:3.11-slim-bookworm

WORKDIR /app

# System deps:
# - build-essential + libpq-dev -> only needed if you're on plain `psycopg2`
#   (not `psycopg2-binary`). Safe to remove libpq-dev if you use the binary wheel.
# - poppler-utils + tesseract-ocr -> only needed if ingestion.py does PDF/OCR
#   parsing (pypdf/unstructured/pdf2image etc). Remove if you don't need it.
# - curl -> handy for debugging/healthchecks from inside the container.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    poppler-utils \
    tesseract-ocr \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# gunicorn is used to run the app in the container — add it to requirements.txt
# if it's not already there, or uncomment the line below.
# RUN pip install --no-cache-dir gunicorn

COPY . .

RUN mkdir -p /app/chroma_db /app/documents \
    && chmod +x /app/entrypoint.sh \
    && useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "3", "--timeout", "120", "app:app"]