import os
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

load_dotenv()

def load_vectorstore():
    embedding_model = OpenAIEmbeddings(
        model="text-embedding-ada-002",
        openai_api_key=os.getenv("OPENAI_API_KEY")
    )
    
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
    
    # results is a list of (Document, score) tuples
    # lower score = more similar (it's a distance metric)
    
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
            # use page content as the unique key
            doc_key = doc.page_content
            
            if doc_key not in scores:
                scores[doc_key] = {
                    "doc": doc,
                    "score": 0
                }
            
            # RRF formula: 1 / (rank + k)
            # rank 0 (best) gets highest score
            scores[doc_key]["score"] += 1 / (rank + k)
    
    # sort by final RRF score, highest first
    reranked = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    
    return reranked


def rerank_results(vectorstore, query, top_k=10, final_k=5):
    # ── Search 1: semantic similarity search ──
    semantic_results = vectorstore.similarity_search_with_score(
        query=query,
        k=top_k
    )
    
    # ── Search 2: keyword based search ──
    # break query into keywords and search for exact matches
    keywords = " ".join([
        word for word in query.split()
        if len(word) > 3        # ignore short words like "the", "is", "of"
    ])
    
    keyword_results = vectorstore.similarity_search_with_score(
        query=keywords,
        k=top_k
    )
    
    # ── Combine with RRF ──
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
    embedding_model = OpenAIEmbeddings(
        model="text-embedding-ada-002",
        openai_api_key=os.getenv("OPENAI_API_KEY")
    )
    vectorstore = Chroma(
        persist_directory="./chroma_db",
        embedding_function=embedding_model
    )
    print("Connected to ChromaDB")
    return vectorstore


def reciprocal_rank_fusion(results_list, k=60):
    scores = {}
    
    for results in results_list:
        for rank, (doc, _) in enumerate(results):
            doc_key = doc.page_content
            
            if doc_key not in scores:
                scores[doc_key] = {
                    "doc": doc,
                    "score": 0
                }
            
            scores[doc_key]["score"] += 1 / (rank + k)
    
    reranked = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    return reranked


def retrieve(vectorstore, query, top_k=10, final_k=5):
    print(f"\nRetrieving for query: '{query}'")
    
    # search 1: semantic
    semantic_results = vectorstore.similarity_search_with_score(
        query=query,
        k=top_k
    )
    
    # search 2: keyword
    keywords = " ".join([
        word for word in query.split()
        if len(word) > 3
    ])
    keyword_results = vectorstore.similarity_search_with_score(
        query=keywords,
        k=top_k
    )
    
    # rerank
    reranked = reciprocal_rank_fusion([semantic_results, keyword_results])
    top_results = reranked[:final_k]
    
    print(f"\nTop {len(top_results)} chunks after reranking:")
    for i, item in enumerate(top_results):
        doc = item["doc"]
        print(f"  {i+1}. source={doc.metadata.get('source')} | page={doc.metadata.get('page')} | score={item['score']:.4f}")
    
    # return just the Document objects for the rest of the pipeline
    return [item["doc"] for item in top_results]


if __name__ == "__main__":
    vectorstore = load_vectorstore()
    
    query = "How does the reranking work?"
    results = retrieve(vectorstore, query)
    
    print("\n── Retrieved Chunks ──")
    for i, doc in enumerate(results):
        print(f"\nChunk {i+1}:")
        print(f"Source: {doc.metadata.get('source')} | Page: {doc.metadata.get('page')}")
        print(f"Content: {doc.page_content[:200]}...")