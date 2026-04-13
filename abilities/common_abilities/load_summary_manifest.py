# 📜 load_summary_manifest.py
# Shared utility for loading summary entries from an agent or thread manifest

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List

# 🛠 Main loader function
def load_manifest(agent_or_path: str,
                  max_entries: int = 5,
                  filter_type: Optional[str] = None,
                  filter_tag: Optional[str] = None,
                  after_date: Optional[str] = None,
                  before_date: Optional[str] = None) -> List[dict]:
    """
    Load recent summary entries from an agent or thread manifest.
    :param agent_or_path: agent name (e.g. 'hermes') or full relative path (e.g. 'threads/idea_generator')
    :param max_entries: max number of entries to return (default 5)
    :param filter_type: only return entries of this type (e.g. 'session')
    :param filter_tag: only return entries with this tag (e.g. 'daily_briefing')
    :param after_date: return entries after this date (inclusive), format YYYY-MM-DD
    :param before_date: return entries before this date (inclusive), format YYYY-MM-DD
    :return: list of summary entries
    """

    # 🔍 Determine manifest path
    manifest_path = Path(agent_or_path) / "summary_manifest.json" if "/" in agent_or_path or agent_or_path.startswith("threads") \
                    else Path("memory") / agent_or_path / "summary_manifest.json"

    if not manifest_path.exists():
        print(f"❌ Manifest not found: {manifest_path}")
        return []

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        print("❌ Failed to parse JSON.")
        return []

    if "summaries" not in data or not isinstance(data["summaries"], list):
        print("❌ Invalid manifest structure.")
        return []

    entries = data["summaries"]

    # 📅 Optional date filtering
    def within_date(entry):
        date_str = entry.get("date")
        if not date_str:
            return False
        if after_date and date_str < after_date:
            return False
        if before_date and date_str > before_date:
            return False
        return True

    # 🔎 Apply filters
    filtered = [e for e in entries
                if (not filter_type or e.get("type") == filter_type)
                and (not filter_tag or e.get("tag") == filter_tag)
                and within_date(e)]

    # 🕓 Sort by timestamp descending
    sorted_entries = sorted(filtered, key=lambda e: e.get("timestamp", ""), reverse=True)

    return sorted_entries[:max_entries]

# 🧪 Example usage
if __name__ == "__main__":
    latest = load_manifest("hermes", max_entries=3, filter_type="session")
    for entry in latest:
        print("---")
        print(json.dumps(entry, indent=2))

