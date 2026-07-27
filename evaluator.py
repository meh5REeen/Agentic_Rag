import os
import json
from dotenv import load_dotenv
from langchain_core.documents import Document
from llm_client import call_gemini

load_dotenv()

EVALUATOR_MODEL = {
    "name": os.getenv("EVALUATOR_MODEL", "llama-3.1-8b-instant")
}


def call_llm(url, model_name, messages, temperature=0, max_tokens=300):
    return call_gemini(
        model_name=model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def format_chunks(retrieved_docs):
    formatted = []
    for i, doc in enumerate(retrieved_docs):
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page", "?")
        formatted.append(
            f"Chunk {i+1} (source: {source}, page: {page}):\n{doc.page_content}"
        )
    return "\n\n".join(formatted)


def evaluate_documents(original_query, rewritten_query, retrieved_docs):
    """
    Evaluates whether retrieved documents are relevant and sufficient.
    
    Returns:
        dict: {
            "relevant": True/False,
            "feedback": "explanation of why docs are not relevant"
        }
    """
    
    print(f"\nEvaluating {len(retrieved_docs)} retrieved chunks...")
    
    formatted_chunks = format_chunks(retrieved_docs)
    
    messages = [
    {
        "role": "system",
        "content": """You are a document relevance evaluator for a RAG system over technical documents.

You must respond in this exact JSON format and nothing else:
{
    "relevant": true or false,
    "feedback": "your explanation here"
}

Mark relevant=true if ANY of these apply:
- The documents discuss the same general topic as the query (e.g. BYOD, mobile security, enterprise devices)
- The documents contain partial information that helps answer the query
- The documents provide related context, policies, or technical details on the subject
- The documents are from the same domain even if they don't answer the exact question word-for-word

Mark relevant=false ONLY if ALL of these apply:
- The documents are completely off-topic with zero connection to the query
- No chunk has any overlap with the subject matter whatsoever

Important rules:
- A mobile security document IS relevant to BYOD, MDM, enterprise data, device policies, and related queries
- Do NOT require an exact match — topical relevance is sufficient
- Do NOT reject chunks just because they lack one specific detail the query asks for
- If at least 1 out of 5 chunks is on-topic, mark relevant=true

Always provide specific feedback explaining your decision.
Return ONLY the JSON object, no extra text."""
    },
    {
        "role": "user",
        "content": f"""Original Query: {original_query}
Rewritten Query: {rewritten_query}

Retrieved Documents:
{formatted_chunks}

Are these documents relevant and sufficient to answer the query?
Respond in JSON format only."""
    }
]

    result = call_llm(
        url=EVALUATOR_MODEL.get("url"),
        model_name=EVALUATOR_MODEL["name"],
        messages=messages,
        temperature=0,
        max_tokens=300
    )
    
    try:
        clean = result.replace("```json", "").replace("```", "").strip()
        evaluation = json.loads(clean)
        
        relevant = evaluation.get("relevant", False)
        feedback = evaluation.get("feedback", "No feedback provided")
        
        print(f"  Relevant: {relevant}")
        print(f"  Feedback: {feedback}")
        
        return {
            "relevant": relevant,
            "feedback": feedback
        }
    
    except json.JSONDecodeError:
        print(f"  Could not parse evaluator response: {result}")
        print(f"  Defaulting to: not relevant")
        
        return {
            "relevant": False,
            "feedback": "Could not evaluate documents. Please retry with a different query."
        }



if __name__ == "__main__":
    
    # simulate retrieved docs
    mock_docs_relevant = [
        Document(
            page_content="RRF stands for Reciprocal Rank Fusion. It combines rankings from multiple retrieval methods using the formula: score = 1 / (rank + k). Documents ranking highly across multiple methods get higher final scores.",
            metadata={"source": "rag_guide.pdf", "page": 3}
        ),
        Document(
            page_content="The reranking step takes the top 10 candidates from vector search and applies RRF to reorder them for better accuracy before passing to the LLM.",
            metadata={"source": "rag_guide.pdf", "page": 5}
        )
    ]
    
    mock_docs_irrelevant = [
        Document(
            page_content="The weather today is sunny with a high of 25 degrees Celsius.",
            metadata={"source": "news.pdf", "page": 1}
        ),
        Document(
            page_content="Recipe for chocolate cake: mix flour, sugar, eggs and butter.",
            metadata={"source": "recipes.pdf", "page": 2}
        )
    ]
    
    print("── Test 1: Relevant documents ──")
    result1 = evaluate_documents(
        original_query="how does reranking work?",
        rewritten_query="How does the RRF reranking algorithm work in the RAG pipeline?",
        retrieved_docs=mock_docs_relevant
    )
    
    print("\n── Test 2: Irrelevant documents ──")
    result2 = evaluate_documents(
        original_query="how does reranking work?",
        rewritten_query="How does the RRF reranking algorithm work in the RAG pipeline?",
        retrieved_docs=mock_docs_irrelevant
    )