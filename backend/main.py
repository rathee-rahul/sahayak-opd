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

from fastapi import FastAPI, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import asyncio, os, sys, json, re, requests
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/gemini-2.5-flash:generateContent"
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

# ── HINDI KEYWORD → DEPARTMENT MAP ────────────────────────────
# Only needed for Hindi words that transliterate too differently
# from English to fuzzy-match (e.g. "nyurolaji" ≠ "neurology").
# Pure English/Hinglish queries go straight to fuzzy matching.
_HINDI_DEPT_KEYWORDS = {
    # Cardiology
    "कार्डियोलॉजी":"Cardiology (Heart)",   "हृदय":"Cardiology (Heart)",
    "दिल":"Cardiology (Heart)",
    # Neurology
    "न्यूरोलॉजी":"Neurology (Brain & Nerves)", "दिमाग":"Neurology (Brain & Nerves)",
    # Neurosurgery
    "न्यूरोसर्जरी":"Neurosurgery (Brain Surgery)",
    # Orthopaedics
    "ऑर्थोपेडिक्स":"Orthopaedics (Bones & Joints)", "हड्डी":"Orthopaedics (Bones & Joints)",
    "जोड़":"Orthopaedics (Bones & Joints)",
    # Gastroenterology
    "गैस्ट्रोएंटरोलॉजी":"Gastroenterology (Stomach & Digestion)", "पेट":"Gastroenterology (Stomach & Digestion)",
    # Pulmonary
    "पल्मोनरी":"Pulmonary Medicine", "फेफड़े":"Pulmonary Medicine",
    # Nephrology
    "नेफ्रोलॉजी":"Nephrology", "किडनी":"Nephrology",
    # Urology
    "यूरोलॉजी":"Urology (Kidney & Urinary)",
    # Endocrinology
    "एंडोक्राइनोलॉजी":"Endocrinology (Diabetes & Hormones)",
    "डायबिटीज":"Endocrinology (Diabetes & Hormones)", "शुगर":"Endocrinology (Diabetes & Hormones)",
    "थायरॉइड":"Endocrinology (Diabetes & Hormones)",
    # Dermatology
    "डर्मेटोलॉजी":"Dermatology & Venereology (Skin)", "त्वचा":"Dermatology & Venereology (Skin)",
    "चर्म":"Dermatology & Venereology (Skin)",
    # ENT
    "ईएनटी":"Otorhinolaryngology - ENT", "कान":"Otorhinolaryngology - ENT",
    "नाक":"Otorhinolaryngology - ENT",
    # Ophthalmology
    "ऑफ्थैल्मोलॉजी":"Ophthalmology (Eyes)", "आंख":"Ophthalmology (Eyes)",
    "नेत्र":"Ophthalmology (Eyes)",
    # Psychiatry
    "साइकेट्री":"Psychiatry (Mental Health)", "मानसिक":"Psychiatry (Mental Health)",
    # Paediatrics
    "पेडियाट्रिक्स":"Paediatrics Medicine (Children)", "बच्चे":"Paediatrics Medicine (Children)",
    "बच्चों":"Paediatrics Medicine (Children)",
    # Obs & Gynae
    "गायनेकोलॉजी":"Obstetrics & Gynaecology", "प्रसूति":"Obstetrics & Gynaecology",
    # Oncology
    "ऑन्कोलॉजी":"Oncology (Cancer)", "कैंसर":"Oncology (Cancer)",
    # Haematology
    "हेमेटोलॉजी":"Haematology (Blood Disorders)", "खून":"Haematology (Blood Disorders)",
    # Rheumatology
    "रूमेटोलॉजी":"Rheumatology (Joint & Autoimmune)",
    # Dental
    "डेंटल":"Dental Surgery", "दांत":"Dental Surgery",
    # Geriatric
    "जेरियाट्रिक":"Geriatric Medicine (Elderly Care)", "बुजुर्ग":"Geriatric Medicine (Elderly Care)",
    # Rehab
    "फिजियोथेरेपी":"Physical Medicine & Rehabilitation",
    "रिहैबिलिटेशन":"Physical Medicine & Rehabilitation",
    # Surgery General
    "सर्जरी":"Surgery (General)",
    # Medicine General
    "मेडिसिन":"Medicine (General)",
}

# ── HINGLISH / ENGLISH CONCEPT KEYWORDS ─────────────────────
# For words that don't fuzzy-match dept names even after transliteration
# e.g. "sugar" → "Endocrinology", "kidney" → "Nephrology"
_CONCEPT_KEYWORDS = {
    # body parts / common names → dept
    "kidney":    "Nephrology",
    "kidni":     "Nephrology",          # transliterated किडनी
    "sugar":     "Endocrinology (Diabetes & Hormones)",
    "diabetes":  "Endocrinology (Diabetes & Hormones)",
    "thyroid":   "Endocrinology (Diabetes & Hormones)",
    "ankha":     "Ophthalmology (Eyes)",  # transliterated आंख
    "aankh":     "Ophthalmology (Eyes)",
    "eye":       "Ophthalmology (Eyes)",
    "eyes":      "Ophthalmology (Eyes)",
    "haddi":     "Orthopaedics (Bones & Joints)",  # transliterated हड्डी
    "hddi":      "Orthopaedics (Bones & Joints)",
    "bone":      "Orthopaedics (Bones & Joints)",
    "bones":     "Orthopaedics (Bones & Joints)",
    "joint":     "Orthopaedics (Bones & Joints)",
    "dil":       "Cardiology (Heart)",
    "heart":     "Cardiology (Heart)",
    "dimag":     "Neurology (Brain & Nerves)",
    "brain":     "Neurology (Brain & Nerves)",
    "cancer":    "Oncology (Cancer)",
    "blood":     "Haematology (Blood Disorders)",
    "khoon":     "Haematology (Blood Disorders)",
    "skin":      "Dermatology & Venereology (Skin)",
    "tvcha":     "Dermatology & Venereology (Skin)",  # transliterated त्वचा
    "bachon":    "Paediatrics Medicine (Children)",   # transliterated बच्चों
    "bacho":     "Paediatrics Medicine (Children)",
    "children":  "Paediatrics Medicine (Children)",
    "child":     "Paediatrics Medicine (Children)",
    "pet":       "Gastroenterology (Stomach & Digestion)",
    "stomach":   "Gastroenterology (Stomach & Digestion)",
    "lungs":     "Pulmonary Medicine",
    "sans":      "Pulmonary Medicine",               # saans = breathing
    "mental":    "Psychiatry (Mental Health)",
    "mansik":    "Psychiatry (Mental Health)",       # transliterated मानसिक
    "teeth":     "Dental Surgery",
    "dant":      "Dental Surgery",                   # transliterated दांत
    "elderly":   "Geriatric Medicine (Elderly Care)",
    "bujurg":    "Geriatric Medicine (Elderly Care)", # transliterated बुजुर्ग
    "rehab":     "Physical Medicine & Rehabilitation",
    "ear":       "Otorhinolaryngology - ENT",
    "nose":      "Otorhinolaryngology - ENT",
    "throat":    "Otorhinolaryngology - ENT",
    "kan":       "Otorhinolaryngology - ENT",        # transliterated कान
    "nak":       "Otorhinolaryngology - ENT",        # transliterated नाक
    "gala":      "Otorhinolaryngology - ENT",        # transliterated गला
    "ent":       "Otorhinolaryngology - ENT",   # whole-word only (see extract_browse_dept)
    "nyuro":        "Neurology (Brain & Nerves)",        # transliterated न्यूरो
    "nyurolaji":    "Neurology (Brain & Nerves)",
    "neurology":    "Neurology (Brain & Nerves)",
    # Neurosurgery must come BEFORE "neuro" so substring match hits it first
    "neurosurgery": "Neurosurgery (Brain Surgery)",
    "brain surgery":"Neurosurgery (Brain Surgery)",
    "brain tumor":  "Neurosurgery (Brain Surgery)",
    "brain tumour": "Neurosurgery (Brain Surgery)",
    "spine surgery":"Neurosurgery (Brain Surgery)",
    "spinal surgery":"Neurosurgery (Brain Surgery)",
    "head injury":  "Neurosurgery (Brain Surgery)",
    "nyurosarjari": "Neurosurgery (Brain Surgery)",  # Hindi transliteration
    "neuro":        "Neurology (Brain & Nerves)",    # generic — after neurosurgery
}

# ── BROWSE DEPARTMENT EXTRACTOR ───────────────────────────────
def extract_browse_dept(message: str) -> str | None:
    """
    Extract department from browse query. Three-step approach:
    1. Exact/fuzzy match against dept names — explicit dept names ALWAYS win
    2. Concept keyword match — handles Hindi/transliterated words like "kidney", "bachon"
    3. Lower-threshold fuzzy fallback
    Note: message is already transliterated to Roman by the time it reaches here.
    """
    msg_lower = message.lower()

    # Step 1 — Fuzzy match against English department names FIRST
    # This ensures explicit dept names (e.g. "Cardiothoracic & Vascular Surgery")
    # are matched before concept keywords like "heart" can intercept.
    best_dept  = None
    best_score = 0

    for dept in DEPARTMENTS:
        short_name = dept.split("(")[0].strip().lower()
        full_name  = dept.lower()
        score = max(
            fuzz.token_set_ratio(msg_lower, full_name),
            fuzz.token_set_ratio(msg_lower, short_name),
            fuzz.partial_ratio(msg_lower,   short_name),
        )
        if score > best_score or (
            score == best_score and best_dept and len(dept) > len(best_dept)
        ):
            best_score = score
            best_dept  = dept

    # High-confidence fuzzy match → return immediately, skip concept keywords
    if best_score >= 75:
        print(f"[Browse] Fuzzy match: '{best_dept}' score={best_score}")
        return best_dept

    # Step 2 — Concept keyword match (for Hindi/transliterated words)
    # Short keywords (<=4 chars e.g. "ent", "pet", "dil") → whole word match only
    # Longer keywords → substring match is fine
    # IMPORTANT: dict is ordered — neurosurgery entries come before "neuro"
    words = set(re.split(r'[\s,?।]+', msg_lower))
    for keyword, dept in _CONCEPT_KEYWORDS.items():
        if keyword.startswith("#"):
            continue
        matched = keyword in words if len(keyword) <= 4 else keyword in msg_lower
        if matched:
            # Guard: don't let "neuro" match when message clearly says "neurosurgery"
            if keyword == "neuro" and "surgery" in msg_lower:
                continue
            print(f"[Browse] Concept keyword: '{keyword}' → '{dept}'")
            return dept

    # Step 3 — Lower-threshold fuzzy fallback
    print(f"[Browse] msg='{msg_lower[:50]}' best='{best_dept}' score={best_score}")
    return best_dept if best_score >= 60 else None


TODAY_VARIANTS = {
    "Monday":    ["mon", "monday"],
    "Tuesday":   ["tue", "tuesday"],
    "Wednesday": ["wed", "wednesday"],
    "Thursday":  ["thu", "thursday"],
    "Friday":    ["fri", "friday"],
    "Saturday":  ["sat", "saturday"],
    "Sunday":    ["sun", "sunday"],
}

def get_today_name() -> str:
    """Return current day name — computed per-call so it never goes stale."""
    return datetime.now().strftime("%A")

# Module-level alias kept only for endpoints that need it at import time.
# All request handlers must call get_today_name() directly.
TODAY_NAME = get_today_name()

def is_available_today(opd_days: str) -> bool:
    if not opd_days:
        return False
    lower = opd_days.lower()
    return any(v in lower for v in TODAY_VARIANTS.get(get_today_name(), []))


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


# ── Hindi → Roman transliteration ────────────────────────────────────────────
# Converts Devanagari voice transcriptions to approximate English phonetics
# so fuzzy matching works against English doctor names in the database.
# e.g. "शर्मा" → "shrma" which fuzzy-matches "sharma" at 80%

_CONSONANTS = {
    'क':'k','ख':'kh','ग':'g','घ':'gh','ङ':'ng',
    'च':'ch','छ':'chh','ज':'j','झ':'jh','ञ':'n',
    'ट':'t','ठ':'th','ड':'d','ढ':'dh','ण':'n',
    'त':'t','थ':'th','द':'d','ध':'dh','न':'n',
    'प':'p','फ':'f','ब':'b','भ':'bh','म':'m',
    'य':'y','र':'r','ल':'l','व':'v',
    'श':'sh','ष':'sh','स':'s','ह':'h',
    'ड़':'r','ढ़':'rh',
}
_VOWELS = {
    'अ':'a','आ':'a','इ':'i','ई':'i','उ':'u','ऊ':'u',
    'ए':'e','ऐ':'ai','ओ':'o','औ':'au','ऋ':'ri','ऑ':'o',
}
_MATRAS = {
    'ा':'a','ि':'i','ी':'i','ु':'u','ू':'u',
    'े':'e','ै':'ai','ो':'o','ौ':'au','ृ':'ri',
    'ं':'n','ः':'','ँ':'n',
}
_VIRAMA = '्'


def transliterate_hindi(text: str) -> str:
    """
    Devanagari → approximate Roman phonetics for fuzzy name matching.
    Not perfect but scores 65-100% fuzzy match against real English spellings.
    Returns original text unchanged if it contains no Devanagari characters.
    """
    if not any('\u0900' <= ch <= '\u097F' for ch in text):
        return text  # already Roman/English — skip

    out = []
    chars = list(text)
    i, n = 0, len(chars)

    while i < n:
        ch = chars[i]
        if ch in _VOWELS:
            out.append(_VOWELS[ch]); i += 1
        elif ch in _CONSONANTS:
            roman = _CONSONANTS[ch]; i += 1
            if i < n and chars[i] == _VIRAMA:
                out.append(roman); i += 1          # virama: no inherent 'a'
            elif i < n and chars[i] in _MATRAS:
                out.append(roman + _MATRAS[chars[i]]); i += 1
            elif i < n and chars[i] in _CONSONANTS:
                out.append(roman)                   # consonant cluster: no 'a'
            else:
                out.append(roman + 'a')             # inherent 'a'
        elif ch in _MATRAS:
            out.append(_MATRAS[ch]); i += 1
        elif ch == _VIRAMA:
            i += 1
        elif ch == ' ':
            out.append(' '); i += 1
        elif ord(ch) < 128:
            out.append(ch.lower()); i += 1
        else:
            i += 1  # skip unknown unicode

    import re as _re
    return _re.sub(r'\s+', ' ', ''.join(out)).strip().lower()


def search_doctor_by_name(query: str, hint_dept: str = None):
    # Input is already transliterated to Roman upstream (sanitize step)
    query_lower = query.strip().lower()

    results = []
    for dept, doctors in DOCTOR_DATA.items():
        for doc in doctors:
            doc_name_lower = doc["name"].lower()
            # Remove "Dr." / "Prof." prefix for cleaner matching
            doc_name_clean = re.sub(r'^(dr\.?|prof\.?)\s*', '', doc_name_lower).strip()
            similarity = max(
                fuzz.partial_ratio(query_lower, doc_name_lower),
                fuzz.partial_ratio(query_lower, doc_name_clean),
            )
            if similarity >= 65:  # lowered from 75 — transliteration is approx
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

def call_gemini(system_prompt: str, messages: list, max_tokens: int = 512, label: str = "") -> str:
    """
    Call Gemini 2.0 Flash via REST API.
    Gemini uses a different format than OpenAI-compatible APIs:
      - system prompt goes as first "user" turn with role "user" + model "model" ack
      - contents array instead of messages array
      - response in candidates[0].content.parts[0].text
    Returns raw JSON string or raises on failure.
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not set")

    # Build contents array — system prompt first, then conversation
    contents = [
        {"role": "user",  "parts": [{"text": system_prompt}]},
        {"role": "model", "parts": [{"text": "Understood. I will respond only in valid JSON."}]},
    ]
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",  # force JSON output
        }
    }

    resp = requests.post(
        f"{GEMINI_URL}?key={GEMINI_API_KEY}",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=15,
    )

    if resp.status_code != 200:
        raise Exception(f"Gemini HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    print(f"[LLM:{label}] Gemini OK")
    return text


def call_llm(system_prompt: str, messages: list, max_tokens: int = 512, label: str = "") -> str:
    """
    LLM cascade: Gemini 2.0 Flash → Groq 70B → Cerebras 8B
    Returns raw JSON string. Empty string if all fail.
    """
    # ── Primary: Gemini 2.0 Flash ─────────────────────────────
    try:
        return call_gemini(system_prompt, messages, max_tokens, label)
    except Exception as e:
        print(f"[LLM:{label}] Gemini failed: {e} → Groq")

    # ── Fallback 1: Groq 70B ──────────────────────────────────
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

    # ── Fallback 2: Cerebras 8B ───────────────────────────────
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
    active_intent:      str  = ""   # "doctor_schedule" | "browse_department" | ""
    age:                int  = None   # pre-filled from age/gender chip row
    gender:             str  = None   # "male" | "female" | None


# ══════════════════════════════════════════════════════════════
# DOCTOR FETCH HELPER
# ══════════════════════════════════════════════════════════════

def fetch_doctors_for_dept(department: str, raw_message: str) -> list:
    """
    Fetch and sort doctors for a department.
    Condition-match first, falls back to full dept list.
    ONLY returns doctors from the specified department — no cross-dept bleed.
    Within matches: today-available doctors always shown first.
    JPNATC always last.
    """
    if not department or department == "Casualty / Emergency":
        return []

    def _is_jpnatc(d): return (d.get("center", "") or "").upper() == "JPNATC"

    all_matches = search_by_condition(raw_message, preferred_dept=department)
    # CRITICAL: filter to preferred dept only — cross-dept matches are irrelevant here
    dept_matches = [m for m in all_matches if m["dept"] == department]
    if dept_matches:
        # Attach the doctor's own dept to the object so buildCard labels it correctly
        matched = [{**m["doctor"], "_dept": m["dept"]} for m in dept_matches[:10]]
        today_non_j = [d for d in matched if is_available_today(d.get("opd_days", "")) and not _is_jpnatc(d)]
        other_non_j = [d for d in matched if not is_available_today(d.get("opd_days", "")) and not _is_jpnatc(d)]
        jpnatc      = [d for d in matched if _is_jpnatc(d)]
        return today_non_j + other_non_j + jpnatc

    # Fallback: no condition match in dept — show all dept doctors
    all_docs = DOCTOR_DATA.get(department, [])
    tagged = [{**d, "_dept": department} for d in all_docs]
    return _sort_jpnatc_last(tagged)


# ══════════════════════════════════════════════════════════════
# /chat ENDPOINT — v8 PIPELINE
# ══════════════════════════════════════════════════════════════

@app.post("/chat")
async def chat(request: ChatRequest, background_tasks: BackgroundTasks):

    raw_message        = request.message
    history            = request.history
    confirmed_symptoms = request.confirmed_symptoms or []
    denied_symptoms    = request.denied_symptoms    or []
    follow_up_count    = request.follow_up_count    or 0
    session_id         = request.session_id         or ""
    active_intent      = request.active_intent      or ""
    prefilled_age      = request.age
    prefilled_gender   = request.gender

    # ── STEP 1: PII Scrub ─────────────────────────────────────
    sanitized = sanitize_input(raw_message)
    if was_sanitized(raw_message, sanitized):
        print("[PII] Input scrubbed")

    # Transliterate Devanagari → Roman so ALL downstream processing
    # (keyword_scan, dept extract, doctor search, LLM prompts) works
    # whether user typed Hindi or spoke via voice.
    # e.g. "कार्डियोलॉजी के डॉक्टर" → "cardiyoloji ke doctor"
    original_for_log = sanitized
    sanitized = transliterate_hindi(sanitized)
    if sanitized != original_for_log:
        print(f"[Transliterate] '{original_for_log[:50]}' → '{sanitized[:50]}'")

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
    features  = parse_extractor_response(llm1_raw, raw_input=sanitized)

    # ── LLM1 RESCUE: if primary_complaint is None but keyword_scan caught flags,
    # synthesize a primary_complaint from the strongest flag so engine can score it.
    # This covers bare inputs like "chest pain", "bukhar", "headache".
    if not features.get("primary_complaint") and raw_flags:
        _FLAG_TO_COMPLAINT = {
            "chest_pain":      "chest pain",
            "breathlessness":  "breathlessness",
            "unconscious":     "unconscious",
            "seizure":         "seizure",
            "stroke":          "stroke",
            "heavy_bleeding":  "heavy bleeding",
            "arm_pain":        "arm pain",
            "sweating":        "sweating",
        }
        for flag, complaint in _FLAG_TO_COMPLAINT.items():
            if raw_flags.get(flag):
                features["primary_complaint"] = complaint
                if not features.get("body_part") and flag == "chest_pain":
                    features["body_part"] = "chest"
                print(f"[LLM1 Rescue] primary set from flag: {complaint}")
                break

    # Merge prefilled age/gender from chip row if LLM1 didn't extract them
    if prefilled_age and not features.get("age"):
        features["age"] = prefilled_age
    if prefilled_gender and not features.get("gender"):
        features["gender"] = prefilled_gender

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

    # ── ACTIVE INTENT OVERRIDE ────────────────────────────────
    # Frontend sends active_intent when user is in Tile 2 (doctor search) mode.
    # This catches cases where extractor LLM misses needs_doctor_name=true
    # (e.g. user types just a name without "Dr." prefix).
    if active_intent == "doctor_schedule":
        needs_doctor = True
    elif active_intent == "browse_department":
        is_browse = True

    # ── BROWSE OVERRIDE ──────────────────────────────────────
    if is_browse:
        python_browse_dept = extract_browse_dept(sanitized)
        if python_browse_dept:
            final_dept = python_browse_dept
            print(f"[Browse] Python extracted dept: {final_dept}")

    doctor_results = []
    dept_doctors   = []
    ambiguous      = False
    doctor_query   = None

    if needs_doctor:
        text_lower = sanitized.lower()

        # ── NAME EXTRACTION ───────────────────────────────────
        # Handles English: "Dr. Sharma", "Dr Anita Dhar ka schedule"
        # Handles Hindi:   "डॉक्टर कमल कटारिया की ओपीडी", "डॉ संजय वाधवा"
        name_match = (
            # English Dr. prefix
            re.search(
                r'\bdr\.?\s+([a-z][a-z\s]{1,30}?)(?:\s+ka|\s+ke|\s+ki|\s+kab|\s+ka\s|,|\?|$)',
                text_lower
            ) or
            # English "doctor" prefix (without Dr.)
            re.search(
                r'\bdoctor\s+([a-z][a-z\s]{1,30}?)(?:\s+ka|\s+ke|\s+ki|\s+kab|\s+opd|\s+ka\s|,|\?|$)',
                text_lower
            ) or
            # Hindi डॉक्टर prefix — capture words after it, stop at ki/ka/ke/kab
            re.search(
                r'(?:डॉक्टर|डॉ\.?)\s+([\u0900-\u097F\s]{2,40}?)(?:\s+की|\s+का|\s+के|\s+कब|$)',
                sanitized
            )
        )

        if name_match:
            doctor_query = name_match.group(1).strip()
        else:
            # Last resort: strip common filler words and use what's left
            filler = re.compile(
                r'(mujhe|batao|bataiye|bataen|dikhao|ka schedule|ki opd|ke bare mein|'
                r'opd kab|kab lagti|schedule kya|search karo|dhundho|find karo|'
                r'की ओपीडी|के बारे में|का शेड्यूल|ओपीडी कब|कब लगती)',
                re.IGNORECASE
            )
            doctor_query = filler.sub("", sanitized).strip(" ?।,")
            # If still too long (>40 chars) it's probably not a name — use sanitized as-is
            if len(doctor_query) > 40:
                doctor_query = sanitized

        doctor_query = doctor_query.strip()
        print(f"[Doctor] Extracted query: '{doctor_query}'")

        matches = search_doctor_by_name(doctor_query, hint_dept=final_dept)
        if matches:
            doctor_results = [{"dept": m["dept"], "doctor": m["doctor"]} for m in matches]
            unique_names   = set(m["doctor"]["name"] for m in matches)
            ambiguous      = len(unique_names) > 1 and len(doctor_query.split()) <= 1
        else:
            # Doctor not found — show clean name in error, not full sentence
            display_name = doctor_query.title() if len(doctor_query) <= 40 else "Yeh"
            clinical["reply"] = (
                f"\"{display_name}\" naam ke doctor AIIMS OPD database mein nahi mile. "
                "Kripya sirf doctor ka naam likhein — jaise \"Dr. Anita Dhar\" ya \"Dr. Sharma\"."
            )
            print(f"[Doctor] Not found: {doctor_query}")

    elif is_browse and final_dept:
        all_docs = DOCTOR_DATA.get(final_dept, [])
        def _is_jpnatc(d): return (d.get("center", "") or "").upper() == "JPNATC"
        tagged_docs  = [{**d, "_dept": final_dept} for d in all_docs]
        todays_main  = [d for d in tagged_docs if is_available_today(d.get("opd_days", "")) and not _is_jpnatc(d)]
        others_main  = [d for d in tagged_docs if not is_available_today(d.get("opd_days", "")) and not _is_jpnatc(d)]
        jpnatc_docs  = [d for d in tagged_docs if _is_jpnatc(d)]
        dept_doctors = todays_main + others_main + jpnatc_docs
        # Override LLM reply for browse — suppress follow-up question
        clinical["reply"] = f"{final_dept} ke doctors neeche dekh sakte hain."
        clinical["follow_up_needed"] = False
        clinical["follow_up_question"] = None

    elif is_selfcare:
        dept_doctors = []

    elif final_dept:
        dept_doctors = fetch_doctors_for_dept(final_dept, sanitized)

    # ── STEP 7: Async ambiguity log (BackgroundTasks — safe fire-and-forget) ──
    background_tasks.add_task(
        log_if_ambiguous,
        sanitized_input    = sanitized,
        features           = features,
        engine_output      = engine_output,
        clinical_output    = clinical,
        confirmed_symptoms = confirmed_symptoms,
        denied_symptoms    = denied_symptoms,
        follow_up_count    = follow_up_count,
        session_id         = session_id or None,
    )

    # ── RESPONSE ──────────────────────────────────────────────
    new_follow_up_count = follow_up_count + (1 if clinical.get("follow_up_needed") else 0)
    today = get_today_name()

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
        "doctor_query":   doctor_query,
        "ambiguous":      ambiguous,

        "debug": {
            "python_top3":    engine_output.get("top3"),
            "confidence_gap": engine_output.get("confidence_gap"),
            "llm_confidence": clinical.get("confidence"),
            "python_correct": clinical.get("python_correct"),
            "pii_scrubbed":   was_sanitized(raw_message, sanitized),
        },

        "today":  today,
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
    today = get_today_name()
    all_docs = DOCTOR_DATA.get(department, [])
    if not all_docs:
        return {"department": department, "today": [], "others": [], "today_name": today}
    todays = [d for d in all_docs if is_available_today(d.get("opd_days", ""))]
    others = [d for d in all_docs if not is_available_today(d.get("opd_days", ""))]
    return {
        "department": department,
        "today":      todays,
        "others":     others,
        "today_name": today,
    }


@app.get("/todays-doctors")
def todays_doctors(department: str = Query(None)):
    results = get_todays_doctors(department)
    return {"today": get_today_name(), "count": len(results), "doctors": results}


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
        "today":             get_today_name(),
        "total_departments": len(DOCTOR_DATA),
        "total_doctors":     sum(len(v) for v in DOCTOR_DATA.values()),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))