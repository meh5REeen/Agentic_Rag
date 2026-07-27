import os
import requests
from dotenv import load_dotenv

load_dotenv()

class WebSearchMCP:
    def __init__(self):
        self.api_key = os.getenv("TAVILY_API_KEY") or "".strip()

    def is_available(self):
        return bool(self.api_key)

    def search(self, query, top_k=5):
        if not self.api_key:
            raise RuntimeError("No web search API key configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {"query": query, "max_results": top_k}
        response = requests.post(
            "https://api.tavily.com/search",
            json=payload,
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        return [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("content"),
            }
            for item in data.get("results", [])[:top_k]
        ]

def get_web_search_tool():
    return WebSearchMCP()
