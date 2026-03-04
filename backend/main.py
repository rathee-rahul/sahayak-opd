from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import os, sys, json, re
from datetime import datetime

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

# ── TODAY DETECTION ──────────────────────────────────────────
TODAY_NAME = datetime.now().strftime("%A")  # e.g. "Wednesday"
TODAY_VARIANTS = {
    "Monday":    ["mon", "monday"],
    "Tuesday":   ["tue", "tuesday"],
    "Wednesday": ["wed", "wednesday"],
    "Thursday":  ["thu", "thursday"],
    "Friday":    ["fri", "friday"],
    "Saturday":  ["sat", "saturday"],
    "Sunday":    ["sun", "sunday"],
}

def is_available_today(opd_days: str) -> bool:
    if not opd_days:
        return False
    lower = opd_days.lower()
    return any(v in lower for v in TODAY_VARIANTS.get(TODAY_NAME, []))


# ── CONDITION SEARCH ─────────────────────────────────────────
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

            score = sum(2 for kw in raw_words if kw in combined)

            if score > 0:
                key = (dept, doc["name"])
                if key not in seen:
                    seen.add(key)
                    if preferred_dept and dept == preferred_dept:
                        score += 5
                    results.append({"dept": dept, "doctor": doc, "score": score})

    results.sort(key=lambda x: -x["score"])
    return results



# ── DEPARTMENT VERIFICATION (Option 2) ───────────────────────
# If LLM suggests a department but condition search finds stronger
# matches in another department, override with the better match.
SYMPTOM_DEPT_MAP = {
    # Cardiology
    "palpitations": "Cardiology (Heart)",
    "arrhythmia": "Cardiology (Heart)",
    "heart attack": "Cardiology (Heart)",
    "heart failure": "Cardiology (Heart)",
    "ecg": "Cardiology (Heart)",
    # Pulmonary
    "cough": "Pulmonary Medicine",
    "asthma": "Pulmonary Medicine",
    "copd": "Pulmonary Medicine",
    "tb": "Pulmonary Medicine",
    "tuberculosis": "Pulmonary Medicine",
    "haemoptysis": "Pulmonary Medicine",
    "ild": "Pulmonary Medicine",
    # Gastroenterology
    "acidity": "Gastroenterology (Stomach & Digestion)",
    "jaundice": "Gastroenterology (Stomach & Digestion)",
    "hepatitis": "Gastroenterology (Stomach & Digestion)",
    "ibs": "Gastroenterology (Stomach & Digestion)",
    "cirrhosis": "Gastroenterology (Stomach & Digestion)",
    # Neurology
    "epilepsy": "Neurology (Brain & Nerves)",
    "seizure": "Neurology (Brain & Nerves)",
    "migraine": "Neurology (Brain & Nerves)",
    "neuropathy": "Neurology (Brain & Nerves)",
    "multiple sclerosis": "Neurology (Brain & Nerves)",
    # Rheumatology
    "rheumatoid": "Rheumatology (Joint & Autoimmune)",
    "lupus": "Rheumatology (Joint & Autoimmune)",
    "autoimmune": "Rheumatology (Joint & Autoimmune)",
    "gout": "Rheumatology (Joint & Autoimmune)",
    "ankylosing": "Rheumatology (Joint & Autoimmune)",
    # Nephrology
    "creatinine": "Nephrology (Kidney Disease)",
    "dialysis": "Nephrology (Kidney Disease)",
    "nephritis": "Nephrology (Kidney Disease)",
    "chronic kidney": "Nephrology (Kidney Disease)",
    # Urology
    "kidney stone": "Urology (Kidney & Urinary)",
    "pathri": "Urology (Kidney & Urinary)",
    "prostate": "Urology (Kidney & Urinary)",
    "blood in urine": "Urology (Kidney & Urinary)",
    # Endocrinology
    "diabetes": "Endocrinology (Diabetes & Hormones)",
    "thyroid": "Endocrinology (Diabetes & Hormones)",
    "hormonal": "Endocrinology (Diabetes & Hormones)",
    # Dermatology
    "skin rash": "Dermatology & Venereology (Skin)",
    "psoriasis": "Dermatology & Venereology (Skin)",
    "eczema": "Dermatology & Venereology (Skin)",
    "hair loss": "Dermatology & Venereology (Skin)",
    # ENT
    "vertigo": "Otorhinolaryngology - ENT",
    "tinnitus": "Otorhinolaryngology - ENT",
    "sinusitis": "Otorhinolaryngology - ENT",
    "hearing loss": "Otorhinolaryngology - ENT",
    # Psychiatry
    "depression": "Psychiatry (Mental Health)",
    "anxiety": "Psychiatry (Mental Health)",
    "addiction": "Psychiatry (Mental Health)",
    "bipolar": "Psychiatry (Mental Health)",
    # Haematology
    "thalassemia": "Haematology (Blood Disorders)",
    "leukaemia": "Haematology (Blood Disorders)",
    "leukemia": "Haematology (Blood Disorders)",
    "bleeding disorder": "Haematology (Blood Disorders)",
    "platelet": "Haematology (Blood Disorders)",
    # Oncology
    "chemotherapy": "Oncology (Cancer)",
    "radiation therapy": "Oncology (Cancer)",
    "tumour": "Oncology (Cancer)",
    "cancer": "Oncology (Cancer)",
}

def verify_department(llm_dept: str, message: str) -> str:
    """
    Cross-check LLM suggested department against symptom keyword map.
    If a high-confidence keyword match points to a different dept, override.
    Returns the verified (possibly corrected) department name.
    """
    if not llm_dept or not message:
        return llm_dept

    msg_lower = message.lower()

    for keyword, correct_dept in SYMPTOM_DEPT_MAP.items():
        if keyword in msg_lower:
            # Only override if LLM picked a different department
            if correct_dept != llm_dept:
                return correct_dept
            else:
                return llm_dept  # LLM was correct, confirm it

    return llm_dept  # No override needed

def filter_by_sub_specialty(doctors: list, sub_specialty: str) -> list:
    if not sub_specialty:
        return doctors
    keyword = sub_specialty.lower()
    return [
        doc for doc in doctors
        if keyword in (doc.get("sub_specialty", "") + " " + doc.get("conditions", "")).lower()
    ]


# ── FUZZY DOCTOR NAME SEARCH ─────────────────────────────────
def search_doctor_by_name(query: str, hint_dept: str = None):
    query = query.lower().strip()
    results = []

    for dept, doctors in DOCTOR_DATA.items():
        for doc in doctors:
            similarity = fuzz.partial_ratio(query, doc["name"].lower())
            if similarity >= 75:
                score = similarity + (10 if hint_dept and dept == hint_dept else 0)
                results.append({"dept": dept, "doctor": doc, "score": score})

    results.sort(key=lambda x: -x["score"])
    return results[:10]


# ── TODAY'S DOCTORS ACROSS ALL DEPTS ─────────────────────────
def get_todays_doctors(department: str = None) -> list:
    results = []
    depts = {department: DOCTOR_DATA[department]} if department and department in DOCTOR_DATA else DOCTOR_DATA
    for dept, doctors in depts.items():
        for doc in doctors:
            if is_available_today(doc.get("opd_days", "")):
                results.append({"dept": dept, "doctor": doc})
    return results


# ── REQUEST MODEL ─────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list = []


# ── CHAT ENDPOINT ─────────────────────────────────────────────
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
        intent       = parsed.get("intent", "general")
        # ── OPTION 2: Verify department against symptom map ──
        if department and intent in ("find_department", "general"):
            department = verify_department(department, request.message)
    except json.JSONDecodeError:
        reply        = "System error. Kripya dobara try karein."
        department   = None
        sub_spec     = None
        is_emergency = False
        doctor_query = None
        intent       = "general"

    doctor_results    = []
    ambiguous         = False
    dept_doctors      = []
    condition_matches = []
    todays_doctors    = []

    # ── INTENT: DOCTOR SCHEDULE ───────────────────────────────
    if intent == "doctor_schedule" or doctor_query:
        matches = search_doctor_by_name(doctor_query, hint_dept=department)
        if matches:
            doctor_results = [{"dept": m["dept"], "doctor": m["doctor"]} for m in matches]
            # Check if ambiguous (multiple different doctors matched)
            unique_names = set(m["doctor"]["name"] for m in matches)
            ambiguous = len(unique_names) > 1 and len(doctor_query.split()) <= 1

    # ── INTENT: BROWSE DEPARTMENT ─────────────────────────────
    elif intent == "browse_department" and department:
        all_docs = DOCTOR_DATA.get(department, [])
        # Sort: today's non-JPNATC first, then others non-JPNATC, then JPNATC last
        def is_jpnatc(d): return (d.get("center", "") or "").upper() == "JPNATC"
        todays_main  = [d for d in all_docs if is_available_today(d.get("opd_days", "")) and not is_jpnatc(d)]
        others_main  = [d for d in all_docs if not is_available_today(d.get("opd_days", "")) and not is_jpnatc(d)]
        jpnatc_docs  = [d for d in all_docs if is_jpnatc(d)]
        dept_doctors = todays_main + others_main + jpnatc_docs

    # ── INTENT: FIND DEPARTMENT (symptom-based) ───────────────
    elif intent == "find_department" and department:
        search_query = (sub_spec or "") + " " + request.message
        all_matches  = search_by_condition(search_query, preferred_dept=department)

        if all_matches:
            # Push JPNATC doctors to end
            non_jpnatc = [m["doctor"] for m in all_matches[:10] if (m["doctor"].get("center","") or "").upper() != "JPNATC"]
            jpnatc     = [m["doctor"] for m in all_matches[:10] if (m["doctor"].get("center","") or "").upper() == "JPNATC"]
            dept_doctors = non_jpnatc + jpnatc
        else:
            all_docs = DOCTOR_DATA.get(department, [])
            dept_doctors = [d for d in all_docs if (d.get("center","") or "").upper() != "JPNATC"] +                            [d for d in all_docs if (d.get("center","") or "").upper() == "JPNATC"]

    # ── INTENT: EMERGENCY ─────────────────────────────────────
    elif intent == "emergency" or is_emergency:
        is_emergency = True
        department   = "Casualty / Emergency"

    # ── FALLBACK: old logic for general messages ──────────────
    elif department and not doctor_query:
        search_query = (sub_spec or "") + " " + request.message
        all_matches  = search_by_condition(search_query, preferred_dept=department)
        if all_matches:
            non_jpnatc = [m["doctor"] for m in all_matches[:10] if (m["doctor"].get("center","") or "").upper() != "JPNATC"]
            jpnatc     = [m["doctor"] for m in all_matches[:10] if (m["doctor"].get("center","") or "").upper() == "JPNATC"]
            dept_doctors = non_jpnatc + jpnatc
        else:
            all_docs = DOCTOR_DATA.get(department, [])
            dept_doctors = [d for d in all_docs if (d.get("center","") or "").upper() != "JPNATC"] +                            [d for d in all_docs if (d.get("center","") or "").upper() == "JPNATC"]

    return {
        "reply": reply,
        "department": department,
        "sub_specialty": sub_spec,
        "is_emergency": is_emergency,
        "doctor_query": doctor_query,
        "doctor_results": doctor_results,
        "doctors": dept_doctors,
        "condition_matches": condition_matches,
        "ambiguous": ambiguous,
        "intent": intent,
        "today": TODAY_NAME,
    }


# ── BROWSE DEPT ENDPOINT (for tile 3 direct API call) ────────
@app.get("/browse-department")
def browse_department(department: str = Query(...)):
    all_docs = DOCTOR_DATA.get(department, [])
    if not all_docs:
        return {"department": department, "today": [], "others": [], "today_name": TODAY_NAME}
    todays = [d for d in all_docs if is_available_today(d.get("opd_days", ""))]
    others = [d for d in all_docs if not is_available_today(d.get("opd_days", ""))]
    return {
        "department": department,
        "today": todays,
        "others": others,
        "today_name": TODAY_NAME,
    }


# ── TODAY'S DOCTORS ENDPOINT ──────────────────────────────────
@app.get("/todays-doctors")
def todays_doctors(department: str = Query(None)):
    results = get_todays_doctors(department)
    return {
        "today": TODAY_NAME,
        "count": len(results),
        "doctors": results
    }


# ── DEPARTMENTS LIST ──────────────────────────────────────────
@app.get("/departments")
def get_departments():
    return {
        "departments": [
            {
                "name": dept,
                "doctor_count": len(docs),
                "available_today": sum(1 for d in docs if is_available_today(d.get("opd_days", "")))
            }
            for dept, docs in DOCTOR_DATA.items()
        ]
    }


@app.get("/doctors")
def get_doctors(department: str = Query(...), sub_specialty: str = Query(None)):
    all_docs = DOCTOR_DATA.get(department, [])
    return {"department": department, "doctors": filter_by_sub_specialty(all_docs, sub_specialty)}


@app.get("/")
def home():
    return {
        "status": "Sahayak backend is running!",
        "today": TODAY_NAME,
        "total_departments": len(DOCTOR_DATA),
        "total_doctors": sum(len(v) for v in DOCTOR_DATA.values()),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))