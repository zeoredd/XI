#!/usr/bin/env python3
"""
🛠️ interactive_conflict_resolver.py
Allows a human to interactively resolve flagged memory conflicts from `disputed_entries.json`.
Offers suggested resolution and amends the memory via script if approved.
"""

import json
from pathlib import Path
from datetime import datetime
import subprocess

# === 📁 Config ===
DISPUTE_LOG = Path("memory/sentinel/disputed_entries.json")
AMEND_SCRIPT = Path("common_abilities/amend_memory_entry.py")  # assumed to exist

# === 🧠 Load disputed entries ===
def load_disputes():
    if not DISPUTE_LOG.exists():
        print("✅ No disputes found.")
        return []
    with open(DISPUTE_LOG, "r") as f:
        return json.load(f)

# === 💾 Save updated disputes ===
def save_disputes(entries):
    with open(DISPUTE_LOG, "w") as f:
        json.dump(entries, f, indent=2)

# === 🎯 Filter unresolved ===
def get_unresolved(entries):
    return [e for e in entries if not e.get("resolved")]

# === 🧰 Prompt user for resolution ===
def prompt_resolution(entry):
    print("\n🚨 Conflict Detected:")
    print(f"Agent: {entry.get('agent')}")
    print(f"Type: {entry.get('type')}")
    print(f"Axiom: {entry.get('axiom')}")
    print(f"Belief ID: {entry.get('belief_id')}")
    print("--- Older Belief ---")
    print(entry.get("older_content", "(unknown)"))
    print("--- Newer Belief ---")
    print(entry.get("newer_content", "(unknown)"))

    print("\n💡 Suggestion: Accept newer version and amend old?")
    decision = input("[y/n]? ").strip().lower()

    if decision == "y":
        try:
            subprocess.run([
                "python3", str(AMEND_SCRIPT),
                "--agent", entry["agent"],
                "--belief_id", entry["belief_id"],
                "--new_content", entry["newer_content"]
            ], check=True)
            print("✅ Amendment script triggered.")
            entry["resolved"] = True
            entry["resolved_at"] = datetime.now().isoformat()
            entry["resolution_action"] = "newer_overrides"
        except Exception as e:
            print(f"❌ Error running amend script: {e}")
    else:
        reason = input("📝 Reason for rejecting (optional): ").strip()
        entry["resolved"] = False
        entry["rejection_reason"] = reason or "none given"
        print("⏸️ Skipped.")

# === 🚀 Main ===
def main():
    print("🔍 Loading disputed memory entries...")
    all_entries = load_disputes()
    unresolved = get_unresolved(all_entries)

    if not unresolved:
        print("🎉 All disputes are resolved!")
        return

    for entry in unresolved:
        prompt_resolution(entry)

    save_disputes(all_entries)
    print("💾 Dispute log updated.")

if __name__ == "__main__":
    main()

