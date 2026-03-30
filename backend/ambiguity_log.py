"""
ambiguity_log.py — Sahayak v8
Supabase Ambiguity Logger.

Purpose:
  Log cases where routing was ambiguous, uncertain, or overridden.
  Runs ASYNC after response is sent — zero added latency to patient.

Storage: Supabase (PostgreSQL)
Table:   ambiguity_logs

What gets logged:
  - confidence_gap < 65% (ambiguous Python scoring)
  - confidence < 70 (LLM 2 uncertain)
  - python_correct = false (LLM overrode Python)
  - follow_up_count >= 2 (forced route after max follow-ups)
  - is_selfcare = true (self-care path taken)

DESIGN: Fire-and-forget async. Never blocks the main response.
"""

import asyncio
import os
from typing import Optional


# ── SUPABASE CONFIG ───────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
TABLE_NAME           = "ambiguity_logs"


# ── SHOULD THIS CASE BE LOGGED? ───────────────────────────────────────────────

def should_log(
    confidence_gap: int,
    llm_confidence: int,
    python_correct: bool,
    follow_up_count: int,
    is_selfcare: bool,
) -> bool:
    if confidence_gap < 65:
        return True
    if llm_confidence < 70:
        return True
    if not python_correct:
        return True
    if follow_up_count >= 2:
        return True
    if is_selfcare:
        return True
    return False


# ── BUILD ROW DICT ────────────────────────────────────────────────────────────

def build_row(
    sanitized_input: str,
    features: dict,
    engine_output: dict,
    clinical_output: dict,
    confirmed_symptoms: list,
    denied_symptoms: list,
    follow_up_count: int,
    session_id: Optional[str] = None,
) -> dict:
    """Build a flat dict matching the ambiguity_logs table columns."""

    top3 = engine_output.get("top3", [])

    return {
        "session_id":        session_id,
        "sanitized_input":   sanitized_input,
        "primary_complaint": features.get("primary_complaint"),
        "age":               features.get("age"),
        "gender":            features.get("gender"),
        "severity_hint":     features.get("severity_hint"),
        "duration":          features.get("duration"),
        "python_top1":       top3[0]["dept"] if len(top3) > 0 else None,
        "python_top2":       top3[1]["dept"] if len(top3) > 1 else None,
        "python_top3":       top3[2]["dept"] if len(top3) > 2 else None,
        "confidence_gap":    engine_output.get("confidence_gap", 0),
        "is_emergency":      engine_output.get("is_emergency", False),
        "is_selfcare":       engine_output.get("is_selfcare", False),
        "show_advisory":     engine_output.get("show_advisory", False),
        "llm_final_dept":    clinical_output.get("final_dept"),
        "llm_severity":      clinical_output.get("severity"),
        "llm_confidence":    clinical_output.get("confidence"),
        "python_correct":    clinical_output.get("python_correct", True),
        "llm_reason":        clinical_output.get("reason"),
        "follow_up_count":   follow_up_count,
        "confirmed_symptoms": ", ".join(confirmed_symptoms) if confirmed_symptoms else None,
        "denied_symptoms":    ", ".join(denied_symptoms)    if denied_symptoms    else None,
        "doctor_override":   None,
        "override_reason":   None,
        "reviewed_by":       None,
        "reviewed_at":       None,
        "user_feedback":     None,
        "feedback_helpful":  None,
    }


# ── ASYNC WRITE TO SUPABASE ───────────────────────────────────────────────────

async def write_to_supabase(row: dict) -> None:
    """
    Insert one row into Supabase ambiguity_logs table via REST API.
    No extra library needed — uses requests which is already installed.
    Fire-and-forget — never raises, never blocks main app.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("[Supabase] WARNING: Keys not set — skipping log")
        return

    try:
        import requests

        url     = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
        headers = {
            "apikey":        SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type":  "application/json",
            "Prefer":        "return=minimal",
        }

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(url, json=row, headers=headers, timeout=10)
        )

        if response.status_code in (200, 201):
            print(f"[Supabase] Log saved — dept={row.get('llm_final_dept')} gap={row.get('confidence_gap')}")
        else:
            print(f"[Supabase] Failed: {response.status_code} {response.text[:150]}")

    except Exception as e:
        print(f"[Supabase] Exception: {e}")


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
    Called from main.py via FastAPI BackgroundTasks after every response.
    Checks if logging needed, builds row, writes to Supabase async.
    Zero latency impact on patient — runs after response is already sent.
    """
    confidence_gap = engine_output.get("confidence_gap", 100)
    llm_confidence = clinical_output.get("confidence", 100)
    python_correct = clinical_output.get("python_correct", True)
    is_selfcare    = engine_output.get("is_selfcare", False)

    if not should_log(
        confidence_gap  = confidence_gap,
        llm_confidence  = llm_confidence,
        python_correct  = python_correct,
        follow_up_count = follow_up_count,
        is_selfcare     = is_selfcare,
    ):
        return  # clean confident case — nothing to log

    row = build_row(
        sanitized_input    = sanitized_input,
        features           = features,
        engine_output      = engine_output,
        clinical_output    = clinical_output,
        confirmed_symptoms = confirmed_symptoms,
        denied_symptoms    = denied_symptoms,
        follow_up_count    = follow_up_count,
        session_id         = session_id,
    )

    await write_to_supabase(row)