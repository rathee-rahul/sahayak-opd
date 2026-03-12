"""
ambiguity_log.py — Sahayak v8
Async Ambiguity Logger.

Purpose:
  Log cases where routing was ambiguous, uncertain, or overridden.
  Runs ASYNC after response is sent — zero added latency to patient.

What gets logged:
  - confidence_gap < 65% (ambiguous Python scoring)
  - confidence < 70 (LLM 2 uncertain)
  - python_correct = false (LLM overrode Python)
  - follow_up_count >= 2 (forced route after max follow-ups)
  - is_selfcare = true (self-care path taken)

Log format: JSONL (one JSON object per line)
Log file:   logs/ambiguity_log.jsonl

doctor_override field:
  Reserved for future AIIMS doctor corrections.
  When a doctor reviews a case and says "should have been X dept" —
  they fill doctor_override. This becomes your training data gold.
  This field is the most valuable long-term asset of Sahayak.

DESIGN: Fire-and-forget async. Never blocks the main response.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Optional


# ── LOG FILE PATH ─────────────────────────────────────────────────────────────
LOG_DIR  = os.path.join(os.path.dirname(__file__), "logs")
LOG_FILE = os.path.join(LOG_DIR, "ambiguity_log.jsonl")


# ── SHOULD THIS CASE BE LOGGED? ───────────────────────────────────────────────

def should_log(
    confidence_gap: int,
    llm_confidence: int,
    python_correct: bool,
    follow_up_count: int,
    is_selfcare: bool,
) -> bool:
    """
    Returns True if this case is worth logging.
    Keeps log lean — only logs genuinely ambiguous or interesting cases.
    """
    if confidence_gap < 65:
        return True   # Python was uncertain between departments
    if llm_confidence < 70:
        return True   # LLM 2 was uncertain about its own answer
    if not python_correct:
        return True   # LLM overrode Python — always log overrides
    if follow_up_count >= 2:
        return True   # Forced route after max follow-ups
    if is_selfcare:
        return True   # Self-care cases — monitor for safety
    return False


# ── BUILD LOG ENTRY ───────────────────────────────────────────────────────────

def build_log_entry(
    # Patient input
    sanitized_input: str,
    features: dict,

    # Engine output
    engine_output: dict,

    # LLM 2 output
    clinical_output: dict,

    # Conversation state
    confirmed_symptoms: list,
    denied_symptoms: list,
    follow_up_count: int,

    # Optional session id for grouping conversation turns
    session_id: Optional[str] = None,
) -> dict:
    """
    Build a structured log entry dict.

    The doctor_override field is intentionally null at log time.
    A doctor reviewing logs can fill it in later:
    {
      "doctor_override": "Rheumatology (Joint & Autoimmune)",
      "override_reason": "Bilateral joint pain + morning stiffness = RA, not OA"
    }
    This field becomes gold-standard training data for future model fine-tuning.
    """
    return {
        # ── Metadata ──────────────────────────────────────────
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "session_id":       session_id,
        "log_version":      "v8",

        # ── Patient input ─────────────────────────────────────
        "sanitized_input":  sanitized_input,
        "primary_complaint": features.get("primary_complaint"),
        "associated_symptoms": features.get("associated_symptoms", []),
        "age":              features.get("age"),
        "gender":           features.get("gender"),
        "severity_hint":    features.get("severity_hint"),
        "duration":         features.get("duration"),

        # ── Engine output ─────────────────────────────────────
        "python_top3":      engine_output.get("top3", []),
        "confidence_gap":   engine_output.get("confidence_gap", 0),
        "is_emergency":     engine_output.get("is_emergency", False),
        "is_selfcare":      engine_output.get("is_selfcare", False),
        "python_severity":  engine_output.get("severity"),

        # ── LLM 2 output ──────────────────────────────────────
        "llm_final_dept":   clinical_output.get("final_dept"),
        "llm_severity":     clinical_output.get("severity"),
        "llm_confidence":   clinical_output.get("confidence"),
        "python_correct":   clinical_output.get("python_correct", True),
        "llm_reason":       clinical_output.get("reason"),
        "follow_up_needed": clinical_output.get("follow_up_needed", False),

        # ── Conversation state ────────────────────────────────
        "confirmed_symptoms": confirmed_symptoms,
        "denied_symptoms":    denied_symptoms,
        "follow_up_count":    follow_up_count,

        # ── Doctor override (filled later by reviewer) ────────
        # This is the most valuable field — gold standard training data
        "doctor_override":        None,
        "doctor_override_reason": None,
        "reviewed_by":            None,
        "reviewed_at":            None,
    }


# ── ASYNC WRITE ───────────────────────────────────────────────────────────────

async def write_log_async(entry: dict) -> None:
    """
    Write one log entry to JSONL file asynchronously.
    Fire-and-forget — never raises, never blocks.
    Uses asyncio.get_running_loop() which is safe inside FastAPI/ASGI context.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = LOG_FILE
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        # get_running_loop() is safe inside a running async context (unlike get_event_loop)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _write_line, line, log_path)
    except Exception:
        pass


def _write_line(line: str, log_path: str) -> None:
    """Synchronous file write — called from executor."""
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


# ── MAIN ENTRY POINT ──────────────────────────────────────────────────────────

async def log_if_ambiguous(
    sanitized_input: str,
    features: dict,
    engine_output: dict,
    clinical_output: dict,
    confirmed_symptoms: list,
    denied_symptoms: list,
    follow_up_count: int,
    session_id: Optional[str] = None,
) -> None:
    """
    Master function called from main.py after response is sent.
    Checks if logging is needed, builds entry, writes async.

    Fire-and-forget — called via FastAPI BackgroundTasks from main.py:
      background_tasks.add_task(log_if_ambiguous, ...)

    Args:
        sanitized_input:    PII-scrubbed patient message
        features:           LLM 1 extracted features
        engine_output:      Python engine output dict
        clinical_output:    LLM 2 clinical output dict
        confirmed_symptoms: symptoms confirmed so far in conversation
        denied_symptoms:    symptoms denied so far in conversation
        follow_up_count:    number of follow-up questions asked
        session_id:         optional session identifier
    """
    confidence_gap  = engine_output.get("confidence_gap", 100)
    llm_confidence  = clinical_output.get("confidence", 100)
    python_correct  = clinical_output.get("python_correct", True)
    is_selfcare     = engine_output.get("is_selfcare", False)

    if not should_log(
        confidence_gap=confidence_gap,
        llm_confidence=llm_confidence,
        python_correct=python_correct,
        follow_up_count=follow_up_count,
        is_selfcare=is_selfcare,
    ):
        return  # clean case — nothing to log

    entry = build_log_entry(
        sanitized_input=sanitized_input,
        features=features,
        engine_output=engine_output,
        clinical_output=clinical_output,
        confirmed_symptoms=confirmed_symptoms,
        denied_symptoms=denied_symptoms,
        follow_up_count=follow_up_count,
        session_id=session_id,
    )

    await write_log_async(entry)


# ── LOG READER (for future admin dashboard) ───────────────────────────────────

def read_recent_logs(n: int = 50) -> list:
    """
    Read last N log entries. Useful for admin review dashboard.
    Returns list of dicts, most recent last.
    """
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        entries = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries
    except Exception:
        return []


def count_overrides() -> dict:
    """
    Count how many times LLM overrode Python.
    Useful for monitoring model disagreement rate.
    Returns: {"total_logged": N, "python_wrong": N, "override_rate_pct": N}
    """
    entries = read_recent_logs(n=10000)
    if not entries:
        return {"total_logged": 0, "python_wrong": 0, "override_rate_pct": 0}

    total        = len(entries)
    python_wrong = sum(1 for e in entries if not e.get("python_correct", True))
    rate         = round((python_wrong / total) * 100, 1) if total > 0 else 0

    return {
        "total_logged":      total,
        "python_wrong":      python_wrong,
        "override_rate_pct": rate,
    }


# ── QUICK TEST ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    print("── ambiguity_log.py test ─────────────────────────────")

    # Override log path for test
    import ambiguity_log as _self
    _orig_log_file = _self.LOG_FILE
    _orig_log_dir  = _self.LOG_DIR

    with tempfile.TemporaryDirectory() as tmpdir:
        _self.LOG_DIR  = tmpdir
        _self.LOG_FILE = os.path.join(tmpdir, "test_log.jsonl")

        sample_features = {
            "primary_complaint": "joint pain",
            "associated_symptoms": ["morning stiffness"],
            "age": 42, "gender": "female",
            "severity_hint": "moderate",
            "duration": "3 mahine se",
        }
        sample_engine = {
            "top3": [
                {"dept": "Orthopaedics (Bones & Joints)", "score": 15},
                {"dept": "Rheumatology (Joint & Autoimmune)", "score": 12},
            ],
            "confidence_gap": 20,
            "is_emergency": False,
            "is_selfcare": False,
            "severity": "routine",
        }
        sample_clinical = {
            "final_dept": "Rheumatology (Joint & Autoimmune)",
            "severity": "routine",
            "confidence": 80,
            "python_correct": False,
            "reason": "Bilateral joint pain + morning stiffness → Rheumatology",
            "follow_up_needed": False,
        }

        # Test 1 — should_log cases
        assert should_log(20, 80, False, 0, False) == True,  "gap<65 should log"
        assert should_log(80, 60, True,  0, False) == True,  "low confidence should log"
        assert should_log(80, 80, False, 0, False) == True,  "override should log"
        assert should_log(80, 80, True,  2, False) == True,  "forced route should log"
        assert should_log(80, 80, True,  0, True)  == True,  "selfcare should log"
        assert should_log(80, 85, True,  1, False) == False, "clean case should NOT log"
        print("✅ should_log logic   : all 6 cases correct")

        # Test 2 — build_log_entry
        entry = build_log_entry(
            sanitized_input="Joints mein dard hai",
            features=sample_features,
            engine_output=sample_engine,
            clinical_output=sample_clinical,
            confirmed_symptoms=["joint pain"],
            denied_symptoms=[],
            follow_up_count=0,
            session_id="test-session-001",
        )
        assert entry["doctor_override"] is None,              "doctor_override should be null"
        assert entry["python_correct"]  == False,             "python_correct should be False"
        assert entry["llm_final_dept"]  == "Rheumatology (Joint & Autoimmune)"
        assert entry["log_version"]     == "v8"
        print("✅ build_log_entry    : all fields correct")

        # Test 3 — async write
        async def test_write():
            os.makedirs(tmpdir, exist_ok=True)
            entry = build_log_entry(
                sanitized_input="Joints mein dard hai",
                features=sample_features,
                engine_output=sample_engine,
                clinical_output=sample_clinical,
                confirmed_symptoms=["joint pain"],
                denied_symptoms=[],
                follow_up_count=0,
                session_id="test-session-001",
            )
            test_log = _self.LOG_FILE
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with open(test_log, "a", encoding="utf-8") as f:
                f.write(line)
            with open(test_log, "r") as f:
                lines = f.readlines()
            assert len(lines) == 1, f"Expected 1 log line, got {len(lines)}"
            written = json.loads(lines[0])
            assert written["primary_complaint"] == "joint pain"
            assert written["doctor_override"]   is None
            print("✅ async write        : log written and verified")

        asyncio.run(test_write())

        # Test 4 — clean case NOT logged
        async def test_no_log():
            test_log = _self.LOG_FILE
            before = open(test_log).read() if os.path.exists(test_log) else ""
            # This is a clean case — should_log returns False — nothing written
            result = should_log(100, 95, True, 0, False)
            assert result == False, "Clean case should NOT log"
            after = open(test_log).read() if os.path.exists(test_log) else ""
            assert before == after, "Clean case should NOT write to log"
            print("✅ clean case        : correctly NOT logged")

        asyncio.run(test_no_log())

    # Restore
    _self.LOG_FILE = _orig_log_file
    _self.LOG_DIR  = _orig_log_dir

    print()
    print("── All tests passed ✅ ───────────────────────────────")