# test_connection.py
import requests

url = "https://19d9-154-192-5-123.ngrok-free.app/v1/chat/completions"

try:
    response = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true"
        },
        json={
            "model": "Qwen/Qwen3.5-4B",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10
        },
        timeout=10
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")

except requests.exceptions.ConnectionError:
    print("❌ Server is down or URL has changed")
except requests.exceptions.Timeout:
    print("❌ Server is up but not responding (model may be loading)")
except Exception as e:
    print(f"❌ Error: {e}")