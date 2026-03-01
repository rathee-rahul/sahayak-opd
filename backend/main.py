from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import os, sys, json, re

sys.path.insert(0, os.path.dirname(__file__))

from groq import Groq
from prompt import SYSTEM_PROMPT

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

frontend_path = os.path.join(os.path.dirname(__file__), "../frontend")
app.mount("/app", StaticFiles(directory=frontend_path, html=True), name="frontend")

# ── Load doctor data once at startup ─────────────────────────────────────────
DOCTOR_DATA_PATH = os.path.join(os.path.dirname(__file__), "doctor_data.json")
with open(DOCTOR_DATA_PATH, "r", encoding="utf-8") as f:
    DOCTOR_DATA = json.load(f)

# ── Build a flat search index: name tokens → list of (dept, doctor) ──────────
DOCTOR_INDEX = {}   # lowercase token → [(dept, doc), ...]

def _tokenize(name: str):
    """Return meaningful name parts, stripping titles."""
    name = re.sub(r'\b(dr\.?|prof\.?|hod|sir|mrs?\.?)\b', '', name, flags=re.IGNORECASE)
    return [t.strip().lower() for t in name.split() if len(t.strip()) >= 3]

for dept, docs in DOCTOR_DATA.items():
    for doc in docs:
        for token in _tokenize(doc.get("name", "")):
            DOCTOR_INDEX.setdefault(token, []).append((dept, doc))


def search_doctor_by_name(query: str, hint_dept: str = None):
    """
    Fuzzy search doctors by name query.
    Returns list of (dept, doc) matches, sorted by match quality.
    If hint_dept is given, prefer matches in that department.
    """
    tokens = _tokenize(query)
    if not tokens:
        return []

    scores = {}  # (dept, doc_name) → score
    for token in tokens:
        # Exact token match
        for dept, doc in DOCTOR_INDEX.get(token, []):
            key = (dept, doc["name"])
            scores[key] = scores.get(key, 0) + 2
        # Partial match across all index keys
        for idx_token, entries in DOCTOR_INDEX.items():
            if token in idx_token or idx_token in token:
                for dept, doc in entries:
                    key = (dept, doc["name"])
                    scores[key] = scores.get(key, 0) + 1

    if not scores:
        return []

    # Sort by score descending; prefer hint_dept
    results = sorted(scores.items(), key=lambda x: (
        -(2 if hint_dept and x[0][0] == hint_dept else 0) - x[1]
    ))

    # Reconstruct (dept, doc) list preserving order, deduped
    seen = set()
    final = []
    for (dept, doc_name), score in results:
        if (dept, doc_name) not in seen:
            seen.add((dept, doc_name))
            # Find the actual doc dict
            for doc in DOCTOR_DATA.get(dept, []):
                if doc["name"] == doc_name:
                    final.append({"dept": dept, "doctor": doc, "score": score})
                    break
    return final


# ── Models ────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list = []


# ── Chat endpoint ─────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(request: ChatRequest):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in request.history:
        messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": request.message})

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=1024,
        temperature=0.4,
    )

    raw = response.choices[0].message.content.strip()

    # Parse structured JSON from AI
    try:
        clean = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(clean)
        reply        = parsed.get("reply", raw)
        department   = parsed.get("department")
        sub_spec     = parsed.get("sub_specialty")
        is_emergency = parsed.get("is_emergency", False)
        doctor_query = parsed.get("doctor_query")
    except (json.JSONDecodeError, KeyError):
        reply        = raw
        department   = None
        sub_spec     = None
        is_emergency = False
        doctor_query = None

    # ── Doctor name lookup ────────────────────────────────────────────────────
    doctor_results = []   # list of {dept, doctor} dicts
    ambiguous      = False

    if doctor_query:
        matches = search_doctor_by_name(doctor_query, hint_dept=department)
        if matches:
            top_score = matches[0]["score"]
            # Group top matches by department to detect same-name cross-dept ambiguity
            top_matches = [m for m in matches if m["score"] >= top_score - 1]

            depts_found = list({m["dept"] for m in top_matches})

            if len(depts_found) == 1:
                # All top matches in same department — return them all (could be multiple docs same dept)
                doctor_results = [{"dept": m["dept"], "doctor": m["doctor"]} for m in top_matches]
                if not department:
                    department = depts_found[0]
            else:
                # Genuinely ambiguous — different departments, surface top candidate per dept
                ambiguous = True
                seen_depts = set()
                for m in top_matches:
                    if m["dept"] not in seen_depts:
                        doctor_results.append({"dept": m["dept"], "doctor": m["doctor"]})
                        seen_depts.add(m["dept"])

    # ── Department-level doctor list (symptom routing) ────────────────────────
    dept_doctors = []
    if department and department in DOCTOR_DATA and not doctor_query:
        dept_doctors = DOCTOR_DATA[department]

    return {
        "reply":          reply,
        "department":     department,
        "sub_specialty":  sub_spec,
        "is_emergency":   is_emergency,
        "doctor_query":   doctor_query,
        "doctor_results": doctor_results,   # specific doctor name search results
        "doctors":        dept_doctors,     # full dept list for symptom routing
        "ambiguous":      ambiguous,        # frontend shows disambiguation UI if true
    }


# ── Doctor search endpoint ────────────────────────────────────────────────────
@app.get("/doctors")
def get_doctors(department: str = Query(...)):
    if department in DOCTOR_DATA:
        return {"department": department, "doctors": DOCTOR_DATA[department]}
    q = department.lower()
    for dept_name, docs in DOCTOR_DATA.items():
        if q in dept_name.lower() or dept_name.lower() in q:
            return {"department": dept_name, "doctors": docs}
    return {"department": department, "doctors": []}


# ── Departments list ──────────────────────────────────────────────────────────
@app.get("/departments")
def get_departments():
    return {
        "departments": [
            {"name": dept, "doctor_count": len(docs)}
            for dept, docs in DOCTOR_DATA.items()
        ]
    }


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/")
def home():
    return {
        "status": "Sahayak backend is running!",
        "total_departments": len(DOCTOR_DATA),
        "total_doctors": sum(len(v) for v in DOCTOR_DATA.values()),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
