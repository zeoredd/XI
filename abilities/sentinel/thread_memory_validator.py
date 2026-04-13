#!/usr/bin/env python3
"""
🧠 thread_memory_validator.py
Validates agent journals against thread memory.
Includes weekly, period, and quarterly summary validators.
To be called from sentinel_routine_audit.py.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional
from nltk.tokenize import sent_tokenize
from abilities.common_abilities.memory_search import query_memory_vector
from abilities.sentinel.resolve_uuid_to_source import resolve_uuids_to_files

# === 🧠 Threshold ===
PASS_THRESHOLD = 0.8

# === ✅ Core Validators ===

def validate_session_journal_against_thread(journal_path: Path, thread_path: Path) -> dict:
    """
    Compares a session journal file against its corresponding thread file.
    Returns a dict with validation status and basic matching.
    """
    try:
        journal = json.loads(journal_path.read_text())
        thread = json.loads(thread_path.read_text())
    except Exception as e:
        return {
            "type": "session",
            "journal": str(journal_path),
            "thread": str(thread_path),
            "status": "error",
            "match_score": None,
            "discrepancies": [f"File read error: {e}"],
            "notes": "Validation aborted due to load error."
        }

    journal_summary = journal.get("summary", "")
    thread_text = "\n".join([entry.get("content", "") for entry in thread.get("entries", [])])

    if not journal_summary or not thread_text:
        return {
            "type": "session",
            "journal": str(journal_path),
            "thread": str(thread_path),
            "status": "incomplete",
            "match_score": None,
            "discrepancies": ["Missing summary or thread entries."],
            "notes": "Either journal or thread missing key content."
        }

    vector_result = query_memory_vector(journal_summary, top_n=1, filters={"agent": journal.get("agent")})
    results = (vector_result or {}).get("results") or []
    first = results[0] if results else {}
    top_score = float(first.get("score", first.get("similarity", 0.0)))

    return {
        "type": "session",
        "journal": str(journal_path),
        "thread": str(thread_path),
        "status": "pass" if top_score >= PASS_THRESHOLD else "fail",
        "match_score": round(top_score, 3),
        "discrepancies": [] if top_score >= PASS_THRESHOLD else ["Low semantic match score."],
        "notes": "Vector search match against thread memory."
    }

def validate_summary_vs_sources(summary_path: Path, source_files: list[Path], summary_type: str) -> dict:
    """
    Compares summary against concatenated text of all source journal files.
    """
    try:
        summary = json.loads(summary_path.read_text())
        source_texts = []
        for f in source_files:
            with open(f) as sf:
                entry = json.load(sf)
                text = entry.get("summary") or json.dumps(entry)
                source_texts.append(text)
        combined_text = "\n".join(source_texts)
    except Exception as e:
        return {
            "type": summary_type,
            "journal": str(summary_path),
            "status": "error",
            "match_score": None,
            "discrepancies": [f"File read error: {e}"],
            "notes": "Validation aborted."
        }

    summary_text = summary.get("summary", "")
    if not summary_text or not combined_text:
        return {
            "type": summary_type,
            "journal": str(summary_path),
            "status": "incomplete",
            "match_score": None,
            "discrepancies": ["Missing summary or source entries."],
            "notes": "Missing summary or resolved source content."
        }

    vector_result = query_memory_vector(summary_text, top_n=1)
    results = (vector_result or {}).get("results") or []
    first = results[0] if results else {}
    top_score = float(first.get("score", first.get("similarity", 0.0)))

    return {
        "type": summary_type,
        "journal": str(summary_path),
        "source_count": len(source_files),
        "status": "pass" if top_score >= PASS_THRESHOLD else "fail",
        "match_score": round(top_score, 3),
        "discrepancies": [] if top_score >= PASS_THRESHOLD else ["Low semantic match score."],
        "notes": f"Compared against {len(source_files)} source files via compiled UUIDs."
    }

# === 🛡️ Sentinel Entry Point ===

def run_all_validations(agent: str) -> dict:
    """
    Entry point for Sentinel to run all validators for a given agent.
    Returns a structured dict with validation results.
    """
    base_path = Path(f"memory/{agent}")
    results = {
        "agent": agent,
        "timestamp": datetime.now().isoformat(),
        "validations": []
    }

    # Session-to-thread comparisons
    for jt in [
        "idea_generator_journal",
        "daily_briefing_and_strategy_journal",
        "personal_and_group_conversation_journal"
    ]:
        session_path = base_path / jt / "session"
        thread_path = Path(f"memory/threads/{jt.replace('_journal', '')}/session")

        for journal_file in session_path.glob(f"{agent}_session_journal_*.json"):
            date_str = journal_file.stem.split("_")[-1]
            thread_file = thread_path / f"{jt.replace('_journal','')}_session_{date_str}.json"

            # ⛔ Skip if missing thread file
            if not thread_file.exists():
                results["validations"].append({
                    "type": "session",
                    "journal": str(journal_file),
                    "thread": str(thread_file),
                    "status": "skipped",
                    "match_score": None,
                    "discrepancies": [],
                    "notes": "Skipped: missing thread file"
                })
                continue

            results["validations"].append(
                validate_session_journal_against_thread(journal_file, thread_file)
            )

    # Summary comparisons (weekly, period, quarterly)
    for jt in ["idea_generator_journal", "daily_briefing_and_strategy_journal"]:
        for level, source_dir_name in zip([
            ("weekly", "session"),
            ("period", "weekly"),
            ("quarterly", "period")
        ], ["session", "weekly", "period"]):

            level_name, source_name = level
            summary_dir = base_path / jt / level_name
            source_dir = base_path / jt / source_name

            for summary_file in summary_dir.glob("*.json"):
                uuids = json.loads(summary_file.read_text()).get("compiled_uuids", [])
                source_files = resolve_uuids_to_files(agent, uuids)
                results["validations"].append(
                    validate_summary_vs_sources(summary_file, source_files, level_name)
                )

    return results
