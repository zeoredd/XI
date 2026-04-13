#!/usr/bin/env python3
"""
🛡️ sentinel_contextual_validator.py
Validates an agent's journal entry against the referenced thread using:
1. Vector search over thread memory (via pgvector)
2. Full-thread fallback using sentinel_call.py if vector fails

Flags unsupported claims, omissions, or hallucinated entries.
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[2]))
from abilities.common_abilities.memory_search import query_memory_vector

try:
    from abilities.sentinel.sentinel_call import run_sentinel_verification
    SENTINEL_MODEL_AVAILABLE = True
except ImportError:
    SENTINEL_MODEL_AVAILABLE = False

try:
    import nltk
    from nltk.tokenize import sent_tokenize
    _NLTK_OK = True
except Exception:
    _NLTK_OK = False
    import re
    def sent_tokenize(text: str):
        # lightweight fallback if nltk/punkt isn't available
        return [s.strip() for s in re.split(r'(?<=[.!?])\s+', str(text)) if s.strip()]

# === ⚙️ Config ===
VECTOR_THRESHOLD = 0.75
AUDIT_LOG_DIR = Path("memory/sentinel/sentinel_audit_journal/contextual_checks")
AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path):
    with open(path) as f:
        return json.load(f)


def extract_journal_sentences(journal):
    text_blocks = [
        journal.get("summary", ""),
        journal.get("thoughts", ""),
        " ".join(journal.get("decisions", []))
    ]
    joined = "\n".join(text_blocks)
    sentences = sent_tokenize(joined)
    return [s.strip() for s in sentences if len(s.strip()) > 6]


def check_sentence_with_vector(sentence, thread_type):
    result = query_memory_vector(
        query=sentence,
        top_n=3,
        filters={"thread_type": thread_type}
    )
    if not result or "results" not in result:
        return False, []

    matches = result["results"]
    best_score = matches[0]["score"] if matches else 0.0
    return best_score >= VECTOR_THRESHOLD, matches


def check_with_model_fallback(sentence, thread_path):
    if not SENTINEL_MODEL_AVAILABLE:
        return "unknown", "sentinel_call.py not available"

    with open(thread_path) as f:
        thread_data = json.load(f)

    thread_text = json.dumps(thread_data)
    verdict, reason = run_sentinel_verification(sentence, thread_text)
    return verdict, reason


def validate_contextual_alignment(thread_path: Path, journal_path: Path) -> dict:
    thread_path = Path(thread_path)
    journal_path = Path(journal_path)

    thread_data = load_json(thread_path)
    thread_type_guess = thread_data.get("thread_type") or thread_data.get("_meta", {}).get("thread_type", "unknown")

    journal = load_json(journal_path)
    journal_sentences = extract_journal_sentences(journal)

    all_results = []
    for sentence in journal_sentences:
        entry = {
            "sentence": sentence,
            "vector_match": False,
            "vector_matches": [],
            "full_thread_verdict": None,
            "reason": None
        }

        vec_hit, matches = check_sentence_with_vector(sentence, thread_type_guess)
        entry["vector_match"] = vec_hit
        entry["vector_matches"] = matches

        if not vec_hit:
            verdict, reason = check_with_model_fallback(sentence, thread_path)
            entry["full_thread_verdict"] = verdict
            entry["reason"] = reason

        all_results.append(entry)

    return {
        "agent": journal.get("agent", "unknown"),
        "thread_file": str(thread_path),
        "journal_file": str(journal_path),
        "timestamp": datetime.now().isoformat(),
        "entries": all_results,
        "vector_threshold": VECTOR_THRESHOLD,
        "written_by": "sentinel"
    }


def main(thread_path, journal_path):
    result = validate_contextual_alignment(thread_path, journal_path)

    out_file = AUDIT_LOG_DIR / f"contextual_mismatch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    print(f"✅ Contextual audit complete. Flagged {sum(1 for r in result['entries'] if not r['vector_match'])} potential issues.")
    print(f"📄 Saved to: {out_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread", required=True, help="Path to thread JSON")
    parser.add_argument("--journal", required=True, help="Path to agent journal JSON")
    args = parser.parse_args()
    main(args.thread, args.journal)

