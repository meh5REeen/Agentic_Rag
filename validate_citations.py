"""
Validation for citation mismatch + clickable-source fixes.

1. RRF must NOT conflate identical text from different documents.
2. Citation map index N must resolve to the correct document_id / viewer URL.
3. Frontend-style link resolution must prefer source_url, then viewer_url, then /docs/<id>.
"""
from langchain_core.documents import Document

from retrieval import reciprocal_rank_fusion
from pipeline import _build_citation_map, _serialize_ranked_docs, _viewer_url


SHARED_TEXT = (
    "AAP Clinical Report on Identification, Evaluation, and Management of "
    "Children With Autism Spectrum Disorder (2020, reaffirmed October 2025)."
)

UNIQUE_A = "Document A unique detail: screening at 18 and 24 months is recommended."
UNIQUE_B = "Document B unique detail: referral pathways differ by state Medicaid rules."


def make_doc(source, document_id, page, text, file_path=None):
    return Document(
        page_content=text,
        metadata={
            "source": source,
            "document_id": document_id,
            "page": page,
            "file_path": file_path or f"/uploads/{source}",
            "source_url": None,
        },
    )


def test_rrf_does_not_conflate_same_text_across_docs():
    doc_a = make_doc("aap_asd_report.pdf", 101, 1, SHARED_TEXT)
    doc_b = make_doc("unrelated_guidelines.pdf", 202, 1, SHARED_TEXT)

    # Two result lists both contain the shared text, but from different docs.
    list1 = [(doc_a, 0.1), (doc_b, 0.2)]
    list2 = [(doc_b, 0.15), (doc_a, 0.25)]

    fused = reciprocal_rank_fusion([list1, list2])
    sources = {(item["doc"].metadata["document_id"], item["doc"].metadata["source"]) for item in fused}

    assert (101, "aap_asd_report.pdf") in sources, f"Missing doc A in fused results: {sources}"
    assert (202, "unrelated_guidelines.pdf") in sources, f"Missing doc B in fused results: {sources}"
    assert len(fused) == 2, f"Expected 2 distinct docs after RRF, got {len(fused)}: {sources}"
    print("PASS: RRF keeps overlapping text from different documents as separate entries")


def test_citation_map_index_matches_source():
    docs = [
        make_doc("aap_asd_report.pdf", 101, 3, SHARED_TEXT + " " + UNIQUE_A),
        make_doc("unrelated_guidelines.pdf", 202, 8, SHARED_TEXT + " " + UNIQUE_B),
    ]
    citations = _build_citation_map(docs)

    assert citations[0]["index"] == 1
    assert citations[0]["document_id"] == 101
    assert citations[0]["source"] == "aap_asd_report.pdf"
    assert citations[0]["url"] == "/docs/101?page=3"
    assert citations[0]["fileUrl"] == "/docs/101?page=3"
    assert citations[0]["sourceFile"] == "aap_asd_report.pdf"

    assert citations[1]["index"] == 2
    assert citations[1]["document_id"] == 202
    assert citations[1]["source"] == "unrelated_guidelines.pdf"
    assert citations[1]["url"] == "/docs/202?page=8"

    # Sanity: [Document N] must not resolve to the other file.
    by_index = {c["index"]: c for c in citations}
    assert by_index[1]["source"] != by_index[2]["source"]
    print("PASS: citation map [Document N] -> correct document_id / /docs/<id>")


def test_serialize_exposes_viewer_url_for_clickable_sources():
    ranked = [
        {"doc": make_doc("aap_asd_report.pdf", 101, 3, UNIQUE_A), "score": 0.9},
        {"doc": make_doc("unrelated_guidelines.pdf", 202, 8, UNIQUE_B), "score": 0.7},
    ]
    serialized = _serialize_ranked_docs(ranked)

    assert serialized[0]["document_id"] == 101
    assert serialized[0]["viewer_url"] == "/docs/101?page=3"
    assert serialized[0]["source_url"] is None

    assert serialized[1]["document_id"] == 202
    assert serialized[1]["viewer_url"] == "/docs/202?page=8"

    # Emulate frontend fallback used by appendStreamingReferencedDocs
    for doc in serialized:
        href = doc.get("viewer_url") or doc.get("url") or doc.get("source_url") or (
            f"/docs/{doc['document_id']}" if doc.get("document_id") else ""
        )
        assert "page=" in href, f"Expected page-aware href for {doc}"
        assert href.startswith(f"/docs/{doc['document_id']}"), f"Expected clickable href for {doc}"
    print("PASS: Sources list can resolve clickable /docs/<document_id> links for local uploads")


def test_frontend_citation_link_resolution():
    citations = _build_citation_map([
        make_doc("aap_asd_report.pdf", 101, 3, UNIQUE_A),
        make_doc("unrelated_guidelines.pdf", 202, 8, UNIQUE_B),
    ])

    def resolve(match_num):
        mapped = next((c for c in citations if c["index"] == match_num), None)
        if not mapped:
            return None
        return mapped.get("fileUrl") or mapped.get("url") or (
            f"/docs/{mapped['document_id']}" if mapped.get("document_id") else None
        )

    # Old bug: href="/docs/$2" would open DB id == citation index (wrong).
    # New behavior: [Document 1] opens document_id 101 at page 3, not /docs/1.
    assert resolve(1) == "/docs/101?page=3"
    assert resolve(2) == "/docs/202?page=8"
    assert resolve(1) != "/docs/1"
    print("PASS: [Document N] links use citation map document_id, not positional DB id")


def test_viewer_url_includes_chunk_when_present():
    docs = [
        make_doc("report.pdf", 101, 4, UNIQUE_A),
    ]
    docs[0].metadata["chunk_index"] = 2
    citations = _build_citation_map(docs)
    assert citations[0]["chunkId"] == 2
    assert citations[0]["fileUrl"] == "/docs/101?page=4&chunk=2"
    assert _viewer_url(101, 4, 2) == "/docs/101?page=4&chunk=2"
    print("PASS: viewer URL includes page and chunk metadata")


def test_citation_map_missing_metadata_keeps_index():
    """Malformed/missing doc id should still emit citation row for frontend fallback."""
    doc = Document(page_content="orphan chunk", metadata={"source": "lost-not-in-db-xyz.pdf", "page": 1})
    citations = _build_citation_map([doc])
    assert citations[0]["index"] == 1
    # May still be None if filename is not registered in documents table.
    assert citations[0]["source"] == "lost-not-in-db-xyz.pdf"
    print("PASS: citation rows without DB match still keep index/source for frontend")


def test_resolve_document_id_from_filename_when_missing():
    """Batch-ingested chunks often lack document_id; resolve via source filename."""
    from pipeline import _resolve_document_id
    from unittest.mock import patch

    md = {
        "source": "peds_20192528.pdf",
        "page": 2,
        "file_path": "documents\\peds_20192528.pdf",
    }
    with patch("pipeline.get_document_id_by_filename", return_value=8):
        assert _resolve_document_id(md) == 8
        citations = _build_citation_map([
            Document(page_content="chunk", metadata=md),
        ])
    assert citations[0]["document_id"] == 8
    assert citations[0]["fileUrl"] == "/docs/8?page=2"
    print("PASS: missing document_id resolves via filename lookup for clickable URLs")


def test_citation_group_regex_extracts_indexes():
    import re
    group_re = re.compile(r"\[(?:(?:Document|Doc)\s+\d+(?:\s*,\s*)?)+\]", re.I)
    index_re = re.compile(r"(?:Document|Doc)\s+(\d+)", re.I)
    text = "See [Document 1, Document 2, Document 3] for details."
    match = group_re.search(text)
    assert match, "grouped citation should match"
    indexes = [int(m.group(1)) for m in index_re.finditer(match.group(0))]
    assert indexes == [1, 2, 3]
    print("PASS: grouped [Document 1, Document 2, Document 3] parses to indexes")


if __name__ == "__main__":
    test_rrf_does_not_conflate_same_text_across_docs()
    test_citation_map_index_matches_source()
    test_serialize_exposes_viewer_url_for_clickable_sources()
    test_frontend_citation_link_resolution()
    test_viewer_url_includes_chunk_when_present()
    test_citation_map_missing_metadata_keeps_index()
    test_resolve_document_id_from_filename_when_missing()
    test_citation_group_regex_extracts_indexes()
    print("\nAll citation validation checks passed.")
