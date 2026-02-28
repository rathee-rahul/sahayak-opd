from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import sys

# Fix import path for both local and Render
sys.path.insert(0, os.path.dirname(__file__))

from groq import Groq
from prompt import SYSTEM_PROMPT

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_path = os.path.join(os.path.dirname(__file__), "../frontend")
app.mount("/app", StaticFiles(directory=frontend_path, html=True), name="frontend")

class ChatRequest(BaseModel):
    message: str
    history: list = []

@app.post("/chat")
async def chat(request: ChatRequest):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for item in request.history:
        messages.append({
            "role": item["role"] if item["role"] != "assistant" else "assistant",
            "content": item["content"]
        })

    messages.append({"role": "user", "content": request.message})

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=1024
    )

    return {"reply": response.choices[0].message.content}

@app.get("/")
def home():
    return {"status": "Sahayak backend is running!"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
```

After saving, we need to push this fix to GitHub. Run these commands in Command Prompt:
```
cd C:\Users\Dell\opd-assistant
```
```
git add .
```
```
git commit -m "Fix import path for Render deployment"
```
```
git push