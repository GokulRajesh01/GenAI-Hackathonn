import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get("GEMINI_API_KEY", "").strip()

url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={api_key}"
payload = {
    "model": "models/text-embedding-004",
    "content": {"parts": [{"text": "Hello world"}]}
}

r = requests.post(url, headers={"Content-Type": "application/json"}, json=payload)
print("text-embedding-004:", r.status_code, r.text[:200])

url2 = f"https://generativelanguage.googleapis.com/v1beta/models/embedding-001:embedContent?key={api_key}"
payload2 = {
    "model": "models/embedding-001",
    "content": {"parts": [{"text": "Hello world"}]}
}

r2 = requests.post(url2, headers={"Content-Type": "application/json"}, json=payload2)
print("embedding-001:", r2.status_code, r2.text[:200])
