"""
main.py — Sahayak v8
Full pipeline rewire.

v8 flow per /chat request:
  ① sanitize_input()           — PII scrub, always first
  ② keyword_scan() + LLM 1    — async parallel
  ③ red_flag_check()           — advisory signal only, never blocks routing
  ④ run_engine()               — score top3, severity, self-care
  ⑤ LLM 2 (clinical)          — final routing + reply
  ⑥ doctor fetch               — from doctor_data.json
  ⑦ log_if_ambiguous()         — async, fire-and-forget

All other endpoints (/browse-department, /todays-doctors,
/departments, /doctors) are UNCHANGED from v1.
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import asyncio, os, sys, json, re
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from groq import Groq
from openai import OpenAI
from thefuzz import fuzz

# ── v8 modules ────────────────────────────────────────────────
from sanitize        import sanitize_input, was_sanitized
from keyword_scan    import keyword_scan
from engine          import run_engine
from extractor_prompt import (
    EXTRACTOR_SYSTEM_PROMPT,
    build_extractor_messages,
    parse_extractor_response,
)
from clinical_prompt import (
    CLINICAL_SYSTEM_PROMPT,
    build_clinical_messages,
    parse_clinical_response,
)
from ambiguity_log import log_if_ambiguous

load_dotenv()

# ── API CLIENTS ───────────────────────────────────────────────
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
cerebras_client = OpenAI(
    base_url="https://api.cerebras.ai/v1",
    api_key=os.getenv("CEREBRAS_API_KEY")
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

frontend_path = os.path.join(os.path.dirname(__file__), "../frontend")
if os.path.exists(frontend_path):
    app.mount("/app", StaticFiles(directory=frontend_path, html=True), name="frontend")

# ── LOAD DOCTOR DATA ──────────────────────────────────────────
DOCTOR_DATA_PATH = os.path.join(os.path.dirname(__file__), "doctor_data.json")
with open(DOCTOR_DATA_PATH, "r", encoding="utf-8") as f:
    DOCTOR_DATA = json.load(f)

# ── REFERRAL-REQUIRED DEPARTMENTS ────────────────────────────
REFERRAL_REQUIRED_DEPTS = {
    "Cardiology (Heart)",
    "Neurology (Brain & Nerves)",
    "Neurosurgery (Brain Surgery)",
    "Endocrinology (Diabetes & Hormones)",
    "Urology (Kidney & Urinary)",
    "Nephrology",
    "Pulmonary Medicine",
    "Rheumatology (Joint & Autoimmune)",
    "Haematology (Blood Disorders)",
    "Gastroenterology (Stomach & Digestion)",
    "G.I. Surgery (Stomach Surgery)",
}

from departments import DEPARTMENTS

# ── BROWSE DEPARTMENT EXTRACTOR ───────────────────────────────
def extract_browse_dept(message: str) -> str | None:
    """
    Extract department name from a browse query using fuzzy matching.
    e.g. "G.I. Surgery mein kaun se doctors hain?" → "G.I. Surgery (Stomach Surgery)"
    Prefers longer/more-specific matches when scores are close.
    Returns best matching department or None.
    """
    msg_lower = message.lower()
    best_dept  = None
    best_score = 0

    for dept in DEPARTMENTS:
        short_name = dept.split("(")[0].strip().lower()
        full_name  = dept.lower()
        score = max(
            fuzz.token_set_ratio(msg_lower, full_name),
            fuzz.token_set_ratio(msg_lower, short_name),
            fuzz.partial_ratio(msg_lower, short_name),
        )
        if score > best_score or (
            score == best_score and best_dept and len(dept) > len(best_dept)
        ):
            best_score = score
            best_dept  = dept

    return best_dept if best_score >= 60 else None


TODAY_NAME = datetime.now().strftime("%A")
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


# ══════════════════════════════════════════════════════════════
# DOCTOR SEARCH FUNCTIONS — UNCHANGED FROM v1
# ══════════════════════════════════════════════════════════════

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


def get_todays_doctors(department: str = None) -> list:
    results = []
    depts = {department: DOCTOR_DATA[department]} if department and department in DOCTOR_DATA else DOCTOR_DATA
    for dept, doctors in depts.items():
        for doc in doctors:
            if is_available_today(doc.get("opd_days", "")):
                results.append({"dept": dept, "doctor": doc})
    return results


def filter_by_sub_specialty(doctors: list, sub_specialty: str) -> list:
    if not sub_specialty:
        return doctors
    keyword = sub_specialty.lower()
    return [
        doc for doc in doctors
        if keyword in (doc.get("sub_specialty", "") + " " + doc.get("conditions", "")).lower()
    ]


def _sort_jpnatc_last(doctors: list) -> list:
    """Push JPNATC doctors to end — preserved from v1."""
    non_jpnatc = [d for d in doctors if (d.get("center", "") or "").upper() != "JPNATC"]
    jpnatc     = [d for d in doctors if (d.get("center", "") or "").upper() == "JPNATC"]
    return non_jpnatc + jpnatc


# ══════════════════════════════════════════════════════════════
# LLM CALL FUNCTIONS
# ══════════════════════════════════════════════════════════════

def call_llm(system_prompt: str, messages: list, max_tokens: int = 512, label: str = "") -> str:
    """Groq primary → Cerebras fallback. Returns raw JSON string."""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=max_tokens,
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        print(f"[LLM:{label}] Groq OK")
        return response.choices[0].message.content
    except Exception as e:
        print(f"[LLM:{label}] Groq failed: {e} → Cerebras")

    try:
        response = cerebras_client.chat.completions.create(
            model="llama3.1-8b",
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=max_tokens,
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        print(f"[LLM:{label}] Cerebras OK")
        return response.choices[0].message.content
    except Exception as e:
        print(f"[LLM:{label}] Cerebras failed: {e}")

    return ""


async def call_llm_async(
    system_prompt: str, messages: list, max_tokens: int = 512, label: str = ""
) -> str:
    """Async wrapper — runs LLM call in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, call_llm, system_prompt, messages, max_tokens, label
    )


# ══════════════════════════════════════════════════════════════
# REQUEST MODEL
# ══════════════════════════════════════════════════════════════

class ChatRequest(BaseModel):
    message:            str
    history:            list = []
    confirmed_symptoms: list = []
    denied_symptoms:    list = []
    follow_up_count:    int  = 0
    session_id:         str  = ""


# ══════════════════════════════════════════════════════════════
# DOCTOR FETCH HELPER
# ══════════════════════════════════════════════════════════════

def fetch_doctors_for_dept(department: str, raw_message: str) -> list:
    """
    Fetch and sort doctors for a department.
    Condition-match first, falls back to full dept list.
    JPNATC always last.
    """
    if not department or department == "Casualty / Emergency":
        return []
    all_matches = search_by_condition(raw_message, preferred_dept=department)
    if all_matches:
        matched = [m["doctor"] for m in all_matches[:10]]
        return _sort_jpnatc_last(matched)
    all_docs = DOCTOR_DATA.get(department, [])
    return _sort_jpnatc_last(all_docs)


# ══════════════════════════════════════════════════════════════
# /chat ENDPOINT — v8 PIPELINE
# ══════════════════════════════════════════════════════════════

@app.post("/chat")
async def chat(request: ChatRequest):

    raw_message        = request.message
    history            = request.history
    confirmed_symptoms = request.confirmed_symptoms or []
    denied_symptoms    = request.denied_symptoms    or []
    follow_up_count    = request.follow_up_count    or 0
    session_id         = request.session_id         or ""

    # ── STEP 1: PII Scrub ─────────────────────────────────────
    sanitized = sanitize_input(raw_message)
    if was_sanitized(raw_message, sanitized):
        print("[PII] Input scrubbed")

    # ── STEP 2: Parallel — keyword_scan + LLM 1 ──────────────
    extractor_messages = build_extractor_messages(sanitized, history)

    raw_flags_task = asyncio.get_event_loop().run_in_executor(
        None, keyword_scan, sanitized
    )
    llm1_task = call_llm_async(
        system_prompt = EXTRACTOR_SYSTEM_PROMPT,
        messages      = extractor_messages,
        max_tokens    = 400,
        label         = "LLM1"
    )

    raw_flags_result, llm1_raw = await asyncio.gather(raw_flags_task, llm1_task)

    raw_flags = raw_flags_result
    features  = parse_extractor_response(llm1_raw)

    print(f"[Engine] primary={features.get('primary_complaint')} "
          f"flags={[k for k, v in raw_flags.items() if v]}")

    # ── STEP 3 + 4: Engine ───────────────────────────────────
    engine_output = run_engine(features, raw_flags)

    print(f"[Engine] advisory={engine_output['show_advisory']} "
          f"selfcare={engine_output['is_selfcare']} "
          f"severity={engine_output['severity']} "
          f"top3={[d['dept'].split('(')[0].strip() for d in engine_output['top3']]} "
          f"gap={engine_output['confidence_gap']}%")

    # ── STEP 5: LLM 2 — clinical routing ─────────────────────
    clinical_messages = build_clinical_messages(
        features           = features,
        engine_output      = engine_output,
        history            = history,
        confirmed_symptoms = confirmed_symptoms,
        denied_symptoms    = denied_symptoms,
        follow_up_count    = follow_up_count,
    )

    llm2_raw = await call_llm_async(
        system_prompt = CLINICAL_SYSTEM_PROMPT,
        messages      = clinical_messages,
        max_tokens    = 512,
        label         = "LLM2"
    )

    clinical = parse_clinical_response(llm2_raw)

    print(f"[LLM2] dept={clinical.get('final_dept')} "
          f"severity={clinical.get('severity')} "
          f"confidence={clinical.get('confidence')} "
          f"python_correct={clinical.get('python_correct')} "
          f"follow_up={clinical.get('follow_up_needed')}")

    # ── STEP 6: Doctor fetch ──────────────────────────────────
    final_dept       = clinical.get("final_dept")
    show_advisory    = engine_output["show_advisory"]
    is_selfcare      = engine_output["is_selfcare"]
    referral_required = (
        clinical.get("referral_required", False) or
        (final_dept in REFERRAL_REQUIRED_DEPTS)
    )

    context_flags = features.get("context_flags", {})
    needs_doctor  = context_flags.get("needs_doctor_name", False)
    is_browse     = context_flags.get("is_browse_request", False)

    # ── BROWSE OVERRIDE ──────────────────────────────────────
    if is_browse:
        python_browse_dept = extract_browse_dept(sanitized)
        if python_browse_dept:
            final_dept = python_browse_dept
            print(f"[Browse] Python extracted dept: {final_dept}")

    doctor_results = []
    dept_doctors   = []
    ambiguous      = False

    if needs_doctor:
        # ── BUG D FIX: added ,| to regex so "Dr. X, Dept" also matches ──
        name_match = re.search(
            r'\bdr\.?\s+([a-z][a-z\s]{2,30}?)(?:\s+ka|\s+ke|\s+ki|\s+kab|\s+ka\s|,|\?|$)',
            sanitized.lower()
        )
        doctor_query = name_match.group(1).strip() if name_match else sanitized
        matches = search_doctor_by_name(doctor_query, hint_dept=final_dept)
        if matches:
            doctor_results = [{"dept": m["dept"], "doctor": m["doctor"]} for m in matches]
            unique_names   = set(m["doctor"]["name"] for m in matches)
            ambiguous      = len(unique_names) > 1 and len(doctor_query.split()) <= 1

    elif is_browse and final_dept:
        all_docs = DOCTOR_DATA.get(final_dept, [])
        def _is_jpnatc(d): return (d.get("center", "") or "").upper() == "JPNATC"
        todays_main  = [d for d in all_docs if is_available_today(d.get("opd_days", "")) and not _is_jpnatc(d)]
        others_main  = [d for d in all_docs if not is_available_today(d.get("opd_days", "")) and not _is_jpnatc(d)]
        jpnatc_docs  = [d for d in all_docs if _is_jpnatc(d)]
        dept_doctors = todays_main + others_main + jpnatc_docs

    elif is_selfcare:
        dept_doctors = []

    elif final_dept:
        dept_doctors = fetch_doctors_for_dept(final_dept, sanitized)

    # ── STEP 7: Async ambiguity log ───────────────────────────
    asyncio.create_task(log_if_ambiguous(
        sanitized_input    = sanitized,
        features           = features,
        engine_output      = engine_output,
        clinical_output    = clinical,
        confirmed_symptoms = confirmed_symptoms,
        denied_symptoms    = denied_symptoms,
        follow_up_count    = follow_up_count,
        session_id         = session_id or None,
    ))

    # ── RESPONSE ──────────────────────────────────────────────
    new_follow_up_count = follow_up_count + (1 if clinical.get("follow_up_needed") else 0)

    return {
        "reply":          clinical.get("reply", ""),
        "disclaimer":     clinical.get("disclaimer", "Yeh preliminary suggestion hai. OPD mein doctor properly assess karenge."),
        "department":     final_dept,
        "severity":       clinical.get("severity", "routine"),
        "action_advice":  clinical.get("action_advice"),
        "reason":         clinical.get("reason"),

        "follow_up_needed":   clinical.get("follow_up_needed", False),
        "follow_up_question": clinical.get("follow_up_question"),
        "follow_up_count":    new_follow_up_count,

        "show_advisory":    show_advisory,
        "is_selfcare":      is_selfcare,
        "referral_required": referral_required,

        "doctors":        dept_doctors,
        "doctor_results": doctor_results,
        "ambiguous":      ambiguous,

        "debug": {
            "python_top3":    engine_output.get("top3"),
            "confidence_gap": engine_output.get("confidence_gap"),
            "llm_confidence": clinical.get("confidence"),
            "python_correct": clinical.get("python_correct"),
            "pii_scrubbed":   was_sanitized(raw_message, sanitized),
        },

        "today":  TODAY_NAME,
        "intent": (
            "selfcare"          if is_selfcare  else
            "doctor_schedule"   if needs_doctor else
            "browse_department" if is_browse    else
            "find_department"   if final_dept   else
            "general"
        ),
    }


# ══════════════════════════════════════════════════════════════
# ALL OTHER ENDPOINTS — UNCHANGED FROM v1
# ══════════════════════════════════════════════════════════════

@app.get("/browse-department")
def browse_department(department: str = Query(...)):
    all_docs = DOCTOR_DATA.get(department, [])
    if not all_docs:
        return {"department": department, "today": [], "others": [], "today_name": TODAY_NAME}
    todays = [d for d in all_docs if is_available_today(d.get("opd_days", ""))]
    others = [d for d in all_docs if not is_available_today(d.get("opd_days", ""))]
    return {
        "department": department,
        "today":      todays,
        "others":     others,
        "today_name": TODAY_NAME,
    }


@app.get("/todays-doctors")
def todays_doctors(department: str = Query(None)):
    results = get_todays_doctors(department)
    return {"today": TODAY_NAME, "count": len(results), "doctors": results}


@app.get("/departments")
def get_departments():
    return {
        "departments": [
            {
                "name":            dept,
                "doctor_count":    len(docs),
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
        "status":            "Sahayak v8 is running!",
        "today":             TODAY_NAME,
        "total_departments": len(DOCTOR_DATA),
        "total_doctors":     sum(len(v) for v in DOCTOR_DATA.values()),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))