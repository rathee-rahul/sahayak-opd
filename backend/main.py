from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import os, sys, json, re

sys.path.insert(0, os.path.dirname(__file__))

from groq import Groq
from prompt import SYSTEM_PROMPT
from thefuzz import fuzz

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

frontend_path = os.path.join(os.path.dirname(__file__), "../frontend")
if os.path.exists(frontend_path):
    app.mount("/app", StaticFiles(directory=frontend_path, html=True), name="frontend")

# ── LOAD DOCTOR DATA ─────────────────────────────────────────
DOCTOR_DATA_PATH = os.path.join(os.path.dirname(__file__), "doctor_data.json")
with open(DOCTOR_DATA_PATH, "r", encoding="utf-8") as f:
    DOCTOR_DATA = json.load(f)

# ── CONDITION SEARCH (UNCHANGED LOGIC) ──────────────────────
def search_by_condition(query: str, preferred_dept: str = None) -> list:
    query_lower = query.lower().strip()
    raw_words = [w.strip() for w in re.split(r'[\s,/]+', query_lower) if len(w.strip()) >= 3]

    results = []
    seen = set()

    for dept, doctors in DOCTOR_DATA.items():
        for doc in doctors:
            combined = (
                doc.get("sub_specialty", "") + " " +
                doc.get("conditions", "") + " " +
                doc.get("unit", "")
            ).lower()

            score = 0
            for kw in raw_words:
                if kw in combined:
                    score += 2

            if score > 0:
                key = (dept, doc["name"])
                if key not in seen:
                    seen.add(key)
                    if preferred_dept and dept == preferred_dept:
                        score += 5
                    results.append({"dept": dept, "doctor": doc, "score": score})

    results.sort(key=lambda x: -x["score"])
    return results


def filter_by_sub_specialty(doctors: list, sub_specialty: str) -> list:
    if not sub_specialty:
        return doctors
    keyword = sub_specialty.lower()
    return [
        doc for doc in doctors
        if keyword in (doc.get("sub_specialty", "") + " " + doc.get("conditions", "")).lower()
    ]


# ── ADVANCED FUZZY DOCTOR SEARCH ────────────────────────────
def search_doctor_by_name(query: str, hint_dept: str = None):
    query = query.lower().strip()
    results = []

    for dept, doctors in DOCTOR_DATA.items():
        for doc in doctors:
            doctor_name = doc["name"].lower()

            similarity = fuzz.partial_ratio(query, doctor_name)

            if similarity >= 75:
                score = similarity

                if hint_dept and dept == hint_dept:
                    score += 10

                results.append({
                    "dept": dept,
                    "doctor": doc,
                    "score": score
                })

    if not results:
        return []

    results.sort(key=lambda x: -x["score"])
    return results[:10]


# ── REQUEST MODEL ────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list = []


# ── CHAT ENDPOINT ─────────────────────────────────────────
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
        response_format={"type": "json_object"}
    )

    raw = response.choices[0].message.content

    try:
        parsed       = json.loads(raw)
        reply        = parsed.get("reply", "")
        department   = parsed.get("department")
        sub_spec     = parsed.get("sub_specialty")
        is_emergency = parsed.get("is_emergency", False)
        doctor_query = parsed.get("doctor_query")
    except json.JSONDecodeError:
        reply        = "System error. Kripya dobara try karein."
        department   = None
        sub_spec     = None
        is_emergency = False
        doctor_query = None

    doctor_results = []
    ambiguous      = False
    dept_doctors   = []
    condition_matches = []
    search_was_filtered = False

    # Doctor name search
    if doctor_query:
        matches = search_doctor_by_name(doctor_query, hint_dept=department)
        if matches:
            doctor_results = [{"dept": m["dept"], "doctor": m["doctor"]} for m in matches]

    # Condition-based search
    if not doctor_query and department:
        search_query = (sub_spec or "") + " " + request.message
        all_matches  = search_by_condition(search_query, preferred_dept=department)

        if all_matches:
            dept_doctors = [m["doctor"] for m in all_matches[:10]]
            search_was_filtered = True
        else:
            dept_doctors = DOCTOR_DATA.get(department, [])

    return {
        "reply": reply,
        "department": department,
        "sub_specialty": sub_spec,
        "is_emergency": is_emergency,
        "doctor_query": doctor_query,
        "doctor_results": doctor_results,
        "doctors": dept_doctors,
        "condition_matches": condition_matches,
        "search_was_filtered": search_was_filtered,
        "ambiguous": ambiguous,
    }


@app.get("/doctors")
def get_doctors(department: str = Query(...), sub_specialty: str = Query(None)):
    all_docs = DOCTOR_DATA.get(department, [])
    return {"department": department, "doctors": filter_by_sub_specialty(all_docs, sub_specialty)}


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
    uvicorn.run("backend.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))