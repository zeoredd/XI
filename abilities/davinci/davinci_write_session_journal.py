#!/usr/bin/env python3

# Ensure we can import abilities/* when run directly
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[2]))
ROOT = Path(__file__).resolve().parents[2]


import subprocess
import json
import uuid
import argparse
from pathlib import Path
from datetime import datetime
from datetime import date, datetime, UTC
from abilities.common_abilities.time_utils import get_current_quarter_and_period
from abilities.common_abilities.parse_thread_for_journal import parse_thread_for_journal

# === 🎛️ CLI + Config ===
parser = argparse.ArgumentParser()
parser.add_argument("--flow_context", type=str, required=True)
args = parser.parse_args()

try:
    flow_context = json.loads(args.flow_context)
except json.JSONDecodeError:
    print("❌ Invalid JSON in --flow_context.")
    exit(1)

AGENT = (
    flow_context.get("agent")
    or flow_context.get("current_agent")
    or "davinci"
)
FLOW_TYPE = flow_context.get("flow_type", "unknown")
TEMP = flow_context.get("temp", 0.6)
MODEL = flow_context.get("model", f"companions/{AGENT}/{AGENT}.gguf")
THREAD_REFS = flow_context.get("thread_refs", [])
TODAY = date.today()
TODAY_STR = TODAY.strftime("%Y%m%d")
JOURNAL_TYPE = f"{FLOW_TYPE}_journal"
MEMORY_DIR = ROOT / "memory" / AGENT / JOURNAL_TYPE / "session"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
MINISIGN_KEY_PATH = ROOT / "keys" / f"{AGENT}_keys" / f"secret_{AGENT}.txt"

# --- 🧩 Session cache archive (local, no imports from orchestrator) ---
from pathlib import Path
from datetime import datetime

def session_cache_txt_path(agent_name: str) -> Path:
    # session cache disabled (kept for compatibility; never used)
    return ROOT / "memory" / agent_name / "session_cache.txt"

def archive_and_clear_session_cache(agent_name: str):
    # session cache disabled — nothing to archive or clear
    return

# === 🧠 Parse GAEL answers from the saved thread (no prompting here) ===
parsed = parse_thread_for_journal(AGENT, flow_context) or {}
summary  = (parsed.get("summary") or "").strip()
thoughts = (parsed.get("thoughts") or "").strip()
decisions= (parsed.get("decisions") or "").strip() # keep as string; JSON will escape \n as \\n

# Belt & suspenders: drop lone fence markers if any slipped through.
for k in ("summary", "thoughts", "decisions"):
    v = locals()[k]
    if v in ("```", "```markdown") or v.startswith("```") and "\n" not in v:
        locals()[k] = "" 

# Fallback: if no summary but long thoughts, add hint
if not any([summary, thoughts, decisions]):
    summary = "[⚠️ No GAEL fields found in thread.]"

# === 📆 Time info ===
calendar_info = get_current_quarter_and_period(TODAY)

entry = {
    "date": str(TODAY),
    "year": calendar_info["year"],
    "day_of_year": calendar_info["day_of_year"],
    "quarter": calendar_info["quarter"],
    "period": calendar_info["period"],
    "agent": AGENT,
    "thread_type": FLOW_TYPE,
    "thread_refs": THREAD_REFS,
    "summary": summary,
    "thoughts": thoughts,
    "decisions": decisions,
    "uuid": str(uuid.uuid4()),
    "signature": "unsigned",
    "written_by": AGENT,
    "_meta": {
        "filename": "",
        "model": MODEL,
        "temp": TEMP,
        "context_length": flow_context.get("context_length", 0),
        "signed": False,
        "timestamp": datetime.now(UTC).isoformat()
    }
}

# 👇 add this just before writing the file
claims_override = flow_context.get("claims_override")
if isinstance(claims_override, list):
    try:
        normalized = []
        for c in claims_override:
            subj = c.get("subject"); pred = c.get("predicate"); val = c.get("value")
            if subj and pred and val:
                normalized.append({"subject": subj, "predicate": pred, "value": val})
        if normalized:
            entry["claims"] = normalized
    except Exception as e:
        print(f"⚠️ claims_override not applied: {e}")



# === 💾 Write Journal File ===
suffix = ""
i = 1
while True:
    try:
        output_file = MEMORY_DIR / f"{AGENT}_session_journal_{TODAY_STR}{suffix}.json"
        if output_file.exists():
            i += 1
            suffix = f"_{i}"
            continue
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2, ensure_ascii=False)
        entry["_meta"]["filename"] = str(output_file)
        break
    except Exception as e:
        print(f"❌ Failed to write journal file: {e}")
        raise SystemExit(2)

# === 🔐 Minisign (optional, fail-fast if configured) ===
entry["_meta"]["filename"] = output_file.name

# Write the JSON file first
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(entry, f, indent=2, ensure_ascii=False)

signed = False
try:
    if MINISIGN_KEY_PATH.exists():
        r = subprocess.run(
            ["minisign", "-Sm", str(output_file), "-s", str(MINISIGN_KEY_PATH)],
            check=False
        )
        signed = (r.returncode == 0)
        if not signed:
            print("❌ minisign returned non-zero; aborting so orchestrator can surface it.")
            raise SystemExit(3)
    else:
        print(f"⚠️ No minisign key at {MINISIGN_KEY_PATH}; leaving unsigned.")
except FileNotFoundError:
    print("⚠️ minisign not found — writing unsigned journal.")
except Exception as e:
    print(f"❌ minisign error: {e}")
    raise SystemExit(3)

entry["_meta"]["signed"] = signed
entry["signature"] = "minisign" if signed else "unsigned"

# existing:
print(f"✅ Journal written{ ' and signed' if signed else '' }: {output_file.name}")

# add this EXACT line (absolute path, easy to parse):
print(f"JOURNAL_PATH={output_file}")

# --- 🧩 right after successfully writing the session journal ---
archive_and_clear_session_cache(AGENT)



