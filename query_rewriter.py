import os
import re
from dotenv import load_dotenv
from llm_client import call_gemini

load_dotenv()

REWRITER_MODEL = {
    "name": os.getenv("REWRITER_MODEL", "llama-3.1-8b-instant")
}
def strip_thinking(text: str) -> str:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'Thinking Process:.*', '', text, flags=re.DOTALL)
    
    return text.strip()

def call_llm(url, model_name, messages, temperature=0, max_tokens=200):
    return strip_thinking(
        call_gemini(
            model_name=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    )


def format_history(conversation_history):
    if not conversation_history:
        return "No conversation history."
    print(conversation_history)
    formatted = []
    for turn in conversation_history:
        role, content = turn          
        role = role.capitalize()
        formatted.append(f"{role}: {content}")
    
    return "\n".join(formatted)


def rewrite_query(original_query, conversation_history):
    print(f"\nOriginal query: '{original_query}'")
    if original_query.lower().strip() in [
        "hi","hellow",
    "hello",
    "hey",
    "hey there",
    "hiya",
    "yo",
    "sup",
    "what's up",
    "whats up",
    "howdy",
    "good morning",
    "good afternoon",
    "good evening",
    "good night",

    # Thanks / appreciation
    "thanks",
    "thank you",
    "thx",
    "ty",
    "thanks a lot",
    "thank you so much",
    "appreciate it",
    "much appreciated",

    # Acknowledgements
    "ok",
    "okay",
    "alright",
    "sure",
    "got it",
    "understood",
    "i see",
    "makes sense",
    "sounds good",
    "fine",
    "cool",
    "nice",
    "great",
    "perfect",
    "awesome",

    # Casual conversation
    "how are you",
    "how are you doing",
    "how's it going",
    "hows it going",
    "what are you doing",
    "what's going on",
    "anything new",
    "tell me something",
    "who are you",

    # Short reactions
    "lol",
    "haha",
    "hehe",
    "hahaha",
    "wow",
    "oh",
    "oh okay",
    "hmm",
    "hmmm",
    "interesting",
    "nice one",
    "great job",

    # Confirmations
    "yes",
    "yeah",
    "yep",
    "yup",
    "nah",
    "no",
    "nope",
    "maybe",
    "probably",

    # Farewells
    "bye",
    "goodbye",
    "see you",
    "see ya",
    "later",
    "take care",
    "talk later"
    ]:
        return original_query
    
    formatted_history = format_history(conversation_history)
    
    messages = [
        {
            "role": "system",
            "content": """You are a query rewriter for a document retrieval system.
Rewrite the user's query to be more specific and retrievable.
Output ONLY the rewritten query — no labels, no preamble, no explanation.
If the query references 'this', 'it', 'the document', resolve the reference using conversation history.
If there are no such references ,return the query unchanged and do not add any additional context if the query is already clear .
"""
        },
        {
            "role": "user",
            "content": f"""Conversation History:
{formatted_history}

Original Query: {original_query}

    Rewrite this query to be self-contained and optimized for vector search. But if it doesn't need rewriting, return it unchanged. Output ONLY the rewritten query."""
        }
    ]
    
    rewritten_query = call_llm(
        url=REWRITER_MODEL.get("url"),
        model_name=REWRITER_MODEL["name"],
        messages=messages,
        temperature=0,
        max_tokens=200
    )
    
    print(f"Rewritten query: '{rewritten_query}'")
    return rewritten_query
    

# query_rewriter.py

def rewrite_query_with_feedback(original_query, rewritten_query, feedback, conversation_history):
    recent = conversation_history[-2:] if conversation_history else []
    history_text = "\n".join([
        f"{role}: {content[:150]}" for role, content in recent
    ]) if recent else "No prior history."

    messages = [
        {
            "role": "system",
            "content": """You are a query rewriter. A previous retrieval attempt failed.
You must produce a DIFFERENT query than the one that failed.
Use different vocabulary, synonyms, broader or narrower terms.
Output ONLY the new query string. No labels, no explanation."""
        },
        {
            "role": "user",
            "content": (
                f"Original question: {original_query}\n"
                f"Failed query: {rewritten_query}\n"
                f"Why it failed: {feedback}\n"
                f"Conversation context: {history_text}\n\n"
                f"Write a DIFFERENT search query that avoids the same failure.\n"
                f"New query:"
            )
        }
    ]

    try:
        raw = call_llm(
            url=REWRITER_MODEL.get("url"),
            model_name=REWRITER_MODEL["name"],
            messages=messages,
            temperature=0.7,
            max_tokens=80
        )
        rewritten = strip_thinking(raw).strip()
    except Exception as e:
        print(f"Rewriter failed: {e}")
        rewritten = original_query

    bad_prefixes = ["new query:", "rewritten query:", "original query:", "failed query:"]
    if (not rewritten
        or rewritten.lower() == rewritten_query.lower()  # reject if identical to failed query
        or any(rewritten.lower().startswith(p) for p in bad_prefixes)
        or len(rewritten) > len(original_query) * 4
        or "\n" in rewritten):
        fallbacks = [
            original_query.replace("recommended", "used in mobile security"),
            original_query.replace("encryption methods", "cryptographic standards TLS AES"),
            f"mobile device {original_query}",
        ]
        rewritten = next((f for f in fallbacks if f != rewritten_query), original_query)

    print(f"New rewritten query: '{rewritten}'")
    return rewritten



if __name__ == "__main__":
    
    conversation_history = [
        {"role": "user", "content": "What is RAG?"},
        {"role": "assistant", "content": "RAG stands for Retrieval Augmented Generation."},
        {"role": "user", "content": "How does the reranking work in it?"},
        {"role": "assistant", "content": "Reranking uses the RRF algorithm to combine results from multiple searches."},
    ]
    
    print("── Test 1: Ambiguous query ──")
    rewrite_query(
        original_query="what about the first step in it?",
        conversation_history=conversation_history
    )
    
    print("\n── Test 2: Already clear query ──")
    rewrite_query(
        original_query="What is the RRF algorithm?",
        conversation_history=conversation_history
    )
    
    print("\n── Test 3: Retry rewriter with feedback ──")
    rewrite_query_with_feedback(
        original_query="what about the first step in it?",
        rewritten_query="What is the first step in the RAG pipeline?",
        feedback="Documents returned were about RAG overview, not specifically about ingestion",
        conversation_history=conversation_history
    )