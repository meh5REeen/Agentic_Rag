# orchestrator.py
import os
import re
from dotenv import load_dotenv
from config.models import ENV_ORCHESTRATOR_MODEL, get_role_preferred_model
from llm_client import call_gemini
from text_utils import strip_thinking_tags
from web_search_mcp import get_web_search_tool

load_dotenv()

ORCHESTRATOR_MODEL = {
    "name": get_role_preferred_model(ENV_ORCHESTRATOR_MODEL)
}


DIRECT_PATTERNS = [
    r'^\s*(hi|hello|hey|thanks|thank you|bye|goodbye)[!.,]?\s*$',
    r'^(who are you|what can you do|what are you|how are you)',
    r'^(write me a (poem|story|joke)|tell me a joke)',
    r'^what is \d+ [\+\-\*\/] \d+',   # math
]

RAG_PATTERNS = [
    r'\b(summarize|summary|executive summary|describe|outline)\b',
    r'\b(document|report|publication|paper|guide|policy|standard)\b',
    r'\b(nist|byod|sp |800-|1800-|iso |cis )\b',
    r'\b(architecture|component|protocol|implementation)\b',
    r'\b(purpose|goal|objective|finding|recommendation|conclusion)\b',
    r'\b(who (wrote|authored|contributed)|which organization|what company)\b',
    r'\b(this|the) (publication|document|report|solution|system|framework|paper)\b',
    r'\b(section|chapter|page|figure|table) \d+\b',
    r'\b(how does .* work in (our|the|this) system)\b',
    r'\b(steps in (our|the|this))\b',
    r'\b(onboarding|refund policy|uploaded)\b',
]

def _keyword_route(query: str):
    """Returns 'direct', 'rag', or 'ambiguous'."""
    q = query.lower().strip()
    for pattern in DIRECT_PATTERNS:
        if re.search(pattern, q, re.IGNORECASE):
            return 'direct'
    for pattern in RAG_PATTERNS:
        if re.search(pattern, q, re.IGNORECASE):
            return 'rag'
    return 'ambiguous'



def call_llm(url, model_name, messages, temperature=0, max_tokens=10):
    return strip_thinking_tags(
        call_gemini(
            model_name=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            disable_reasoning=True,
        )
    )


def get_snapshot(conversation_history) -> str:
    """Condenses long history into a single descriptive line."""
    if not conversation_history:
        return "No prior conversation."

    formatted = "\n".join([
        f"{role.capitalize()}: {content}"
        for role, content in conversation_history
    ])

    messages = [
        {
            "role": "system",
            "content": (
                "You will be given a conversation history. "
                "Write ONE descriptive sentence summarizing everything discussed — "
                "topics, questions asked, and answers given. "
                "Output only that single sentence, nothing else."
            )
        },
        {
            "role": "user",
            "content": f"Conversation history:\n{formatted}"
        }
    ]

    try:
        result = call_llm(
            url=ORCHESTRATOR_MODEL.get("url"),
            model_name=ORCHESTRATOR_MODEL["name"],
            messages=messages,
            temperature=0,
            max_tokens=150
        )
        print(f"Snapshot: {result}")
        return result or "No summary available."
    except Exception as e:
        print(f"Snapshot generation failed: {e}")
        return "No summary available."


def _llm_route(query: str, snapshot: str, recent_history: str) -> bool:
    """LLM fallback for ambiguous queries. Returns True if RAG needed."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a query router. Output ONE word only: RAG or DIRECT.\n\n"
                "RAG   — query asks about specific content in documents, reports, "
                "publications, systems, or anything requiring factual retrieval.\n"
                "DIRECT — casual chat, greetings, general knowledge, math, jokes.\n\n"
                "Rules:\n"
                "- Conversation history is context only — do NOT base the decision solely on it.\n"
                "- Base the decision primarily on the query itself.\n"
                "- Output exactly one word: RAG or DIRECT."
            )
        },
        {
            "role": "user",
            "content": (
                f"Conversation snapshot: {snapshot}\n\n"
                f"Recent history:\n{recent_history}\n\n"
                f"Query: {query}\n\n"
                "RAG or DIRECT?"
            )
        }
    ]

    try:
        raw = call_llm(
            url=ORCHESTRATOR_MODEL.get("url"),
            model_name=ORCHESTRATOR_MODEL["name"],
            messages=messages,
            temperature=0,
            max_tokens=10
        )
        cleaned = raw.strip().upper()
        print(f"Orchestrator LLM raw: {repr(cleaned)}")
        return "RAG" in cleaned
    except Exception as e:
        print(f"Orchestrator LLM failed: {e}. Defaulting to RAG.")
        return True



def needs_rag(query: str, conversation_history=None) -> bool:
    conversation_history = conversation_history or []

    web_search = get_web_search_tool()
    if web_search.is_available() and re.search(r'\b(web search|web|search|search web|search:|web:|google|bing|tavily|today|latest|news|weather|current|release|upcoming|stock|price|who won|what happened|recent)\b', query, re.IGNORECASE):
        print(f"Orchestrator decision for '{query[:60]}': web-search fallback selected")
        return False

    keyword_result = _keyword_route(query)

    if keyword_result == 'rag':
        print(f"Orchestrator decision for '{query[:60]}': RAG needed (keyword)")
        return True

    if keyword_result == 'direct':
        print(f"Orchestrator decision for '{query[:60]}': No RAG needed (keyword)")
        return False

    print(f"Orchestrator decision for '{query[:60]}': ambiguous — calling LLM...")

    snapshot = get_snapshot(conversation_history)

    recent = conversation_history[-6:]
    recent_history_text = "\n".join([
        f"{role.capitalize()}: {content[:200]}"
        for role, content in recent
    ]) if recent else "No recent history."

    result = _llm_route(query, snapshot, recent_history_text)
    print(f"Orchestrator decision for '{query[:60]}': {'RAG needed' if result else 'No RAG needed'} (LLM)")
    return result


if __name__ == "__main__":
    test_queries = [
        "What is 2 + 2?",
        "Tell me a joke",
        "What does the refund policy say?",
        "Summarize the uploaded report",
        "What is the capital of France?",
        "What are the steps in our onboarding process?",
        "How does the RAG pipeline work in our system?",
        "Write me a poem about clouds",
    ]

    conversation_history = [
        ("user", "My Android phone has been getting a lot of pop-up ads recently. Could it be malware?"),
        ("assistant", "Yes, frequent pop-up ads can be a sign of adware or malware."),
        ("user", "Yes, I downloaded a free battery optimizer from a website a few days ago."),
        ("assistant", "That could be the cause. Apps from unofficial websites may contain malware."),
        ("user", "How do I know if an app has dangerous permissions?"),
        ("assistant", "Go to Settings > Apps > Select the app > Permissions."),
        ("user", "The app has permission to access Accessibility Services. Is that bad?"),
        ("assistant", "Accessibility permission is very powerful and can be misused by malicious apps."),
        ("user", "Can malware steal my banking passwords?"),
        ("assistant", "Yes, some mobile malware uses keylogging or overlay attacks to capture credentials."),
        ("user", "Should I factory reset my phone?"),
        ("assistant", "A factory reset is a good last resort if malware cannot be removed."),
    ]

    print("── Orchestrator Tests ──\n")
    for query in test_queries:
        result = needs_rag(query, conversation_history=conversation_history)
        print(f"  → {'RAG' if result else 'DIRECT'} | {query}\n")