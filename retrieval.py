import os
from dotenv import load_dotenv
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

load_dotenv()

OLLAMA_EMBEDDING_API_BASE = os.getenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")

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
def load_vectorstore():
    embedding_model = get_embedding_model()

    vectorstore = Chroma(
        persist_directory="./chroma_db",
        embedding_function=embedding_model
    )
    
    print("Connected to ChromaDB")
    return vectorstore

def vector_search(vectorstore, query, top_k=10):
    results = vectorstore.similarity_search_with_score(
        query=query,
        k=top_k
    )
    
    
    print(f"\nVector search returned {len(results)} chunks")
    for i, (doc, score) in enumerate(results):
        print(f"  Chunk {i+1}: score={score:.4f} | source={doc.metadata.get('source')} | page={doc.metadata.get('page')}")
    
    return results

def reciprocal_rank_fusion(results_list, k=60):
    """
    results_list: a list of lists, each inner list is ranked search results
    k: constant that controls influence of lower-ranked results (default 60)
    """
    scores = {}
    
    for results in results_list:
        for rank, (doc, _) in enumerate(results):
            doc_key = doc.page_content
            
            if doc_key not in scores:
                scores[doc_key] = {
                    "doc": doc,
                    "score": 0
                }
            
            
            scores[doc_key]["score"] += 1 / ((rank+1) + k)
    
    reranked = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    
    return reranked


def rerank_results(vectorstore, query, top_k=10, final_k=5):
    semantic_results = vectorstore.similarity_search_with_score(
        query=query,
        k=top_k
    )
    
    
    keywords = " ".join([
        word for word in query.split()
        if len(word) > 3        
    ])
    
    keyword_results = vectorstore.similarity_search_with_score(
        query=keywords,
        k=top_k
    )
    
    reranked = reciprocal_rank_fusion([semantic_results, keyword_results])
    
    # return only top final_k results
    top_results = reranked[:final_k]
    
    print(f"\nAfter reranking, top {len(top_results)} chunks selected:")
    for i, item in enumerate(top_results):
        doc = item["doc"]
        score = item["score"]
        print(f"  Chunk {i+1}: RRF score={score:.4f} | source={doc.metadata.get('source')} | page={doc.metadata.get('page')}")
    
    return top_results


def load_vectorstore():
    embedding_model = get_embedding_model()

    vectorstore = Chroma(
        persist_directory="./chroma_db",
        embedding_function=embedding_model
    )
    print("Connected to ChromaDB")
    return vectorstore


def _retrieve_ranked(vectorstore, query, top_k=10, final_k=5, project_id=None):
    print(f"\nRetrieving for query: '{query}'")
    where_filter = {"project_id": project_id if project_id else "general"}

    semantic_results = vectorstore.similarity_search_with_score(
        query=query, k=top_k, filter=where_filter
    )

    keywords = " ".join([word for word in query.split() if len(word) > 3])
    keyword_results = vectorstore.similarity_search_with_score(
        query=keywords, k=top_k, filter=where_filter
    )

    reranked = reciprocal_rank_fusion([semantic_results, keyword_results])
    top_results = reranked[:final_k]

    print(f"\nTop {len(top_results)} chunks after reranking:")
    for i, item in enumerate(top_results):
        doc = item["doc"]
        print(f"  {i+1}. source={doc.metadata.get('source')} | page={doc.metadata.get('page')} | score={item['score']:.4f}")

    return top_results


def retrieve(vectorstore, query, top_k=10, final_k=5, project_id=None):
    top_results = _retrieve_ranked(vectorstore, query, top_k=top_k, final_k=final_k, project_id=project_id)
    return [item["doc"] for item in top_results]

def retrieve_with_scores(vectorstore, query, top_k=10, final_k=5, project_id=None):
    """
    Same retrieval as `retrieve`, but also returns the RRF score for each
    chunk so callers (e.g. the pipeline trace) can show why a chunk was picked.

    Returns a list of {"doc": Document, "score": float}, ordered best-first.
    """
    return _retrieve_ranked(vectorstore, query, top_k=top_k, final_k=final_k, project_id=project_id)


if __name__ == "__main__":
    vectorstore = load_vectorstore()
    
    query = "WHat is meant by Mobile Security?"
    results = retrieve(vectorstore, query)
    
    print("\n   Retrieved Chunks    ")
    for i, doc in enumerate(results):
        print(f"\nChunk {i+1}:")
        print(f"Source: {doc.metadata.get('source')} | Page: {doc.metadata.get('page')}")
        print(f"Content: {doc.page_content[:200]}...")