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

# ── LOAD DOCTOR DATA ─────────────────────────────────────────────────────────
DOCTOR_DATA_PATH = os.path.join(os.path.dirname(__file__), "doctor_data.json")
with open(DOCTOR_DATA_PATH, "r", encoding="utf-8") as f:
    DOCTOR_DATA = json.load(f)

# ── DOCTOR NAME SEARCH INDEX ─────────────────────────────────────────────────
DOCTOR_INDEX = {}

def _tokenize(name: str):
    name = re.sub(r'\b(dr\.?|prof\.?|hod|sir|mrs?\.?)\b', '', name, flags=re.IGNORECASE)
    return [t.strip().lower() for t in name.split() if len(t.strip()) >= 3]

for dept, docs in DOCTOR_DATA.items():
    for doc in docs:
        for token in _tokenize(doc.get("name", "")):
            DOCTOR_INDEX.setdefault(token, []).append((dept, doc))


# ── LAYMEN TERM SYNONYM MAP ──────────────────────────────────────────────────
# Maps common Hindi/English layman words to medical keywords in doctor_data.json
SYNONYM_MAP = {
    # Behavioural / Child
    "hyperactivity": ["adhd", "hyperactivity", "behavioural", "neurodevelopmental"],
    "hyperactive":   ["adhd", "hyperactivity", "behavioural"],
    "adhd":          ["adhd", "hyperactivity", "attention"],
    "chanchal":      ["adhd", "hyperactivity", "behavioural"],
    "autism":        ["autism", "autism spectrum", "neurodevelopmental"],
    "learning problem": ["learning disability", "adhd", "neurodevelopmental"],
    "concentrate nahi": ["adhd", "learning disability", "attention"],
    # Heart
    "dil ki bimari":  ["heart", "cardiology", "cardiac"],
    "heart problem":  ["heart", "cardiac", "cardiology"],
    "bp":             ["hypertension", "blood pressure"],
    "high bp":        ["hypertension", "blood pressure"],
    "blood pressure": ["hypertension", "blood pressure"],
    # Skin
    "daane":    ["rash", "skin", "dermatology", "acne"],
    "khujli":   ["itching", "skin", "allergy", "dermatitis"],
    "pimples":  ["acne", "pimples", "skin"],
    # Stomach
    "pet dard":  ["abdominal pain", "stomach", "gastroenterology"],
    "ulta aana": ["vomiting", "nausea", "gastroenterology"],
    "dast":      ["diarrhoea", "loose motion", "gastroenterology"],
    "kabz":      ["constipation", "gastroenterology"],
    "acidity":   ["acidity", "gerd", "reflux"],
    "bawaseer":  ["piles", "haemorrhoids", "surgery"],
    "piles":     ["piles", "haemorrhoids", "surgery"],
    # Bones
    "ghutne dard": ["knee pain", "joint pain", "orthopaedics"],
    "haddi dard":  ["bone pain", "fracture", "orthopaedics"],
    "kamar dard":  ["back pain", "spine", "orthopaedics"],
    "slip disc":   ["disc", "spine", "back pain"],
    # Eye
    "dhundhla dikhna": ["blurred vision", "ophthalmology"],
    "chasma":          ["refractive error", "vision", "ophthalmology"],
    # ENT
    "kaan dard":  ["ear pain", "ent", "otitis"],
    "naak band":  ["nasal obstruction", "ent", "rhinitis"],
    "gala kharab":["throat", "ent", "tonsil"],
    "sunai nahi": ["hearing loss", "ent", "audiometry"],
    # Diabetes / Hormones
    "sugar":    ["diabetes", "blood sugar", "endocrinology"],
    "thyroid":  ["thyroid", "endocrinology"],
    "motapa":   ["obesity", "weight", "endocrinology"],
    # Urology
    "peshab mein jalan": ["urinary", "urology", "uti"],
    "pathri":            ["kidney stone", "calculus", "urology"],
    "kidney stone":      ["kidney stone", "urology", "calculus"],
    # Vascular
    "varicose vein":   ["varicose", "vascular", "vein"],
    "naso mein sujan": ["varicose", "vascular", "vein"],
    # Mental Health
    "neend nahi": ["sleep", "insomnia", "psychiatry"],
    "tension":    ["anxiety", "stress", "psychiatry"],
    # Blood
    "khoon ki kami": ["anaemia", "haematology", "blood"],
    "anemia":        ["anaemia", "haematology", "blood"],
    # Cancer
    "cancer":  ["cancer", "oncology", "tumour"],
    "gath":    ["lump", "tumour", "swelling"],
    # Neuro
    "sir dard": ["headache", "migraine", "neurology"],
    "migraine": ["migraine", "headache", "neurology"],
    "fits":     ["seizure", "epilepsy", "fits"],
    "laqwa":    ["paralysis", "stroke", "neurology"],
}


# ── CONDITION SEARCH ACROSS ALL DEPARTMENTS ──────────────────────────────────
def search_by_condition(query: str, preferred_dept: str = None) -> list:
    """
    Search ALL departments for doctors matching query keywords.
    Uses SYNONYM_MAP to expand layman terms to medical keywords.
    Preferred dept doctors get a score boost.
    """
    query_lower = query.lower().strip()

    # Expand via synonym map
    search_keywords = set()
    for layman, medical_terms in SYNONYM_MAP.items():
        if layman in query_lower or query_lower in layman:
            search_keywords.update(medical_terms)

    # Also add raw query words directly
    raw_words = [w.strip() for w in re.split(r'[\s,/]+', query_lower) if len(w.strip()) >= 3]
    search_keywords.update(raw_words)

    if not search_keywords:
        return []

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
            for kw in search_keywords:
                if kw in combined:
                    if kw in doc.get("conditions", "").lower():
                        score += 3
                    elif kw in doc.get("sub_specialty", "").lower():
                        score += 2
                    else:
                        score += 1

            if score > 0:
                key = (dept, doc["name"])
                if key not in seen:
                    seen.add(key)
                    if preferred_dept and dept == preferred_dept:
                        score += 5
                    results.append({"dept": dept, "doctor": doc, "score": score})

    results.sort(key=lambda x: -x["score"])
    return results


# ── SUB-SPECIALTY FILTER (single dept fallback) ───────────────────────────────
def filter_by_sub_specialty(doctors: list, sub_specialty: str) -> list:
    if not sub_specialty:
        return doctors
    keyword = sub_specialty.lower()
    keywords = [k.strip() for k in re.split(r'[,/\s]+', keyword) if len(k.strip()) >= 3]
    filtered = [
        doc for doc in doctors
        if any(
            kw in (doc.get("sub_specialty", "") + " " + doc.get("conditions", "")).lower()
            for kw in keywords
        )
    ]
    return filtered if filtered else doctors


# ── DOCTOR NAME SEARCH ────────────────────────────────────────────────────────
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


# ── REQUEST MODEL ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list = []


# ── CHAT ENDPOINT ─────────────────────────────────────────────────────────────
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

    # Parse AI JSON response
    try:
        clean = re.sub(r"^\x60\x60\x60json\s*|^\x60\x60\x60\s*|\x60\x60\x60$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(clean)
        reply        = parsed.get("reply", raw)
        department   = parsed.get("department")
        sub_spec     = parsed.get("sub_specialty")
        is_emergency = parsed.get("is_emergency", False)
        doctor_query = parsed.get("doctor_query")
    except (json.JSONDecodeError, KeyError):
        json_match = re.search(r'\{[\s\S]*?"reply"[\s\S]*?\}', raw)
        if json_match:
            try:
                parsed       = json.loads(json_match.group())
                reply        = parsed.get("reply", raw)
                department   = parsed.get("department")
                sub_spec     = parsed.get("sub_specialty")
                is_emergency = parsed.get("is_emergency", False)
                doctor_query = parsed.get("doctor_query")
            except Exception:
                reply, department, sub_spec = raw, None, None
                is_emergency, doctor_query  = False, None
        else:
            reply, department, sub_spec = raw, None, None
            is_emergency, doctor_query  = False, None

    # Safety net: strip leaked JSON from reply
    reply = re.sub(r'\s*\{[\s\S]*?"reply"[\s\S]*?\}\s*', '', reply).strip()

    # Doctor name search
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

    # DUAL SEARCH: condition keyword search across all depts
    dept_doctors        = []
    condition_matches   = []
    search_was_filtered = False

    if not doctor_query and department:
        # Search using patient message + AI sub_specialty
        search_query = (sub_spec or "") + " " + request.message
        all_matches  = search_by_condition(search_query, preferred_dept=department)

        if all_matches:
            top_score      = all_matches[0]["score"]
            strong_matches = [m for m in all_matches if m["score"] >= top_score - 4]

            # Check if we found strong matches outside AI-suggested department
            other_dept_matches = [m for m in strong_matches if m["dept"] != department]

            if other_dept_matches:
                # Cross-department results found — surface them all
                condition_matches   = strong_matches[:15]
                search_was_filtered = True
            else:
                # All matches within AI-suggested department
                dept_doctors        = [m["doctor"] for m in strong_matches]
                search_was_filtered = True

        # Fallback: no keyword match found — show all dept doctors
        if not dept_doctors and not condition_matches:
            all_docs     = DOCTOR_DATA.get(department, [])
            dept_doctors = filter_by_sub_specialty(all_docs, sub_spec)

    return {
        "reply":               reply,
        "department":          department,
        "sub_specialty":       sub_spec,
        "is_emergency":        is_emergency,
        "doctor_query":        doctor_query,
        "doctor_results":      doctor_results,
        "doctors":             dept_doctors,
        "condition_matches":   condition_matches,
        "search_was_filtered": search_was_filtered,
        "ambiguous":           ambiguous,
    }


# ── /doctors ENDPOINT ─────────────────────────────────────────────────────────
@app.get("/doctors")
def get_doctors(department: str = Query(...), sub_specialty: str = Query(None)):
    if department in DOCTOR_DATA:
        all_docs = DOCTOR_DATA[department]
    else:
        q = department.lower()
        all_docs = []
        for dept_name, docs in DOCTOR_DATA.items():
            if q in dept_name.lower() or dept_name.lower() in q:
                all_docs = docs
                department = dept_name
                break
    return {"department": department, "doctors": filter_by_sub_specialty(all_docs, sub_specialty)}


# ── /departments ENDPOINT ─────────────────────────────────────────────────────
@app.get("/departments")
def get_departments():
    return {
        "departments": [
            {"name": dept, "doctor_count": len(docs)}
            for dept, docs in DOCTOR_DATA.items()
        ]
    }


# ── ROOT ──────────────────────────────────────────────────────────────────────
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