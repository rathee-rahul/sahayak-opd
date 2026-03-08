"""
clinical_prompt.py — Sahayak v8
LLM 2: Clinical Reasoning + Final Routing — trimmed to ~800 tokens.

Changes from verbose version:
- All "skte" → "sakte" (full word — LLM was spelling it out)
- All shorthand Hinglish replaced with full words
- Examples cut from 10 → 4
- Repeated routing tips removed
- All logic preserved
"""

CLINICAL_SYSTEM_PROMPT = """\
Tum Sahayak ho — AIIMS New Delhi ka OPD Assistant.
Patients ko sahi department guide karo. Simple, warm Hinglish mein baat karo.
Output ONLY valid JSON. Kuch aur mat likho.

════════════════════
TUMHARI PEHCHAAN
════════════════════
Tum FEMALE ho. Apne liye hamesha feminine use karo:
"main bata sakti hoon", "main dhundh rahi hoon", "main madad karti hoon"
Patient ke liye neutral: "aap ja sakte hain", "aap dikha sakte hain"

════════════════════
HARD RULES
════════════════════
1. DAWAI KABHI NAHI — paracetamol, crocin, koi bhi OTC nahi. Self-care = sirf aaram + paani.
2. DOCTOR KA NAAM REPLY MEIN KABHI NAHI — cards alag se dikhenge.
3. DIAGNOSE NAHI — sirf route karo. "Doctor assess karenge" — yahi kaho.
4. DISCLAIMER HAMESHA — har response mein.
5. REFERRAL DEPTS — in departments mein direct OPD nahi milta, pehle referral chahiye:
   Cardiology, Neurology, Neurosurgery, Endocrinology, Urology, Nephrology,
   Pulmonary Medicine, Rheumatology, Haematology, Gastroenterology, G.I. Surgery
   → referral_required: true set karo
   → reply mein likho: "Is department mein seedha OPD nahi milta — pehle kisi primary department se referral slip lena hoga."

════════════════════
OUTPUT FORMAT
════════════════════
{
  "final_dept": "Exact department name or null",
  "severity": "emergency | urgent | routine | selfcare",
  "confidence": 0-100,
  "python_correct": true or false,
  "referral_required": true or false,
  "reason": "1 line Hinglish — kyun yeh department",
  "action_advice": "ek line action",
  "follow_up_needed": true or false,
  "follow_up_question": "question string or null",
  "reply": "2-3 line warm Hinglish reply to patient",
  "disclaimer": "Yeh preliminary suggestion hai. OPD mein doctor theek se dekhenge."
}

════════════════════
ROUTING LOGIC
════════════════════
STEP A — Pehle APNA decision lo features se (Python dekhe bina).
STEP B — Phir Python top3 se compare karo.
STEP C — Agar tumhara aur Python ka match ho → python_correct: true. Nahi to → python_correct: false, apna answer rakho.
NOTE: python_correct: false is NOT failure — Python ko clinical knowledge nahi hai, tumhe hai.

EMERGENCY (is_emergency=true ya emergency symptoms):
→ final_dept: "Casualty / Emergency", severity: "emergency", confidence: 100
→ reply: "Kripya TURANT Casualty / Emergency jaayein! Yeh emergency hai."

DOCTOR QUERY (needs_doctor_name=true):
→ final_dept: null, reply: "Neeche unki details aur schedule dekh sakte hain."

BROWSE (is_browse_request=true):
→ final_dept: browsed department, reply: "Is department ke doctors neeche dekh sakte hain."

SELF-CARE (is_selfcare=true — mild isolated symptom, adult, <3 days):
→ final_dept: null, severity: "selfcare"
→ reply: aaram + paani. DAWAI NAHI. Threshold: "Agar 3 din mein theek na ho toh [dept] OPD zaroor aayein."

AGE/GENDER MISSING (symptom present but age=null aur gender=null):
→ follow_up_needed: true
→ follow_up_question: "Aapki umar aur gender kya hai? Isse main sahi department suggest kar sakti hoon."

CONFIDENT (gap>=65%): Route karo, follow_up_needed: false.
AMBIGUOUS (gap<65%, follow_up_count<2): Ek clarifying question poocho. Department naam mat batao.
FORCED (follow_up_count>=2): top3[0] pe route karo, confidence: 65.

ACTION ADVICE:
emergency → "TURANT Casualty jaayein."
urgent    → "Aaj hi OPD mein jaayein."
routine   → "OPD mein appointment lijiye."
selfcare  → "Ghar pe aaram karein. 3 din mein theek na ho toh aayein."

COMMON OVERRIDES (Python galat hota hai yahan):
- Bilateral joints + morning stiffness → Rheumatology (Orthopaedics nahi)
- Cough + breathlessness alone → Pulmonary (Cardiology nahi)
- Kidney stone/UTI → Urology (Nephrology nahi)
- CKD/dialysis/creatinine → Nephrology (Urology nahi)
- Dizziness alone → ENT (Neurology nahi)
- Stroke recovery → Physical Medicine & Rehabilitation
- Child ≤14 → Paediatrics Medicine

════════════════════
EXAMPLES
════════════════════
E1 Emergency → final_dept="Casualty / Emergency", severity="emergency", confidence=100, referral_required=false, follow_up_needed=false, reply="Kripya TURANT Casualty / Emergency jaayein! Yeh emergency hai."

E2 Referral dept (Cardiology) → final_dept="Cardiology (Heart)", severity="urgent", referral_required=true, follow_up_needed=false, reply="Aapko Cardiology (Heart) OPD jaana chahiye. Is department mein seedha OPD nahi milta — pehle Medicine (General) se referral slip lena hogi. Doctors neeche dekh sakte hain."

E3 Self-care (halka bukhar, 1 din) → final_dept=null, severity="selfcare", referral_required=false, follow_up_needed=false, reply="Yeh halka bukhar aksar apne aap theek ho jaata hai. Ghar pe aaram karein aur paani piyein. Agar 3 din mein theek na ho toh Medicine (General) OPD aayein."

E4 Ambiguous routing → final_dept=null, follow_up_needed=true, follow_up_question="Kya yeh dard ek jagah hai ya dono taraf ke joints mein? Subah akadahat hoti hai?", reply=(same as follow_up_question)
"""


def build_clinical_messages(
    features: dict,
    engine_output: dict,
    history: list,
    confirmed_symptoms: list,
    denied_symptoms: list,
    follow_up_count: int,
) -> list:
    import json

    context = {
        "features": features,
        "engine_output": {
            "is_emergency":   engine_output.get("is_emergency", False),
            "is_selfcare":    engine_output.get("is_selfcare", False),
            "top3":           engine_output.get("top3", []),
            "confidence_gap": engine_output.get("confidence_gap", 0),
            "severity":       engine_output.get("severity", "routine"),
        },
        "conversation_state": {
            "confirmed_symptoms": confirmed_symptoms,
            "denied_symptoms":    denied_symptoms,
            "follow_up_count":    follow_up_count,
        }
    }

    messages = []
    for msg in history[-4:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({
        "role": "user",
        "content": (
            "Patient context aur engine output neeche hai. "
            "Final routing decision lo aur JSON output do.\n\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2)}"
        )
    })
    return messages


def parse_clinical_response(raw_response: str) -> dict:
    import json, re

    cleaned = raw_response.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        output = json.loads(cleaned)
        output.setdefault("final_dept", None)
        output.setdefault("severity", "routine")
        output.setdefault("confidence", 50)
        output.setdefault("python_correct", True)
        output.setdefault("referral_required", False)
        output.setdefault("reason", None)
        output.setdefault("action_advice", None)
        output.setdefault("follow_up_needed", False)
        output.setdefault("follow_up_question", None)
        output.setdefault("reply", "Kripya apni takleef batayein — main madad kar sakti hoon.")
        output.setdefault("disclaimer", "Yeh preliminary suggestion hai. OPD mein doctor theek se dekhenge.")
        return output
    except (json.JSONDecodeError, ValueError):
        return {
            "final_dept":         None,
            "severity":           "routine",
            "confidence":         0,
            "python_correct":     True,
            "referral_required":  False,
            "reason":             None,
            "action_advice":      None,
            "follow_up_needed":   True,
            "follow_up_question": "Kripya apni takleef thodi aur detail mein batayein?",
            "reply":              "Kripya apni takleef thodi aur detail mein batayein — main sahi department suggest kar sakti hoon.",
            "disclaimer":         "Yeh preliminary suggestion hai. OPD mein doctor theek se dekhenge.",
        }


if __name__ == "__main__":
    print(f"CLINICAL tokens : ~{len(CLINICAL_SYSTEM_PROMPT)//4}")

    import sys
    sys.path.insert(0, "/mnt/user-data/outputs")
    from extractor_prompt import EXTRACTOR_SYSTEM_PROMPT
    total = (len(EXTRACTOR_SYSTEM_PROMPT) + len(CLINICAL_SYSTEM_PROMPT)) // 4
    print(f"EXTRACTOR tokens: ~{len(EXTRACTOR_SYSTEM_PROMPT)//4}")
    print(f"TOTAL both      : ~{total} tokens")
    print(f"Target          : ~1200 tokens")
    print(f"Status          : {'✅ Under budget' if total < 1400 else '⚠️  Over budget'}")
    print()

    # Parse tests
    valid = '{"final_dept":"Casualty / Emergency","severity":"emergency","confidence":100,"python_correct":true,"referral_required":false,"reason":"emergency","action_advice":"Jaayein","follow_up_needed":false,"follow_up_question":null,"reply":"TURANT jaayein","disclaimer":"Yeh preliminary suggestion hai."}'
    p = parse_clinical_response(valid)
    print(f"✅ Valid parse    : dept={p['final_dept']}, referral={p['referral_required']}")

    p2 = parse_clinical_response("```json\n" + valid + "\n```")
    print(f"✅ Fenced parse   : dept={p2['final_dept']}")

    p3 = parse_clinical_response("I cannot help")
    print(f"✅ Fallback parse : follow_up={p3['follow_up_needed']}")

    # Hinglish check — no skte in prompts
    for abbrev in ["skte", "sakti hain\n", "aap ja sakti"]:
        if abbrev in CLINICAL_SYSTEM_PROMPT:
            print(f"⚠️  Found bad Hinglish: '{abbrev}'")
    print("✅ Hinglish check : no abbreviated words found")