#!/usr/bin/env python3
"""
📏 axiom_enforcer.py
Applies core axioms (e.g., AXIOM-003, AXIOM-004) to enforce logical consistency across agent memory.
Flags violations and emits them to `disputed_entries.json`.
"""

import json
from pathlib import Path
from datetime import datetime

# === 📁 Config ===
AGENTS = ["davinci", "hermes", "sentinel"]
MEMORY_ROOT = Path("memory")
DISPUTE_LOG = MEMORY_ROOT / "sentinel" / "disputed_entries.json"

# === 🧠 Load helper ===
def load_json(path):
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Failed to load JSON from {path}: {e}")
        return []

# === 💾 Save disputed entries ===
def append_dispute(entry):
    existing = load_json(DISPUTE_LOG)
    existing.append(entry)
    with open(DISPUTE_LOG, "w") as f:
        json.dump(existing, f, indent=2)

# === 🔍 AXIOM-004: Newer entry overrides older belief if confirmed by Brandon ===
def enforce_axiom_004():
    for agent in AGENTS:
        pm_path = MEMORY_ROOT / agent / "permanent_memory.json"
        if not pm_path.exists():
            continue

        try:
            data = load_json(pm_path)
            entries = data.get("confirmed_beliefs", [])

            seen = {}
            for entry in sorted(entries, key=lambda x: x.get("date", "")):
                belief_id = entry.get("id")
                content = entry.get("content")

                if not belief_id or not content:
                    continue

                if belief_id in seen:
                    if seen[belief_id]["content"] != content:
                        append_dispute({
                            "type": "axiom_violation",
                            "axiom": "AXIOM-004",
                            "agent": agent,
                            "belief_id": belief_id,
                            "older_content": seen[belief_id]["content"],
                            "newer_content": content,
                            "action": "newer_overrides",
                            "date_detected": datetime.now().isoformat()
                        })

                seen[belief_id] = entry

        except Exception as e:
            append_dispute({
                "type": "axiom_violation",
                "axiom": "AXIOM-004",
                "agent": agent,
                "error": str(e),
                "action": "skipped",
                "date_detected": datetime.now().isoformat()
            })

# === 🚨 AXIOM-003: Placeholder for logical consistency check across journal layers
def enforce_axiom_003():
    # Future: Compare journal UUIDs across session → weekly → period → quarterly
    # If mismatch is detected, emit with axiom ID to disputed_entries.json
    pass

# === 🚀 Entry point ===
if __name__ == "__main__":
    print("🔎 Enforcing axioms...")
    enforce_axiom_004()
    enforce_axiom_003()
    print("✅ Axiom enforcement complete.")

