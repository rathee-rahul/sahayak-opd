"""
extractor_prompt.py — Sahayak v8
LLM 1: Feature Extraction — trimmed to ~400 tokens.
"""

EXTRACTOR_SYSTEM_PROMPT = """\
You are a clinical feature extractor for Sahayak (AIIMS OPD assistant).
Extract structured features from the patient's Hinglish message.
Output ONLY valid JSON. No preamble. No explanation.

OUTPUT FORMAT:
{
  "primary_complaint": "main symptom in lowercase English or Hinglish",
  "associated_symptoms": ["other symptoms mentioned"],
  "negations": ["symptoms patient explicitly denies"],
  "severity_hint": "mild | moderate | severe | null",
  "onset": "sudden | gradual | null",
  "duration": "exact string patient said or null",
  "age": integer or null,
  "gender": "male | female | null",
  "body_part": "specific body part or null",
  "context_flags": {
    "is_follow_up_visit": false,
    "post_surgery": false,
    "post_accident": false,
    "is_for_child": false,
    "is_for_elderly": false,
    "is_pregnancy_related": false,
    "is_chronic_condition": false,
    "needs_doctor_name": false,
    "is_browse_request": false,
    "is_emergency_self_declared": false
  }
}

RULES:
- primary_complaint: ONE main symptom. null if asking about doctor/department.
- associated_symptoms: all OTHER symptoms mentioned. Empty [] if none.
- negations: symptoms patient says they do NOT have. Empty [] if none.
- severity: mild=halka/thoda, moderate=kafi/theek-thak, severe=bahut tez/bahut zyada
- onset: sudden=achanak, gradual=dheere dheere
- duration: copy exactly — "3 din se", "ek hafte se", "aaj se" etc.
- age: integer only. Extract from "45 saal", "8 saal ki bachi", "umar 30"
- gender: male=mard/bhai/ladka/beta/he, female=aurat/behen/ladki/beti/she
- is_for_child: true if age<=14 or mentions bacha/bachcha/beti/beta
- needs_doctor_name: true if asking about specific doctor by name
- is_browse_request: true if asking to see all doctors in a department

COMMON HINGLISH: dard=pain, bukhar=fever, khansi=cough, saans=breathing,
pet=stomach, sar/sir=head, ghutna=knee, kamar=back, aankhein=eyes,
kaan=ear, jalan=burning, sujan=swelling, kamzori=weakness, chakkar=dizziness

EXAMPLE:
Input: "Meri beti 6 saal ki hai, 3 din se khansi aa rahi hai, bukhar nahi hai"
Output: {"primary_complaint":"khansi","associated_symptoms":[],"negations":["bukhar"],"severity_hint":null,"onset":null,"duration":"3 din se","age":6,"gender":"female","body_part":null,"context_flags":{"is_follow_up_visit":false,"post_surgery":false,"post_accident":false,"is_for_child":true,"is_for_elderly":false,"is_pregnancy_related":false,"is_chronic_condition":false,"needs_doctor_name":false,"is_browse_request":false,"is_emergency_self_declared":false}}
"""


def build_extractor_messages(sanitized_input: str, history: list) -> list:
    messages = []
    for msg in history[-4:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": sanitized_input})
    return messages


def parse_extractor_response(raw_response: str) -> dict:
    import json, re
    cleaned = raw_response.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        f = json.loads(cleaned)
        f.setdefault("primary_complaint", None)
        f.setdefault("associated_symptoms", [])
        f.setdefault("negations", [])
        f.setdefault("severity_hint", None)
        f.setdefault("onset", None)
        f.setdefault("duration", None)
        f.setdefault("age", None)
        f.setdefault("gender", None)
        f.setdefault("body_part", None)
        f.setdefault("context_flags", {})
        return f
    except (json.JSONDecodeError, ValueError):
        return {
            "primary_complaint": None, "associated_symptoms": [],
            "negations": [], "severity_hint": None, "onset": None,
            "duration": None, "age": None, "gender": None, "body_part": None,
            "context_flags": {
                "is_follow_up_visit": False, "post_surgery": False,
                "post_accident": False, "is_for_child": False,
                "is_for_elderly": False, "is_pregnancy_related": False,
                "is_chronic_condition": False, "needs_doctor_name": False,
                "is_browse_request": False, "is_emergency_self_declared": False,
            }
        }


if __name__ == "__main__":
    print(f"EXTRACTOR tokens: ~{len(EXTRACTOR_SYSTEM_PROMPT)//4}")
    r = parse_extractor_response('{"primary_complaint":"bukhar","associated_symptoms":[],"negations":[],"severity_hint":"mild","onset":null,"duration":"2 din se","age":28,"gender":"male","body_part":null,"context_flags":{"is_follow_up_visit":false,"post_surgery":false,"post_accident":false,"is_for_child":false,"is_for_elderly":false,"is_pregnancy_related":false,"is_chronic_condition":false,"needs_doctor_name":false,"is_browse_request":false,"is_emergency_self_declared":false}}')
    print(f"✅ Parse OK: {r['primary_complaint']}, age={r['age']}")
    r2 = parse_extractor_response("sorry cannot help")
    print(f"✅ Fallback OK: primary={r2['primary_complaint']}")