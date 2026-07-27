# import os
# import re
# import time
# import requests
# from dotenv import load_dotenv
# from langchain_core.documents import Document

# load_dotenv()

# RESPONSE_MODEL = {
#     "url": "https://19d9-154-192-5-123.ngrok-free.app/v1/chat/completions",
#     "name": "Qwen/Qwen3.5-4B"
# }


# def strip_thinking(text: str) -> str:
#     text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
#     text = re.sub(r'Thinking Process:.*', '', text, flags=re.DOTALL)
    
#     return text.strip()

# def call_llm(url, model_name, messages, temperature=0, max_tokens=800):
#     MODEL_MAX_CONTEXT = 2048
#     BUFFER = 100  # safety margin
    
#     # Estimate prompt tokens (1 token ≈ 4 chars)
#     prompt_chars = sum(len(m.get("content", "")) for m in messages)
#     estimated_prompt_tokens = prompt_chars // 4
    
#     # Calculate how many tokens are actually available for output
#     available_tokens = MODEL_MAX_CONTEXT - estimated_prompt_tokens - BUFFER
#     actual_max_tokens = max(150, min(max_tokens, available_tokens))
    
#     if actual_max_tokens < max_tokens:
#         print(f"Prompt ~{estimated_prompt_tokens} tokens, reducing max_tokens {max_tokens}→{actual_max_tokens}")
    
#     payload = {
#         "model": model_name,
#         "messages": messages,
#         "temperature": temperature,
#         "max_tokens": actual_max_tokens,
#         "chat_template_kwargs": {"enable_thinking": False}
#     }
    
#     headers = {
#         "Content-Type": "application/json",
#         "ngrok-skip-browser-warning": "true"
#     }
#     for attempt in range(3):
#         try:
#             response = requests.post(
#                     url,
#                     json=payload,
#                     headers=headers,
#                     timeout=60
#             )            

#             if not response.ok:
#                 print(f"Status: {response.status_code}")
#                 print(f"Response: {response.text}")
#                 response.raise_for_status()
#             return response.json()["choices"][0]["message"]["content"].strip()

#         except requests.exceptions.RequestException as e:
#             print(f"Attempt {attempt+1} failed: {e}")

#             if attempt == 2:
#                 raise

#             time.sleep(2)

    
#     if not response.ok:
#         print(f"Status code: {response.status_code}")
#         print(f"Response body: {response.text}")
#         response.raise_for_status()
    
#     return strip_thinking(response.json()["choices"][0]["message"]["content"].strip())


# def format_history(conversation_history):
#     if not conversation_history:
#         return []
    
#     recent = conversation_history[-10:]
    
#     return [
#         {"role": role, "content": content}
#         for role, content in recent
#     ]

# def format_docs(retrieved_docs):
#     formatted = []
#     for i, doc in enumerate(retrieved_docs):
#         source = doc.metadata.get("source", "unknown")
#         page = doc.metadata.get("page", "?")
#         formatted.append(
#             f"[Document {i+1} | Source: {source} | Page: {page}]\n{doc.page_content}"
#         )
#     return "\n\n---\n\n".join(formatted)


# def generate_direct_response(original_query, conversation_history):
#     print(f"\nGenerating direct response (no RAG)...")
    
#     messages = [
#         {
#             "role": "system",
#             "content": """You are a helpful, knowledgeable assistant.Be concise. /no_think"
# Answer the user's question clearly and accurately.
# Use the conversation history for context where relevant.
# Be concise but complete."""
#         },
#         *format_history(conversation_history),
#         {
#             "role": "user",
#             "content": original_query
#         }
#     ]
    
#     response = call_llm(
#         url=RESPONSE_MODEL["url"],
#         model_name=RESPONSE_MODEL["name"],
#         messages=messages,
#         temperature=0.7,
#         max_tokens=900
#     )
    
#     print(f"Direct response generated.")
#     return response


# def generate_grounded_response(original_query, rewritten_query, retrieved_docs, conversation_history):
#     print(f"\nGenerating grounded response (RAG)...")
    
#     formatted_docs = format_docs(retrieved_docs)
#     recent = conversation_history[-2:] if conversation_history else []
#     truncated_history = [
#         {"role": role, "content": content[:300]}  # cap each turn at 300 chars
#         for role, content in recent
#     ]

    
#     messages = [
#         {
#             "role": "system",
#             "content": """Answer strictly using provided documents and history. Cite like [Document 1]. State clearly if information is missing or partial. Do not invent facts. Be concise. /no_think"
# """
#         },
#         *format_history(truncated_history),
#         {
#             "role": "user",
#             "content": f"""Retrieved Documents:
# {formatted_docs}

# Original Question: {original_query}
# Clarified Question: {rewritten_query}

# Please answer the original question based on the retrieved documents."""
#         }
#     ]
    
#     response = call_llm(
#         url=RESPONSE_MODEL["url"],
#         model_name=RESPONSE_MODEL["name"],
#         messages=messages,
#         temperature=0.3,    
#         max_tokens=900 
#     )
    
#     print(f"Grounded response generated.")
#     return response

# def generate_safe_response(original_query):
#     return (
#         f"I wasn't able to find relevant information in the knowledge base "
#         f"to answer your question: '{original_query}'. "
#         f"Please try rephrasing your question or check if the relevant "
#         f"documents have been uploaded."
#     )


# if __name__ == "__main__":
    
#     conversation_history = [
#         ("user", "What is RAG?"),
#         ("assistant", "RAG stands for Retrieval Augmented Generation."),
#     ]
    
#     print("── Test 1: Direct Response ──")
#     response = generate_direct_response(
#         original_query="What is the capital of France?",
#         conversation_history=conversation_history
#     )
#     print(f"\nResponse:\n{response}")

#     print("\n── Test 2: Grounded Response ──")
#     mock_docs = [
#         Document(
#             page_content="RRF stands for Reciprocal Rank Fusion. It combines rankings from multiple retrieval methods using the formula: score = 1 / (rank + k).",
#             metadata={"source": "rag_guide.pdf", "page": 3}
#         ),
#         Document(
#             page_content="The reranking step improves retrieval accuracy by combining semantic search and keyword search results.",
#             metadata={"source": "rag_guide.pdf", "page": 5}
#         )
#     ]
    
#     response = generate_grounded_response(
#         original_query="how does reranking work?",
#         rewritten_query="How does the RRF reranking algorithm work in the RAG pipeline?",
#         retrieved_docs=mock_docs,
#         conversation_history=conversation_history
#     )
#     print(f"\nResponse:\n{response}")
    
#     print("\n── Test 3: Safe Fallback ──")
#     response = generate_safe_response("What is our company vacation policy?")
#     print(f"\nResponse:\n{response}")
import os
import re
from dotenv import load_dotenv
from langchain_core.documents import Document
from llm_client import call_gemini

load_dotenv()

RESPONSE_MODEL = {
    "name": os.getenv("RESPONSE_MODEL", "llama-3.3-70b-versatile")
}


def strip_thinking(text: str) -> str:
    if not text:
        return text
    if '</think>' in text:
        after = text.split('</think>', 1)[-1].strip()
        if after:
            return after
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'Thinking Process:.*', '', text, flags=re.DOTALL)
    return text.strip()


def call_llm(url, model_name, messages, temperature=0, max_tokens=600):
    MODEL_MAX_CONTEXT = 2048
    BUFFER = 100

    prompt_chars = sum(len(m.get("content", "")) for m in messages)
    estimated_prompt_tokens = prompt_chars // 4

    available_tokens = MODEL_MAX_CONTEXT - estimated_prompt_tokens - BUFFER
    actual_max_tokens = max(150, min(max_tokens, available_tokens))

    if actual_max_tokens < max_tokens:
        print(f"Prompt ~{estimated_prompt_tokens} tokens, reducing max_tokens {max_tokens}→{actual_max_tokens}")

    return strip_thinking(
        call_gemini(
            model_name=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=actual_max_tokens,
        )
    )


def _build_history_messages(conversation_history, max_turns=2, max_chars=300):
    """
    Safely converts conversation history tuples into validated message dicts.
    Enforces alternating roles and caps content length.
    """
    if not conversation_history:
        return []

    recent = conversation_history[-max_turns:]
    messages = []
    last_role = None

    for turn in recent:
        role, content = turn
        if role not in ("user", "assistant"):
            continue
        if not content or not content.strip():
            continue
        if role == last_role:          # skip duplicate consecutive roles
            continue
        messages.append({"role": role, "content": content[:max_chars]})
        last_role = role

    return messages


def format_docs(retrieved_docs):
    formatted = []
    for i, doc in enumerate(retrieved_docs):
        source = doc.metadata.get("source", "unknown")
        page   = doc.metadata.get("page", "?")
        formatted.append(
            f"[Document {i+1} | Source: {source} | Page: {page}]\n{doc.page_content}"
        )
    return "\n\n---\n\n".join(formatted)


def generate_direct_response(original_query, conversation_history):
    print(f"\nGenerating direct response (no RAG)...")

    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant. Answer clearly and concisely."
        }
    ]

    history_msgs = _build_history_messages(conversation_history)
    messages.extend(history_msgs)

    if messages[-1]["role"] == "user":
        messages[-1]["content"] += f"\n\n{original_query}"
    else:
        messages.append({"role": "user", "content": original_query})

    response = call_llm(
        url=RESPONSE_MODEL.get("url"),
        model_name=RESPONSE_MODEL["name"],
        messages=messages,
        temperature=0.7,
        max_tokens=600
    )

    result = strip_thinking(response) if response else ""
    print(f"Direct response generated.")
    return result if result.strip() else "I was unable to generate a response. Please try again."


def generate_grounded_response(original_query, rewritten_query, retrieved_docs, conversation_history):
    print(f"\nGenerating grounded response (RAG)...")

    formatted_docs = format_docs(retrieved_docs)
    if len(formatted_docs) > 1200:
        formatted_docs = formatted_docs[:1200] + "\n...[truncated]"

    messages = [
        {
            "role": "system",
            "content": "Answer using the documents only. Cite sources like [Document 1]. Be concise. If information is missing say so."
        }
    ]

    history_msgs = _build_history_messages(conversation_history)
    messages.extend(history_msgs)

    user_content = (
        f"Documents:\n{formatted_docs}\n\n"
        f"Question: {original_query}"
    )

    # Ensure we don't end up with two consecutive user messages
    if messages[-1]["role"] == "user":
        messages[-1]["content"] += f"\n\n{user_content}"
    else:
        messages.append({"role": "user", "content": user_content})

    response = call_llm(
        url=RESPONSE_MODEL.get("url"),
        model_name=RESPONSE_MODEL["name"],
        messages=messages,
        temperature=0.3,
        max_tokens=600
    )

    result = strip_thinking(response) if response else ""
    print(f"Grounded response generated.")
    return result if result.strip() else "I was unable to generate a response. Please try again."


def generate_safe_response(original_query):
    return (
        f"I wasn't able to find relevant information in the knowledge base "
        f"to answer your question: '{original_query}'. "
        f"Please try rephrasing your question or check if the relevant "
        f"documents have been uploaded."
    )


if __name__ == "__main__":
    conversation_history = [
        ("user", "What is RAG?"),
        ("assistant", "RAG stands for Retrieval Augmented Generation."),
    ]

    print("── Test 1: Direct Response ──")
    response = generate_direct_response(
        original_query="What is the capital of France?",
        conversation_history=conversation_history
    )
    print(f"\nResponse:\n{response}")

    print("\n── Test 2: Grounded Response ──")
    mock_docs = [
        Document(
            page_content="RRF stands for Reciprocal Rank Fusion. It combines rankings from multiple retrieval methods using the formula: score = 1 / (rank + k).",
            metadata={"source": "rag_guide.pdf", "page": 3}
        ),
        Document(
            page_content="The reranking step improves retrieval accuracy by combining semantic search and keyword search results.",
            metadata={"source": "rag_guide.pdf", "page": 5}
        )
    ]

    response = generate_grounded_response(
        original_query="how does reranking work?",
        rewritten_query="How does the RRF reranking algorithm work in the RAG pipeline?",
        retrieved_docs=mock_docs,
        conversation_history=conversation_history
    )
    print(f"\nResponse:\n{response}")

    print("\n── Test 3: Safe Fallback ──")
    response = generate_safe_response("What is our company vacation policy?")
    print(f"\nResponse:\n{response}")