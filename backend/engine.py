"""
engine.py — Sahayak v8
Python Scoring Engine.

Responsibilities:
  1. red_flag_check()       — detects concerning symptom combos -> soft advisory only
  2. score_departments()    — feature scoring -> top 3 + confidence gap
  3. get_severity()         — Urgent / Routine / Self-care
  4. is_selfcare_eligible() — isolated mild symptom -> self-care path

DESIGN PRINCIPLE:
  This app is a navigation tool for AIIMS OPD patients — not a triage system.
  90%+ of users are routine OPD patients from remote areas seeking the right dept.
  We NEVER hard-route to emergency. We always route to the relevant OPD department.

  If red-flag symptom combos are detected, we set show_advisory = True so LLM 2
  includes a calm note: "Agar symptoms bahut severe hain — Casualty bhi available hai."
  The patient, who knows their own body, makes the final call.
"""

import re
from typing import Dict, List, Optional, Tuple


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — RED FLAG ADVISORY DETECTION
# ══════════════════════════════════════════════════════════════════════════════
# These do NOT route to emergency. They trigger a soft advisory shown alongside
# the normal OPD department routing.

# Raw flag combinations that warrant a soft advisory
RED_FLAG_COMBO_RULES = [
    ["chest_pain", "breathlessness"],
    ["chest_pain", "arm_pain"],
    ["chest_pain", "sweating"],
    ["breathlessness", "unconscious"],
    ["breathlessness", "arm_pain"],
    ["unconscious"],
    ["stroke"],
    ["heavy_bleeding"],
    ["seizure"],
]

# Feature-level phrases that trigger advisory (unconditional)
RED_FLAG_FEATURE_TRIGGERS = [
    "unconscious", "behosh", "fainted", "collapsed",
    "seizure", "fits", "convulsion", "jhatke",
    "stroke", "facial droop", "chehra teda", "laqwa",
    "heavy bleeding", "khoon band nahi",
    "vomiting blood", "ulti mein khoon", "haematemesis",
    "loss of consciousness", "not breathing",
    "lips turning blue", "blue lips",
    "facial drooping", "face drooping",
    "unable to speak", "loss of speech",
]

# Chest pain only triggers advisory when combined with severity or these symptoms
CHEST_PAIN_ADVISORY_COMBOS = [
    "breathlessness", "shortness of breath", "saans",
    "sweating", "pasina", "arm pain", "arm weakness",
    "jaw pain", "nausea", "vomiting",
]

ACUTE_SEVERITIES = {"severe", "acute", "high", "critical", "tez", "bahut", "zyada"}


def red_flag_check(features: Dict, raw_flags: Dict[str, bool]) -> bool:
    """
    Detects whether a soft advisory should be shown alongside OPD routing.
    Never routes to emergency — only signals the advisory flag.

    Returns:
        True if soft advisory is warranted, else False
    """
    primary    = (features.get("primary_complaint") or "").lower()
    associated = [s.lower() for s in (features.get("associated_symptoms") or [])]
    severity   = (features.get("severity_hint") or "").lower()
    all_text   = primary + " " + " ".join(associated)

    # Rehab context suppresses advisory for stroke/seizure terms
    REHAB_SUPPRESSORS = [
        "rehab", "rehabilitation", "physiotherapy", "after stroke",
        "post stroke", "post-stroke", "requiring rehab", "needing therapy",
        "gait retraining", "spinal injury",
    ]
    if any(s in all_text for s in REHAB_SUPPRESSORS):
        return False

    # Unconditional feature red flags
    for trigger in RED_FLAG_FEATURE_TRIGGERS:
        if trigger in all_text:
            return True

    # Chest pain: advisory only when severe OR combined with another red flag
    has_chest_pain = (
        "chest pain" in all_text or "seene mein dard" in all_text or
        "chest dard" in all_text or raw_flags.get("chest_pain", False)
    )
    if has_chest_pain:
        if severity in ACUTE_SEVERITIES:
            return True
        if any(combo in all_text for combo in CHEST_PAIN_ADVISORY_COMBOS):
            return True
        if raw_flags.get("arm_pain") or raw_flags.get("sweating"):
            return True

    # Raw flag combos
    for rule in RED_FLAG_COMBO_RULES:
        if all(raw_flags.get(flag, False) for flag in rule):
            return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — SELF-CARE ELIGIBILITY
# ══════════════════════════════════════════════════════════════════════════════

SELFCARE_ELIGIBLE_SYMPTOMS = [
    "fever", "bukhar",
    "headache", "sar dard", "sir dard", "halka sar dard",
    "common cold", "cold", "runny nose", "naak se paani", "nazla",
    "body ache", "badan dard", "halka badan dard",
    "sore throat", "gala dard", "halka gala dard",
    "sneezing", "chheenk",
]

SELFCARE_BLOCKERS = [
    "chest", "breathless", "saans", "behosh", "unconscious",
    "blood", "khoon", "seizure", "fits", "stroke",
    "vomit", "ulti", "diarrhea", "loose motion",
    "severe", "bahut tez", "bahut zyada",
    "pregnancy", "pregnant", "rash",
]

LONG_DURATION_KEYWORDS = [
    "4 din", "5 din", "6 din", "7 din",
    "week", "hafte", "10 din", "15 din", "month", "mahine",
]


def is_selfcare_eligible(features: Dict) -> bool:
    """
    Returns True ONLY when ALL conditions are met:
      1. Primary complaint is in eligible list
      2. No associated symptoms (isolated)
      3. Severity is mild or unknown
      4. Duration is short (< 4 days) or not stated
      5. Patient is NOT child < 5 or elderly >= 65
      6. No red-flag blocker words present
    """
    primary    = (features.get("primary_complaint") or "").lower()
    associated = features.get("associated_symptoms") or []
    severity   = (features.get("severity_hint") or "").lower()
    duration   = (features.get("duration") or "").lower()
    age        = features.get("age")

    # Rule 1 — eligible symptom
    if not any(sym in primary for sym in SELFCARE_ELIGIBLE_SYMPTOMS):
        return False

    # Rule 2 — isolated (no associated symptoms)
    if associated and len(associated) > 0:
        return False

    # Rule 3 — not moderate or severe
    if severity in ["moderate", "severe", "high", "tez", "bahut", "zyada"]:
        return False

    # Rule 4 — short duration
    for kw in LONG_DURATION_KEYWORDS:
        if kw in duration:
            return False

    # Rule 5 — age safety
    if age is not None:
        try:
            age_int = int(age)
            if age_int < 5 or age_int >= 65:
                return False
        except (ValueError, TypeError):
            pass

    # Rule 6 — no blockers
    all_text = primary + " " + " ".join(str(s) for s in associated)
    if any(blocker in all_text for blocker in SELFCARE_BLOCKERS):
        return False

    return True


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — DEPARTMENT SCORING RULES
# ══════════════════════════════════════════════════════════════════════════════
# Format: (symptom_keyword, department, points)
# Points: 10=very specific  7=specific  5=moderate  3=weak  2=general

SCORING_RULES: List[Tuple[str, str, int]] = [

    # CARDIOLOGY
    ("chest pain",                  "Cardiology (Heart)",                       10),
    ("chest tightness",             "Cardiology (Heart)",                       10),
    ("chest pressure",              "Cardiology (Heart)",                       10),
    ("palpitations",                "Cardiology (Heart)",                       10),
    ("skipping heartbeat",          "Cardiology (Heart)",                       10),
    ("irregular heartbeat",         "Cardiology (Heart)",                       10),
    ("slow heart rate",             "Cardiology (Heart)",                        7),
    ("fast heart rate",             "Cardiology (Heart)",                        7),
    ("racing heart",                "Cardiology (Heart)",                        7),
    ("heart pounding",              "Cardiology (Heart)",                        7),
    ("dhadkan",                     "Cardiology (Heart)",                        7),
    ("arrhythmia",                  "Cardiology (Heart)",                       10),
    ("heart failure",               "Cardiology (Heart)",                       10),
    ("heart disease",               "Cardiology (Heart)",                        7),
    ("coronary artery disease",     "Cardiology (Heart)",                       10),
    ("ecg abnormality",             "Cardiology (Heart)",                       10),
    ("leg swelling",                "Cardiology (Heart)",                        7),
    ("bilateral leg swelling",      "Cardiology (Heart)",                       10),
    ("shortness of breath lying",   "Cardiology (Heart)",                       10),
    ("breathless lying flat",       "Cardiology (Heart)",                       10),
    ("shortness of breath on exertion", "Cardiology (Heart)",                   10),
    ("breathlessness on exertion",  "Cardiology (Heart)",                       10),
    ("breathless during exertion",  "Cardiology (Heart)",                       10),
    ("breathless during activity",  "Cardiology (Heart)",                       10),
    ("fainting",                    "Cardiology (Heart)",                        7),
    ("syncope",                     "Cardiology (Heart)",                       10),
    ("ecg",                         "Cardiology (Heart)",                        7),
    ("high bp",                     "Cardiology (Heart)",                        7),
    ("hypertension",                "Cardiology (Heart)",                        7),
    ("blood pressure",              "Cardiology (Heart)",                        5),
    ("bp",                          "Cardiology (Heart)",                        3),
    ("heart attack",                "Cardiology (Heart)",                       10),
    ("angina",                      "Cardiology (Heart)",                       10),

    # PULMONARY
    ("cough",                        "Pulmonary Medicine",                        3),
    ("khansi",                       "Pulmonary Medicine",                        3),
    ("chronic cough",                "Pulmonary Medicine",                        7),
    ("persistent cough",             "Pulmonary Medicine",                        7),
    ("breathlessness",               "Pulmonary Medicine",                        5),
    ("shortness of breath",          "Pulmonary Medicine",                        5),
    ("difficulty breathing",         "Pulmonary Medicine",                        5),
    ("saans",                        "Pulmonary Medicine",                        5),
    ("asthma",                       "Pulmonary Medicine",                       10),
    ("copd",                         "Pulmonary Medicine",                       10),
    ("tb",                           "Pulmonary Medicine",                       10),
    ("tuberculosis",                 "Pulmonary Medicine",                       10),
    ("lung",                         "Pulmonary Medicine",                        7),
    ("lung disease",                 "Pulmonary Medicine",                        7),
    ("lung cancer",                  "Pulmonary Medicine",                       10),
    ("ild",                          "Pulmonary Medicine",                       10),
    ("interstitial lung disease",    "Pulmonary Medicine",                       10),
    ("pulmonary fibrosis",           "Pulmonary Medicine",                       10),
    ("pulmonary hypertension",       "Pulmonary Medicine",                       10),
    ("sleep apnea",                  "Pulmonary Medicine",                       10),
    ("sleep apnoea",                 "Pulmonary Medicine",                       10),
    ("haemoptysis",                  "Pulmonary Medicine",                       10),
    ("blood in sputum",              "Pulmonary Medicine",                       10),  # increased: TB/bronchiectasis more likely than cancer
    ("sputum",                       "Pulmonary Medicine",                        5),
    ("phlegm",                       "Pulmonary Medicine",                        5),
    ("wheeze",                       "Pulmonary Medicine",                        7),
    ("wheezing",                     "Pulmonary Medicine",                        7),
    ("night cough",                  "Pulmonary Medicine",                        7),
    ("cough at night",               "Pulmonary Medicine",                        7),
    # Pulmonary combo signals — clearly not cardiac when these appear with breathlessness
    ("cough breathless",             "Pulmonary Medicine",                        7),
    ("wheeze breathless",            "Pulmonary Medicine",                        7),
    ("khansi saans",                 "Pulmonary Medicine",                        7),
    ("asthma attack",                "Pulmonary Medicine",                       10),
    ("inhaler",                      "Pulmonary Medicine",                       10),
    ("nebulizer",                    "Pulmonary Medicine",                        7),
    ("nebuliser",                    "Pulmonary Medicine",                        7),

    # GASTROENTEROLOGY
    ("acidity",                     "Gastroenterology (Stomach & Digestion)",   7),
    ("heartburn",                   "Gastroenterology (Stomach & Digestion)",   7),
    ("acid reflux",                 "Gastroenterology (Stomach & Digestion)",   7),
    ("bitter taste",                "Gastroenterology (Stomach & Digestion)",   5),
    ("nausea",                      "Gastroenterology (Stomach & Digestion)",   5),
    ("difficulty swallowing",       "Gastroenterology (Stomach & Digestion)",   7),
    ("food stuck",                  "Gastroenterology (Stomach & Digestion)",   7),
    ("dark tarry stool",            "Gastroenterology (Stomach & Digestion)",  10),
    ("tarry stool",                 "Gastroenterology (Stomach & Digestion)",  10),
    ("black stool",                 "Gastroenterology (Stomach & Digestion)",  10),
    ("yellow eyes",                 "Gastroenterology (Stomach & Digestion)",  10),
    ("upper abdominal pain",        "Gastroenterology (Stomach & Digestion)",   7),
    ("abdominal pain",              "Gastroenterology (Stomach & Digestion)",   5),
    ("stomach discomfort",          "Gastroenterology (Stomach & Digestion)",   7),
    ("abdominal discomfort",        "Gastroenterology (Stomach & Digestion)",   7),
    ("bowel movement",              "Gastroenterology (Stomach & Digestion)",   7),
    ("irregular bowel",             "Gastroenterology (Stomach & Digestion)",   7),
    ("early satiety",               "Gastroenterology (Stomach & Digestion)",   7),
    ("feeling full quickly",        "Gastroenterology (Stomach & Digestion)",   7),
    ("jaundice",                    "Gastroenterology (Stomach & Digestion)",  10),
    ("hepatitis",                   "Gastroenterology (Stomach & Digestion)",  10),
    ("liver",                       "Gastroenterology (Stomach & Digestion)",   7),
    ("ibs",                         "Gastroenterology (Stomach & Digestion)",  10),
    ("diarrhoea",                   "Gastroenterology (Stomach & Digestion)",   5),
    ("diarrhea",                    "Gastroenterology (Stomach & Digestion)",   5),
    ("loose motion",                "Gastroenterology (Stomach & Digestion)",   5),
    ("constipation",                "Gastroenterology (Stomach & Digestion)",   5),
    ("bloating",                    "Gastroenterology (Stomach & Digestion)",   5),
    ("pet mein dard",               "Gastroenterology (Stomach & Digestion)",   5),
    ("stomach pain",                "Gastroenterology (Stomach & Digestion)",   5),
    ("cirrhosis",                   "Gastroenterology (Stomach & Digestion)",  10),
    ("crohn",                       "Gastroenterology (Stomach & Digestion)",  10),
    ("ulcer",                       "Gastroenterology (Stomach & Digestion)",   7),

    # G.I. SURGERY
    ("appendix",                    "G.I. Surgery (Stomach Surgery)",           10),
    ("appendicitis",                "G.I. Surgery (Stomach Surgery)",           10),
    ("hernia",                      "G.I. Surgery (Stomach Surgery)",           10),
    ("gallbladder",                 "G.I. Surgery (Stomach Surgery)",            7),
    ("gallstone",                   "G.I. Surgery (Stomach Surgery)",           10),
    ("piles",                       "G.I. Surgery (Stomach Surgery)",            7),
    ("fissure",                     "G.I. Surgery (Stomach Surgery)",            7),
    ("pancreas",                    "G.I. Surgery (Stomach Surgery)",            7),
    ("colorectal",                  "G.I. Surgery (Stomach Surgery)",           10),
    ("colon polyp",                 "G.I. Surgery (Stomach Surgery)",           10),
    ("hiatal hernia",               "G.I. Surgery (Stomach Surgery)",           10),
    ("fundoplication",              "G.I. Surgery (Stomach Surgery)",           10),
    ("liver cyst",                  "G.I. Surgery (Stomach Surgery)",           10),
    ("liver transplant",            "G.I. Surgery (Stomach Surgery)",           10),
    ("liver surgery",               "G.I. Surgery (Stomach Surgery)",            7),
    ("bile duct",                   "G.I. Surgery (Stomach Surgery)",            7),
    ("gi bleeding",                 "G.I. Surgery (Stomach Surgery)",           10),
    ("bowel surgery",               "G.I. Surgery (Stomach Surgery)",            7),
    ("intestine surgery",           "G.I. Surgery (Stomach Surgery)",            7),
    ("stomach surgery",             "G.I. Surgery (Stomach Surgery)",            5),
    ("surgical removal",            "G.I. Surgery (Stomach Surgery)",            5),

    # NEUROLOGY
    ("migraine",                    "Neurology (Brain & Nerves)",               10),
    ("epilepsy",                    "Neurology (Brain & Nerves)",               10),
    ("seizure",                     "Neurology (Brain & Nerves)",               10),
    ("neuropathy",                  "Neurology (Brain & Nerves)",               10),
    ("memory loss",                 "Neurology (Brain & Nerves)",                7),
    ("short-term memory",           "Neurology (Brain & Nerves)",                7),
    ("getting lost",                "Neurology (Brain & Nerves)",                5),
    ("stroke",                      "Neurology (Brain & Nerves)",               10),
    ("paralysis",                   "Neurology (Brain & Nerves)",               10),
    ("multiple sclerosis",          "Neurology (Brain & Nerves)",               10),
    ("numbness",                    "Neurology (Brain & Nerves)",                5),
    ("tingling",                    "Neurology (Brain & Nerves)",                5),
    ("tremor",                      "Neurology (Brain & Nerves)",                7),
    ("resting tremor",              "Neurology (Brain & Nerves)",               10),
    ("muscle weakness",             "Neurology (Brain & Nerves)",                7),
    ("drooping eyelid",             "Neurology (Brain & Nerves)",               10),
    ("myasthenia",                  "Neurology (Brain & Nerves)",               10),
    ("parkinson",                   "Neurology (Brain & Nerves)",               10),
    ("burning feet",                "Neurology (Brain & Nerves)",                7),
    ("burning tingling",            "Neurology (Brain & Nerves)",                7),
    ("diabetic neuropathy",         "Neurology (Brain & Nerves)",               10),
    ("slow walking",                "Neurology (Brain & Nerves)",                5),
    ("stiff muscles",               "Neurology (Brain & Nerves)",                5),
    ("photophobia",                 "Neurology (Brain & Nerves)",               10),
    ("sensitivity to light",        "Neurology (Brain & Nerves)",                7),
    ("light sensitivity",           "Neurology (Brain & Nerves)",                7),
    ("neck stiffness",              "Neurology (Brain & Nerves)",                7),
    ("meningitis",                  "Neurology (Brain & Nerves)",               10),
    ("one-sided headache",          "Neurology (Brain & Nerves)",                7),
    ("throbbing headache",          "Neurology (Brain & Nerves)",                7),

    # NEUROSURGERY
    ("brain tumour",                "Neurosurgery (Brain Surgery)",             10),
    ("brain tumor",                 "Neurosurgery (Brain Surgery)",             10),
    ("head injury",                 "Neurosurgery (Brain Surgery)",             10),
    ("head trauma",                 "Neurosurgery (Brain Surgery)",             10),
    ("spine surgery",               "Neurosurgery (Brain Surgery)",             10),
    ("disc",                        "Neurosurgery (Brain Surgery)",              7),
    ("slipped disc",                "Neurosurgery (Brain Surgery)",             10),
    ("herniated disc",              "Neurosurgery (Brain Surgery)",             10),
    ("spinal nerve",                "Neurosurgery (Brain Surgery)",              7),
    ("hydrocephalus",               "Neurosurgery (Brain Surgery)",             10),
    ("shunt",                       "Neurosurgery (Brain Surgery)",             10),
    ("fluid in brain",              "Neurosurgery (Brain Surgery)",             10),
    ("csf leak",                    "Neurosurgery (Brain Surgery)",             10),
    ("fluid leaking from nose",     "Neurosurgery (Brain Surgery)",             10),
    ("trigeminal",                  "Neurosurgery (Brain Surgery)",             10),
    ("nerve decompression",         "Neurosurgery (Brain Surgery)",             10),
    ("facial pain triggered",       "Neurosurgery (Brain Surgery)",              7),
    ("cord compression",            "Neurosurgery (Brain Surgery)",             10),
    ("spinal cord compression",     "Neurosurgery (Brain Surgery)",             10),
    ("inability to walk",           "Neurosurgery (Brain Surgery)",              7),
    ("difficulty walking",          "Neurosurgery (Brain Surgery)",              7),
    ("loss of bladder control",     "Neurosurgery (Brain Surgery)",             10),
    ("loss of bowel control",       "Neurosurgery (Brain Surgery)",             10),
    ("raised intracranial",         "Neurosurgery (Brain Surgery)",             10),
    ("raised icp",                  "Neurosurgery (Brain Surgery)",             10),
    ("vomiting with headache",      "Neurosurgery (Brain Surgery)",             10),
    ("headache with vomiting",      "Neurosurgery (Brain Surgery)",             10),
    ("leg numbness",                "Neurosurgery (Brain Surgery)",              7),
    ("numbness in leg",             "Neurosurgery (Brain Surgery)",              7),
    # ── Spinal structural defects → Neurosurgery (spine unit at AIIMS) ──────
    ("pars intercularis",           "Neurosurgery (Brain Surgery)",             10),
    ("pars interarticularis",       "Neurosurgery (Brain Surgery)",             10),
    ("spondylolysis",               "Neurosurgery (Brain Surgery)",             10),
    ("spondylolisthesis",           "Neurosurgery (Brain Surgery)",             10),
    ("spinal fracture",             "Neurosurgery (Brain Surgery)",             10),
    ("vertebral fracture",          "Neurosurgery (Brain Surgery)",             10),
    ("compression fracture",        "Neurosurgery (Brain Surgery)",             10),
    ("burst fracture",              "Neurosurgery (Brain Surgery)",             10),
    ("bilateral pars",              "Neurosurgery (Brain Surgery)",             10),
    ("l5 fracture",                 "Neurosurgery (Brain Surgery)",             10),
    ("l4 fracture",                 "Neurosurgery (Brain Surgery)",             10),
    ("l3 fracture",                 "Neurosurgery (Brain Surgery)",             10),
    ("l2 fracture",                 "Neurosurgery (Brain Surgery)",             10),
    ("l1 fracture",                 "Neurosurgery (Brain Surgery)",             10),
    ("l5 defect",                   "Neurosurgery (Brain Surgery)",             10),
    ("spinal instability",          "Neurosurgery (Brain Surgery)",             10),
    ("vertebral slip",              "Neurosurgery (Brain Surgery)",             10),
    ("spinal fusion",               "Neurosurgery (Brain Surgery)",             10),
    ("lumbar fusion",               "Neurosurgery (Brain Surgery)",             10),
    ("cervical fracture",           "Neurosurgery (Brain Surgery)",             10),
    ("thoracic fracture",           "Neurosurgery (Brain Surgery)",             10),
    ("atlanto",                     "Neurosurgery (Brain Surgery)",             10),
    ("cvj",                         "Neurosurgery (Brain Surgery)",             10),
    ("craniovertebral",             "Neurosurgery (Brain Surgery)",             10),

    # ORTHOPAEDICS
    ("fracture",                    "Orthopaedics (Bones & Joints)",            10),
    ("haddi",                       "Orthopaedics (Bones & Joints)",             7),
    ("bone",                        "Orthopaedics (Bones & Joints)",             5),
    ("back pain",                   "Orthopaedics (Bones & Joints)",             7),
    ("kamar dard",                  "Orthopaedics (Bones & Joints)",             7),
    ("sports injury",               "Orthopaedics (Bones & Joints)",            10),
    ("acl",                         "Orthopaedics (Bones & Joints)",            10),
    ("acl injury",                  "Orthopaedics (Bones & Joints)",            10),
    ("arthritis",                   "Orthopaedics (Bones & Joints)",             7),
    ("arthroscopy",                 "Orthopaedics (Bones & Joints)",            10),
    ("scoliosis",                   "Orthopaedics (Bones & Joints)",            10),
    ("kyphosis",                    "Orthopaedics (Bones & Joints)",            10),
    ("carpal tunnel",               "Orthopaedics (Bones & Joints)",            10),
    ("club foot",                   "Orthopaedics (Bones & Joints)",            10),
    ("spinal stenosis",             "Orthopaedics (Bones & Joints)",            10),
    ("back deformity",              "Orthopaedics (Bones & Joints)",             7),
    ("disc problem",                "Orthopaedics (Bones & Joints)",             7),
    ("bone pain",                   "Orthopaedics (Bones & Joints)",             7),
    ("knee pain",                   "Orthopaedics (Bones & Joints)",             7),
    ("ghutne mein dard",            "Orthopaedics (Bones & Joints)",             7),
    ("hip pain",                    "Orthopaedics (Bones & Joints)",             7),
    ("shoulder pain",               "Orthopaedics (Bones & Joints)",             5),
    ("stiff shoulder",              "Orthopaedics (Bones & Joints)",             7),
    ("frozen shoulder",             "Orthopaedics (Bones & Joints)",            10),
    ("heel pain",                   "Orthopaedics (Bones & Joints)",             7),
    ("plantar fasciitis",           "Orthopaedics (Bones & Joints)",            10),
    ("neck pain",                   "Orthopaedics (Bones & Joints)",             7),
    ("cervical",                    "Orthopaedics (Bones & Joints)",             7),
    ("difficulty bearing weight",   "Orthopaedics (Bones & Joints)",             7),
    ("cannot raise arm",            "Orthopaedics (Bones & Joints)",             7),
    ("clicking knee",               "Orthopaedics (Bones & Joints)",             7),
    ("giving way",                  "Orthopaedics (Bones & Joints)",             5),
    ("inability to stand",          "Orthopaedics (Bones & Joints)",             7),
    ("cannot stand",                "Orthopaedics (Bones & Joints)",             7),
    ("leg pain",                    "Orthopaedics (Bones & Joints)",             5),
    ("limping",                     "Orthopaedics (Bones & Joints)",             5),
    # SI joint / sacroiliac — ortho first, rheumatology only if autoimmune confirmed
    ("si joint",                    "Orthopaedics (Bones & Joints)",            12),  # beats ankylosing alone
    ("sacroiliac",                  "Orthopaedics (Bones & Joints)",            10),
    ("sacral pain",                 "Orthopaedics (Bones & Joints)",            10),
    ("pelvic pain",                 "Orthopaedics (Bones & Joints)",             7),
    ("lower back joint",            "Orthopaedics (Bones & Joints)",             7),
    ("tailbone",                    "Orthopaedics (Bones & Joints)",             7),
    ("coccyx",                      "Orthopaedics (Bones & Joints)",            10),
    ("lumbar",                      "Orthopaedics (Bones & Joints)",             7),
    ("lumbosacral",                 "Orthopaedics (Bones & Joints)",            10),

    # RHEUMATOLOGY
    ("rheumatoid",                  "Rheumatology (Joint & Autoimmune)",        10),
    ("lupus",                       "Rheumatology (Joint & Autoimmune)",        10),
    ("autoimmune",                  "Rheumatology (Joint & Autoimmune)",        10),
    ("gout",                        "Rheumatology (Joint & Autoimmune)",        10),
    ("uric acid",                   "Rheumatology (Joint & Autoimmune)",        10),
    ("ankylosing spondylitis",      "Rheumatology (Joint & Autoimmune)",        10),
    ("ankylosing",                  "Rheumatology (Joint & Autoimmune)",         7),  # reduced — SI joint pain overrides
    ("joint swelling",              "Rheumatology (Joint & Autoimmune)",         7),
    ("joint pain",                  "Rheumatology (Joint & Autoimmune)",         5),
    ("multi-joint",                 "Rheumatology (Joint & Autoimmune)",         7),
    ("multiple joints",             "Rheumatology (Joint & Autoimmune)",         7),
    ("dono taraf dard",             "Rheumatology (Joint & Autoimmune)",         7),
    ("morning stiffness",           "Rheumatology (Joint & Autoimmune)",         7),
    ("stiffness at rest",           "Rheumatology (Joint & Autoimmune)",         7),
    ("worse at rest",               "Rheumatology (Joint & Autoimmune)",         7),
    ("improves with exercise",      "Rheumatology (Joint & Autoimmune)",         7),
    ("dry eyes",                    "Rheumatology (Joint & Autoimmune)",         5),
    ("dry mouth",                   "Rheumatology (Joint & Autoimmune)",         5),
    ("sjogren",                     "Rheumatology (Joint & Autoimmune)",        10),
    ("swollen big toe",             "Rheumatology (Joint & Autoimmune)",        10),
    ("painful red toe",             "Rheumatology (Joint & Autoimmune)",         7),
    ("muscle pain",                 "Rheumatology (Joint & Autoimmune)",         5),
    ("vasculitis",                  "Rheumatology (Joint & Autoimmune)",        10),
    ("rheumatoid arthritis",        "Rheumatology (Joint & Autoimmune)",        10),

    # NEPHROLOGY
    ("chronic kidney",              "Nephrology",                               10),
    ("kidney failure",              "Nephrology",                               10),
    ("dialysis",                    "Nephrology",                               10),
    ("creatinine",                  "Nephrology",                               10),
    ("nephritis",                   "Nephrology",                               10),
    ("ckd",                         "Nephrology",                               10),
    ("proteinuria",                 "Nephrology",                                7),
    ("protein in urine",            "Nephrology",                               10),
    ("frothy urine",                "Nephrology",                               10),
    ("foamy urine",                 "Nephrology",                               10),
    ("kidney disease",              "Nephrology",                                7),
    ("kidney function",             "Nephrology",                                7),
    ("swelling face and legs",      "Nephrology",                                7),
    ("decreased urine output",      "Nephrology",                               10),
    ("reduced urine output",        "Nephrology",                               10),
    ("oliguria",                    "Nephrology",                               10),
    ("leg swelling",                "Nephrology",                                5),
    ("electrolyte imbalance",       "Nephrology",                               10),
    ("kidney transplant",           "Nephrology",                               10),
    ("nephrotic",                   "Nephrology",                               10),

    # UROLOGY
    ("kidney stone",                "Urology (Kidney & Urinary)",               10),
    ("pathri",                      "Urology (Kidney & Urinary)",               10),
    ("uti",                         "Urology (Kidney & Urinary)",               10),
    ("urine infection",             "Urology (Kidney & Urinary)",               10),
    ("burning urination",           "Urology (Kidney & Urinary)",               10),
    ("painful urination",           "Urology (Kidney & Urinary)",               10),
    ("burning sensation urination", "Urology (Kidney & Urinary)",               10),
    ("burning sensation",           "Urology (Kidney & Urinary)",                5),
    ("peshaab mein jalan",          "Urology (Kidney & Urinary)",               10),
    ("blood in urine",              "Urology (Kidney & Urinary)",               10),
    ("prostate",                    "Urology (Kidney & Urinary)",               10),
    ("prostate cancer",             "Urology (Kidney & Urinary)",               10),
    ("bladder",                     "Urology (Kidney & Urinary)",                7),
    ("bladder problems",            "Urology (Kidney & Urinary)",                7),
    ("bladder cancer",              "Urology (Kidney & Urinary)",               10),
    ("urinary",                     "Urology (Kidney & Urinary)",                5),
    ("peshaab",                     "Urology (Kidney & Urinary)",                5),
    ("urine stream",                "Urology (Kidney & Urinary)",               10),
    ("weak urine",                  "Urology (Kidney & Urinary)",               10),
    ("difficulty urinating",        "Urology (Kidney & Urinary)",               10),
    ("difficulty passing urine",    "Urology (Kidney & Urinary)",               10),
    ("passing urine",               "Urology (Kidney & Urinary)",                7),
    ("frequent urination",          "Urology (Kidney & Urinary)",                7),
    ("testicular pain",             "Urology (Kidney & Urinary)",               10),
    ("testicular swelling",         "Urology (Kidney & Urinary)",               10),
    ("cloudy urine",                "Urology (Kidney & Urinary)",                7),
    ("foul smelling urine",         "Urology (Kidney & Urinary)",                7),
    ("erectile dysfunction",        "Urology (Kidney & Urinary)",               10),
    ("impotence",                   "Urology (Kidney & Urinary)",               10),
    ("andrology",                   "Urology (Kidney & Urinary)",               10),
    ("male infertility",            "Urology (Kidney & Urinary)",               10),
    ("low sperm",                   "Urology (Kidney & Urinary)",               10),
    ("kidney cancer",               "Urology (Kidney & Urinary)",               10),
    ("bedwetting",                  "Urology (Kidney & Urinary)",                7),
    ("urinary incontinence",        "Urology (Kidney & Urinary)",                7),
    ("incontinence",                "Urology (Kidney & Urinary)",                7),

    # ENDOCRINOLOGY
    ("diabetes",                    "Endocrinology (Diabetes & Hormones)",      10),
    ("sugar",                       "Endocrinology (Diabetes & Hormones)",       7),
    ("blood sugar",                 "Endocrinology (Diabetes & Hormones)",      10),
    ("thyroid",                     "Endocrinology (Diabetes & Hormones)",      10),
    ("hypothyroid",                 "Endocrinology (Diabetes & Hormones)",      10),
    ("hyperthyroid",                "Endocrinology (Diabetes & Hormones)",      10),
    ("thyroid disorders",           "Endocrinology (Diabetes & Hormones)",      10),
    ("brittle nails",               "Endocrinology (Diabetes & Hormones)",       7),
    ("hair thinning",               "Endocrinology (Diabetes & Hormones)",       7),
    ("feeling of cold",             "Endocrinology (Diabetes & Hormones)",       5),
    ("excessive sweating",          "Endocrinology (Diabetes & Hormones)",       7),
    ("bulging eyes",                "Endocrinology (Diabetes & Hormones)",      10),
    ("delayed puberty",             "Endocrinology (Diabetes & Hormones)",      10),
    ("puberty disorders",           "Endocrinology (Diabetes & Hormones)",      10),
    ("short stature",               "Endocrinology (Diabetes & Hormones)",      10),
    ("growth disorders",            "Endocrinology (Diabetes & Hormones)",       7),
    ("growth spurt",                "Endocrinology (Diabetes & Hormones)",       7),
    ("hormonal",                    "Endocrinology (Diabetes & Hormones)",       7),
    ("hormonal imbalance",          "Endocrinology (Diabetes & Hormones)",      10),
    ("pcos",                        "Endocrinology (Diabetes & Hormones)",      10),
    ("adrenal",                     "Endocrinology (Diabetes & Hormones)",      10),
    ("insulin",                     "Endocrinology (Diabetes & Hormones)",       7),
    ("osteoporosis",                "Endocrinology (Diabetes & Hormones)",      10),
    ("calcium problems",            "Endocrinology (Diabetes & Hormones)",      10),
    ("bone density",                "Endocrinology (Diabetes & Hormones)",       7),
    ("pituitary",                   "Endocrinology (Diabetes & Hormones)",      10),
    ("metabolic disorders",         "Endocrinology (Diabetes & Hormones)",       7),
    ("obesity",                     "Endocrinology (Diabetes & Hormones)",       7),
    ("weight gain",                 "Endocrinology (Diabetes & Hormones)",       3),
    ("weight loss",                 "Endocrinology (Diabetes & Hormones)",       3),

    # DERMATOLOGY
    ("skin rash",                   "Dermatology & Venereology (Skin)",         10),
    ("rash",                        "Dermatology & Venereology (Skin)",          7),
    ("psoriasis",                   "Dermatology & Venereology (Skin)",         10),
    ("eczema",                      "Dermatology & Venereology (Skin)",         10),
    ("fungal",                      "Dermatology & Venereology (Skin)",          7),
    ("fungal infection",            "Dermatology & Venereology (Skin)",         10),
    ("hair loss",                   "Dermatology & Venereology (Skin)",          7),
    ("baal jhad",                   "Dermatology & Venereology (Skin)",          7),
    ("acne",                        "Dermatology & Venereology (Skin)",          7),
    ("cystic acne",                 "Dermatology & Venereology (Skin)",         10),
    ("itching",                     "Dermatology & Venereology (Skin)",          5),
    ("khujli",                      "Dermatology & Venereology (Skin)",          5),
    ("vitiligo",                    "Dermatology & Venereology (Skin)",         10),
    ("white patches on skin",       "Dermatology & Venereology (Skin)",         10),
    ("white patch",                 "Dermatology & Venereology (Skin)",          7),
    ("nail thickening",             "Dermatology & Venereology (Skin)",          7),
    ("nail discoloration",          "Dermatology & Venereology (Skin)",          7),
    ("yellowish nail",              "Dermatology & Venereology (Skin)",          7),
    ("skin bumps",                  "Dermatology & Venereology (Skin)",          7),
    ("hard bumps",                  "Dermatology & Venereology (Skin)",          7),
    ("nodule on skin",              "Dermatology & Venereology (Skin)",          7),
    ("skin nodule",                 "Dermatology & Venereology (Skin)",          7),
    ("bumps on",                    "Dermatology & Venereology (Skin)",          5),
    ("warts",                       "Dermatology & Venereology (Skin)",          7),
    ("open sore",                   "Dermatology & Venereology (Skin)",          7),
    ("genital sore",                "Dermatology & Venereology (Skin)",         10),
    ("skin lesion",                 "Dermatology & Venereology (Skin)",          7),
    ("skin infection",              "Dermatology & Venereology (Skin)",          7),
    ("urticaria",                   "Dermatology & Venereology (Skin)",         10),
    ("hives",                       "Dermatology & Venereology (Skin)",          7),
    ("allergy",                     "Dermatology & Venereology (Skin)",          5),
    ("allergic rash",               "Dermatology & Venereology (Skin)",         10),
    ("pigmentation",                "Dermatology & Venereology (Skin)",          7),
    ("leprosy",                     "Dermatology & Venereology (Skin)",         10),
    ("std",                         "Dermatology & Venereology (Skin)",         10),
    ("sexually transmitted",        "Dermatology & Venereology (Skin)",         10),

    # ENT
    ("ear pain",                    "Otorhinolaryngology - ENT",                10),
    ("kaan dard",                   "Otorhinolaryngology - ENT",                10),
    ("ear discharge",               "Otorhinolaryngology - ENT",                10),
    ("hearing loss",                "Otorhinolaryngology - ENT",                10),
    ("ringing in ears",             "Otorhinolaryngology - ENT",                10),
    ("tinnitus",                    "Otorhinolaryngology - ENT",                10),
    ("sinusitis",                   "Otorhinolaryngology - ENT",                10),
    ("sinus",                       "Otorhinolaryngology - ENT",                 7),
    ("nasal polyp",                 "Otorhinolaryngology - ENT",                10),
    ("nose block",                  "Otorhinolaryngology - ENT",                 7),
    ("nasal congestion",            "Otorhinolaryngology - ENT",                 7),
    ("nasal obstruction",           "Otorhinolaryngology - ENT",                 7),
    ("nosebleed",                   "Otorhinolaryngology - ENT",                 7),
    ("naak band",                   "Otorhinolaryngology - ENT",                 7),
    ("throat pain",                 "Otorhinolaryngology - ENT",                 7),
    ("throat irritation",           "Otorhinolaryngology - ENT",                 7),
    ("throat discomfort",           "Otorhinolaryngology - ENT",                 5),
    ("sore throat",                 "Otorhinolaryngology - ENT",                 7),
    ("tonsils",                     "Otorhinolaryngology - ENT",                10),
    ("tonsillitis",                 "Otorhinolaryngology - ENT",                10),
    ("gala dard",                   "Otorhinolaryngology - ENT",                 7),
    ("hoarse voice",                "Otorhinolaryngology - ENT",                 7),
    ("hoarseness",                  "Otorhinolaryngology - ENT",                 7),
    ("voice problem",               "Otorhinolaryngology - ENT",                 7),
    ("voice hoarseness",            "Otorhinolaryngology - ENT",                 7),
    ("vocal cord",                  "Otorhinolaryngology - ENT",                10),
    ("voice change",                "Otorhinolaryngology - ENT",                 7),
    ("voice disorder",              "Otorhinolaryngology - ENT",                 7),
    ("lump in throat",              "Otorhinolaryngology - ENT",                 7),
    ("neck lump",                   "Otorhinolaryngology - ENT",                 7),
    ("globus",                      "Otorhinolaryngology - ENT",                 7),
    ("snoring",                     "Otorhinolaryngology - ENT",                 7),
    ("balance disorder",            "Otorhinolaryngology - ENT",                 7),
    ("acoustic neuroma",            "Otorhinolaryngology - ENT",                10),
    ("vertigo",                     "Otorhinolaryngology - ENT",                10),
    ("dizziness",                   "Otorhinolaryngology - ENT",                 5),
    ("chakkar",                     "Otorhinolaryngology - ENT",                 5),
    ("swallowing",                  "Otorhinolaryngology - ENT",                 5),
    ("fullness in ear",             "Otorhinolaryngology - ENT",                 7),

    # OPHTHALMOLOGY
    ("eye pain",                    "Ophthalmology (Eyes)",                     10),
    ("aankh dard",                  "Ophthalmology (Eyes)",                     10),
    ("blurred vision",              "Ophthalmology (Eyes)",                     10),
    ("cataract",                    "Ophthalmology (Eyes)",                     10),
    ("glaucoma",                    "Ophthalmology (Eyes)",                     10),
    ("retina",                      "Ophthalmology (Eyes)",                     10),
    ("cornea",                      "Ophthalmology (Eyes)",                      7),
    ("vision",                      "Ophthalmology (Eyes)",                      5),
    ("aankhon mein",                "Ophthalmology (Eyes)",                      5),
    ("eye strain",                  "Ophthalmology (Eyes)",                      7),
    ("eye redness",                 "Ophthalmology (Eyes)",                      7),
    ("red eye",                     "Ophthalmology (Eyes)",                      7),
    ("watering eye",                "Ophthalmology (Eyes)",                      5),
    ("watering eyes",               "Ophthalmology (Eyes)",                      5),
    ("floaters",                    "Ophthalmology (Eyes)",                     10),
    ("flashes of light",            "Ophthalmology (Eyes)",                     10),
    ("peripheral vision",           "Ophthalmology (Eyes)",                     10),
    ("eye pressure",                "Ophthalmology (Eyes)",                     10),
    ("double vision",               "Ophthalmology (Eyes)",                      7),
    ("crossed eye",                 "Ophthalmology (Eyes)",                      7),
    ("squint",                      "Ophthalmology (Eyes)",                     10),
    ("strabismus",                  "Ophthalmology (Eyes)",                     10),
    ("lazy eye",                    "Ophthalmology (Eyes)",                     10),
    ("amblyopia",                   "Ophthalmology (Eyes)",                     10),
    ("dry eyes",                    "Ophthalmology (Eyes)",                      5),
    ("gritty eyes",                 "Ophthalmology (Eyes)",                      7),
    ("uveitis",                     "Ophthalmology (Eyes)",                     10),
    ("macular degeneration",        "Ophthalmology (Eyes)",                     10),
    ("retinal detachment",          "Ophthalmology (Eyes)",                     10),
    ("diabetic retinopathy",        "Ophthalmology (Eyes)",                     10),
    ("keratoconus",                 "Ophthalmology (Eyes)",                     10),
    ("corneal ulcer",               "Ophthalmology (Eyes)",                     10),
    ("sensitivity to light",        "Ophthalmology (Eyes)",                      5),

    # PSYCHIATRY
    ("depression",                  "Psychiatry (Mental Health)",               10),
    ("anxiety",                     "Psychiatry (Mental Health)",               10),
    ("stress",                      "Psychiatry (Mental Health)",                5),
    ("addiction",                   "Psychiatry (Mental Health)",               10),
    ("alcohol use",                 "Psychiatry (Mental Health)",               10),
    ("alcohol addiction",           "Psychiatry (Mental Health)",               10),
    ("drug use",                    "Psychiatry (Mental Health)",                7),
    ("substance abuse",             "Psychiatry (Mental Health)",               10),
    ("substance use disorder",      "Psychiatry (Mental Health)",               10),
    ("de-addiction",                "Psychiatry (Mental Health)",               10),
    ("sleep disorder",              "Psychiatry (Mental Health)",                7),
    ("sleep problems",              "Psychiatry (Mental Health)",                7),
    ("insomnia",                    "Psychiatry (Mental Health)",                7),
    ("neend nahi",                  "Psychiatry (Mental Health)",                7),
    ("bipolar",                     "Psychiatry (Mental Health)",               10),
    ("adhd",                        "Psychiatry (Mental Health)",               10),
    ("schizophrenia",               "Psychiatry (Mental Health)",               10),
    ("eating disorder",             "Psychiatry (Mental Health)",               10),
    ("anorexia",                    "Psychiatry (Mental Health)",               10),
    ("bulimia",                     "Psychiatry (Mental Health)",               10),
    ("mental",                      "Psychiatry (Mental Health)",                5),
    ("sadness",                     "Psychiatry (Mental Health)",               10),
    ("persistent sadness",          "Psychiatry (Mental Health)",               10),
    ("loss of interest",            "Psychiatry (Mental Health)",               10),
    ("low mood",                    "Psychiatry (Mental Health)",               10),
    ("hopelessness",                "Psychiatry (Mental Health)",               10),
    ("mood swings",                 "Psychiatry (Mental Health)",               10),
    ("extreme mood",                "Psychiatry (Mental Health)",                7),
    ("panic attack",                "Psychiatry (Mental Health)",               10),
    ("flashbacks",                  "Psychiatry (Mental Health)",               10),
    ("trauma",                      "Psychiatry (Mental Health)",                7),
    ("ptsd",                        "Psychiatry (Mental Health)",               10),
    ("nightmares",                  "Psychiatry (Mental Health)",                7),
    ("fear of social",              "Psychiatry (Mental Health)",               10),
    ("social anxiety",              "Psychiatry (Mental Health)",               10),
    ("panic",                       "Psychiatry (Mental Health)",                7),
    ("obsessive",                   "Psychiatry (Mental Health)",               10),
    ("compulsive",                  "Psychiatry (Mental Health)",               10),
    ("ocd",                         "Psychiatry (Mental Health)",               10),
    ("intrusive thoughts",          "Psychiatry (Mental Health)",               10),
    ("repetitive behaviour",        "Psychiatry (Mental Health)",                7),
    ("impulsivity",                 "Psychiatry (Mental Health)",                7),

    # HAEMATOLOGY
    ("thalassemia",                 "Haematology (Blood Disorders)",            10),
    ("leukaemia",                   "Haematology (Blood Disorders)",            10),
    ("leukemia",                    "Haematology (Blood Disorders)",            10),
    ("blood cancer",                "Haematology (Blood Disorders)",            10),
    ("lymphoma",                    "Haematology (Blood Disorders)",            10),
    ("multiple myeloma",            "Haematology (Blood Disorders)",            10),
    ("myeloma",                     "Haematology (Blood Disorders)",            10),
    ("aplastic anemia",             "Haematology (Blood Disorders)",            10),
    ("bone marrow failure",         "Haematology (Blood Disorders)",            10),
    ("bone marrow transplant",      "Haematology (Blood Disorders)",            10),
    ("coagulation disorders",       "Haematology (Blood Disorders)",            10),
    ("coagulation defects",         "Haematology (Blood Disorders)",            10),
    ("bleeding disorder",           "Haematology (Blood Disorders)",            10),
    ("prolonged bleeding",          "Haematology (Blood Disorders)",            10),
    ("bruising",                    "Haematology (Blood Disorders)",             7),
    ("easy bruising",               "Haematology (Blood Disorders)",            10),
    ("frequent bruising",           "Haematology (Blood Disorders)",            10),
    ("platelet",                    "Haematology (Blood Disorders)",            10),
    ("thrombocytopenia",            "Haematology (Blood Disorders)",            10),
    ("itp",                         "Haematology (Blood Disorders)",            10),
    ("anaemia",                     "Haematology (Blood Disorders)",             7),
    ("anemia",                      "Haematology (Blood Disorders)",             7),
    ("sickle cell",                 "Haematology (Blood Disorders)",            10),
    ("haemophilia",                 "Haematology (Blood Disorders)",            10),
    ("swollen lymph nodes",         "Haematology (Blood Disorders)",            10),
    ("lymph node",                  "Haematology (Blood Disorders)",             7),
    ("night sweats",                "Haematology (Blood Disorders)",             7),
    ("drenching sweats",            "Haematology (Blood Disorders)",            10),
    ("high white blood cell",       "Haematology (Blood Disorders)",            10),
    ("white blood cell",            "Haematology (Blood Disorders)",             7),
    ("blood clot",                  "Haematology (Blood Disorders)",            10),
    ("dvt",                         "Haematology (Blood Disorders)",            10),
    ("deep vein thrombosis",        "Haematology (Blood Disorders)",            10),
    ("thrombosis",                  "Haematology (Blood Disorders)",            10),
    ("heavy menstrual bleeding",    "Haematology (Blood Disorders)",             7),
    ("pale gums",                   "Haematology (Blood Disorders)",             7),
    ("pale skin",                   "Haematology (Blood Disorders)",             5),

    # ONCOLOGY
    ("cancer",                      "Oncology (Cancer)",                        10),
    ("chemotherapy",                "Oncology (Cancer)",                        10),
    ("radiation",                   "Oncology (Cancer)",                        10),
    ("tumour",                      "Oncology (Cancer)",                        10),
    ("tumor",                       "Oncology (Cancer)",                        10),
    ("malignancy",                  "Oncology (Cancer)",                        10),
    ("biopsy",                      "Oncology (Cancer)",                         7),
    ("malignant",                   "Oncology (Cancer)",                        10),
    ("multicolored mole",           "Oncology (Cancer)",                        10),
    ("abnormal mole",               "Oncology (Cancer)",                        10),
    ("persistent lump",             "Oncology (Cancer)",                        10),
    ("unexplained lump",            "Oncology (Cancer)",                        10),
    ("lump in body",                "Oncology (Cancer)",                        10),
    ("ganth",                       "Oncology (Cancer)",                         7),  # Hindi = lump/swelling
    ("lamp in",                     "Oncology (Cancer)",                         7),  # voice mishear of lump
    ("ghaav",                       "Surgery (General)",                         5),  # Hindi = wound/sore
    ("solid tumour",                "Oncology (Cancer)",                        10),
    ("mass on xray",                "Oncology (Cancer)",                        10),
    ("coughing up blood",           "Oncology (Cancer)",                        10),
    ("blood in sputum",             "Oncology (Cancer)",                         5),  # reduced: Pulmonary more likely for haemoptysis at OPD
    ("unexplained weight loss",     "Oncology (Cancer)",                         7),
    ("cancer follow-up",            "Oncology (Cancer)",                        10),
    ("psa",                         "Oncology (Cancer)",                         7),
    ("bone marrow",                 "Oncology (Cancer)",                        10),

    # OBSTETRICS & GYNAECOLOGY
    ("pregnancy",                   "Obstetrics & Gynaecology",                 10),
    ("pregnant",                    "Obstetrics & Gynaecology",                 10),
    ("antenatal",                   "Obstetrics & Gynaecology",                 10),
    ("postnatal",                   "Obstetrics & Gynaecology",                 10),
    ("periods",                     "Obstetrics & Gynaecology",                 10),
    ("irregular periods",           "Obstetrics & Gynaecology",                 10),
    ("menstrual",                   "Obstetrics & Gynaecology",                 10),
    ("menopause",                   "Obstetrics & Gynaecology",                 10),
    ("menopausal",                  "Obstetrics & Gynaecology",                 10),
    ("hot flashes",                 "Obstetrics & Gynaecology",                 10),
    ("post-menopausal",             "Obstetrics & Gynaecology",                 10),
    ("pelvic pain",                 "Obstetrics & Gynaecology",                 10),
    ("vaginal discharge",           "Obstetrics & Gynaecology",                 10),
    ("masik dharm",                 "Obstetrics & Gynaecology",                 10),
    ("pcod",                        "Obstetrics & Gynaecology",                 10),
    ("ovarian",                     "Obstetrics & Gynaecology",                 10),
    ("infertility",                 "Obstetrics & Gynaecology",                 10),
    ("difficulty conceiving",       "Obstetrics & Gynaecology",                 10),
    ("uterus",                      "Obstetrics & Gynaecology",                 10),
    ("vaginal",                     "Obstetrics & Gynaecology",                 10),
    ("garbh",                       "Obstetrics & Gynaecology",                 10),

    # PAEDIATRICS
    ("child",                       "Paediatrics Medicine (Children)",           5),
    ("bacha",                       "Paediatrics Medicine (Children)",           7),
    ("bachcha",                     "Paediatrics Medicine (Children)",           7),
    ("infant",                      "Paediatrics Medicine (Children)",          10),
    ("newborn",                     "Paediatrics Medicine (Children)",          10),
    ("neonatal",                    "Paediatrics Medicine (Children)",          10),
    ("navajaatit",                  "Paediatrics Medicine (Children)",          10),
    ("feeding issues",              "Paediatrics Medicine (Children)",           7),
    ("growth problems",             "Paediatrics Medicine (Children)",           7),
    ("malnutrition",                "Paediatrics Medicine (Children)",           7),
    ("vaccination",                 "Paediatrics Medicine (Children)",           7),
    ("neonatal care",               "Paediatrics Medicine (Children)",          10),
    ("developmental delay",         "Paediatrics Medicine (Children)",          10),
    ("childhood illness",           "Paediatrics Medicine (Children)",           5),

    # PAEDIATRIC SURGERY
    ("child surgery",               "Paediatric Surgery (Children Surgery)",    10),
    ("bacha surgery",               "Paediatric Surgery (Children Surgery)",    10),
    ("undescended testicle",        "Paediatric Surgery (Children Surgery)",    10),
    ("undescended testis",          "Paediatric Surgery (Children Surgery)",    10),
    ("congenital bowel",            "Paediatric Surgery (Children Surgery)",    10),
    ("bowel obstruction",           "Paediatric Surgery (Children Surgery)",     7),
    ("congenital abdominal",        "Paediatric Surgery (Children Surgery)",    10),
    ("congenital defects",          "Paediatric Surgery (Children Surgery)",    10),
    ("cleft lip",                   "Paediatric Surgery (Children Surgery)",    10),
    ("cleft palate",                "Paediatric Surgery (Children Surgery)",    10),
    ("cyst on neck",                "Paediatric Surgery (Children Surgery)",     7),
    ("hirschsprung",                "Paediatric Surgery (Children Surgery)",    10),
    ("biliary atresia",             "Paediatric Surgery (Children Surgery)",    10),
    ("hypospadias",                 "Paediatric Surgery (Children Surgery)",    10),
    ("neural tube defect",          "Paediatric Surgery (Children Surgery)",    10),
    ("spina bifida",                "Paediatric Surgery (Children Surgery)",    10),
    ("hernia in children",          "Paediatric Surgery (Children Surgery)",    10),
    ("surgery in children",         "Paediatric Surgery (Children Surgery)",     7),
    ("wilms tumour",                "Paediatric Surgery (Children Surgery)",    10),

    # GERIATRIC
    ("dementia",                    "Geriatric Medicine (Elderly Care)",        10),
    ("frailty",                     "Geriatric Medicine (Elderly Care)",        10),
    ("frailness",                   "Geriatric Medicine (Elderly Care)",        10),
    ("elderly",                     "Geriatric Medicine (Elderly Care)",         7),
    ("budhapa",                     "Geriatric Medicine (Elderly Care)",         7),
    ("falls",                       "Geriatric Medicine (Elderly Care)",         5),
    ("confusion in elderly",        "Geriatric Medicine (Elderly Care)",        10),
    ("aging problems",              "Geriatric Medicine (Elderly Care)",         7),
    ("multiple illnesses",          "Geriatric Medicine (Elderly Care)",         5),

    # PHYSICAL MEDICINE & REHAB
    ("paralysis rehab",             "Physical Medicine & Rehabilitation",       10),
    ("physiotherapy",               "Physical Medicine & Rehabilitation",       10),
    ("rehabilitation",              "Physical Medicine & Rehabilitation",       10),
    ("rehab",                       "Physical Medicine & Rehabilitation",        7),
    ("spinal cord",                 "Physical Medicine & Rehabilitation",        7),
    ("spinal injury",               "Physical Medicine & Rehabilitation",        7),
    ("disability",                  "Physical Medicine & Rehabilitation",        5),
    ("prosthetic",                  "Physical Medicine & Rehabilitation",       10),
    ("amputation",                  "Physical Medicine & Rehabilitation",       10),
    ("post-amputation",             "Physical Medicine & Rehabilitation",       10),
    ("gait retraining",             "Physical Medicine & Rehabilitation",       10),
    ("muscle spasticity",           "Physical Medicine & Rehabilitation",       10),
    ("spasticity",                  "Physical Medicine & Rehabilitation",       10),
    ("cerebral palsy",              "Physical Medicine & Rehabilitation",       10),
    ("contracture",                 "Physical Medicine & Rehabilitation",       10),
    ("biofeedback",                 "Physical Medicine & Rehabilitation",       10),
    ("pelvic floor",                "Physical Medicine & Rehabilitation",        7),
    ("needing therapy",             "Physical Medicine & Rehabilitation",        7),
    ("post stroke",                 "Physical Medicine & Rehabilitation",       10),
    ("after stroke",                "Physical Medicine & Rehabilitation",       10),
    ("stroke rehab",                "Physical Medicine & Rehabilitation",       10),

    # BURNS & PLASTIC SURGERY
    ("burns",                       "Burns & Plastic Surgery",                  10),
    ("burn injuries",               "Burns & Plastic Surgery",                  10),
    ("burn scar",                   "Burns & Plastic Surgery",                  10),
    ("keloid",                      "Burns & Plastic Surgery",                  10),
    ("scar",                        "Burns & Plastic Surgery",                   7),
    ("contracture",                 "Burns & Plastic Surgery",                  10),
    ("skin grafting",               "Burns & Plastic Surgery",                  10),
    ("wound care",                  "Burns & Plastic Surgery",                   7),
    ("wound healing",               "Burns & Plastic Surgery",                   7),
    ("plastic surgery",             "Burns & Plastic Surgery",                  10),
    ("cosmetic surgery",            "Burns & Plastic Surgery",                   7),
    ("hand surgery",                "Burns & Plastic Surgery",                   7),
    ("cleft lip",                   "Burns & Plastic Surgery",                   5),  # reduced: children → Paediatric Surgery; adults → Burns
    ("cleft palate",                "Burns & Plastic Surgery",                   5),  # reduced: same reason
    ("reconstructive",              "Burns & Plastic Surgery",                  10),
    ("reconstruction",              "Burns & Plastic Surgery",                  10),
    ("breast reconstruction",       "Burns & Plastic Surgery",                  10),
    ("mastectomy",                  "Burns & Plastic Surgery",                  10),
    ("facial laceration",           "Burns & Plastic Surgery",                  10),
    ("lacerations",                 "Burns & Plastic Surgery",                   7),
    ("missing tissue",              "Burns & Plastic Surgery",                  10),

    # DENTAL
    ("tooth",                       "Dental Surgery",                           10),
    ("daant",                       "Dental Surgery",                           10),
    ("gum",                         "Dental Surgery",                            7),
    ("gum disease",                 "Dental Surgery",                           10),
    ("jaw pain",                    "Dental Surgery",                            7),
    ("jaw clicking",                "Dental Surgery",                           10),
    ("dental",                      "Dental Surgery",                           10),
    ("toothache",                   "Dental Surgery",                           10),
    ("molar",                       "Dental Surgery",                           10),
    ("dental implant",              "Dental Surgery",                           10),
    ("implants",                    "Dental Surgery",                            7),
    ("missing teeth",               "Dental Surgery",                            7),
    ("dentures",                    "Dental Surgery",                            7),
    ("braces",                      "Dental Surgery",                            7),
    ("orthodontics",                "Dental Surgery",                           10),
    ("root canal",                  "Dental Surgery",                           10),
    ("tooth extraction",            "Dental Surgery",                           10),
    ("mouth ulcer",                 "Dental Surgery",                            7),
    ("oral cancer",                 "Dental Surgery",                           10),
    ("white patches inside cheek",  "Dental Surgery",                           10),
    ("tmj",                         "Dental Surgery",                           10),

    # CARDIOTHORACIC SURGERY
    ("bypass",                      "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("cabg",                        "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("heart surgery",               "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("chest surgery",               "Cardiothoracic & Vascular Surgery (Heart Surgery)",  7),
    ("lung tumor",                  "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("lung tumour",                 "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("lung mass",                   "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("lung surgery",                "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("aortic",                      "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("aorta",                       "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("aneurysm",                    "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("valve surgery",               "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("valve disease",               "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("heart valve",                 "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("congenital heart",            "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("blocked coronary",            "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("coronary artery blockage",    "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("coronary blockage",           "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("vascular",                    "Cardiothoracic & Vascular Surgery (Heart Surgery)",  7),
    ("cramping in calves",          "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("calf pain walking",           "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("weak pulse",                  "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),
    ("peripheral artery",           "Cardiothoracic & Vascular Surgery (Heart Surgery)", 10),

    # SURGERY GENERAL
    ("breast lump",                 "Surgery (General)",                        10),
    ("breast lamp",                 "Surgery (General)",                        10),  # voice mishear of lump
    ("breast ganth",                "Surgery (General)",                        10),  # Hindi ganth = lump
    ("breast mein ganth",           "Surgery (General)",                        10),
    ("breast mein lamp",            "Surgery (General)",                        10),
    ("breast mein lump",            "Surgery (General)",                        10),
    ("breast swelling",             "Surgery (General)",                         8),
    ("breast mass",                 "Surgery (General)",                        10),
    ("ganth in breast",             "Surgery (General)",                        10),
    ("ganth breast",                "Surgery (General)",                        10),
    ("lump in armpit",              "Surgery (General)",                         7),
    ("swelling in armpit",          "Surgery (General)",                         7),
    ("ganth in armpit",             "Surgery (General)",                         7),  # Hindi
    ("thyroid surgery",             "Surgery (General)",                         7),
    ("thyroid cancer",              "Surgery (General)",                         7),
    ("thyroid nodule",              "Surgery (General)",                         7),
    ("goiter",                      "Surgery (General)",                        10),
    ("abscess",                     "Surgery (General)",                         7),
    ("wound",                       "Surgery (General)",                         5),
    ("non-healing ulcer",           "Surgery (General)",                        10),
    ("non healing wound",           "Surgery (General)",                        10),
    ("ingrown toenail",             "Surgery (General)",                         7),
    ("lump near anus",              "Surgery (General)",                        10),
    ("rectal bleeding",             "Surgery (General)",                         7),
    ("piles",                       "Surgery (General)",                        10),
    ("haemorrhoids",                "Surgery (General)",                        10),
    ("hemorrhoids",                 "Surgery (General)",                        10),
    ("hernia",                      "Surgery (General)",                         7),
    ("fistula",                     "Surgery (General)",                         7),
    ("anal fissure",                "Surgery (General)",                         7),
    ("rectal prolapse",             "Surgery (General)",                        10),
    ("gallbladder",                 "Surgery (General)",                         7),
    ("gallstone",                   "Surgery (General)",                         7),
    ("varicose veins",              "Surgery (General)",                        10),
    ("varicose",                    "Surgery (General)",                         7),
    ("bariatric",                   "Surgery (General)",                        10),
    ("weight loss surgery",         "Surgery (General)",                        10),
    ("soft tissue sarcoma",         "Surgery (General)",                        10),
    ("appendix",                    "Surgery (General)",                         5),

    # MEDICINE GENERAL (catch-all)
    ("fever",                       "Medicine (General)",                        3),
    ("bukhar",                      "Medicine (General)",                        3),
    ("fatigue",                     "Medicine (General)",                        3),
    ("weakness",                    "Medicine (General)",                        2),
    ("kamzori",                     "Medicine (General)",                        2),
    ("body ache",                   "Medicine (General)",                        3),
    ("headache",                    "Medicine (General)",                        3),
    ("thirst",                      "Medicine (General)",                        3),
    ("sir dard",                    "Medicine (General)",                        3),
    ("general checkup",             "Medicine (General)",                        5),
    ("general illness",             "Medicine (General)",                        5),
    ("infections",                  "Medicine (General)",                        3),
    ("tropical diseases",           "Medicine (General)",                        7),
    ("hypertension",                "Medicine (General)",                        3),
    ("anaemia",                     "Medicine (General)",                        3),
]


def score_departments(features: Dict) -> List[Dict]:
    """
    Score all departments based on extracted features.
    Returns top 3 departments with scores.

    Primary complaint gets 3x weight vs associated symptoms.
    Negated symptoms are skipped.
    Age and gender apply boosts.
    """
    primary    = (features.get("primary_complaint") or "").lower()
    associated = [s.lower() for s in (features.get("associated_symptoms") or [])]
    negations  = [s.lower() for s in (features.get("negations") or [])]
    age        = features.get("age")
    gender     = (features.get("gender") or "").lower()

    # Primary complaint weighted 3x
    symptom_text = (primary + " ") * 3 + " ".join(associated)

    scores: Dict[str, int] = {}

    # Short acronyms (≤4 chars, letters only) need word-boundary matching to avoid
    # substring false matches e.g. "ild" inside "mild" or "child"
    ACRONYM_KWS = {kw for kw, _, _ in SCORING_RULES if len(kw) <= 4 and kw.isalpha()}

    for keyword, dept, points in SCORING_RULES:
        if keyword in ACRONYM_KWS:
            if not re.search(r'\b' + re.escape(keyword) + r'\b', symptom_text):
                continue
        elif keyword not in symptom_text:
            continue
        if any(keyword in neg for neg in negations):
            continue
        scores[dept] = scores.get(dept, 0) + points

    # ── Combo scoring: multi-symptom red flag patterns ──────────────────────
    # These fire when a dangerous symptom COMBINATION is present that
    # individual keywords miss because each symptom alone routes elsewhere.
    full_text = primary + " " + " ".join(associated)

    def has(kw): return kw in full_text

    # Raised ICP triad: severe headache + vomiting + visual disturbance
    if has("headache") and has("vomiting") and (has("blurred") or has("vision") or has("blurry")):
        scores["Neurosurgery (Brain Surgery)"] = scores.get("Neurosurgery (Brain Surgery)", 0) + 15

    # Cauda equina / myelopathy: back pain + leg numbness + walking difficulty
    if has("back pain") and (has("numbness") or has("numb")) and (has("walk") or has("gait")):
        scores["Neurosurgery (Brain Surgery)"] = scores.get("Neurosurgery (Brain Surgery)", 0) + 12

    # Stroke pattern: facial + weakness or speech → Neurology OPD
    if (has("facial") or has("face")) and (has("weakness") or has("speech") or has("slurring")):
        scores["Neurology (Brain & Nerves)"] = scores.get("Neurology (Brain & Nerves)", 0) + 20

    # Surgical lung: lung + tumor/mass + surgical context
    if has("lung") and (has("tumor") or has("tumour") or has("mass")) and (has("surg") or has("remov")):
        scores["Cardiothoracic & Vascular Surgery (Heart Surgery)"] = (
            scores.get("Cardiothoracic & Vascular Surgery (Heart Surgery)", 0) + 15)

    # Migraine triad: headache + nausea/vomiting + light sensitivity
    if has("headache") and (has("nausea") or has("vomiting")) and (has("light") or has("sensitivity")):
        scores["Neurology (Brain & Nerves)"] = scores.get("Neurology (Brain & Nerves)", 0) + 10

    # Diabetic foot / surgical ulcer: non-healing wound + diabetes
    if (has("non-healing") or has("non healing") or has("diabetic foot")) and has("diabetes"):
        scores["Surgery (General)"] = scores.get("Surgery (General)", 0) + 10

    # Age boosts
    if age is not None:
        try:
            age_int = int(age)
            if age_int <= 14:
                # Both paediatric departments get equal age boost
                # so surgical vs medical decision is made on symptom keywords alone
                scores["Paediatrics Medicine (Children)"] = (
                    scores.get("Paediatrics Medicine (Children)", 0) + 15
                )
                scores["Paediatric Surgery (Children Surgery)"] = (
                    scores.get("Paediatric Surgery (Children Surgery)", 0) + 15
                )
            elif age_int >= 65:
                scores["Geriatric Medicine (Elderly Care)"] = (
                    scores.get("Geriatric Medicine (Elderly Care)", 0) + 10
                )
        except (ValueError, TypeError):
            pass

    # Gender boost
    if gender in ["female", "f", "mahila", "aurat", "lady", "woman"]:
        scores["Obstetrics & Gynaecology"] = (
            scores.get("Obstetrics & Gynaecology", 0) + 3
        )

    # Sort and return top 3
    if not scores:
        return [{"dept": "Medicine (General)", "score": 1}]

    sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
    return [{"dept": dept, "score": score} for dept, score in sorted_scores[:3]]


def get_confidence_gap(top3: List[Dict]) -> int:
    """
    Returns confidence gap % between top 1 and top 2.
    Gap < 65% → ambiguous → LLM 2 should ask follow-up.
    Gap >= 65% → confident → LLM 2 can route directly.
    """
    if len(top3) < 2:
        return 100
    top_score    = top3[0]["score"]
    second_score = top3[1]["score"]
    if top_score == 0:
        return 0
    return int(((top_score - second_score) / top_score) * 100)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — SEVERITY
# ══════════════════════════════════════════════════════════════════════════════

URGENT_SEVERITY_HINTS  = ["moderate", "severe", "high", "tez", "zyada", "bahut"]
URGENT_ONSET_KEYWORDS  = ["sudden", "achanak", "abrupt"]
URGENT_DURATION_KEYWORDS = [
    "5 din", "6 din", "7 din", "week", "hafte",
    "10 din", "15 din", "month", "mahine",
]


def get_severity(features: Dict, is_selfcare: bool = False) -> str:
    """
    Returns one of: "urgent" | "routine" | "selfcare"
    """
    if is_selfcare:
        return "selfcare"

    severity_hint = (features.get("severity_hint") or "").lower()
    onset         = (features.get("onset") or "").lower()
    duration      = (features.get("duration") or "").lower()
    age           = features.get("age")

    if severity_hint in URGENT_SEVERITY_HINTS:
        return "urgent"

    if any(kw in onset for kw in URGENT_ONSET_KEYWORDS):
        return "urgent"

    if any(kw in duration for kw in URGENT_DURATION_KEYWORDS):
        return "urgent"

    if age is not None:
        try:
            if int(age) < 5 or int(age) >= 65:
                return "urgent"
        except (ValueError, TypeError):
            pass

    return "routine"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — MASTER FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def run_engine(features: Dict, raw_flags: Dict[str, bool]) -> Dict:
    """
    Master function called from main.py.
    Runs all engine steps and returns complete output.

    Returns:
        {
            "show_advisory":  bool  — True if soft Casualty advisory should be shown,
            "is_selfcare":    bool,
            "top3":           [{"dept":..., "score":...}, ...],
            "confidence_gap": int (0-100),
            "severity":       "urgent"|"routine"|"selfcare",
        }
    """
    # Step 1 — Red flag advisory check
    show_advisory = red_flag_check(features, raw_flags)

    # Step 2 — Self-care eligibility
    is_selfcare = is_selfcare_eligible(features)

    # Step 3 — Score departments
    top3 = score_departments(features)

    # Step 4 — Confidence gap
    confidence_gap = get_confidence_gap(top3)

    # Step 5 — Severity
    severity = get_severity(features, is_selfcare)

    return {
        "show_advisory":  show_advisory,
        "is_selfcare":    is_selfcare,
        "top3":           top3,
        "confidence_gap": confidence_gap,
        "severity":       severity,
    }


# ══════════════════════════════════════════════════════════════════════════════
# QUICK TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    NO_FLAGS = {k: False for k in [
        "chest_pain", "arm_pain", "breathlessness", "unconscious",
        "stroke", "heavy_bleeding", "seizure", "vomiting_blood",
        "head_injury", "high_fever", "severe_abdominal",
        "child_emergency", "sweating",
    ]}

    tests = [
        {
            "label": "⚠️  Chest pain alone — OPD routing, NO advisory",
            "features": {"primary_complaint": "chest pain", "associated_symptoms": [], "severity_hint": None, "onset": None, "duration": None, "age": 35, "gender": "male", "negations": [], "body_part": None, "context_flags": {}},
            "raw_flags": {**NO_FLAGS, "chest_pain": True},
            "expect": {"show_advisory": False, "is_selfcare": False},
        },
        {
            "label": "⚠️  Chest pain + breathlessness — OPD + advisory",
            "features": {"primary_complaint": "chest pain", "associated_symptoms": ["breathlessness"], "severity_hint": "moderate", "onset": "gradual", "duration": "2 din", "age": 45, "gender": "male", "negations": [], "body_part": None, "context_flags": {}},
            "raw_flags": {**NO_FLAGS, "chest_pain": True, "breathlessness": True},
            "expect": {"show_advisory": True, "is_selfcare": False},
        },
        {
            "label": "⚠️  Chest pain severe — OPD + advisory",
            "features": {"primary_complaint": "chest pain", "associated_symptoms": [], "severity_hint": "severe", "onset": "sudden", "duration": "1 ghanta", "age": 52, "gender": "male", "negations": [], "body_part": None, "context_flags": {}},
            "raw_flags": {**NO_FLAGS, "chest_pain": True, "sweating": True, "arm_pain": True},
            "expect": {"show_advisory": True},
        },
        {
            "label": "⚠️  Behosh — OPD + advisory",
            "features": {"primary_complaint": "behosh", "associated_symptoms": [], "severity_hint": "severe", "onset": "sudden", "duration": None, "age": 40, "gender": "male", "negations": [], "body_part": None, "context_flags": {}},
            "raw_flags": {**NO_FLAGS, "unconscious": True},
            "expect": {"show_advisory": True},
        },
        {
            "label": "🏠 Mild fever alone — Self-care, no advisory",
            "features": {"primary_complaint": "fever", "associated_symptoms": [], "severity_hint": "mild", "onset": "gradual", "duration": "1 din", "age": 28, "gender": "male", "negations": [], "body_part": None, "context_flags": {}},
            "raw_flags": NO_FLAGS,
            "expect": {"is_selfcare": True, "severity": "selfcare", "show_advisory": False},
        },
        {
            "label": "🏠 Mild headache alone — Self-care",
            "features": {"primary_complaint": "sar dard", "associated_symptoms": [], "severity_hint": "mild", "onset": "gradual", "duration": "2 din", "age": 35, "gender": "female", "negations": [], "body_part": None, "context_flags": {}},
            "raw_flags": NO_FLAGS,
            "expect": {"is_selfcare": True, "show_advisory": False},
        },
        {
            "label": "🚫 Fever in child < 5 — NOT self-care",
            "features": {"primary_complaint": "fever", "associated_symptoms": [], "severity_hint": "mild", "onset": "gradual", "duration": "1 din", "age": 3, "gender": "male", "negations": [], "body_part": None, "context_flags": {}},
            "raw_flags": NO_FLAGS,
            "expect": {"is_selfcare": False, "severity": "urgent"},
        },
        {
            "label": "🟢 Diabetes routine — Endocrinology, no advisory",
            "features": {"primary_complaint": "diabetes", "associated_symptoms": ["weight loss"], "severity_hint": "mild", "onset": "gradual", "duration": "6 mahine", "age": 48, "gender": "male", "negations": [], "body_part": None, "context_flags": {}},
            "raw_flags": NO_FLAGS,
            "expect": {"is_selfcare": False, "show_advisory": False},
        },
        {
            "label": "🟢 Kidney stone — Urology, no advisory",
            "features": {"primary_complaint": "kidney stone", "associated_symptoms": ["peshaab mein jalan"], "severity_hint": "moderate", "onset": "sudden", "duration": "1 din", "age": 38, "gender": "male", "negations": [], "body_part": None, "context_flags": {}},
            "raw_flags": NO_FLAGS,
            "expect": {"show_advisory": False},
        },
        {
            "label": "👶 Child 4 yrs — Paediatrics boosted",
            "features": {"primary_complaint": "fever", "associated_symptoms": ["loose motion"], "severity_hint": "moderate", "onset": "gradual", "duration": "2 din", "age": 4, "gender": "male", "negations": [], "body_part": None, "context_flags": {}},
            "raw_flags": NO_FLAGS,
            "expect": {"is_selfcare": False, "severity": "urgent"},
        },
    ]

    print("── engine.py test ───────────────────────────────────")
    all_passed = True
    for tc in tests:
        result = run_engine(tc["features"], tc["raw_flags"])
        passed = all(result.get(k) == v for k, v in tc["expect"].items())
        if not passed:
            all_passed = False
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"\n{status} {tc['label']}")
        print(f"       advisory={result['show_advisory']}  selfcare={result['is_selfcare']}  severity={result['severity']}")
        print(f"       top3={[d['dept'].split('(')[0].strip() for d in result['top3']]}")
        print(f"       gap={result['confidence_gap']}%")
        if not passed:
            for k, v in tc["expect"].items():
                if result.get(k) != v:
                    print(f"       ❌ expected {k}={v}, got {result.get(k)}")

    print(f"\n── {'All tests passed ✅' if all_passed else 'Some tests failed ❌'} ──")