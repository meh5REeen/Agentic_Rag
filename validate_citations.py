"""
Validation for citation mismatch + clickable-source fixes.

1. RRF must NOT conflate identical text from different documents.
2. Citation map index N must resolve to the correct document_id / viewer URL.
3. Frontend-style link resolution must prefer source_url, then viewer_url, then /docs/<id>.
"""
from langchain_core.documents import Document

from retrieval import reciprocal_rank_fusion
from pipeline import _build_citation_map, _serialize_ranked_docs


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
    assert citations[0]["url"] == "/docs/101"

    assert citations[1]["index"] == 2
    assert citations[1]["document_id"] == 202
    assert citations[1]["source"] == "unrelated_guidelines.pdf"
    assert citations[1]["url"] == "/docs/202"

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
    assert serialized[0]["viewer_url"] == "/docs/101"
    assert serialized[0]["source_url"] is None

    assert serialized[1]["document_id"] == 202
    assert serialized[1]["viewer_url"] == "/docs/202"

    # Emulate frontend fallback used by appendStreamingReferencedDocs
    for doc in serialized:
        href = doc.get("url") or doc.get("source_url") or doc.get("viewer_url") or (
            f"/docs/{doc['document_id']}" if doc.get("document_id") else ""
        )
        assert href == f"/docs/{doc['document_id']}", f"Expected clickable href for {doc}"
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
        return mapped.get("url") or (
            f"/docs/{mapped['document_id']}" if mapped.get("document_id") else None
        )

    # Old bug: href="/docs/$2" would open DB id == citation index (wrong).
    # New behavior: [Document 1] opens document_id 101, not /docs/1.
    assert resolve(1) == "/docs/101"
    assert resolve(2) == "/docs/202"
    assert resolve(1) != "/docs/1"
    print("PASS: [Document N] links use citation map document_id, not positional DB id")


if __name__ == "__main__":
    test_rrf_does_not_conflate_same_text_across_docs()
    test_citation_map_index_matches_source()
    test_serialize_exposes_viewer_url_for_clickable_sources()
    test_frontend_citation_link_resolution()
    print("\nAll citation validation checks passed.")
