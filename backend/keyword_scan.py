"""
keyword_scan.py — Sahayak v8
Hinglish Emergency Keyword Fast-Path.

Scans raw sanitized text DIRECTLY for emergency red-flag words.
Runs async SIMULTANEOUSLY with LLM 1 — zero added latency.
Produces raw_flags{} boolean dict.

Purpose: Close the LLM 1 bottleneck.
If LLM 1 misses a Hinglish emergency phrase — this catches it.
Both outputs feed into emergency_check() in engine.py.
"""

import re
from typing import Dict

# ── KEYWORD CLUSTERS ─────────────────────────────────────────────────────────
# Each cluster = one medical emergency concept
# Multiple Hinglish phrasings for the same symptom
# Pattern is: word boundary aware, case insensitive

_KEYWORD_CLUSTERS: Dict[str, list] = {

    # ── CHEST PAIN ────────────────────────────────────────────
    "chest_pain": [
        r"seene\s*mein\s*dard",
        r"seene\s*mein\s*daba",
        r"seene\s*mein\s*jalan",
        r"seene\s*mein\s*bhaari",
        r"chest\s*pain",
        r"chest\s*mein\s*dard",
        r"chest\s*dard",
        r"sine\s*mein\s*dard",
        r"chhati\s*mein\s*dard",
        r"chaati\s*mein\s*dard",
        r"dil\s*mein\s*dard",
        r"heart\s*pain",
    ],

    # ── LEFT ARM PAIN ─────────────────────────────────────────
    "arm_pain": [
        r"baaye\s*haath\s*mein\s*dard",
        r"baayein\s*haath\s*mein\s*dard",
        r"left\s*arm\s*(mein\s*)?dard",
        r"left\s*arm\s*pain",
        r"haath\s*mein\s*dard\s*(aur|or|with)\s*seena",
        r"baazu\s*mein\s*dard",
    ],

    # ── BREATHLESSNESS ────────────────────────────────────────
    "breathlessness": [
        r"saans\s*(nahi|nahin|nahi\s*aa\s*raha|band|ruk)",
        r"saans\s*lene\s*mein\s*(takleef|dikkat|mushkil|problem)",
        r"saans\s*phool",
        r"breathing\s*(nahi|problem|issue|band|ruk)",
        r"breathless",
        r"dam\s*(ghut|ghutna|nahi)",
        r"oxygen\s*(nahi|kam|low)",
        r"sans\s*(nahi|band|ruk)",
    ],

    # ── UNCONSCIOUS / FAINTING ────────────────────────────────
    "unconscious": [
        r"behosh",
        r"hosh\s*(nahi|kho|gaya|gayi)",
        r"unconscious",
        r"faint",
        r"gir\s*(pade|gayi|gaya|pada)",
        r"girna",
        r"chhata\s*kho",
        r"aankhein\s*band\s*ho",
    ],

    # ── STROKE SIGNS ──────────────────────────────────────────
    "stroke": [
        r"chehra\s*(teda|tircha|aadha)",
        r"muh\s*(teda|tircha|aadha)",
        r"face\s*(drooping|teda|numb)",
        r"ek\s*taraf\s*(kamzori|sunjpan|numbness)",
        r"haath\s*(uthha\s*nahi|utha\s*nahi|kamzor\s*ho)",
        r"baat\s*(nahi|karna\s*mushkil|samajh\s*nahi)",
        r"bolne\s*mein\s*(takleef|dikkat)",
        r"stroke",
        r"paralysis",
        r"laqwa",
    ],

    # ── HEAVY BLEEDING ────────────────────────────────────────
    "heavy_bleeding": [
        r"bahut\s*zyada\s*khoon",
        r"khoon\s*band\s*nahi",
        r"bleeding\s*(band\s*nahi|zyada|heavy|bahut)",
        r"heavy\s*bleeding",
        r"rakta\s*sraav",
        r"khoon\s*nikal\s*raha",
        r"lahu\s*(zyada|band\s*nahi)",
    ],

    # ── SEVERE HEAD INJURY ────────────────────────────────────
    "head_injury": [
        r"sar\s*(par|mein)\s*(chot|lagi|injury|laga)",
        r"sir\s*(par|mein)\s*(chot|lagi|injury)",
        r"head\s*injury",
        r"sar\s*phata",
        r"gir\s*(ke|kar)\s*sar",
        r"accident\s*(mein|ke\s*baad)\s*sar",
    ],

    # ── SWEATING (with other symptoms — red flag combo) ───────
    "sweating": [
        r"pasina\s*(aa\s*raha|chhoot|bahut|thanda)",
        r"cold\s*sweat",
        r"thanda\s*pasina",
        r"sweating\s*(bahut|zyada|suddenly|achanak)",
        r"paseena\s*(thanda|achanak|zyada)",
    ],

    # ── SEVERE VOMITING / BLOOD IN VOMIT ─────────────────────
    "vomiting_blood": [
        r"ulti\s*mein\s*khoon",
        r"vomit\s*mein\s*blood",
        r"khoon\s*ki\s*ulti",
        r"haematemesis",
        r"blood\s*vomiting",
    ],

    # ── HIGH FEVER WITH RED FLAGS ─────────────────────────────
    "high_fever": [
        r"tez\s*bukhar",
        r"bahut\s*zyada\s*bukhar",
        r"104\s*(degree|f|fever)",
        r"103\s*(degree|f|fever)",
        r"102\s*(degree|f|fever)",
        r"fever\s*(bahut|zyada|high|tez)",
        r"bukhar\s*(bahut|zyada|tez|kam\s*nahi)",
    ],

    # ── SEIZURE / FIT ─────────────────────────────────────────
    "seizure": [
        r"fits?\b",
        r"seizure",
        r"jhatkay",
        r"jhatke\s*(aa|pad|lag)",
        r"epilepsy\s*attack",
        r"dora\s*pada",
        r"convulsion",
        r"haath\s*pair\s*kaanp",
    ],

    # ── SEVERE ABDOMINAL PAIN ─────────────────────────────────
    "severe_abdominal": [
        r"pet\s*mein\s*bahut\s*(tez|zyada)\s*dard",
        r"pet\s*mein\s*(uthha|utha|katne\s*wala)\s*dard",
        r"severe\s*abdominal",
        r"pet\s*phata\s*ja\s*raha",
        r"appendix\s*(dard|phata|attack)",
    ],

    # ── CHILD EMERGENCY ───────────────────────────────────────
    "child_emergency": [
        r"bacha\s*(saans|behosh|fits|seizure|nahi\s*bol)",
        r"bachche\s*(ko\s*)?(saans|behosh|fits|seizure)",
        r"newborn\s*(problem|emergency|saans)",
        r"navajaatit\s*(saans|dard)",
        r"infant\s*(emergency|saans|fits)",
    ],

}

# Pre-compile all patterns
_COMPILED_CLUSTERS: Dict[str, list] = {
    flag: [re.compile(pat, re.IGNORECASE) for pat in patterns]
    for flag, patterns in _KEYWORD_CLUSTERS.items()
}


# ── MAIN FUNCTION ─────────────────────────────────────────────────────────────

def keyword_scan(text: str) -> Dict[str, bool]:
    """
    Scan sanitized patient text for emergency Hinglish keywords.
    Returns raw_flags{} — boolean dict of detected emergency signals.

    Args:
        text: sanitized patient message (after sanitize_input())

    Returns:
        dict like:
        {
            "chest_pain": True,
            "arm_pain": False,
            "breathlessness": True,
            "unconscious": False,
            ...
        }
    """
    if not text or not isinstance(text, str):
        return {flag: False for flag in _COMPILED_CLUSTERS}

    raw_flags: Dict[str, bool] = {}

    for flag, patterns in _COMPILED_CLUSTERS.items():
        raw_flags[flag] = any(pat.search(text) for pat in patterns)

    return raw_flags


def any_emergency_flag(raw_flags: Dict[str, bool]) -> bool:
    """
    Returns True if ANY emergency flag is set.
    Quick check before passing to engine.py emergency_check().
    """
    return any(raw_flags.values())


def triggered_flags(raw_flags: Dict[str, bool]) -> list:
    """
    Returns list of flag names that are True.
    Useful for logging.
    """
    return [flag for flag, val in raw_flags.items() if val]


# ── QUICK TEST ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_cases = [
        # Emergency cases — should trigger flags
        ("Seene mein dard aur saans lene mein takleef",     ["chest_pain", "breathlessness"]),
        ("Baayein haath mein dard aur pasina aa raha hai",  ["arm_pain", "sweating"]),
        ("Behosh ho gaya achanak",                          ["unconscious"]),
        ("Chehra teda ho gaya, ek taraf kamzori",           ["stroke"]),
        ("Bahut zyada khoon nikal raha hai band nahi",      ["heavy_bleeding"]),
        ("Tez bukhar aur fits pad rahe hain",               ["high_fever", "seizure"]),
        ("Saans nahi aa raha, dam ghut raha hai",           ["breathlessness"]),
        ("Sar par chot lagi accident mein",                 ["head_injury"]),
        ("Ulti mein khoon aa raha hai",                     ["vomiting_blood"]),

        # Non-emergency — should NOT trigger any flags
        ("Mujhe sirf bukhar hai 2 din se",                  []),
        ("Sar dard hai halka sa",                           []),
        ("Pet mein thoda dard hai",                         []),
        ("Khansi aa rahi hai",                              []),
        ("Naak se paani aa raha hai",                       []),
    ]

    print("── keyword_scan.py test ──────────────────────────")
    all_passed = True
    for text, expected_flags in test_cases:
        flags = keyword_scan(text)
        triggered = triggered_flags(flags)
        passed = all(f in triggered for f in expected_flags)
        status = "✅ PASS" if passed else "❌ FAIL"
        if not passed:
            all_passed = False
        print(f"{status} | {text}")
        if triggered:
            print(f"       Flags: {triggered}")
        if not passed:
            print(f"       Expected: {expected_flags}")
        print()

    print("── Summary ───────────────────────────────────────")
    print("All tests passed ✅" if all_passed else "Some tests failed ❌")
