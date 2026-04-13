# 📜 append_summary_manifest.py
# Secure, append-only manifest writer for XI agent summaries
# Only accepts prewritten cache entries, validates and appends to summary_manifest.json

import json
import sys
from datetime import datetime
from pathlib import Path

# 🛡 Field limits
MAX_TOTAL_CHARS = 2048
MAX_TAG_LEN = 50
MAX_TYPE_LEN = 30
MAX_PATH_LEN = 200
MAX_KEYWORDS = 10
MAX_KEYWORD_LEN = 30

# 📁 Usage: python3 append_summary_manifest.py hermes

def validate_entry(entry, agent_name):
    required_fields = ["type", "tag", "path", "written_by", "signed"]
    for field in required_fields:
        if field not in entry:
            return False, f"Missing required field: {field}"

    # Length checks
    if len(entry["type"]) > MAX_TYPE_LEN:
        return False, "Field 'type' too long"
    if len(entry["tag"]) > MAX_TAG_LEN:
        return False, "Field 'tag' too long"
    if len(entry["path"]) > MAX_PATH_LEN:
        return False, "Field 'path' too long"
    if entry["written_by"] != agent_name:
        return False, "Field 'written_by' does not match agent"

    # Keywords check
    keywords = entry.get("keywords", [])
    if not isinstance(keywords, list):
        return False, "Field 'keywords' must be a list"
    if len(keywords) > MAX_KEYWORDS:
        return False, "Too many keywords"
    if any(len(word) > MAX_KEYWORD_LEN for word in keywords):
        return False, "One or more keywords too long"

    # Total character limit
    flat = json.dumps(entry)
    if len(flat) > MAX_TOTAL_CHARS:
        return False, "Entry too large"

    return True, None

def extract_date_from_path(path):
    # Try to find a YYYYMMDD block in the filename
    import re
    match = re.search(r"(20\d{6})", path)
    if match:
        raw = match.group(1)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return datetime.utcnow().strftime("%Y-%m-%d")

def append_manifest(agent):
    cache_path = Path(f"memory/{agent}/summary_manifest_cache.json")
    manifest_path = Path(f"memory/{agent}/summary_manifest.json")

    if not cache_path.exists():
        print(f"❌ No cache file found for {agent}.")
        return

    with open(cache_path, "r", encoding="utf-8") as f:
        cache_data = json.load(f)

    if not isinstance(cache_data, dict) or "summaries" not in cache_data:
        print("❌ Invalid cache structure.")
        return

    entries = cache_data["summaries"]
    print(f"📦 Found {len(entries)} summary entries in cache.")

    # Load existing manifest or create new
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        manifest = {"summaries": []}

    # Deduplicate existing paths
    existing_paths = {e["path"] for e in manifest["summaries"]}
    added = 0

    for entry in entries:
        valid, reason = validate_entry(entry, agent)
        if not valid:
            print(f"⚠️ Skipping entry: {reason}")
            continue

        if entry["path"] in existing_paths:
            print(f"⚠️ Duplicate path already in manifest: {entry['path']}")
            continue

        # Add auto fields
        entry["timestamp"] = datetime.utcnow().isoformat()
        entry["date"] = extract_date_from_path(entry["path"])

        manifest["summaries"].append(entry)
        existing_paths.add(entry["path"])
        added += 1

    # Write back manifest
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Wipe cache after processing
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"summaries": []}, f, indent=2)

    print(f"✅ {added} entries appended to {agent}'s summary manifest.")
    print("🧼 Cache cleared.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 append_summary_manifest.py <agent_name>")
        sys.exit(1)

    append_manifest(sys.argv[1])

