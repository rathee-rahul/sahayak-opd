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
 
# Load doctor data once at startup
DOCTOR_DATA_PATH = os.path.join(os.path.dirname(__file__), "doctor_data.json")
with open(DOCTOR_DATA_PATH, "r", encoding="utf-8") as f:
    DOCTOR_DATA = json.load(f)
 
# Build search index: token -> [(dept, doc)]
DOCTOR_INDEX = {}
 
def _tokenize(name: str):
    name = re.sub(r'\b(dr\.?|prof\.?|hod|sir|mrs?\.?)\b', '', name, flags=re.IGNORECASE)
    return [t.strip().lower() for t in name.split() if len(t.strip()) >= 3]
 
for dept, docs in DOCTOR_DATA.items():
    for doc in docs:
        for token in _tokenize(doc.get("name", "")):
            DOCTOR_INDEX.setdefault(token, []).append((dept, doc))


# ─── SUB-SPECIALTY FILTER ────────────────────────────────────────────────────
def filter_by_sub_specialty(doctors: list, sub_specialty: str) -> list:
    """Filter doctors by sub_specialty keyword against sub_specialty and conditions fields.
    Returns filtered list if matches found, else returns full list as fallback."""
    if not sub_specialty:
        return doctors
    keyword = sub_specialty.lower()
    # Try to match individual keywords from the sub_specialty string
    keywords = [k.strip() for k in re.split(r'[,/\s]+', keyword) if len(k.strip()) >= 3]
    filtered = []
    for doc in doctors:
        doc_sub = doc.get("sub_specialty", "").lower()
        doc_cond = doc.get("conditions", "").lower()
        combined = doc_sub + " " + doc_cond
        if any(kw in combined for kw in keywords):
            filtered.append(doc)
    return filtered if filtered else doctors  # fallback to all if no match


def search_doctor_by_name(query: str, hint_dept: str = None):
    tokens = _tokenize(query)
    if not tokens:
        return []
    scores = {}
    for token in tokens:
        for dept, doc in DOCTOR_INDEX.get(token, []):
            key = (dept, doc["name"])
            scores[key] = scores.get(key, 0) + 2
        for idx_token, entries in DOCTOR_INDEX.items():
            if token in idx_token or idx_token in token:
                for dept, doc in entries:
                    key = (dept, doc["name"])
                    scores[key] = scores.get(key, 0) + 1
    if not scores:
        return []
    results = sorted(scores.items(), key=lambda x: (
        -(2 if hint_dept and x[0][0] == hint_dept else 0) - x[1]
    ))
    seen = set()
    final = []
    for (dept, doc_name), score in results:
        if (dept, doc_name) not in seen:
            seen.add((dept, doc_name))
            for doc in DOCTOR_DATA.get(dept, []):
                if doc["name"] == doc_name:
                    final.append({"dept": dept, "doctor": doc, "score": score})
                    break
    return final
 
 
class ChatRequest(BaseModel):
    message: str
    history: list = []
 
 
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
 
    try:
        clean = re.sub(r"^\x60\x60\x60json\s*|^\x60\x60\x60\s*|\x60\x60\x60$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(clean)
        reply        = parsed.get("reply", raw)
        department   = parsed.get("department")
        sub_spec     = parsed.get("sub_specialty")
        is_emergency = parsed.get("is_emergency", False)
        doctor_query = parsed.get("doctor_query")
    except (json.JSONDecodeError, KeyError):
        reply, department, sub_spec = raw, None, None
        is_emergency, doctor_query  = False, None
 
    doctor_results = []
    ambiguous      = False
 
    if doctor_query:
        matches = search_doctor_by_name(doctor_query, hint_dept=department)
        if matches:
            top_score   = matches[0]["score"]
            top_matches = [m for m in matches if m["score"] >= top_score - 1]
            depts_found = list({m["dept"] for m in top_matches})
            if len(depts_found) == 1:
                doctor_results = [{"dept": m["dept"], "doctor": m["doctor"]} for m in top_matches]
                if not department:
                    department = depts_found[0]
            else:
                ambiguous  = True
                seen_depts = set()
                for m in top_matches:
                    if m["dept"] not in seen_depts:
                        doctor_results.append({"dept": m["dept"], "doctor": m["doctor"]})
                        seen_depts.add(m["dept"])
 
    dept_doctors = []
    if department and department in DOCTOR_DATA and not doctor_query:
        all_docs = DOCTOR_DATA[department]
        # ── KEY FIX: filter by sub_specialty if AI returned one ──
        dept_doctors = filter_by_sub_specialty(all_docs, sub_spec)
 
    return {
        "reply":          reply,
        "department":     department,
        "sub_specialty":  sub_spec,
        "is_emergency":   is_emergency,
        "doctor_query":   doctor_query,
        "doctor_results": doctor_results,
        "doctors":        dept_doctors,
        "ambiguous":      ambiguous,
    }
 
 
@app.get("/doctors")
def get_doctors(department: str = Query(...), sub_specialty: str = Query(None)):
    # Find the department (with fuzzy fallback)
    if department in DOCTOR_DATA:
        all_docs = DOCTOR_DATA[department]
    else:
        q = department.lower()
        all_docs = []
        matched_dept = department
        for dept_name, docs in DOCTOR_DATA.items():
            if q in dept_name.lower() or dept_name.lower() in q:
                all_docs = docs
                matched_dept = dept_name
                department = matched_dept
                break

    # ── KEY FIX: filter by sub_specialty if provided ──
    filtered_docs = filter_by_sub_specialty(all_docs, sub_specialty)

    return {"department": department, "doctors": filtered_docs}
 
 
@app.get("/departments")
def get_departments():
    return {
        "departments": [
            {"name": dept, "doctor_count": len(docs)}
            for dept, docs in DOCTOR_DATA.items()
        ]
    }
 
 
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