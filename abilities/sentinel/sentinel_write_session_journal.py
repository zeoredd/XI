#!/usr/bin/env python3
"""
🛡️ sentinel_write_session_journal.py
Writes structured session journal for Sentinel's audit journal.
Accepts --flow_context JSON from flow orchestrator, signs with Minisign, archives, and wipes session_cache.
"""

import sys
from pathlib import Path  # <-- add this BEFORE using Path

# Make project root importable (…/XI)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# now safe to import project modules
from abilities.common_abilities.time_utils import get_current_quarter_and_period

import subprocess
import shutil
import json
import uuid
import argparse
from pathlib import Path
from datetime import date, datetime, UTC


# 🎛️ Parse flow context from CLI
parser = argparse.ArgumentParser()
parser.add_argument("--flow_context", type=str, required=True, help="JSON string of flow context")
args = parser.parse_args()

try:
    flow_context = json.loads(args.flow_context)
except json.JSONDecodeError:
    print("❌ Invalid JSON in --flow_context.")
    exit(1)

# 🔧 Config
AGENT_NAME = "sentinel"
FLOW_TYPE = flow_context.get("flow_type", "audit")  # default to 'audit'
JOURNAL_TYPE = "sentinel_audit_journal"
TEMP = flow_context.get("temp", 0.3)
MODEL = flow_context.get("model", "unknown_model")
CONTEXT_LENGTH = flow_context.get("context_length", 0)
THREAD_REFS = flow_context.get("thread_refs", [])

MINISIGN_KEY_PATH = Path("keys") / f"{AGENT_NAME}_keys" / f"secret_{AGENT_NAME}.txt"

# 📁 Paths
today = date.today()
today_str = today.strftime("%Y%m%d")
memory_dir = Path(f"memory/{AGENT_NAME}/{JOURNAL_TYPE}/session")
memory_dir.mkdir(parents=True, exist_ok=True)

# Session cache disabled — skip journal write quietly
print("ℹ️ session cache disabled — skipping sentinel journal write.")
sys.exit(0)

# 📆 Time info
calendar_info = get_current_quarter_and_period(today)

# 🗓️ Compose journal entry
uuid_val = str(uuid.uuid4())

# (unreachable: exited above)


# 🧼 Sanitize thoughts/decisions for recursive memory loops
import re

def sanitize_thoughts_field(text):
    if not isinstance(text, str):
        return text

    # Try to extract embedded JSON block: {"entries": [...]}
    match = re.search(r'(\{\\?"entries\\?".*?\})', text)
    if match:
        try:
            # Clean and decode the stringified JSON
            embedded = match.group(1)
            # Undo over-escaping (e.g., \\n, \\" etc.)
            embedded_clean = embedded.encode().decode("unicode_escape")
            parsed = json.loads(embedded_clean)
            if isinstance(parsed, dict) and "entries" in parsed:
                return "\n".join(e.get("content", "").strip() for e in parsed["entries"])
        except Exception as e:
            print(f"⚠️ Failed to parse embedded entries block: {e}")
            return None

    return text

# 🧠 Apply sanitization and detect corruption

journal_guidance = "Add a brief summary; keep thoughts concise; list concrete decisions."

cleaned_thoughts = sanitize_thoughts_field(thoughts)
if cleaned_thoughts is None or cleaned_thoughts.strip() in ("", "[Corrupted memory block removed]"):
    print("⚠️ Detected corrupted journal input. Inserting fallback content.")
    is_corrupted = True
    cleaned_thoughts = (
        "⚠️ Journal content was corrupted or malformed.\n"
        "This fallback was inserted to preserve continuity.\n\n"
        f"{journal_guidance.strip()}\n\n"
        "--- Note ---\n"
        "Agent encountered invalid or unusable flash cache / reflection data."
    )
else:
    is_corrupted = False

# 🧠 Sanitize decisions list
if isinstance(decisions, str):
    try:
        decoded = json.loads(decisions)
        if isinstance(decoded, list):
            decisions = [str(d).strip() for d in decoded]
        else:
            decisions = []
    except Exception:
        decisions = []
elif not isinstance(decisions, list):
    decisions = []

thoughts = cleaned_thoughts

# 🧠 Optional reminder if summary is missing but we recovered useful reflections
if not summary.strip() and len(thoughts.strip()) > 150:
    summary = "[⚠️ No summary provided. Agent should consider adding a short overview in future entries.]"

# 📦 Compose final journal entry
entry = {
    "date": str(today),
    "year": calendar_info["year"],
    "day_of_year": calendar_info["day_of_year"],
    "quarter": calendar_info["quarter"],
    "period": calendar_info["period"],
    "agent": AGENT_NAME,
    "thread_type": FLOW_TYPE,
    "thread_refs": THREAD_REFS,
    "summary": summary,
    "thoughts": thoughts,
    "decisions": decisions,
    "uuid": uuid_val,
    "signature": "unsigned",
    "written_by": AGENT_NAME,
    "_meta": {
        "filename": "",
        "model": MODEL,
        "temp": TEMP,
        "context_length": CONTEXT_LENGTH,
        "signed": True,
        "signed_by": "system",
        "timestamp": datetime.now(UTC).isoformat()
    }
}

# 🗋 Determine output filename, check for collision
suffix = ""
i = 1
while True:
    output_file = memory_dir / f"{AGENT_NAME}_session_journal_{today_str}{suffix}.json"
    if not output_file.exists():
        break
    i += 1
    suffix = f"_{i}"

# 📂 Write journal
with open(output_file, "w") as f:
    json.dump(entry, f, indent=2)

# 🔑 Sign with Minisign
# BEFORE signing, set the metadata flag to what you *intend* to happen.
entry["_meta"]["signed"] = True
entry["_meta"]["signed_by"] = "system"

# Write once (final content that will be signed)
with open(output_file, "w") as f:
    json.dump(entry, f, indent=2)

# Sign the on-disk bytes we just wrote
result = subprocess.run([
    "minisign", "-S", "-s", MINISIGN_KEY_PATH, "-m", str(output_file),
    "-t", f"agent={AGENT_NAME};uuid={uuid_val};date={today_str}"
])

# If signing failed, reflect that separately (do NOT rewrite the journal body)
if result.returncode != 0:
    print("⚠️ Minisign signing failed.")
    # Optionally write a tiny sidecar status file:
    (output_file.with_suffix(".signstatus")).write_text("failed", encoding="utf-8")
else:
    print(f"✅ Journal written and signed: {output_file.name}")

# 📆 Archive and wipe cache
def archive_and_wipe_cache(agent_name: str, journal_type: str, max_backups: int = 100):
    # session cache disabled — nothing to archive or wipe
    return

archive_and_wipe_cache(AGENT_NAME, JOURNAL_TYPE)

