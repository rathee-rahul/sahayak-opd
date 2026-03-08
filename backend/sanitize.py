"""
sanitize.py — Sahayak v8
PII Scrubbing: strips private data before it reaches any LLM.
Always the FIRST step in the pipeline.

Removes:
  - Indian mobile numbers  (10-digit starting with 6-9)
  - Aadhaar numbers        (12-digit, with or without spaces)
  - Email addresses
  - Patient IDs            (PT/AIIMS followed by digits)
  - UHID numbers           (UHID followed by digits)
  - Generic long numbers   (8+ digit standalone numbers)
"""

import re

# ── PII PATTERNS ─────────────────────────────────────────────────────────────

_PATTERNS = [
    # Indian mobile: 10 digits starting with 6, 7, 8, or 9
    (r'\b[6-9]\d{9}\b',                      '[PHONE]'),

    # Aadhaar with spaces: 4-4-4 format e.g. 1234 5678 9012
    (r'\b\d{4}\s\d{4}\s\d{4}\b',             '[AADHAAR]'),

    # Aadhaar without spaces: 12 consecutive digits
    (r'\b\d{12}\b',                           '[AADHAAR]'),

    # Email addresses
    (r'[\w.\-]+@[\w.\-]+\.\w{2,}',           '[EMAIL]'),

    # AIIMS Patient ID / UHID  e.g. PT12345, UHID-789012, AIIMS/2024/1234
    (r'\b(?:PT|UHID|AIIMS)[/\-]?\d{4,}\b',  '[PATIENT_ID]'),

    # Generic standalone long numbers (8+ digits) — catch-all for IDs
    (r'\b\d{8,}\b',                           '[ID]'),
]

# Pre-compile for speed
_COMPILED = [(re.compile(pat, re.IGNORECASE), repl) for pat, repl in _PATTERNS]


# ── MAIN FUNCTION ─────────────────────────────────────────────────────────────

def sanitize_input(text: str) -> str:
    """
    Strip PII from raw patient input.
    Returns cleaned text safe to send to LLMs.

    Args:
        text: raw message from patient

    Returns:
        cleaned text with PII replaced by placeholder tokens
    """
    if not text or not isinstance(text, str):
        return text or ""

    cleaned = text
    for pattern, replacement in _COMPILED:
        cleaned = pattern.sub(replacement, cleaned)

    return cleaned.strip()


def was_sanitized(original: str, cleaned: str) -> bool:
    """
    Returns True if any PII was found and replaced.
    Useful for logging purposes.
    """
    return original.strip() != cleaned.strip()


# ── QUICK TEST ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_cases = [
        "Mera naam Rahul hai, 9876543210, seene mein dard hai",
        "Patient ID PT12345, bukhar hai 3 din se",
        "UHID-789012 wale patient ke liye OPD chahiye",
        "Mera Aadhaar 1234 5678 9012 hai, sir dard hai",
        "Email pe bhejo: rahul.kumar@gmail.com, pet mein dard",
        "Mujhe sirf bukhar hai",                                  # no PII
        "9845678901 pe call karo, saans lene mein takleef",
        "123456789012 patient ko cardiology jaana hai",           # 12-digit Aadhaar
    ]

    print("── sanitize.py test ──────────────────────────────")
    for t in test_cases:
        cleaned = sanitize_input(t)
        flag = " ← PII removed" if was_sanitized(t, cleaned) else ""
        print(f"IN : {t}")
        print(f"OUT: {cleaned}{flag}")
        print()
